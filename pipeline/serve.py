"""Local server: the same web map as GitHub Pages plus live computation on the laptop (demo / field office).

  GET  /                  web/ (static site; it detects the API and switches to "live" mode)
  GET  /api/status        {"live": true, "model": <weights or null>, "device": ...}
  POST /api/analyze       body = one GeoTIFF (header X-Filename; ?canopy=classical|model)
                          -> classical detector + the AI model on that tile -> GeoJSON layers in the tile's CRS,
                             a preview image with its bounds, counts / lengths / areas and the processing time
  POST /api/route         {"mode": "inspector"|"farmer", "min_gap": 3.0, "time": 20, "start": [x, y], "zone": GeoJSON geometry,
                           "points": [{"id": "A01", "x": .., "y": ..}, ...]}   (points: route ONLY through these, e.g.
                           register mismatches; the result is checked with pipeline.validate)
                          (start and zone optional: a custom START, and only the targets inside the zone)
                          -> {"job": id}: pipeline.route on the targets that pass the filters (cached distances)
  GET  /api/job/<id>      {"state": "running"|"done"|"error", "log": [...], "route": ..., "targets": ..., "seconds"}
  POST /api/compliance    body = registry extract as CSV (registry/rvv_template.csv columns)
                          -> pipeline.compliance on it -> the new compliance.json (written to out/live/, the
                             published web/data/compliance.json with the demo registry is left as it is)

Usage:  python -m pipeline.serve [--port 8000] [--weights runs/vineyard/multi11/weights/best.pt] [--device cpu]
Binds to 127.0.0.1 only. Nothing here changes route.geojson / measurements.csv: live results go to out/live/.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import subprocess
import sys
import threading
import time
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.baseline import P, process_tile  # noqa: E402
from pipeline.cvat_io import write_cvat  # noqa: E402
from pipeline.tiles import Tile  # noqa: E402
from pipeline.to_geojson import convert  # noqa: E402

WEB = C.ROOT / "web"
LIVE = C.OUT / "live"
MAX_UPLOAD = 200 * 2**20
TYPES = {"inspector": {"gap", "waste"}, "farmer": {"waste"}}
JOBS: dict[str, dict] = {}
STATE = {"model": None, "weights": None, "device": "cpu", "lock": threading.Lock()}


def read_geotiff(path: Path, name: str) -> Tile:
    """Any georeferenced GeoTIFF (ModelTiepoint + ModelPixelScale), not only the Sireț3 tile names."""
    with Image.open(path) as im:
        tags = im.tag_v2
        if 33922 not in tags or 33550 not in tags:
            raise ValueError("fișierul nu are georeferențiere GeoTIFF (ModelTiepoint / ModelPixelScale)")
        tie, scale = tags[33922], tags[33550]
        w, h = im.size
    m = re.search(r"r(\d+)_c(\d+)", name)
    return Tile(name, path, int(m.group(1)) if m else 0, int(m.group(2)) if m else 0,
                float(tie[3]), float(tie[4]), float(scale[0]), w, h)


def model():
    if STATE["model"] is None and STATE["weights"]:
        from ultralytics import YOLO
        STATE["model"] = YOLO(STATE["weights"])
    return STATE["model"]


def analyze(data: bytes, name: str, canopy: str) -> dict:
    t0 = time.time()
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(name).name) or "upload.tif"
    if not name.lower().endswith((".tif", ".tiff")):
        name += ".tif"
    d = LIVE / "upload" / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_bytes(data)
    tile = read_geotiff(path, name)
    if abs(tile.res - C.PX) > 0.3 * C.PX:
        raise ValueError(f"rezoluția e {tile.res * 100:.1f} cm/px; detectorul e calibrat pe {C.PX * 100:.1f} cm/px (±30 %)")
    objs = process_tile(tile, P())
    t_classic = time.time() - t0
    n_waste, t_model = 0, 0.0
    if model() is not None:
        from pipeline.infer_multi import tile_objects
        t1 = time.time()
        with STATE["lock"]:
            objs, boxes = tile_objects(model(), tile.read(), objs, canopy, 0.25, 0.5, STATE["device"])
        n_waste = sum(o["label"] == "waste" for o in objs)
        t_model = time.time() - t1
    xml = d / "annotations.xml"
    write_cvat({name: objs}, xml)
    layers, summary = convert(xml, d)
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((1024, 1024))
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=80)
    summary.pop("row_lengths", None)
    return {"name": name, "bounds": [round(v, 2) for v in tile.bounds], "res_m": tile.res,
            "image": "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode(),
            "layers": {k: {"type": "FeatureCollection", "features": v} for k, v in layers.items()},
            "summary": summary, "waste_boxes": n_waste, "model": Path(STATE["weights"]).name if STATE["weights"] else None,
            "canopy_source": canopy if model() is not None else "classical",
            "seconds": {"classical": round(t_classic, 1), "model": round(t_model, 1), "total": round(time.time() - t0, 1)}}


def run_route(job: dict, mode: str, min_gap: float, tlimit: int, start=None, zone=None, points=None) -> None:
    d = LIVE / job["id"]
    d.mkdir(parents=True, exist_ok=True)
    feats = json.loads((C.OUT / "targets.geojson").read_text())["features"]
    keep = [f for f in feats if f["properties"]["type"] in TYPES[mode]
            and (f["properties"]["type"] == "waste" or f["properties"].get("gap_m", 0) >= min_gap)]
    if points:                                   # only the given visit points (never written to route.geojson)
        keep = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(p["x"]), float(p["y"])]},
                 "properties": {"type": "gap", "gap_m": 99.0, "id": str(p["id"]), "x": float(p["x"]), "y": float(p["y"]),
                                "kind": "visit", "reachable": True}} for p in points]
        min_gap = 0.0
    if zone:
        from shapely.geometry import Point, shape
        z = shape(zone).buffer(0)
        keep = [f for f in keep if z.contains(Point(f["geometry"]["coordinates"]))]
        job["log"].append(f"zonă: {z.area / 1e4:.2f} ha")
    (d / "targets_in.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": keep}))
    job["log"].append(f"{len(keep)} ținte ({mode}, gol ≥ {min_gap:g} m)")
    if not keep:
        job.update(state="done", route=None, targets={"type": "FeatureCollection", "features": []})
        return
    cmd = [sys.executable, "-m", "pipeline.route", "--mode", mode, "--targets", str(d / "targets_in.geojson"),
           "--out", str(d / "route.geojson"), "--targets-out", str(d / "targets.geojson"), "--time", str(tlimit)]
    if start:
        cmd += ["--start", f"{float(start[0]):.2f},{float(start[1]):.2f}"]
        job["log"].append(f"START ales: {float(start[0]):.1f}, {float(start[1]):.1f}")
    p = subprocess.Popen(cmd, cwd=C.ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in p.stdout:
        job["log"].append(line.rstrip())
        job["log"] = job["log"][-30:]
    p.wait()
    if p.returncode != 0:
        job["state"] = "error"
        return
    check = None
    if points and (C.OUT / "pre_global.xml").exists():          # official rules: <= 2 % outside, back at START
        v = subprocess.run([sys.executable, "-m", "pipeline.validate", "--route", str(d / "route.geojson"), "--inp", str(C.OUT / "pre_global.xml"),
                            "--targets", str(d / "targets_in.geojson"), "--json", str(d / "check.json")], cwd=C.ROOT, capture_output=True, text=True)
        job["log"] += [ln for ln in v.stdout.splitlines() if ln.strip()][-5:]
        if (d / "check.json").exists():
            check = json.loads((d / "check.json").read_text())
    job.update(state="done", route=json.loads((d / "route.geojson").read_text()),
               targets=json.loads((d / "targets.geojson").read_text()), check=check)


def compliance(data: bytes, name: str) -> dict:
    d = LIVE / "compliance" / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    src = d / (re.sub(r"[^A-Za-z0-9_.-]", "_", Path(name).name) or "registru.csv")
    src.write_bytes(data)
    out = d / "compliance.json"
    p = subprocess.run([sys.executable, "-m", "pipeline.compliance", "--registry-csv", str(src), "--out", str(out)],
                       cwd=C.ROOT, capture_output=True, text=True)
    if p.returncode != 0 or not out.exists():
        raise ValueError((p.stderr or p.stdout).strip().splitlines()[-1] if (p.stderr or p.stdout) else "compliance a eșuat")
    return json.loads(out.read_text())


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):            # keep the console for the API
        if self.path.startswith("/api/"):
            super().log_message(fmt, *args)

    def send_json(self, obj, code=200):
        body = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            return self.send_json({"live": True, "model": Path(STATE["weights"]).name if STATE["weights"] else None,
                                   "device": STATE["device"], "targets": (C.OUT / "targets.geojson").exists()})
        m = re.match(r"^/api/job/([0-9a-f]{8})$", self.path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self.send_json({"error": "job necunoscut"}, 404)
            return self.send_json({k: v for k, v in job.items() if k != "thread"} | {"seconds": round(time.time() - job["t0"], 1)})
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            return self.send_json({"error": "fișier prea mare (max 200 MB)"}, 413)
        data = self.rfile.read(n)
        try:
            if self.path.startswith("/api/analyze"):
                canopy = "model" if "canopy=model" in self.path else "classical"
                return self.send_json(analyze(data, self.headers.get("X-Filename", "upload.tif"), canopy))
            if self.path.startswith("/api/compliance"):
                return self.send_json(compliance(data, self.headers.get("X-Filename", "registru.csv")))
            if self.path.startswith("/api/route"):
                q = json.loads(data or b"{}")
                mode = q.get("mode", "inspector")
                if mode not in TYPES:
                    return self.send_json({"error": "mode"}, 400)
                if not (C.OUT / "targets.geojson").exists():
                    return self.send_json({"error": "lipsește out/targets.geojson: rulează întâi pipeline.run"}, 409)
                job = {"id": uuid.uuid4().hex[:8], "state": "running", "log": [], "t0": time.time(), "mode": mode}
                JOBS[job["id"]] = job
                threading.Thread(target=run_route, args=(job, mode, float(q.get("min_gap", 3.0)),
                                                          int(q.get("time", 20)), q.get("start"), q.get("zone"), q.get("points")), daemon=True).start()
                return self.send_json({"job": job["id"]})
        except Exception as e:                    # noqa: BLE001 - report to the page, keep serving
            return self.send_json({"error": str(e)}, 400)
        return self.send_json({"error": "necunoscut"}, 404)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--weights", default=str(C.ROOT / "runs/vineyard/multi11/weights/best.pt"))
    ap.add_argument("--device", default="cpu", help="cpu | mps (use cpu while a training run holds the GPU)")
    a = ap.parse_args()
    STATE["weights"] = a.weights if Path(a.weights).exists() else None
    STATE["device"] = a.device
    LIVE.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), partial(Handler, directory=str(WEB)))
    print(f"http://127.0.0.1:{a.port}  (web/ + API; model: {STATE['weights'] or 'none, classical only'})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
