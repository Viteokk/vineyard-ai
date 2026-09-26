"""Cadastral parcels (ASP, public WFS on geodata.gov.md) x detected vineyard blocks -> web/data/cadastre.geojson.

Source: GeoServer of the national spatial data infrastructure, layer cadastru_data:terenuri (cadastral number,
land use, area; ASP - Departamentul Cadastru), EPSG:4026 (MOLDREF99), fetched once for the study area bbox and
cached in data/cadastre/terenuri.json. Reprojected to EPSG:32635 and clipped to the study area.

Per parcel: detected vineyard area inside it (blocks.geojson), share of the parcel, the blocks it touches, and flags:
  rvv       > 0,15 ha of vines detected -> registration in the Registrul vitivinicol is mandatory (Legea 57/2006
            art. 21(1)); the RVV public check searches by cadastral number
  landuse   vines detected on a parcel registered for buildings / roads (land-use mismatch to check)
Per block (-> used by pipeline.compliance): the cadastral numbers under it with the vine area in each.

Usage:  python -m pipeline.cadastre [--refresh]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

WFS = "https://geodata.gov.md/geoserver/cadastru_data/wfs"
CACHE = C.DATA / "cadastre" / "terenuri.json"
OUT = C.ROOT / "web" / "data" / "cadastre.geojson"
NON_AGRI = ("construc", "cale de comunica", "locative")
RVV_MIN_HA = 0.15
SOURCE = "Date cadastrale: ASP, Departamentul Cadastru, prin geodata.gov.md (WFS cadastru_data:terenuri)"


def fetch(bounds_utm, path: Path) -> None:
    t = Transformer.from_crs(32635, 4026, always_xy=True)
    x0, y0, x1, y1 = bounds_utm
    xs, ys = zip(*[t.transform(x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))])
    bbox = f"{min(xs):.1f},{min(ys):.1f},{max(xs):.1f},{max(ys):.1f},EPSG:4026"
    url = (f"{WFS}?service=WFS&version=2.0.0&request=GetFeature&typeNames=cadastru_data%3Aterenuri"
           f"&outputFormat=application%2Fjson&count=20000&bbox={bbox}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # curl uses the system certificate store (python.org builds on macOS often lack one)
    subprocess.run(["curl", "-sS", "--fail", "--max-time", "180", "-o", str(path), url], check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="download the parcels again")
    ap.add_argument("--blocks", default=str(C.OUT / "blocks.geojson"))
    a = ap.parse_args()
    study = unary_union([shape(f["geometry"]) for f in json.loads((C.ROUTE_IN / "study_area.geojson").read_text())["features"]])
    if a.refresh or not CACHE.exists():
        fetch(study.buffer(50).bounds, CACHE)
    raw = json.loads(CACHE.read_text())
    to_utm = Transformer.from_crs(4026, 32635, always_xy=True).transform

    blocks = [(f["properties"]["vineyard_id"], shape(f["geometry"])) for f in json.loads(Path(a.blocks).read_text())["features"]]
    btree = STRtree([g for _, g in blocks])
    feats, per_block = [], defaultdict(list)
    for f in raw["features"]:
        g = transform(to_utm, shape(f["geometry"]))
        if not g.is_valid:
            g = g.buffer(0)
        if not g.intersects(study):
            continue
        p = f["properties"]
        cod = (p.get("codcadastral") or "").strip()
        landuse = " ".join((p.get("landuse") or "").split())
        vine, vids = 0.0, []
        for i in btree.query(g):
            inter = g.intersection(blocks[i][1]).area
            if inter > 1.0:
                vine += inter
                vids.append(blocks[i][0])
                per_block[blocks[i][0]].append({"cod": cod, "landuse": landuse, "vine_ha": round(inter / 1e4, 3),
                                                "parcel_ha": round(g.area / 1e4, 3)})
        flags = []
        if vine / 1e4 > RVV_MIN_HA:
            flags.append("rvv")
        if vine / 1e4 >= 0.05 and any(k in landuse.lower() for k in NON_AGRI):
            flags.append("landuse")
        geom = mapping(g.simplify(0.2))
        rnd = lambda c: [rnd(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], 1), round(c[1], 1)]  # noqa: E731
        geom["coordinates"] = rnd(geom["coordinates"])
        feats.append({"type": "Feature", "geometry": geom, "properties": {
            "cod": cod, "landuse": landuse, "aria_decl": (p.get("aria") or "").strip(), "parcel_ha": round(g.area / 1e4, 3),
            "vine_ha": round(vine / 1e4, 3), "vine_share": round(vine / g.area, 3) if g.area else 0, "blocks": sorted(set(vids)),
            "flags": flags}})
    fc = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}},
          "source": SOURCE, "features": feats}
    OUT.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")))
    (C.OUT / "cadastre_blocks.json").write_text(json.dumps(per_block, ensure_ascii=False))
    n_vine = sum(1 for f in feats if f["properties"]["vine_ha"] > 0)
    n_rvv = sum("rvv" in f["properties"]["flags"] for f in feats)
    n_lu = sum("landuse" in f["properties"]["flags"] for f in feats)
    print(f"{len(feats)} cadastral parcels in the study area, {n_vine} with detected vines, {n_rvv} with > {RVV_MIN_HA} ha "
          f"(RVV mandatory), {n_lu} with vines on a non-agricultural land use -> {OUT} "
          f"({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
