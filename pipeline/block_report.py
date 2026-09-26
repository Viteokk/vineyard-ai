"""Per-block task list for the web map -> web/data/blocks_report.json (and out/blocks_report.csv).

For each vineyard_id, from measurements.csv (same numbers the jury sees), the inspection targets and blocks.geojson:
  rows, disrupted rows (%), status (green < 10 %, yellow 10-30 %, red > 30 % disrupted), gap length, missing vines
  (~ gap length / 1.2 m, the row spacing along the row measured on the reference tiles), waste in the block,
  canopy / inter-row / block area, the disrupted row_ids, and the block's bounding box for zooming.
Costs are not stored: the page multiplies with the cutting price and the MDL 52 000-80 000 / ha maintenance range
(challenge brief), so the user can change the price.
Check: the block sums equal the total line of measurements.csv (fails loudly otherwise).

Usage:  python -m pipeline.block_report [--measurements measurements.csv] [--targets out/targets.geojson]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from shapely.geometry import mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

VINE_SPACING_M = 1.2


def status(share: float) -> str:
    return "green" if share < 0.10 else ("yellow" if share <= 0.30 else "red")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--measurements", default=str(C.ROOT / "measurements.csv"))
    ap.add_argument("--targets", default=str(C.OUT / "targets.geojson"))
    ap.add_argument("--blocks", default=str(C.OUT / "blocks.geojson"))
    ap.add_argument("--out", default=str(C.ROOT / "web" / "data" / "blocks_report.json"))
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.measurements)))
    total = next(r for r in rows if r["level"] == "total")
    blocks = {r["vineyard_id"]: r for r in rows if r["level"] == "block"}
    disrupted = defaultdict(list)
    for r in rows:
        if r["level"] == "row" and r["row_structure"] == "disrupted":
            disrupted[r["vineyard_id"]].append(r["row_id"])
    gaps, waste = defaultdict(lambda: [0, 0.0]), defaultdict(int)
    if Path(a.targets).exists():
        for f in json.loads(Path(a.targets).read_text())["features"]:
            p = f["properties"]
            if p["type"] == "gap":
                gaps[p["vineyard_id"]][0] += 1
                gaps[p["vineyard_id"]][1] += p.get("gap_m", 0.0)
            elif p["type"] == "waste" and p.get("vineyard_id"):
                waste[p["vineyard_id"]] += 1
    geo, shapes = {}, {}
    if Path(a.blocks).exists():
        for f in json.loads(Path(a.blocks).read_text())["features"]:
            g = shape(f["geometry"])
            geo[f["properties"]["vineyard_id"]] = (round(g.area, 1), [round(v, 1) for v in g.bounds])
            shapes[f["properties"]["vineyard_id"]] = g.simplify(0.3)

    out = []
    for vid, b in sorted(blocks.items()):
        n = int(b["rows"])
        dis = sorted(disrupted.get(vid, []))
        share = len(dis) / n if n else 0.0
        ng, gl = gaps.get(vid, [0, 0.0])
        area, bbox = geo.get(vid, (None, None))
        out.append({"vineyard_id": vid, "rows": n, "disrupted_rows": len(dis), "disrupted_share": round(share, 3),
                    "status": status(share), "row_length_m": float(b["length_m"]), "gaps": ng,
                    "gap_length_m": round(gl, 1), "missing_vines": int(round(gl / VINE_SPACING_M)),
                    "waste": waste.get(vid, 0), "canopy_m2": float(b["canopy_m2"]), "interrow_m2": float(b["interrow_m2"]),
                    "block_area_m2": area, "bbox": bbox, "disrupted_row_ids": dis})
    order = {"red": 0, "yellow": 1, "green": 2}
    out.sort(key=lambda r: (order[r["status"]], -r["missing_vines"]))

    # the same totals as measurements.csv (per-block canopy / inter-row areas are unions inside each block)
    s_rows = sum(r["rows"] for r in out)
    s_len = sum(r["row_length_m"] for r in out)
    assert s_rows == int(total["rows"]), (s_rows, total["rows"])
    assert abs(s_len - float(total["length_m"])) < 1.0, (s_len, total["length_m"])
    assert len(out) == int(total["blocks"]), (len(out), total["blocks"])
    summary = {"blocks": len(out), "rows": s_rows, "row_length_m": round(s_len, 1),
               "canopy_m2": float(total["canopy_m2"]), "interrow_m2": float(total["interrow_m2"]),
               "missing_vines": sum(r["missing_vines"] for r in out), "gap_length_m": round(sum(r["gap_length_m"] for r in out), 1),
               "waste": sum(r["waste"] for r in out),
               "status": {k: sum(r["status"] == k for r in out) for k in ("red", "yellow", "green")},
               "vine_spacing_m": VINE_SPACING_M, "maintenance_mdl_per_ha": [52000, 80000], "cutting_price_mdl": 15}
    Path(a.out).write_text(json.dumps({"summary": summary, "blocks": out}, separators=(",", ":")))
    if shapes:                          # block outlines coloured by status on the map (simplified to 0.3 m)
        def rnd(c):
            return [rnd(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], 1), round(c[1], 1)]
        feats = [{"type": "Feature", "properties": {"vineyard_id": r["vineyard_id"], "status": r["status"]},
                  "geometry": {**mapping(shapes[r["vineyard_id"]]),
                               "coordinates": rnd(mapping(shapes[r["vineyard_id"]])["coordinates"])}}
                 for r in out if r["vineyard_id"] in shapes]
        (Path(a.out).parent / "blocks_status.geojson").write_text(json.dumps(
            {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}},
             "features": feats}, separators=(",", ":")))
    with open(C.OUT / "blocks_report.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["vineyard_id", "status", "rows", "disrupted_rows", "gaps", "gap_length_m", "missing_vines", "waste",
                    "canopy_m2", "interrow_m2", "disrupted_row_ids"])
        for r in out:
            w.writerow([r["vineyard_id"], r["status"], r["rows"], r["disrupted_rows"], r["gaps"], r["gap_length_m"],
                        r["missing_vines"], r["waste"], r["canopy_m2"], r["interrow_m2"], " ".join(r["disrupted_row_ids"])])
    print(f"{len(out)} blocks: {summary['status']} · {summary['missing_vines']} missing vines · totals match "
          f"measurements.csv ({s_rows} rows, {s_len / 1000:.2f} km) -> {a.out}")


if __name__ == "__main__":
    main()
