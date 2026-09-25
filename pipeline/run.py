"""One command from the supplied tiles to route.geojson, route_waste.geojson and measurements.csv.

Stages (each is also a CLI on its own, see the module docstrings):
  detect    pipeline.baseline  (+ pipeline.infer_yolo when --weights is given)  -> out/detect.xml
  blocks    pipeline.blocks    global vineyard_id / row_id                        -> out/pre_global.xml
  targets   pipeline.targets   row gaps >= 5 m + waste                            -> out/targets.geojson
  route     pipeline.route     --mode inspector (blue) and --mode farmer (red)    -> route.geojson, route_waste.geojson
  validate  pipeline.validate  official route rules                               -> out/route_check_*.json
  measure   pipeline.measurements                                                 -> measurements.csv
  export    pipeline.export_cvat  Marcaj upload ZIPs                              -> out/upload/*.zip
  web       scripts.make_web_tiles + scripts.build_web_map                        -> web/data/
Timings per stage and the hardware go to out/timing.json (quoted in README.md).

Usage:  python -m pipeline.run --all                       # everything, classical detector
        python -m pipeline.run --all --weights runs/vineyard/canopy/weights/best.pt
        python -m pipeline.run --from targets              # re-run from a stage (e.g. Sunday, after Marcaj)
        python -m pipeline.run --from targets --inp out/marcaj_global.xml
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

STAGES = ["detect", "blocks", "targets", "route", "validate", "measure", "export", "web"]
PY = sys.executable


def sh(args: list[str]) -> float:
    t = time.time()
    print("$ " + " ".join(args), flush=True)
    subprocess.run(args, check=True, cwd=C.ROOT)
    return time.time() - t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--from", dest="start", choices=STAGES, default="detect")
    ap.add_argument("--to", dest="stop", choices=STAGES, default="web")
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--weights", default="", help="YOLO-seg weights: canopies from YOLO, rows from the classical detector")
    ap.add_argument("--inp", default="", help="for --from targets/route/...: the global xml to use (default out/pre_global.xml)")
    ap.add_argument("--route-time", type=int, default=60)
    a = ap.parse_args()
    if not a.all and a.start == "detect" and a.stop == "web":
        ap.error("use --all, or --from/--to")
    todo = STAGES[STAGES.index(a.start):STAGES.index(a.stop) + 1]
    C.OUT.mkdir(exist_ok=True)
    timing_path = C.OUT / "timing.json"
    timing = json.loads(timing_path.read_text()) if timing_path.exists() else {}
    timing["hardware"] = f"{platform.machine()} · {platform.platform()} · python {platform.python_version()}"
    detect_xml = C.OUT / "detect.xml"
    global_xml = Path(a.inp) if a.inp else C.OUT / "pre_global.xml"
    for stage in todo:
        t = time.time()
        if stage == "detect":
            sh([PY, "-m", "pipeline.baseline", "--tiles", a.tiles, "--out", str(C.OUT / "baseline_all.xml")])
            if a.weights:
                sh([PY, "-m", "pipeline.infer_yolo", "--weights", a.weights, "--base", str(C.OUT / "baseline_all.xml"),
                    "--tiles", a.tiles, "--out", str(detect_xml)])
            else:
                shutil.copy(C.OUT / "baseline_all.xml", detect_xml)
        elif stage == "blocks":
            sh([PY, "-m", "pipeline.blocks", "--inp", str(detect_xml), "--out", str(global_xml), "--tiles", a.tiles])
        elif stage == "targets":
            sh([PY, "-m", "pipeline.targets", "--inp", str(global_xml), "--tiles", a.tiles])
        elif stage == "route":
            for mode in ("inspector", "farmer"):
                sh([PY, "-m", "pipeline.route", "--mode", mode, "--inp", str(global_xml), "--tiles", a.tiles,
                    "--time", str(a.route_time)])
        elif stage == "validate":
            for mode, f in (("inspector", "route.geojson"), ("farmer", "route_waste.geojson")):
                subprocess.run([PY, "-m", "pipeline.validate", "--route", f, "--inp", str(global_xml), "--tiles", a.tiles,
                                "--targets", str(C.OUT / f"targets_{mode}.geojson"),
                                "--json", str(C.OUT / f"route_check_{mode}.json")], cwd=C.ROOT)
        elif stage == "measure":
            sh([PY, "-m", "pipeline.measurements", "--inp", str(global_xml), "--tiles", a.tiles, "--out", "measurements.csv"])
        elif stage == "export":
            sh([PY, "-m", "pipeline.export_cvat", "--inp", str(global_xml), "--tiles", a.tiles])
        elif stage == "web":
            sh([PY, "scripts/make_web_tiles.py"])
            sh([PY, "scripts/build_web_map.py", "--pred", str(global_xml)])
            web = C.ROOT / "web" / "data"
            for f in ("route.geojson", "route_waste.geojson"):
                shutil.copy(C.ROOT / f, web / f)
            for mode in ("inspector", "farmer"):
                for src, dst in ((f"targets_{mode}.geojson", f"targets_{mode}.geojson"), (f"route_check_{mode}.json", f"route_check_{mode}.json")):
                    if (C.OUT / src).exists():
                        shutil.copy(C.OUT / src, web / dst)
            if (C.OUT / "blocks.geojson").exists():
                shutil.copy(C.OUT / "blocks.geojson", web / "pred" / "blocks.geojson")
        timing[stage] = round(time.time() - t, 1)
        timing_path.write_text(json.dumps(timing, indent=1))
        print(f"== {stage}: {timing[stage]:.0f} s", flush=True)
    total = sum(v for k, v in timing.items() if k in STAGES)
    print(f"done: {', '.join(f'{k} {timing[k]:.0f}s' for k in STAGES if k in timing)} | total {total / 60:.1f} min on {timing['hardware']}")


if __name__ == "__main__":
    main()
