"""EU-compatible vineyard register (DEMO): parcel sheet with declared vs drone-measured fields.

Model (registry/schema.json, Reg. (EU) 2018/273 art. 7 + annexes III-IV): grower -> parcel -> events.
All registry content is SYNTHETIC ("DEMO-..."); only the measured fields come from the drone pipeline.

  --make-demo  (re)writes registry/parcels_demo.geojson from the detected blocks and links registry/rvv_demo.json by
               cad_nr. Deliberate scenarios, used by the tests and by the mismatch check (US-3):
                 whole                 registered block = one parcel
                 split                 block split in two parallel to the rows, both halves registered
                 split_unauthorised    block split in two, second half without authorisation_nr / RVV code
                 no_vines              parcel registered as planted, no vines on the image
                 abandoned_declared    registered as abandoned, vines visible on the image
  default      fills the measured fields per parcel from the pipeline outputs:
                 rows clipped to the parcel (web/data/pred/rows.geojson), row spacing per block (compliance.row_spacing),
                 gaps (out/targets.geojson), inter-row cover (web/data/pred/interrows.geojson)
               -> web/data/register.geojson (map + parcel sheet) and out/register.csv (register extract).

Usage:  python -m pipeline.register [--make-demo]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import split, unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.compliance import row_spacing  # noqa: E402

REG = C.ROOT / "registry"
WEB = C.ROOT / "web" / "data"
PARCELS = REG / "parcels_demo.geojson"
MIN_ROW_IN = 2.0                      # a row counts for a parcel when at least this many metres lie inside it
NO_VINES_SHARE = 0.10                 # measured planted area < 10 % of the parcel -> no vines on the image
ABANDONED = {"vegetation_share": 0.80, "gap_share": 0.40}   # heuristic, documented in README


def load_fc(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text())["features"]


def row_direction(lines: list) -> float:
    """Length-weighted mean direction (radians, mod pi) of the row axes."""
    sx = sy = 0.0
    for ln in lines:
        c = list(ln.coords)
        for (x0, y0), (x1, y1) in zip(c, c[1:]):
            L = math.hypot(x1 - x0, y1 - y0)
            a = 2 * math.atan2(y1 - y0, x1 - x0)
            sx += L * math.cos(a)
            sy += L * math.sin(a)
    return math.atan2(sy, sx) / 2


def split_parallel(poly, angle: float):
    """Split a block polygon in two halves with a line through its centroid parallel to the rows."""
    c = poly.centroid
    dx, dy = math.cos(angle) * 5000, math.sin(angle) * 5000
    cut = LineString([(c.x - dx, c.y - dy), (c.x + dx, c.y + dy)])
    side = lambda p: (math.cos(angle) * (p.y - c.y) - math.sin(angle) * (p.x - c.x)) > 0
    parts = list(split(poly, cut).geoms)
    a = unary_union([p for p in parts if side(p.representative_point())])
    b = unary_union([p for p in parts if not side(p.representative_point())])
    return a, b


def make_demo(blocks_fc, rows_by_block, registry_path: Path) -> list[dict]:
    rnd = random.Random(273)                                   # Reg. (EU) 2018/273
    reg = json.loads(registry_path.read_text())
    recs = {r["vineyard_id"]: r for r in reg["records"]}
    blocks = {f["properties"]["vineyard_id"]: shape(f["geometry"]).buffer(0) for f in blocks_fc}
    registered = sorted((v for v in recs if v in blocks), key=lambda v: -blocks[v].area)
    split_ok = registered[:4]                                  # the 4 largest registered blocks: two registered halves
    split_bad = registered[4:6]                                # next 2: second half without authorisation
    abandoned = registered[-1:]                                # smallest registered block: register says abandoned
    parcels, n = [], 0

    def cad():
        nonlocal n
        n += 1
        return f"DEMO-{n:04d}"

    def grower(r):
        return "DEMO-G-" + r["exploatant"].split()[-1]

    def events(r, status, cad_nr):
        ev = [{"type": "planting", "date": f"{r['planting_year']}-04-{rnd.randint(10, 28):02d}", "result": "înregistrat",
               "note": f"autorizație {r.get('authorisation_nr') or '—'}"}]
        if r["planting_year"] < 1995 and rnd.random() < 0.5:
            ev.append({"type": "replanting", "date": f"{rnd.choice([2015, 2017, 2019])}-04-{rnd.randint(10, 28):02d}",
                       "result": "parțial", "note": "înlocuire butuci lipsă"})
        if rnd.random() < 0.6:
            ev.append({"type": "inspection", "date": f"{rnd.choice([2022, 2023, 2024])}-0{rnd.randint(5, 9)}-{rnd.randint(10, 28):02d}",
                       "result": rnd.choice(["conform", "conform", "observații"]), "note": "control ONVV (DEMO)"})
        if status == "abandoned":
            ev.append({"type": "inspection", "date": "2024-07-15", "result": "abandonată", "note": "declarație exploatant (DEMO)"})
        return sorted(ev, key=lambda e: e["date"])

    def parcel(geom, r, scenario, share=1.0, authorised=True, status="planted"):
        cad_nr = cad()
        spacing = rnd.choice([2.5, 2.6, 2.7]) if scenario != "no_vines" else 2.5
        props = {"cad_nr": cad_nr, "grower_id": grower(r) if r else f"DEMO-G-{90 + n % 10:02d}",
                 "rvv_code": r["rvv_code"] if (r and authorised) else None,
                 "vineyard_id": r["vineyard_id"] if r else None,
                 "declared_area_ha": round(r["declared_area_ha"] * share, 3) if r else round(geom.area / 1e4 * 0.95, 3),
                 "variety": r["variety"] if r else "Fetească Neagră",
                 "planting_year": r["planting_year"] if r else 2016,
                 "planting_scheme": f"{spacing:.1f} × 1,2 m".replace(".", ","),
                 "authorisation_nr": (f"DEMO-AUT-{r['planting_year']}-{n:03d}" if r else f"DEMO-AUT-2016-{n:03d}") if authorised else None,
                 "status": status, "pdo_pgi": f"IGP {r['igp']}" if r and r.get("igp") else "IGP Codru",
                 "scenario": scenario, "demo": True}
        props["events"] = events({**(r or {"planting_year": 2016}), "authorisation_nr": props["authorisation_nr"]}, status, cad_nr)
        parcels.append({"type": "Feature", "geometry": mapping(geom.simplify(0.2)), "properties": props})
        return cad_nr

    for vid in sorted(registered):
        r, g = recs[vid], blocks[vid]
        if vid in split_ok or vid in split_bad:
            a, b = split_parallel(g, row_direction(rows_by_block.get(vid, [])))
            sa = a.area / (a.area + b.area)
            first = parcel(a, r, "split" if vid in split_ok else "split_unauthorised", sa)
            second = parcel(b, r, "split" if vid in split_ok else "split_unauthorised", 1 - sa, authorised=vid in split_ok)
            r["cad_nr"] = [first, second] if vid in split_ok else [first]
        elif vid in abandoned:
            r["cad_nr"] = [parcel(g, r, "abandoned_declared", status="abandoned")]
        else:
            r["cad_nr"] = [parcel(g, r, "whole")]
    # 2 parcels registered as planted where the image shows no vines: rectangles in the study area, >= 25 m from any block
    study = unary_union([shape(f["geometry"]) for f in load_fc(C.ROUTE_IN / "study_area.geojson")])
    allb = unary_union(list(blocks.values())).buffer(25)
    x0, y0, x1, y1 = study.bounds
    placed = 0
    for i in range(4000):
        if placed == 2:
            break
        cx, cy = x0 + (x1 - x0) * rnd.random(), y0 + (y1 - y0) * rnd.random()
        rect = shape({"type": "Polygon", "coordinates": [[(cx - 35, cy - 22), (cx + 35, cy - 22), (cx + 35, cy + 22), (cx - 35, cy + 22), (cx - 35, cy - 22)]]})
        if study.contains(rect) and not rect.intersects(allb) and all(not rect.intersects(shape(p["geometry"]).buffer(25)) for p in parcels):
            parcel(rect, None, "no_vines")
            placed += 1
    assert placed == 2, "could not place the no-vines demo parcels"
    fc = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}},
          "_about": "REGISTRU VITICOL DEMONSTRATIV - parcele, cultivatori, autorizații și evenimente SINTETICE (DEMO). "
                    "Structura urmează Reg. (UE) 2018/273 art. 7 și anexele III-IV. Nu sunt date reale ONVV / ASP.",
          "features": parcels}
    PARCELS.write_text(json.dumps(fc, ensure_ascii=False, indent=0))
    reg["records"] = list(recs.values())
    registry_path.write_text(json.dumps(reg, ensure_ascii=False, indent=1))
    print(f"demo parcels: {len(parcels)} -> {PARCELS} (split {len(split_ok)}, unauthorised halves {len(split_bad)}, "
          f"abandoned {len(abandoned)}, no vines {placed}); cad_nr linked in {registry_path.name}")
    return parcels


def measure(parcels, rows_fc, targets_fc, inter_fc, spacing, sp_default, vine_spacing):
    rows = [(f["properties"]["vineyard_id"], f["properties"]["row_id"], shape(f["geometry"])) for f in rows_fc]
    gaps = [(Point(f["geometry"]["coordinates"]), f["properties"].get("gap_m", 0.0), f["properties"].get("vineyard_id")) for f in targets_fc
            if f["properties"].get("type") == "gap"]
    inter = [(f["properties"].get("interrow_cover", "unassessable"), shape(f["geometry"]).buffer(0)) for f in inter_fc]
    out = []
    for p in parcels:
        g = shape(p["geometry"]).buffer(0)
        pr = p["properties"]
        L, ids, by_vid = 0.0, set(), defaultdict(float)
        for vid, rid, ln in rows:
            if not ln.intersects(g):
                continue
            li = ln.intersection(g).length
            if li > 0:
                L += li
                by_vid[vid] += li
                if li >= MIN_ROW_IN:
                    ids.add(rid)
        vid = max(by_vid, key=by_vid.get) if by_vid else pr.get("vineyard_id")
        s = spacing.get(vid, sp_default)
        gap = sum(gm for pt, gm, _ in gaps if g.contains(pt))
        cover = defaultdict(float)
        for c, ip in inter:
            if ip.intersects(g):
                cover[c] += ip.intersection(g).area
        ca = sum(cover.values())
        share = {k: round(cover[k] / ca, 3) if ca else 0.0 for k in ("bare_soil", "vegetation", "mixed", "unassessable")}
        area_ha = L * s / 1e4
        gap_share = min(gap / L, 1.0) if L else 0.0
        nominal = 10000 / (s * vine_spacing)
        if area_ha < NO_VINES_SHARE * g.area / 1e4:
            detected = "no_vines"
        elif share["vegetation"] > ABANDONED["vegetation_share"] and gap_share > ABANDONED["gap_share"]:
            detected = "abandoned"
        else:
            detected = "planted"
        m = {"parcel_area_ha": round(g.area / 1e4, 4), "measured_area_ha": round(area_ha, 4), "rows": len(ids),
             "row_length_m": round(L, 1), "row_spacing_m": round(s, 2), "density_nominal": int(round(nominal)) if L else 0,
             "density_effective": int(round(nominal * (1 - gap_share))) if L else 0, "gap_length_m": round(gap, 1),
             "gap_share": round(gap_share, 3), "missing_vines": int(round(gap / vine_spacing)),
             "bare_soil_share": share["bare_soil"], "vegetation_share": share["vegetation"], "mixed_share": share["mixed"],
             "status_detected": detected, "detected_vineyard_id": vid if L else None}
        if pr["status"] == "planted" and detected == "planted":
            m["area_diff_ha"] = round(m["measured_area_ha"] - pr["declared_area_ha"], 4)
            m["area_diff_pct"] = round(m["area_diff_ha"] / pr["declared_area_ha"] * 100, 1) if pr["declared_area_ha"] else None
        out.append({"type": "Feature", "geometry": p["geometry"], "properties": {**pr, "measured": m}})
    return out


CSV_COLS = ["cad_nr", "grower_id", "rvv_code", "vineyard_id", "status", "status_detected", "declared_area_ha", "measured_area_ha",
            "area_diff_ha", "area_diff_pct", "parcel_area_ha", "variety", "planting_year", "planting_scheme", "row_spacing_m",
            "rows", "row_length_m", "density_nominal", "density_effective", "gap_share", "missing_vines", "vegetation_share",
            "authorisation_nr", "pdo_pgi", "scenario", "events"]


def write_csv(feats, path: Path, flight: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(f"# Extras registru viticol DEMO · generat {date.today().isoformat()} · sursa: zbor UAV Sireț3 {flight}, pipeline vineyard-ai "
                 f"(câmpuri măsurate) + registru sintetic (câmpuri declarate) · DATE DEMONSTRATIVE, nu înregistrări reale ONVV/ASP\n")
        w = csv.writer(fh)
        w.writerow(CSV_COLS)
        for f in feats:
            p, m = f["properties"], f["properties"]["measured"]
            row = {**p, **m, "events": "; ".join(f"{e['date']} {e['type']} {e['result']}" for e in p["events"])}
            w.writerow(["" if row.get(c) is None else row.get(c) for c in CSV_COLS])


def validate(feats) -> None:
    for f in feats:
        p = f["properties"]
        assert p.get("cad_nr", "").startswith("DEMO-"), f"parcel without DEMO cad_nr: {p}"
        assert f.get("geometry") and shape(f["geometry"]).area > 0, f"{p['cad_nr']}: empty geometry"
        assert p.get("status") in ("planted", "grubbed_up", "abandoned"), f"{p['cad_nr']}: bad status"
        assert p.get("demo") is True, f"{p['cad_nr']}: demo flag missing"
        if p["status"] == "planted" and p["measured"]["status_detected"] == "planted":
            assert "area_diff_ha" in p["measured"], f"{p['cad_nr']}: area difference not computed"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--make-demo", action="store_true", help="(re)write registry/parcels_demo.geojson and link rvv_demo.json")
    ap.add_argument("--blocks", default=str(C.OUT / "blocks.geojson"))
    ap.add_argument("--rows", default=str(WEB / "pred" / "rows.geojson"))
    ap.add_argument("--inter", default=str(WEB / "pred" / "interrows.geojson"))
    ap.add_argument("--targets", default=str(C.OUT / "targets.geojson"))
    ap.add_argument("--registry", default=str(REG / "rvv_demo.json"))
    a = ap.parse_args()
    t0 = time.time()
    crit = json.loads((REG / "criteria.json").read_text())
    rows_fc = load_fc(Path(a.rows))
    if a.make_demo or not PARCELS.exists():
        by = defaultdict(list)
        for f in rows_fc:
            by[f["properties"]["vineyard_id"]].append(shape(f["geometry"]))
        make_demo(load_fc(Path(a.blocks)), by, Path(a.registry))
    parcels = load_fc(PARCELS)
    spacing = row_spacing(Path(a.rows))
    sp_default = statistics.median(spacing.values()) if spacing else 2.6
    feats = measure(parcels, rows_fc, load_fc(Path(a.targets)), load_fc(Path(a.inter)), spacing, sp_default, crit["vine_spacing_m"])
    validate(feats)
    flight = "2025-05-20"
    fc = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}},
          "demo": True, "generated": date.today().isoformat(), "flight_date": flight,
          "about": "Registru viticol DEMONSTRATIV (sintetic) cu câmpuri măsurate din zborul UAV. Structură conform Reg. (UE) 2018/273 art. 7.",
          "features": feats}
    (WEB / "register.geojson").write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    write_csv(feats, C.OUT / "register.csv", flight)
    dt = round(time.time() - t0, 1)
    tp = C.OUT / "timing.json"
    tj = json.loads(tp.read_text()) if tp.exists() else {}
    tj["register"] = dt
    tp.write_text(json.dumps(tj, indent=1))
    by = defaultdict(int)
    for f in feats:
        by[f["properties"]["measured"]["status_detected"]] += 1
    print(f"register: {len(feats)} parcels ({dict(by)}) -> web/data/register.geojson, out/register.csv ({dt}s)")


if __name__ == "__main__":
    main()
