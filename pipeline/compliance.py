"""Conformitate & compensatii: what the drone measured vs what the Registrul Vitivinicol (ONVV) and the AIPA
subsidy request declare, per vineyard block -> web/data/compliance.json (+ out/compliance.csv).

Measured (from the pipeline, same numbers as measurements.csv / blocks_report.json):
  row spacing   median distance between neighbouring row axes of the block (web/data/pred/rows.geojson)
  planted area  total row length x row spacing
  density       nominal = 10 000 / (row spacing x vine spacing); effective = nominal x (1 - gap length / row length)
  gaps          gap length / row length (missing or dead vines)
Declared (registry/rvv_demo.json): RVV code, variety, planting year, declared area, IGP, AIPA request.
  !! DEMO DATA: there is no public API for the RVV or AIPA files. `--make-demo` writes clearly labelled synthetic
  records (a few deliberate mismatches) so the workflow can be shown; production = data exchange ONVV / AIPA (MConnect).
Criteria (registry/criteria.json): IGP zone and admitted varieties, AIPA density bands (reference values), tolerances.

Cadastre (pipeline.cadastre -> out/cadastre_blocks.json, public ASP parcels): the cadastral numbers under each
block with the vine area in each; vines on a parcel registered for buildings / roads -> warning.

Checks per block: registered in RVV · cadastral parcels · IGP zone · variety admitted for IGP · declared vs measured area ·
minimum area for support · declared vs measured density band · plantation integrity (gaps).
Status: conform (all pass) · verificare (any warning) · neconform (any fail) · sub_prag (< 0,15 ha, unregistered garden). The inspector visits only
`verificare` / `neconform` blocks (list in compliance.json -> "visit").

Real data: `--registry-csv extras.csv` (template registry/rvv_template.csv: one row per parcel / block, columns
vineyard_id, rvv_code, exploatant, variety, planting_year, declared_area_ha, declared_density, igp, aipa_measure,
aipa_year, requested_lei). Empty cells make that check "info" instead of pass / fail.
`--visit-route`: the inspector route through the blocks to visit only (gaps + waste of those blocks) ->
web/data/variants/route_compliance.geojson (pipeline.route, cached distances).

Usage:  python -m pipeline.compliance [--make-demo] [--registry-csv extras.csv] [--visit-route]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

REG = C.ROOT / "registry"
WEB = C.ROOT / "web" / "data"


def row_spacing(rows_path: Path) -> dict[str, float]:
    by = defaultdict(list)
    for f in json.loads(rows_path.read_text())["features"]:
        p = f["properties"]
        by[p.get("vineyard_id")].append((p.get("row_id"), shape(f["geometry"])))
    out = {}
    for vid, ls in by.items():
        ds = []
        for rid, line in ls:
            c = line.interpolate(0.5, normalized=True)
            d = [c.distance(o) for r2, o in ls if r2 != rid]
            d = [x for x in d if 0.8 < x < 6.0]          # neighbouring rows only
            if d:
                ds.append(min(d))
        if len(ds) >= 3:
            out[vid] = statistics.median(ds)
    return out


def band(density: float, bands: list[dict]) -> dict:
    for b in bands:
        if b["min"] <= density <= b["max"]:
            return b
    return bands[0]


def make_demo(blocks: list[dict], measured: dict, crit: dict, path: Path) -> None:
    rnd = random.Random(2026)
    good = [v for v, m in crit["varieties"].items() if m["igp"]]
    native = [v for v, m in crit["varieties"].items() if m["igp"] and m["autohton"]]
    recs = []
    for i, b in enumerate(sorted(blocks, key=lambda r: r["vineyard_id"])):
        vid = b["vineyard_id"]
        m = measured[vid]
        if m["area_ha"] < 0.15:                      # below the RVV threshold: garden vineyard, not registered
            continue
        if i % 17 == 5:                              # deliberate: planted but missing from the register
            continue
        roll = rnd.random()
        area_f = rnd.uniform(0.96, 1.04)
        if roll < 0.12:
            area_f = rnd.uniform(1.18, 1.40)         # deliberate: over-declared area
        variety = rnd.choice(native if rnd.random() < 0.5 else good)
        if i % 23 == 7:
            variety = "Isabella"                     # deliberate: variety not admitted for IGP
        year = rnd.choice([1978, 1985, 1992, 2004, 2012, 2018, 2021, 2024, 2025, 2025])
        dens_decl = int(round(m["density_nominal"] * rnd.uniform(0.97, 1.03) / 10) * 10)
        if roll > 0.9:
            dens_decl = int(dens_decl * 1.25)        # deliberate: declared a higher density band
        rec = {"vineyard_id": vid, "rvv_code": f"RVV-DEMO-{vid}", "exploatant": f"Exploatant demo {i + 1:02d}",
               "variety": variety, "planting_year": year, "declared_area_ha": round(m["area_ha"] * area_f, 3),
               "igp": crit["igp"]["zone"], "declared_density": dens_decl, "demo": True}
        if year >= 2024 and m["area_ha"] >= crit["aipa"]["min_area_ha"] * 0.8:
            b_ = band(dens_decl, crit["aipa"]["density_bands"])
            bonus = crit["aipa"]["bonus_lei_per_ha"]["igp"] if crit["varieties"].get(variety, {}).get("igp") else 0
            rec["aipa_request"] = {"measure": crit["aipa"]["measure"], "year": year,
                                   "requested_lei": int(round(rec["declared_area_ha"] * (b_["lei_per_ha"] + bonus), -2)),
                                   "demo": True}
        recs.append(rec)
    path.write_text(json.dumps({"_about": "DATE DEMONSTRATIVE (sintetice) - nu sunt inregistrari reale din RVV/AIPA.",
                                "records": recs}, ensure_ascii=False, indent=1))
    print(f"demo registry: {len(recs)} records -> {path}")


CSV_NUM = {"planting_year": int, "declared_area_ha": float, "declared_density": int, "aipa_year": int, "requested_lei": int}


def read_registry_csv(path: Path) -> list[dict]:
    """Registry extract (RVV / AIPA) as CSV -> the same record format as rvv_demo.json; empty cells are left out."""
    recs = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("vineyard_id"):
                continue
            r = {"vineyard_id": row["vineyard_id"].upper(), "demo": row.get("demo", "").lower() in ("1", "true", "da")}
            for k in ("rvv_code", "exploatant", "variety", "igp"):
                if row.get(k):
                    r[k] = row[k]
            for k in ("planting_year", "declared_area_ha", "declared_density"):
                if row.get(k):
                    r[k] = CSV_NUM[k](float(row[k].replace(",", ".")))
            if row.get("requested_lei"):
                r["aipa_request"] = {"measure": row.get("aipa_measure") or "cerere AIPA",
                                     "year": int(row["aipa_year"]) if row.get("aipa_year") else r.get("planting_year"),
                                     "requested_lei": int(float(row["requested_lei"])), "demo": r["demo"]}
            r.setdefault("rvv_code", "—")
            r.setdefault("exploatant", "—")
            recs.append(r)
    return recs


def check(cid, label, status, detail, rule="", source=""):
    return {"id": cid, "label": label, "status": status, "detail": detail, "rule": rule, "source": source}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=str(WEB / "blocks_report.json"))
    ap.add_argument("--rows", default=str(WEB / "pred" / "rows.geojson"))
    ap.add_argument("--criteria", default=str(REG / "criteria.json"))
    ap.add_argument("--registry", default=str(REG / "rvv_demo.json"))
    ap.add_argument("--out", default=str(WEB / "compliance.json"))
    ap.add_argument("--make-demo", action="store_true", help="(re)write the synthetic registry records")
    ap.add_argument("--registry-csv", default="", help="real registry extract (CSV, see registry/rvv_template.csv)")
    ap.add_argument("--visit-route", action="store_true", help="also compute the route through the blocks to visit")
    ap.add_argument("--targets", default=str(C.OUT / "targets.geojson"))
    ap.add_argument("--per-block", type=int, default=2, help="control points per block on the visit route")
    a = ap.parse_args()

    crit = json.loads(Path(a.criteria).read_text())
    blocks = json.loads(Path(a.report).read_text())["blocks"]
    sp = row_spacing(Path(a.rows))
    sp_default = statistics.median(sp.values()) if sp else 2.6
    vs = crit["vine_spacing_m"]
    tol = crit["tolerances"]
    bands = crit["aipa"]["density_bands"]

    measured = {}
    for b in blocks:
        s = sp.get(b["vineyard_id"], sp_default)
        L = b["row_length_m"]
        gap = min(b["gap_length_m"] / L, 1.0) if L else 0.0
        nominal = 10000 / (s * vs)
        measured[b["vineyard_id"]] = {
            "rows": b["rows"], "row_length_m": round(L, 1), "row_spacing_m": round(s, 2),
            "spacing_measured": b["vineyard_id"] in sp, "area_ha": round(L * s / 10000, 3),
            "density_nominal": int(round(nominal)), "density_effective": int(round(nominal * (1 - gap))),
            "gap_share": round(gap, 3), "missing_vines": b["missing_vines"], "waste": b["waste"], "bbox": b["bbox"]}

    if a.registry_csv:
        records = read_registry_csv(Path(a.registry_csv))
        reg_source = Path(a.registry_csv).name
    else:
        reg_path = Path(a.registry)
        if a.make_demo or not reg_path.exists():
            make_demo(blocks, measured, crit, reg_path)
        records = json.loads(reg_path.read_text())["records"]
        reg_source = reg_path.name
        assert all(r.get("demo") for r in records), "synthetic registry records must carry demo: true"
    registry = {r["vineyard_id"]: r for r in records}
    cad_path = C.OUT / "cadastre_blocks.json"
    cadastre = json.loads(cad_path.read_text()) if cad_path.exists() else {}
    is_demo = bool(records) and all(r.get("demo") for r in records)

    out = []
    for b in blocks:
        vid = b["vineyard_id"]
        m = measured[vid]
        r = registry.get(vid)
        cs = []
        if m["area_ha"] < crit["registry"]["min_parcel_ha"]:
            cs.append(check("rvv", "Registrul Vitivinicol", "info",
                            f"{m['area_ha']:.2f} ha < {crit['registry']['min_parcel_ha']} ha: înregistrare neobligatorie",
                            "parcele > 0,15 ha", crit["registry"]["source"]))
        elif r is None:
            cs.append(check("rvv", "Registrul Vitivinicol", "fail",
                            f"plantație de {m['area_ha']:.2f} ha găsită pe imagine, fără înregistrare în RVV",
                            "înregistrare obligatorie > 0,15 ha; fără ea: fără subvenții", crit["registry"]["source"]))
        else:
            cs.append(check("rvv", "Registrul Vitivinicol", "pass", f"{r['rvv_code']} · {r['exploatant']}",
                            "", crit["registry"]["source"]))
        parcels = sorted(cadastre.get(vid, []), key=lambda x: -x["vine_ha"])
        if parcels:
            bad = [x for x in parcels if x["vine_ha"] >= 0.05 and any(k in x["landuse"].lower() for k in ("construc", "cale de comunica", "locative"))]
            cs.append(check("cadastru", "Parcele cadastrale", "warn" if bad else "info",
                            f"{len(parcels)} parcele: " + ", ".join(f"{x['cod']} ({x['vine_ha']:.2f} ha)" for x in parcels[:4])
                            + (" …" if len(parcels) > 4 else "")
                            + (f" · vie pe teren „{bad[0]['landuse']}”: {', '.join(x['cod'] for x in bad[:3])}" if bad else ""),
                            "modul de folosință din cadastru vs vie detectată",
                            "https://geodata.gov.md/geoserver/ows?service=WMS&request=GetCapabilities"))
        if r:
            ok_zone = r.get("igp") == crit["igp"]["zone"]
            cs.append(check("igp", "Zona IGP", "pass" if ok_zone else ("info" if not r.get("igp") else "warn"),
                            f"declarat {r.get('igp') or '-'} · amplasament {crit['igp']['zone']}", "", crit["igp"]["sources"][0]))
            v = crit["varieties"].get(r.get("variety", ""))
            if not r.get("variety"):
                cs.append(check("soi", "Soi", "info", "soi nedeclarat în extras"))
            elif v is None:
                cs.append(check("soi", "Soi", "warn", f"{r['variety']}: nu e în lista de referință", "caiet de sarcini IGP"))
            else:
                cs.append(check("soi", "Soi", "pass" if v["igp"] else "fail",
                                f"{r['variety']}" + (" (autohton)" if v["autohton"] else "") +
                                ("" if v["igp"] else f" · {v.get('note', 'neadmis IGP')}"), "caiet de sarcini IGP (demonstrativ)"))
            if r.get("declared_area_ha") is None:
                cs.append(check("area", "Suprafață declarată vs măsurată", "info", f"măsurat {m['area_ha']:.2f} ha; nedeclarat în extras"))
            else:
                dev = (r["declared_area_ha"] - m["area_ha"]) / m["area_ha"] if m["area_ha"] else 0
                st = "pass" if abs(dev) <= tol["area_pass"] else ("warn" if abs(dev) <= tol["area_warn"] else "fail")
                cs.append(check("area", "Suprafață declarată vs măsurată", st,
                                f"declarat {r['declared_area_ha']:.2f} ha · măsurat {m['area_ha']:.2f} ha ({dev:+.0%})",
                                f"toleranță ±{tol['area_pass']:.0%} / ±{tol['area_warn']:.0%}"))
            # planting scheme: declared density vs the one implied by the measured row spacing (same AIPA band?)
            if r.get("declared_density") is None:
                cs.append(check("density", "Schema de plantare (densitate)", "info",
                                f"măsurat {m['density_nominal']} butuci/ha; nedeclarat în extras"))
            else:
                bd, bm = band(r["declared_density"], bands), band(m["density_nominal"], bands)
                st = "pass" if bm["lei_per_ha"] >= bd["lei_per_ha"] else "fail"
                cs.append(check("density", "Schema de plantare (densitate)", st,
                                f"declarat {r['declared_density']} butuci/ha · măsurat {m['density_nominal']} "
                                f"(rânduri la {m['row_spacing_m']} m × {vs} m pe rând)",
                                "aceeași bandă de densitate AIPA", crit["aipa"]["sources"][0]))
        g = m["gap_share"]
        req = (r or {}).get("aipa_request")
        st = "pass" if g <= tol["gaps_pass"] else ("fail" if req and g > tol["gaps_warn"] else "warn")
        cs.append(check("gaps", "Integritatea plantației", st,
                        f"{g:.0%} din lungimea rândurilor lipsă · ~{m['missing_vines']} butuci",
                        f"≤{tol['gaps_pass']:.0%} conform (recepția AIPA cere prindere ≥ 90 %); la cererile AIPA >{tol['gaps_warn']:.0%} = neconform",
                        crit["aipa"]["sources"][0]))
        if m["waste"]:
            cs.append(check("waste", "Deșeuri în bloc", "info", f"{m['waste']} deșeuri detectate"))

        elig = None
        if req:
            if m["area_ha"] < crit["aipa"]["min_area_ha"]:
                cs.append(check("min_area", "Suprafață minimă sprijin", "fail",
                                f"{m['area_ha']:.2f} ha < {crit['aipa']['min_area_ha']} ha", "", crit["aipa"]["sources"][0]))
            bm = band(m["density_effective"], bands)      # paid on the vines actually standing (reception)
            igp_ok = crit["varieties"].get(r.get("variety", ""), {}).get("igp", False)
            bonus = crit["aipa"]["bonus_lei_per_ha"]["igp"] if igp_ok else 0
            decl_area = r.get("declared_area_ha", m["area_ha"])
            eligible_area = min(m["area_ha"], decl_area) if m["area_ha"] >= crit["aipa"]["min_area_ha"] else 0
            amount = int(round(eligible_area * (bm["lei_per_ha"] + bonus), -2))
            elig = {"measure": req["measure"], "requested_lei": req["requested_lei"], "eligible_lei": amount,
                    "difference_lei": amount - req["requested_lei"], "lei_per_ha": bm["lei_per_ha"], "bonus_igp": bonus,
                    "eligible_area_ha": round(eligible_area, 3), "reception_doc": crit["aipa"]["reception_doc"]}

        fails = [c for c in cs if c["status"] == "fail"]
        warns = [c for c in cs if c["status"] == "warn"]
        status = "neconform" if fails else ("verificare" if warns else "conform")
        if m["area_ha"] < crit["registry"]["min_parcel_ha"] and not r:
            status = "sub_prag"                       # garden vineyard below the RVV threshold
        actions = []
        if any(c["id"] == "rvv" for c in fails):
            actions.append("Notificare ONVV: plantație neînregistrată în Registrul Vitivinicol")
        if any(c["id"] in ("area", "density", "min_area") for c in fails + warns):
            actions.append("Vizită în teren: măsurare suprafață și densitate înainte de plata AIPA")
        if any(c["id"] == "soi" for c in fails):
            actions.append("Verificare soi în teren: soi neadmis pentru IGP")
        if any(c["id"] == "gaps" for c in fails + warns):
            actions.append(f"Completare goluri: ~{m['missing_vines']} butuci de replantat")
        if any(c["id"] == "cadastru" and c["status"] == "warn" for c in cs):
            actions.append("Verificare cadastru: vie pe teren înregistrat pentru construcții / drumuri")
        out.append({"vineyard_id": vid, "status": status, "measured": m, "registry": r, "checks": cs, "cadastre": parcels,
                    "eligibility": elig, "actions": actions, "demo": bool(r and r.get("demo"))})
    assert len(out) == len(blocks)

    order = {"neconform": 0, "verificare": 1, "conform": 2, "sub_prag": 3}
    out.sort(key=lambda x: (order[x["status"]], -(x["measured"]["area_ha"])))
    reqs = [x["eligibility"] for x in out if x["eligibility"]]
    summary = {"blocks": len(out), "status": {k: sum(x["status"] == k for x in out) for k in order},
               "registered": sum(1 for x in out if x["registry"]),
               "unregistered_ha": round(sum(x["measured"]["area_ha"] for x in out if not x["registry"]
                                            and x["measured"]["area_ha"] >= crit["registry"]["min_parcel_ha"]), 2),
               "requests": len(reqs), "requested_lei": sum(e["requested_lei"] for e in reqs),
               "eligible_lei": sum(e["eligible_lei"] for e in reqs),
               "visit": [x["vineyard_id"] for x in out if x["status"] in ("neconform", "verificare")],
               "row_spacing_median_m": round(sp_default, 2), "vine_spacing_m": vs, "demo_registry": is_demo,
               "registry_source": reg_source,
               "about": crit["_about"]}
    Path(a.out).write_text(json.dumps({"summary": summary, "criteria": crit, "blocks": out},
                                      ensure_ascii=False, separators=(",", ":")))
    C.OUT.mkdir(exist_ok=True)
    with open(C.OUT / "compliance.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["vineyard_id", "status", "area_ha_measured", "area_ha_declared", "density_effective",
                    "density_declared", "gap_share", "variety", "requested_lei", "eligible_lei", "failed_checks"])
        for x in out:
            r, m, e = x["registry"] or {}, x["measured"], x["eligibility"] or {}
            w.writerow([x["vineyard_id"], x["status"], m["area_ha"], r.get("declared_area_ha", ""), m["density_effective"],
                        r.get("declared_density", ""), m["gap_share"], r.get("variety", ""), e.get("requested_lei", ""),
                        e.get("eligible_lei", ""), " ".join(c["id"] for c in x["checks"] if c["status"] == "fail")])
    if a.visit_route:
        # one control visit per block: its longest gap and the target closest to the block centre (the inspector
        # checks area, density, variety and gaps on the spot; the full gap tour stays in route.geojson)
        visit = set(summary["visit"])
        by = defaultdict(list)
        for f in json.loads(Path(a.targets).read_text())["features"]:
            if f["properties"].get("vineyard_id") in visit and f["properties"].get("reachable", True):
                by[f["properties"]["vineyard_id"]].append(f)
        feats = []
        for vid, fs in by.items():
            x0, y0, x1, y1 = measured[vid]["bbox"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            pick = {max(fs, key=lambda f: f["properties"].get("gap_m", 0))["properties"]["id"],
                    min(fs, key=lambda f: (f["geometry"]["coordinates"][0] - cx) ** 2 + (f["geometry"]["coordinates"][1] - cy) ** 2)["properties"]["id"]}
            feats += [f for f in fs if f["properties"]["id"] in pick][: a.per_block]
        tin = C.OUT / "targets_compliance.geojson"
        tin.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
        var = WEB / "variants"
        var.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "pipeline.route", "--mode", "inspector", "--targets", str(tin),
                        "--out", str(var / "route_compliance.geojson"),
                        "--targets-out", str(var / "targets_compliance.geojson"), "--time", "20"],
                       cwd=C.ROOT, check=True, stdout=subprocess.DEVNULL)
        p = json.loads((var / "route_compliance.geojson").read_text())["features"][0]["properties"]
        print(f"visit route: {len(visit)} blocks ({len(by)} with reachable points), {len(feats)} control points -> {p['length_m'] / 1000:.2f} km, "
              f"{p['targets_visited']}/{p['targets_total']} targets, outside {p['outside_share'] * 100:.2f} %")
    s = summary
    print(f"{s['blocks']} blocks: {s['status']} · RVV {s['registered']} registered, {s['unregistered_ha']} ha unregistered · "
          f"{s['requests']} AIPA requests: {s['requested_lei']:,} lei requested vs {s['eligible_lei']:,} lei eligible · "
          f"visit {len(s['visit'])} blocks -> {a.out}")


if __name__ == "__main__":
    main()
