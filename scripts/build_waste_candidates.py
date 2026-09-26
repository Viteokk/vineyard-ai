"""Waste checklist -> web layer: where to look for waste during the Marcaj correction.

Two automatic sources, both unconfirmed: the AI model's waste boxes near the vineyards (pipeline.infer_multi ->
out/waste_model.json; within 10 m of a block, <= 2.5 m per side, best score first) and the top-N ranked
candidates of the classical scanner (pipeline.waste).

Candidates only: nothing here is annotated waste and nothing feeds the routes or the Marcaj ZIPs. Each point
carries its rank, tile, pixel centre and box (for finding it in Marcaj), UTM box, score, area and the block it
would take as vineyard_id (inside a block, or the nearest one within 10 m, as in the annotation rules).

Usage:  python scripts/build_waste_candidates.py [--top 200] [--model out/waste_model.json]
        -> web/data/waste_candidates.geojson + web/data/waste_cand/NNN_rRRR_cCCC.jpg (thumbnails)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import Point, box, shape
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

WEB = Path(__file__).resolve().parents[1] / "web" / "data"
WASTE_DIST = 10.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=200)
    ap.add_argument("--cand", default=str(C.OUT / "waste_candidates.json"))
    ap.add_argument("--thumbs", default=str(C.OUT / "waste_checklist"))
    ap.add_argument("--blocks", default=str(C.OUT / "blocks.geojson"))
    ap.add_argument("--model", default=str(C.OUT / "waste_model.json"), help="AI model waste boxes (infer_multi)")
    ap.add_argument("--model-side", type=float, default=2.5, help="max box side (m) for model boxes")
    a = ap.parse_args()

    cands = json.loads(Path(a.cand).read_text())[: a.top]
    tiles = {t["name"]: t["bounds"] for t in json.loads((WEB / "mosaic_index.json").read_text())["tiles"]}
    blocks = json.loads(Path(a.blocks).read_text())["features"]
    geoms = [shape(f["geometry"]) for f in blocks]
    tree = STRtree(geoms)
    thumbs = {p.name[:3]: p for p in Path(a.thumbs).glob("*.jpg")}
    dst = WEB / "waste_cand"
    shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True)

    feats = []
    # 1) AI model boxes near the vineyards
    model = json.loads(Path(a.model).read_text()) if Path(a.model).exists() else []
    near = []
    for m in model:
        g = box(*m["box"])
        side = max(m["box"][2] - m["box"][0], m["box"][3] - m["box"][1])
        i = tree.nearest(g)
        d = geoms[i].distance(g)
        if d <= WASTE_DIST and side <= a.model_side:
            near.append((m, blocks[i]["properties"]["vineyard_id"], d))
    for k, (m, vid, d) in enumerate(near, 1):
        x0, y0, x1, y1 = m["px"]
        im = Image.open(C.TILES / m["tile"]).convert("RGB")
        cx, cy, S = (x0 + x1) / 2, (y0 + y1) / 2, 128
        crop = im.crop((int(cx - S / 2), int(cy - S / 2), int(cx + S / 2), int(cy + S / 2)))
        L, T = int(cx - S / 2), int(cy - S / 2)
        ImageDraw.Draw(crop).rectangle([x0 - L - 2, y0 - T - 2, x1 - L + 2, y1 - T + 2], outline="red", width=2)
        name = f"m{k:03d}_{m['tile'][7:16]}.jpg"
        crop.save(dst / name, quality=88)
        ux, uy = (m["box"][0] + m["box"][2]) / 2, (m["box"][1] + m["box"][3]) / 2
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(ux, 2), round(uy, 2)]},
                      "properties": {"rank": k, "source": "model", "tile": m["tile"], "kind": "model", "score": m["score"],
                                     "area_m2": round((m["box"][2] - m["box"][0]) * (m["box"][3] - m["box"][1]), 3),
                                     "on_passage": False, "px": [int(v) for v in m["px"]], "box": m["box"],
                                     "vineyard_id": vid, "block_dist_m": round(d, 1), "thumb": f"data/waste_cand/{name}"}})
    off = len(feats)
    # 2) classical scanner, top N
    for rank, c in enumerate(cands, 1):
        x0, _, _, y1 = tiles[c["tile"]]
        cx, cy = c["x"] + c["w"] / 2, c["y"] + c["h"] / 2
        ux, uy = x0 + cx * C.PX, y1 - cy * C.PX
        pt = Point(ux, uy)
        i = tree.nearest(pt)
        d = geoms[i].distance(pt)
        vid = blocks[i]["properties"]["vineyard_id"] if d <= WASTE_DIST else ""
        th = thumbs.get(f"{rank:03d}")
        if th:
            shutil.copy2(th, dst / th.name)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(ux, 2), round(uy, 2)]},
                      "properties": {
                          "rank": off + rank, "source": "classical", "tile": c["tile"], "kind": c["kind"], "score": c["score"],
                          "area_m2": c["area_m2"], "on_passage": bool(c["on_passage"]),
                          "px": [int(c["x"]), int(c["y"]), int(c["x"] + c["w"]), int(c["y"] + c["h"])],
                          "box": [round(x0 + c["x"] * C.PX, 2), round(y1 - (c["y"] + c["h"]) * C.PX, 2),
                                  round(x0 + (c["x"] + c["w"]) * C.PX, 2), round(y1 - c["y"] * C.PX, 2)],
                          "vineyard_id": vid, "block_dist_m": round(d, 1),
                          "thumb": f"data/waste_cand/{th.name}" if th else None}})
    out = WEB / "waste_candidates.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection",
                               "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32635"}},
                               "features": feats}, separators=(",", ":")))
    inside = sum(1 for f in feats if f["properties"]["vineyard_id"])
    print(f"{off} model boxes near the vineyards + {len(feats) - off} classical = {len(feats)} candidates ({inside} within {WASTE_DIST:.0f} m of a block), "
          f"{sum(1 for f in feats if f['properties']['thumb'])} thumbnails -> {out}")


if __name__ == "__main__":
    main()
