"""Possible unauthorised plantings / outdated register entries: where vines are (drone) vs where they are registered.

Reg. (EU) 1308/2013 art. 62-72 (authorisation scheme) and Reg. (EU) 2018/273 art. 7, 37 (register kept up to date).
Only real vineyards count: detected blocks with >= min_rows rows AND >= min_area_ha (register threshold, 0.15 ha);
smaller garden vineyards are listed as "below threshold", never flagged. Thresholds and legal texts:
registry/criteria.json -> "mismatch".

  Type A  possible unauthorised planting: part of a detected vineyard not covered by a registered parcel that is
          planted and authorised (rvv_code + authorisation_nr); flagged if >= a_min_ha OR >= a_min_share of the block
  Type B  possible grubbed-up / abandoned: parcel registered as planted but < b_max_cover of it covered by detected
          vineyard, or status_detected = abandoned / no_vines

Every item is a "possible ..." signal with its area, share, ids, legal basis, confidence note and a visit point
snapped to the nearest passable place (inter-row or authorised passage). The official route.geojson is not touched.

Inputs:  out/blocks.geojson, web/data/blocks_report.json, web/data/compliance.json (measured area per block),
         web/data/register.geojson (pipeline.register), web/data/pred/interrows.geojson, data/route/passages.geojson
Outputs: web/data/register_mismatch.geojson, out/register_mismatch.csv

Usage:  python -m pipeline.register_mismatch
"""
from __future__ import annotations

import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import nearest_points, unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

WEB = C.ROOT / "web" / "data"


def fc(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text())["features"]


def main() -> None:
    t0 = time.time()
    crit = json.loads((C.ROOT / "registry" / "criteria.json").read_text())
    mm = crit["mismatch"]
    report = {b["vineyard_id"]: b for b in json.loads((WEB / "blocks_report.json").read_text())["blocks"]}
    measured = {b["vineyard_id"]: b["measured"] for b in json.loads((WEB / "compliance.json").read_text())["blocks"]}
    blocks = {f["properties"]["vineyard_id"]: shape(f["geometry"]).buffer(0) for f in fc(C.OUT / "blocks.geojson")}
    parcels = fc(WEB / "register.geojson")
    allowed = unary_union([shape(f["geometry"]) for f in fc(WEB / "pred" / "interrows.geojson")]
                          + [shape(f["geometry"]) for f in fc(C.ROUTE_IN / "passages.geojson")]).buffer(0)

    valid = unary_union([shape(p["geometry"]).buffer(0) for p in parcels
                         if p["properties"]["status"] == "planted" and p["properties"]["rvv_code"] and p["properties"]["authorisation_nr"]])
    detected = unary_union(list(blocks.values()))
    items, below = [], []

    def visit_point(geom):
        c = geom.representative_point()
        p = nearest_points(allowed, c)[0]
        return [round(p.x, 2), round(p.y, 2)], round(p.distance(c), 1)

    def add(kind, geom, **k):
        pt, snap = visit_point(geom)
        items.append({"type": "Feature", "geometry": mapping(geom.simplify(0.3)),
                      "properties": {"id": f"{kind}{len([i for i in items if i['properties']['type'] == kind]) + 1:02d}", "type": kind,
                                     "label": mm["labels"][kind], "legal": mm["legal"][kind], "confidence": mm["confidence"],
                                     "visit": pt, "visit_snap_m": snap, "demo": True, **k}})

    for vid, g in sorted(blocks.items()):
        rows = report.get(vid, {}).get("rows", 0)
        area = measured.get(vid, {}).get("area_ha", g.area / 1e4)
        if rows < mm["min_rows"] or area < mm["min_area_ha"]:
            below.append({"vineyard_id": vid, "rows": rows, "area_ha": round(area, 3)})
            continue
        unc = g.difference(valid).buffer(-0.5).buffer(0.5)          # drop sub-metre slivers along parcel edges
        ua = unc.area
        share = ua / g.area if g.area else 0
        if ua / 1e4 >= mm["a_min_ha"] or share >= mm["a_min_share"]:
            partial = [p["properties"] for p in parcels if shape(p["geometry"]).intersects(g)]
            why = ("nicio parcelă înregistrată" if not partial else
                   "parcelă fără autorizație / cod RVV" if any(not p["authorisation_nr"] or not p["rvv_code"] for p in partial) else
                   "parcelă cu alt status în registru" if any(p["status"] != "planted" for p in partial) else "acoperire parțială")
            add("A", unc, vineyard_id=vid, cad_nr=[p["cad_nr"] for p in partial], area_ha=round(ua / 1e4, 4), area_m2=round(ua),
                share_pct=round(share * 100, 1), share_of="bloc", reason=why, block_area_ha=round(area, 3))

    for p in parcels:
        pr, g = p["properties"], shape(p["geometry"]).buffer(0)
        if pr["status"] != "planted":
            continue
        cover = g.intersection(detected).area / g.area if g.area else 0
        st = pr["measured"]["status_detected"]
        if cover < mm["b_max_cover"] or st in ("abandoned", "no_vines"):
            add("B", g, cad_nr=[pr["cad_nr"]], vineyard_id=pr.get("vineyard_id"), area_ha=round(g.area / 1e4, 4), area_m2=round(g.area),
                share_pct=round(cover * 100, 1), share_of="parcelă acoperită de vie detectată",
                reason="fără vie pe imagine" if st == "no_vines" or cover < mm["b_max_cover"] else "abandonată (euristic)")

    items.sort(key=lambda f: (f["properties"]["type"], -f["properties"]["area_ha"]))
    for k in ("A", "B"):                                         # ids by area within each type: A01 = largest
        for i, f in enumerate(x for x in items if x["properties"]["type"] == k):
            f["properties"]["id"] = f"{k}{i + 1:02d}"
    out = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}},
           "demo": True, "generated": date.today().isoformat(), "thresholds": {k: v for k, v in mm.items() if not isinstance(v, dict) and k != "_about"},
           "below_threshold": below, "features": items}
    (WEB / "register_mismatch.geojson").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    cols = ["id", "type", "label", "vineyard_id", "cad_nr", "area_ha", "area_m2", "share_pct", "share_of", "reason", "visit_x", "visit_y", "legal"]
    with (C.OUT / "register_mismatch.csv").open("w", newline="", encoding="utf-8") as fh:
        fh.write(f"# Posibile nepotriviri drone vs registru DEMO · {date.today().isoformat()} · semnal pentru control, nu verdict\n")
        w = csv.writer(fh)
        w.writerow(cols)
        for f in items:
            p = f["properties"]
            w.writerow([p["id"], p["type"], p["label"], p.get("vineyard_id") or "", " ".join(p["cad_nr"]), p["area_ha"], p["area_m2"],
                        p["share_pct"], p["share_of"], p["reason"], p["visit"][0], p["visit"][1], p["legal"]])
    dt = round(time.time() - t0, 1)
    tp = C.OUT / "timing.json"
    tj = json.loads(tp.read_text()) if tp.exists() else {}
    tj["register_mismatch"] = dt
    tp.write_text(json.dumps(tj, indent=1))
    na = sum(f["properties"]["type"] == "A" for f in items)
    print(f"mismatch: {na} type A, {len(items) - na} type B, {len(below)} blocks below threshold "
          f"-> web/data/register_mismatch.geojson, out/register_mismatch.csv ({dt}s)")


if __name__ == "__main__":
    main()
