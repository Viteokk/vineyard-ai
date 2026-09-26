"""Moldova context layer for the web map -> web/data/moldova.geojson (EPSG:32635, like everything else).

  country  Natural Earth 1:10m outline (world-atlas@2.0.2 countries-10m.json, public domain), whole territory
  raion    districts from the national cadastre (geodata.gov.md WFS cadastru_data:UAT2, ASP), simplified 150 m;
           the service covers the right bank only, so the left bank (UTA din stânga Nistrului, with Bender) is
           derived as country minus raions, keeping only the large pieces (> 500 km²)
  commune  the commune of the study area (cadastru_data:UAT1, name 'Sireți'), simplified 10 m
Downloads with curl (system certificates) into data/moldova/ once; --refresh to download again.

Usage:  python scripts/build_moldova.py [--commune Sireți] [--refresh]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform, unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

RAW = C.DATA / "moldova"
OUT = C.ROOT / "web" / "data" / "moldova.geojson"
WFS = "https://geodata.gov.md/geoserver/cadastru_data/wfs?service=WFS&version=2.0.0&request=GetFeature&outputFormat=application%2Fjson"
ATLAS = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-10m.json"


def get(url: str, path: Path, refresh: bool) -> dict:
    if refresh or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["curl", "-sS", "--fail", "--max-time", "180", "-o", str(path), url], check=True)
    return json.loads(path.read_text())


def topo_country(topo: dict, country_id: str):
    """Decode one country of a quantized TopoJSON (world-atlas) into a shapely geometry in lon/lat."""
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(idx):
        out = []
        for i in idx:
            pts = arcs[i] if i >= 0 else arcs[~i][::-1]
            out += pts if not out else pts[1:]
        return out

    geo = next(g for g in topo["objects"]["countries"]["geometries"] if str(g.get("id")) == country_id)
    polys = [geo["arcs"]] if geo["type"] == "Polygon" else geo["arcs"]
    return unary_union([Polygon(ring(p[0]), [ring(h) for h in p[1:]]) for p in polys])


def rnd(c):
    return [rnd(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0]), round(c[1])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commune", default="Sireți")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    ll2utm = Transformer.from_crs(4326, 32635, always_xy=True).transform
    md2utm = Transformer.from_crs(4026, 32635, always_xy=True).transform
    feats = []

    country = transform(ll2utm, topo_country(get(ATLAS, RAW / "countries-10m.json", a.refresh), "498"))
    feats.append({"type": "Feature", "properties": {"kind": "country", "name": "Republica Moldova"},
                  "geometry": mapping(country.simplify(80))})

    raions = get(WFS + "&typeNames=cadastru_data%3AUAT2", RAW / "uat2.json", a.refresh)
    for f in raions["features"]:
        g = transform(md2utm, shape(f["geometry"])).simplify(150)
        feats.append({"type": "Feature", "properties": {"kind": "raion", "name": f["properties"]["name"]},
                      "geometry": mapping(g)})

    right = unary_union([shape(f["geometry"]).buffer(0) for f in feats if f["properties"]["kind"] == "raion"])
    rest = country.buffer(0).difference(right)
    rest = rest.buffer(-400).buffer(400).intersection(country)           # drop the thin slivers along the borders
    left = unary_union([p for p in getattr(rest, "geoms", [rest]) if p.area > 500e6])
    if not left.is_empty:
        feats.append({"type": "Feature", "properties": {"kind": "raion", "name": "UTA din stânga Nistrului",
                                                        "left_bank": True, "note": "fără date cadastrale ASP"},
                      "geometry": mapping(left.simplify(150))})

    q = urllib.parse.quote(f"name LIKE '%{a.commune}%'")
    com = get(WFS + "&typeNames=cadastru_data%3AUAT1&CQL_FILTER=" + q, RAW / "commune.json", a.refresh)
    for f in com["features"]:
        g = transform(md2utm, shape(f["geometry"])).simplify(10)
        feats.append({"type": "Feature", "properties": {"kind": "commune", "name": f["properties"]["gfullname"]},
                      "geometry": mapping(g)})
    for f in feats:
        f["geometry"]["coordinates"] = rnd(f["geometry"]["coordinates"])
    OUT.write_text(json.dumps({"type": "FeatureCollection",
                               "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}},
                               "source": "Contur: Natural Earth (domeniu public). Raioane și sat: ASP, geodata.gov.md.",
                               "features": feats}, ensure_ascii=False, separators=(",", ":")))
    print(f"country + {len(raions['features'])} raions + {len(com['features'])} commune(s) -> {OUT} "
          f"({OUT.stat().st_size / 1e3:.0f} kB), bounds {[round(v) for v in country.bounds]}")


if __name__ == "__main__":
    main()
