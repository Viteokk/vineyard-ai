"""Environmental indicators per vineyard block (activity data for ISO 14001 clause 9.1, monitoring and measurement).

Per vineyard_id, from the pipeline outputs (EPSG:32635, metres, horizontal):
  block_area_ha         block polygon area (blocks_report.json)
  waste_count           annotated waste inside the block;  waste_per_ha = waste_count / block_area_ha
  waste_candidates_ai   AI waste candidates (not confirmed) inside the block, for information
  bare_soil_share, vegetation_share, mixed_share, unassessable_share
                        inter-row cover, area-weighted over the block's interrow_area polygons
  missing_vines         ~ gap length / 1.2 m vine spacing (blocks_report.json)
  missing_vines_share   gap length / row length
  inspection_km_block   walking km to inspect only this block (approximation, see below)
  inspection_km_saved   full inspection tour km - inspection_km_block
Approximation for the km (a separate optimal route per block would take ~1 min each): the official route.geojson
visits the targets in a known order; km_block = DETOUR x (straight-line distance between consecutive targets of the
same block in that order + 2 x straight-line distance START -> nearest target of the block), DETOUR = 1.3 (assumed
walking detour along inter-rows and passages). Blocks with no visited target: inspection_km_block = null.
Activity data only: no GHG / CO2 calculation (ISO 14064-1 out of scope).

Outputs: web/data/env_indicators.json, out/env_indicators.csv (one row per block + TOTAL). Totals are checked
against blocks_report.json (fails loudly if they differ by more than 1 %).

Usage:  python -m pipeline.env_indicators
"""
from __future__ import annotations

import csv
import json
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from shapely.geometry import LineString, Point, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

WEB = C.ROOT / "web" / "data"
FLIGHT = "2025-05-20"
DETOUR = 1.3
COVERS = ("bare_soil", "vegetation", "mixed", "unassessable")
COLS = ["vineyard_id", "block_area_ha", "waste_count", "waste_per_ha", "waste_candidates_ai", "bare_soil_share", "vegetation_share",
        "mixed_share", "unassessable_share", "missing_vines", "missing_vines_share", "row_length_m", "inspection_km_block", "inspection_km_saved"]


def fc(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text())["features"]


