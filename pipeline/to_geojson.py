"""CVAT (pixel, per tile) -> GeoJSON layers for measurements, route and the web app.

Writes EPSG:32635 (official CRS, metres) to --out and the same files to web/data/ (the web map uses
Leaflet CRS.Simple in UTM metres, so no reprojection is needed):
  canopies.geojson   Polygon     vineyard_id, tile, area_m2
  rows.geojson       LineString  vineyard_id, row_id, row_structure, tile, length_m
  interrows.geojson  Polygon     vineyard_id, interrow_cover, tile, area_m2
  waste.geojson      Polygon     waste_id, vineyard_id, tile, cx, cy
  summary.json       counts, total row length, canopy area (union), inter-row area, per-row lengths

Usage: python -m pipeline.to_geojson --cvat data/examples/annotations.xml
       python -m pipeline.to_geojson --cvat out/marcaj_export.xml      (Sunday, after Marcaj export)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import LineString, Polygon, box, mapping
from shapely.ops import transform, unary_union
from shapely.validation import make_valid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat  # noqa: E402
from pipeline.tiles import open_tile  # noqa: E402

TO_WGS = Transformer.from_crs("EPSG:32635", "EPSG:4326", always_xy=True).transform
CRS_UTM = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}}


def fc(features, utm=True):
    d = {"type": "FeatureCollection", "features": features}
    if utm:
        d["crs"] = CRS_UTM
    return d


def feat(geom, props):
    return {"type": "Feature", "properties": props, "geometry": mapping(geom)}


def convert(cvat_path: Path, tiles_dir: Path):
    data = read_cvat(cvat_path)
    layers = defaultdict(list)
    geoms = defaultdict(list)
    row_len = defaultdict(float)
    row_block = {}
    wid = 0
    for name, objs in sorted(data.items()):
        tp = tiles_dir / name
        if not tp.exists():
            tp = C.EXAMPLES / "images" / name
        t = open_tile(tp)
        for o in objs:
            a = o["attrs"]
            if o["type"] == "box":
                g = box(*t.px_to_utm([[o["xtl"], o["ybr"]]])[0], *t.px_to_utm([[o["xbr"], o["ytl"]]])[0])
            else:
                pts = t.px_to_utm(o["points"])
                g = LineString(pts) if o["type"] == "polyline" else Polygon(pts)
                if g.geom_type == "Polygon" and not g.is_valid:
                    g = make_valid(g)
            lab = o["label"]
            if lab == "vineyard":
                layers["canopies"].append(feat(g, {"vineyard_id": a.get("vineyard_id", ""), "tile": name,
                                                   "area_m2": round(g.area, 3)}))
                geoms["canopies"].append(g)
            elif lab == "row":
                rid = a.get("row_id", "")
                layers["rows"].append(feat(g, {"vineyard_id": a.get("vineyard_id", ""), "row_id": rid,
                                               "row_structure": a.get("row_structure", ""), "tile": name,
                                               "length_m": round(g.length, 2)}))
                row_len[rid] += g.length
                row_block[rid] = a.get("vineyard_id", "")
            elif lab == "interrow_area":
                layers["interrows"].append(feat(g, {"vineyard_id": a.get("vineyard_id", ""),
                                                    "interrow_cover": a.get("interrow_cover", ""),
                                                    "tile": name, "area_m2": round(g.area, 3)}))
                geoms["interrows"].append(g)
            elif lab == "waste":
                wid += 1
                c = g.centroid
                layers["waste"].append(feat(g, {"waste_id": f"W{wid:03d}", "vineyard_id": a.get("vineyard_id", ""),
                                                "tile": name, "cx": round(c.x, 2), "cy": round(c.y, 2)}))
    canopy_area = unary_union(geoms["canopies"]).area if geoms["canopies"] else 0.0
    inter_area = unary_union(geoms["interrows"]).area if geoms["interrows"] else 0.0
    blocks = sorted({f["properties"]["vineyard_id"] for L in layers.values() for f in L
                     if f["properties"].get("vineyard_id")})
    summary = {
        "blocks": len(blocks), "block_ids": blocks, "rows": len(row_len),
        "total_row_length_m": round(sum(row_len.values()), 2),
        "canopy_area_m2": round(canopy_area, 2), "canopy_area_ha": round(canopy_area / 1e4, 4),
        "interrow_area_m2": round(inter_area, 2), "interrow_area_ha": round(inter_area / 1e4, 4),
        "canopy_count": len(geoms["canopies"]), "waste_count": len(layers["waste"]),
        "row_lengths": [{"row_id": r, "vineyard_id": row_block[r], "length_m": round(L, 2)}
                        for r, L in sorted(row_len.items())],
    }
    return layers, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cvat", required=True)
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--out", default=str(C.OUT / "geojson"))
    ap.add_argument("--web", default=str(C.ROOT / "web" / "data"))
    a = ap.parse_args()
    layers, summary = convert(Path(a.cvat), Path(a.tiles))
    out, web = Path(a.out), Path(a.web)
    out.mkdir(parents=True, exist_ok=True)
    web.mkdir(parents=True, exist_ok=True)
    for name in ("canopies", "rows", "interrows", "waste"):
        feats = layers.get(name, [])
        (out / f"{name}.geojson").write_text(json.dumps(fc(feats)))
        (web / f"{name}.geojson").write_text(json.dumps(fc(feats)))
    # organiser inputs for the map (start, passages, forbidden, study area), already EPSG:32635
    for f in C.ROUTE_IN.glob("*.geojson"):
        (web / f.name).write_text(f.read_text())
    for p in (out / "summary.json", web / "summary.json"):
        p.write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "row_lengths"}, indent=1))


def _shape(f):
    from shapely.geometry import shape
    return shape(f["geometry"])


if __name__ == "__main__":
    main()
