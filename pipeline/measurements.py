"""measurements.csv: block and row counts, row lengths, canopy and inter-row areas by vineyard_id / row_id.

All horizontal, EPSG:32635 (metres), no terrain correction. Canopy area = area of the UNION of canopy polygons
(overlaps counted once); a row's length = sum of its segments over all tiles.

One CSV, three levels (column `level`):
  total  whole study area: blocks, rows, total row length, canopy / inter-row area
  block  one line per vineyard_id: rows, row length, canopy / inter-row area
  row    one line per row_id: vineyard_id, length_m, segments (tiles), row_structure values seen

Usage:  python -m pipeline.measurements --inp out/pre_global.xml [--out measurements.csv]
        (Sunday: --inp out/marcaj_export.xml)
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.to_geojson import convert  # noqa: E402

COLS = ["level", "vineyard_id", "row_id", "blocks", "rows", "segments", "length_m",
        "canopy_m2", "canopy_ha", "interrow_m2", "interrow_ha", "row_structure"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", default=str(C.OUT / "pre_global.xml"))
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--out", default=str(C.ROOT / "measurements.csv"))
    a = ap.parse_args()
    layers, _ = convert(Path(a.inp), Path(a.tiles))
    row_len, row_seg, row_blk, row_struct = defaultdict(float), defaultdict(int), {}, defaultdict(set)
    for f in layers.get("rows", []):
        p = f["properties"]
        g = shape(f["geometry"])
        row_len[p["row_id"]] += g.length
        row_seg[p["row_id"]] += 1
        row_blk[p["row_id"]] = p["vineyard_id"]
        row_struct[p["row_id"]].add(p.get("row_structure", ""))
    can_by, int_by = defaultdict(list), defaultdict(list)
    for f in layers.get("canopies", []):
        can_by[f["properties"]["vineyard_id"]].append(shape(f["geometry"]))
    for f in layers.get("interrows", []):
        int_by[f["properties"]["vineyard_id"]].append(shape(f["geometry"]))
    blocks = sorted({b for b in list(row_blk.values()) + list(can_by) + list(int_by) if b})

    def area(gs):
        return unary_union(gs).area if gs else 0.0

    out = []
    tot_can = area([g for gs in can_by.values() for g in gs])
    tot_int = area([g for gs in int_by.values() for g in gs])
    out.append({"level": "total", "blocks": len(blocks), "rows": len(row_len), "segments": sum(row_seg.values()),
                "length_m": round(sum(row_len.values()), 2), "canopy_m2": round(tot_can, 2),
                "canopy_ha": round(tot_can / 1e4, 4), "interrow_m2": round(tot_int, 2),
                "interrow_ha": round(tot_int / 1e4, 4)})
    for b in blocks:
        rids = [r for r, bb in row_blk.items() if bb == b]
        ca, ia = area(can_by.get(b, [])), area(int_by.get(b, []))
        out.append({"level": "block", "vineyard_id": b, "rows": len(rids), "segments": sum(row_seg[r] for r in rids),
                    "length_m": round(sum(row_len[r] for r in rids), 2), "canopy_m2": round(ca, 2),
                    "canopy_ha": round(ca / 1e4, 4), "interrow_m2": round(ia, 2), "interrow_ha": round(ia / 1e4, 4)})
    for r in sorted(row_len, key=lambda r: (row_blk[r], r)):
        out.append({"level": "row", "vineyard_id": row_blk[r], "row_id": r, "segments": row_seg[r],
                    "length_m": round(row_len[r], 2), "row_structure": "|".join(sorted(row_struct[r] - {""}))})
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(out)
    t = out[0]
    print(f"{t['blocks']} blocks, {t['rows']} rows, {t['length_m'] / 1000:.2f} km of rows, "
          f"canopy {t['canopy_ha']} ha, inter-rows {t['interrow_ha']} ha -> {a.out}")


if __name__ == "__main__":
    main()