def main() -> None:
    t0 = time.time()
    rep = json.loads((WEB / "blocks_report.json").read_text())
    blocks, summ = rep["blocks"], rep["summary"]
    cover = defaultdict(lambda: defaultdict(float))
    for f in fc(WEB / "pred" / "interrows.geojson"):
        p = f["properties"]
        a = p.get("area_m2") or shape(f["geometry"]).area
        cover[p.get("vineyard_id")][p.get("interrow_cover", "unassessable")] += a
    cand = defaultdict(int)
    for f in fc(WEB / "waste_candidates.geojson"):
        if f["properties"].get("vineyard_id"):
            cand[f["properties"]["vineyard_id"]] += 1

    route = LineString(fc(C.ROOT / "route.geojson")[0]["geometry"]["coordinates"])
    full_km = route.length / 1000
    start = Point(fc(C.ROUTE_IN / "start.geojson")[0]["geometry"]["coordinates"])
    seq = sorted(((f["properties"]["order"], f["properties"].get("vineyard_id"), Point(f["geometry"]["coordinates"]))
                  for f in fc(WEB / "targets_inspector.geojson") if f["properties"].get("visited") and f["properties"].get("order")),
                 key=lambda t: t[0])
    inblock, near = defaultdict(float), {}
    for (_, v0, p0), (_, v1, p1) in zip(seq, seq[1:]):                # consecutive targets of the same block
        if v0 == v1:
            inblock[v0] += p0.distance(p1)
    for _, vid, pt in seq:
        near[vid] = min(near.get(vid, float("inf")), pt.distance(start))

    rows = []
    for b in sorted(blocks, key=lambda x: x["vineyard_id"]):
        vid = b["vineyard_id"]
        area_ha = b["block_area_m2"] / 1e4
        cv = cover[vid]
        tot = sum(cv.values())
        share = {c: round(cv[c] / tot, 4) if tot else 0.0 for c in COVERS}
        L = b["row_length_m"]
        km_block = None
        if vid in near:
            km_block = round(DETOUR * (inblock[vid] + 2 * near[vid]) / 1000, 2)
        rows.append({"vineyard_id": vid, "block_area_ha": round(area_ha, 4), "waste_count": b["waste"],
                     "waste_per_ha": round(b["waste"] / area_ha, 2) if area_ha else 0.0, "waste_candidates_ai": cand[vid],
                     **{f"{c}_share": share[c] for c in COVERS}, "missing_vines": b["missing_vines"],
                     "missing_vines_share": round(min(b["gap_length_m"] / L, 1.0), 4) if L else 0.0, "row_length_m": round(L, 1),
                     "inspection_km_block": km_block, "inspection_km_saved": round(full_km - km_block, 2) if km_block is not None else None,
                     "_cover_m2": tot, "_gap_m": b["gap_length_m"]})

    area = sum(r["block_area_ha"] for r in rows)
    ctot = sum(r["_cover_m2"] for r in rows)
    total = {"vineyard_id": "TOTAL", "block_area_ha": round(area, 4), "waste_count": sum(r["waste_count"] for r in rows),
             "waste_per_ha": round(sum(r["waste_count"] for r in rows) / area, 2) if area else 0.0,
             "waste_candidates_ai": sum(r["waste_candidates_ai"] for r in rows),
             **{f"{c}_share": round(sum(r[f"{c}_share"] * r["_cover_m2"] for r in rows) / ctot, 4) if ctot else 0.0 for c in COVERS},
             "missing_vines": sum(r["missing_vines"] for r in rows),
             "missing_vines_share": round(sum(r["_gap_m"] for r in rows) / sum(r["row_length_m"] for r in rows), 4),
             "row_length_m": round(sum(r["row_length_m"] for r in rows), 1), "inspection_km_block": round(full_km, 2), "inspection_km_saved": None}

    # consistency with blocks_report.json (AC2): fail loudly
    def close(a, b, what):
        assert abs(a - b) <= 0.01 * max(abs(b), 1e-9), f"{what}: {a:.2f} vs blocks_report {b:.2f} (> 1 %)"
    close(area * 1e4, sum(b["block_area_m2"] for b in blocks), "block area")
    close(total["missing_vines"], summ["missing_vines"], "missing vines")
    close(ctot, summ["interrow_m2"], "inter-row area")
    close(total["row_length_m"], summ["row_length_m"], "row length")

    for r in rows:
        r.pop("_cover_m2"), r.pop("_gap_m")
    meta = {"source": f"Ortofoto UAV Sireț3, zbor {FLIGHT}, 2,5 cm/px, EPSG:32635; adnotări vineyard-ai (pre-adnotări AI, corectate în Marcaj)",
            "method": "Suprafețe orizontale din poligoane; acoperirea inter-rândurilor ponderată cu aria (atributul interrow_cover); "
                      "butuci lipsă ≈ lungimea golurilor / 1,2 m; km inspecție pe bloc ≈ 1,3 × (distanța în linie dreaptă între țintele succesive "
                      "ale blocului în ordinea traseului oficial + 2 × distanța de la START) (aproximare, vezi README).",
            "units": {"block_area_ha": "ha", "waste_per_ha": "bucăți/ha", "*_share": "fracție 0–1", "missing_vines": "butuci",
                      "row_length_m": "m", "inspection_km_*": "km"},
            "scope": "Date de activitate (ISO 14001, clauza 9.1). Fără calcul de emisii GES / CO2 (ISO 14064-1 în afara scopului).",
            "flight_date": FLIGHT, "generated": date.today().isoformat(), "full_tour_km": round(full_km, 2)}
    (WEB / "env_indicators.json").write_text(json.dumps({"meta": meta, "blocks": rows, "total": total}, ensure_ascii=False, separators=(",", ":")))
    with (C.OUT / "env_indicators.csv").open("w", newline="", encoding="utf-8") as fh:
        for k in ("source", "method", "scope"):
            fh.write(f"# {k}: {meta[k]}\n")
        fh.write("# units: " + "; ".join(f"{k} = {v}" for k, v in meta["units"].items()) + "\n")
        w = csv.writer(fh)
        w.writerow(COLS)
        for r in rows + [total]:
            w.writerow(["" if r.get(c) is None else r.get(c) for c in COLS])
    dt = round(time.time() - t0, 1)
    tp = C.OUT / "timing.json"
    tj = json.loads(tp.read_text()) if tp.exists() else {}
    tj["env_indicators"] = dt
    tp.write_text(json.dumps(tj, indent=1))
    print(f"env indicators: {len(rows)} blocks + TOTAL, {total['block_area_ha']:.2f} ha, bare soil {total['bare_soil_share']:.0%}, "
          f"missing vines {total['missing_vines']} -> web/data/env_indicators.json, out/env_indicators.csv ({dt}s)")


if __name__ == "__main__":
    main()
