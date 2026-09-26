"""Sunday: from the corrected Marcaj export to the deliverables in one command.

  python scripts/sunday.py EXPORT.zip [EXPORT2.zip ...]            # full run: route.geojson, route_waste.geojson,
                                                                    # measurements.csv, web data
  python scripts/sunday.py EXPORT.zip --dry out/dry2               # same chain into a separate folder (no deliverable
                                                                    # touched): to test the export before the real run
Accepts Marcaj / CVAT for images 1.1 exports: ZIPs or annotations.xml files (one per task or one for the project).
Steps:
  1. merge every annotations.xml, image names normalised to the tile file name (exports may prefix folders)
  2. report: tiles, objects per label, missing attributes (row_id / vineyard_id / row_structure / interrow_cover),
     tiles that changed against the uploaded pre-annotations (out/pre_global_v3.xml)
  3. out/marcaj_export.xml; global IDs: the IDs as annotated in Marcaj (what the jury counts), or --reblock to recompute
     them from geometry with pipeline.blocks (only if many rows lack IDs)
  4. targets -> routes (inspector, farmer) -> validation -> measurements (+ web data on a full run), timed
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat, write_cvat  # noqa: E402

PY = sys.executable


def sh(args, log):
    t = time.time()
    print("$ " + " ".join(map(str, args)), flush=True)
    r = subprocess.run([str(a) for a in args], cwd=C.ROOT, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip().splitlines()
    for ln in out[-6:]:
        print("   " + ln)
    log.append((" ".join(map(str, args[:3])), round(time.time() - t, 1), r.returncode))
    if r.returncode != 0:
        sys.exit(f"FAILED: {' '.join(map(str, args))}")
    return out


def load_export(paths):
    merged, sources = {}, Counter()
    tmp = Path(tempfile.mkdtemp())
    xmls = []
    for p in map(Path, paths):
        if p.suffix == ".zip":
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if n.endswith(".xml"):
                        dst = tmp / f"{len(xmls):03d}.xml"
                        dst.write_bytes(z.read(n))
                        xmls.append(dst)
        else:
            xmls.append(p)
    for x in xmls:
        for name, objs in read_cvat(x).items():
            tile = Path(name).name
            if tile in merged and len(merged[tile]) >= len(objs):
                sources["duplicate_kept_first"] += 1
                continue
            merged[tile] = objs
            sources["images"] += 1
    return merged, len(xmls)


def report(d):
    lab = Counter(o["label"] for v in d.values() for o in v)
    miss = Counter()
    for v in d.values():
        for o in v:
            a = o.get("attrs", {})
            if o["label"] in ("vineyard", "row", "interrow_area") and not a.get("vineyard_id"):
                miss[f"{o['label']} without vineyard_id"] += 1
            if o["label"] == "row":
                miss["row without row_id"] += not a.get("row_id")
                miss["row without row_structure"] += not a.get("row_structure")
            if o["label"] == "interrow_area":
                miss["interrow without interrow_cover"] += not a.get("interrow_cover")
    tiles = {p.name for p in C.TILES.glob("siret3_r*_c*.tif")}
    print(f"export: {len(d)} tiles ({len(tiles - set(d))} of the 311 missing), objects {dict(lab)}")
    bad = {k: v for k, v in miss.items() if v}
    print("missing attributes: " + (", ".join(f"{k}: {v}" for k, v in bad.items()) if bad else "none"))
    up = C.OUT / "pre_global_v3.xml"
    if up.exists():
        pre = read_cvat(up)
        changed = [t for t in tiles if len(d.get(t, [])) != len(pre.get(t, []))]
        print(f"tiles changed in Marcaj against the uploaded pre-annotations: {len(changed)}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="+", help="Marcaj export ZIP(s) or annotations.xml")
    ap.add_argument("--dry", default="", help="write everything into this folder; no deliverable is touched")
    ap.add_argument("--reblock", action="store_true", help="recompute vineyard_id / row_id from geometry (pipeline.blocks)")
    ap.add_argument("--route-time", type=int, default=60)
    a = ap.parse_args()
    t0, log = time.time(), []
    d, nx = load_export(a.export)
    print(f"read {nx} annotation file(s)")
    missing = report(d)
    dry = Path(a.dry) if a.dry else None
    base = dry or C.OUT
    base.mkdir(parents=True, exist_ok=True)
    exp = base / "marcaj_export.xml"
    write_cvat(d, exp)
    glob_xml = base / "marcaj_global.xml"
    blocks_geo = base / "blocks.geojson"                 # outlines read by block_report / cadastre / register / mismatch
    if a.reblock:
        sh([PY, "-m", "pipeline.blocks", "--inp", exp, "--out", glob_xml, "--geojson", blocks_geo], log)
    else:
        shutil.copy(exp, glob_xml)
        if missing.get("row without row_id", 0) > 20:
            print("WARNING: many rows without row_id; consider --reblock")
        sh([PY, "-m", "pipeline.blocks", "--inp", glob_xml, "--geojson", blocks_geo, "--outlines-only"], log)
    if dry:
        sh([PY, "-m", "pipeline.targets", "--inp", glob_xml, "--out", dry / "targets.geojson"], log)
        for mode, f in (("inspector", "route.geojson"), ("farmer", "route_waste.geojson")):
            sh([PY, "-m", "pipeline.route", "--mode", mode, "--inp", glob_xml, "--targets", dry / "targets.geojson",
                "--out", dry / f, "--targets-out", dry / f"targets_{mode}.geojson", "--time", a.route_time], log)
            sh([PY, "-m", "pipeline.validate", "--route", dry / f, "--inp", glob_xml, "--targets", dry / f"targets_{mode}.geojson",
                "--json", dry / f"route_check_{mode}.json"], log)
        sh([PY, "-m", "pipeline.measurements", "--inp", glob_xml, "--out", dry / "measurements.csv"], log)
    else:
        # map layers first: register / env_indicators / register_mismatch read web/data/pred/interrows.geojson
        sh([PY, "scripts/build_web_map.py", "--pred", glob_xml], log)
        sh([PY, "-m", "pipeline.run", "--from", "targets", "--to", "measure", "--inp", glob_xml, "--route-time", a.route_time], log)
        sh([PY, "-m", "pipeline.run", "--from", "web", "--to", "web", "--inp", glob_xml], log)
    print("\ntimings: " + ", ".join(f"{n} {s}s" for n, s, _ in log) + f" | total {round((time.time() - t0) / 60, 1)} min")


if __name__ == "__main__":
    main()
