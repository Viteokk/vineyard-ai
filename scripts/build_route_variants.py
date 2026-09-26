"""Precomputed inspector routes for other gap thresholds -> web/data/variants/ (the static site has no Python).

The live server (pipeline.serve) computes any threshold on demand; GitHub Pages shows these instead.
Usage:  python scripts/build_route_variants.py [--gaps 5 8 10] [--time 20]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

WEB = C.ROOT / "web" / "data" / "variants"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gaps", type=float, nargs="+", default=[5, 8, 10])
    ap.add_argument("--time", type=int, default=20)
    a = ap.parse_args()
    WEB.mkdir(parents=True, exist_ok=True)
    feats = json.loads((C.OUT / "targets.geojson").read_text())["features"]
    index = {}
    for g in a.gaps:
        keep = [f for f in feats if f["properties"]["type"] == "waste" or f["properties"].get("gap_m", 0) >= g]
        tin = C.OUT / f"targets_gap{g:g}.geojson"
        tin.write_text(json.dumps({"type": "FeatureCollection", "features": keep}))
        route, tout = WEB / f"route_inspector_gap{g:g}.geojson", WEB / f"targets_inspector_gap{g:g}.geojson"
        subprocess.run([sys.executable, "-m", "pipeline.route", "--mode", "inspector", "--targets", str(tin),
                        "--out", str(route), "--targets-out", str(tout), "--time", str(a.time)], cwd=C.ROOT, check=True)
        p = json.loads(route.read_text())["features"][0]["properties"]
        index[f"{g:g}"] = {"route": f"data/variants/{route.name}", "targets": f"data/variants/{tout.name}", **p}
        print(f"gap >= {g:g} m: {p['length_m'] / 1000:.2f} km, {p['targets_visited']}/{p['targets_total']} targets")
    (WEB / "index.json").write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()
