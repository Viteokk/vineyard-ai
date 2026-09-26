"""Split the inspector tour into day tours that each start and end at START and fit a time budget.

The full tour (route.geojson) visits the targets in an optimised order. The visited targets are cut into K groups
of consecutive stops (equal shares of the tour length); each group gets its own closed route with pipeline.route
(same passable grid, same outside-share budget, cached distances). K grows until every tour fits
speed x hours. Output: web/data/tours/tour_<k>.geojson + targets_<k>.geojson + index.json.

Usage:  python -m pipeline.tours [--speed 4] [--hours 6] [--time 15]
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

from shapely.geometry import LineString, Point, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

WEB = C.ROOT / "web" / "data" / "tours"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", default=str(C.ROOT / "route.geojson"))
    ap.add_argument("--targets", default=str(C.OUT / "targets_inspector.geojson"))
    ap.add_argument("--speed", type=float, default=4.0, help="km/h")
    ap.add_argument("--hours", type=float, default=6.0, help="walking hours per day")
    ap.add_argument("--time", type=int, default=15, help="TSP time limit per tour (s)")
    ap.add_argument("--max-days", type=int, default=8)
    a = ap.parse_args()
    budget = a.speed * 1000 * a.hours
    line = shape(json.loads(Path(a.route).read_text())["features"][0]["geometry"])
    feats = [f for f in json.loads(Path(a.targets).read_text())["features"] if f["properties"].get("visited")]
    feats.sort(key=lambda f: line.project(Point(f["geometry"]["coordinates"])))
    pos = [line.project(Point(f["geometry"]["coordinates"])) for f in feats]
    WEB.mkdir(parents=True, exist_ok=True)
    k = max(1, math.ceil(line.length / budget))
    while k <= a.max_days:
        tours, ok = [], True
        for d in range(k):
            lo, hi = d * line.length / k, (d + 1) * line.length / k
            group = [f for f, p in zip(feats, pos) if lo <= p < hi or (d == k - 1 and p >= hi)]
            tin = C.OUT / f"tour_in_{d + 1}.geojson"
            tin.write_text(json.dumps({"type": "FeatureCollection", "features": group}))
            rout, tout = WEB / f"tour_{d + 1}.geojson", WEB / f"targets_{d + 1}.geojson"
            subprocess.run([sys.executable, "-m", "pipeline.route", "--mode", "inspector", "--targets", str(tin),
                            "--out", str(rout), "--targets-out", str(tout), "--time", str(a.time)],
                           cwd=C.ROOT, check=True, stdout=subprocess.DEVNULL)
            p = json.loads(rout.read_text())["features"][0]["properties"]
            tours.append({"day": d + 1, "route": f"data/tours/{rout.name}", "targets": f"data/tours/{tout.name}",
                          "length_m": p["length_m"], "minutes": round(p["length_m"] / (a.speed * 1000) * 60),
                          "targets_total": p["targets_total"], "targets_visited": p["targets_visited"],
                          "outside_share": p["outside_share"]})
            print(f"  k={k} day {d + 1}: {p['length_m'] / 1000:.2f} km, {p['targets_visited']}/{p['targets_total']} "
                  f"targets, outside {p['outside_share'] * 100:.2f} %", flush=True)
            if p["length_m"] > budget:
                ok = False
                break
        if ok:
            break
        k += 1
    for old in WEB.glob("*.geojson"):
        if int(old.stem.split("_")[-1]) > len(tours):
            old.unlink()
    total_t = sum(t["targets_visited"] for t in tours)
    index = {"speed_kmh": a.speed, "hours": a.hours, "budget_m": budget, "days": len(tours),
             "targets_full_tour": len(feats), "targets_visited": total_t, "tours": tours}
    (WEB / "index.json").write_text(json.dumps(index, indent=1))
    print(f"{len(tours)} day tours of <= {a.hours:g} h at {a.speed:g} km/h: "
          f"{sum(t['length_m'] for t in tours) / 1000:.2f} km, {total_t}/{len(feats)} targets of the full tour "
          f"({total_t / max(len(feats), 1) * 100:.0f} %) -> {WEB / 'index.json'}")


if __name__ == "__main__":
    main()
