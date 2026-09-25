"""Build the web map assets: orthophoto mosaic chunks + prediction layers (all in UTM, EPSG:32635).

- web/data/mosaic/c_<i>_<j>.webp  4x4 source tiles per chunk at 0.1 m/px (2048 px), no-data transparent.
  Few files instead of 311 (artifact hosts cap the file count); the map places each by its UTM bounds.
- web/data/hires/<tile>.webp        full 0.025 m/px for the reference tiles (shown when zoomed in).
- web/data/mosaic_index.json        {"chunks": [{img, bounds}], "hires": [{img, bounds, name}]}
- web/data/pred/*.geojson + summary.json   automatic pre-annotations (out/baseline_all.xml) for all tiles.

Needs web/data/tiles/*.jpg from scripts/make_web_tiles.py.
Usage: python scripts/build_web_map.py [--pred out/baseline_all.xml]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.tiles import all_tiles, open_tile  # noqa: E402
from pipeline.to_geojson import convert, fc  # noqa: E402

WEB = C.ROOT / "web" / "data"
N = 4          # source tiles per chunk side
SRC = 512      # px of a web tile (0.1 m/px)
TILE_M = 51.2


def rgba(img: Image.Image) -> Image.Image:
    a = np.asarray(img.convert("RGB"))
    nodata = a.max(2) < 18                      # black borders of the orthomosaic (JPEG-noisy)
    alpha = np.where(nodata, 0, 255).astype(np.uint8)
    return Image.fromarray(np.dstack([a, alpha]), "RGBA")


def round_coords(obj, nd=2):
    if isinstance(obj, list):
        return [round_coords(v, nd) for v in obj]
    if isinstance(obj, float):
        return round(obj, nd)
    return obj


def compact_polys(feats):
    """Polygons grouped by tile as delta-encoded integer centimetres (GeoJSON of 38k canopies is >20 MB).
    {"x0", "y0", "tiles": {tile: [[x, y, dx, dy, ...], ...]}} - exterior rings only, relative to (x0, y0)."""
    xs = [c[0] for f in feats for c in _ring(f)]
    ys = [c[1] for f in feats for c in _ring(f)]
    x0, y0 = (min(xs), min(ys)) if xs else (0.0, 0.0)
    tiles: dict[str, list] = {}
    for f in feats:
        ring = _ring(f)[:-1]
        pts = [(round((x - x0) * 100), round((y - y0) * 100)) for x, y in ring]
        flat, px, py = [], 0, 0
        for k, (x, y) in enumerate(pts):
            flat += [x, y] if k == 0 else [x - px, y - py]
            px, py = x, y
        tiles.setdefault(f["properties"]["tile"], []).append(flat)
    return {"x0": round(x0, 2), "y0": round(y0, 2), "tiles": tiles}


def _ring(f):
    g = f["geometry"]
    return g["coordinates"][0] if g["type"] == "Polygon" else max(g["coordinates"], key=len)[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default=str(C.OUT / "baseline_all.xml"))
    ap.add_argument("--quality", type=int, default=72)
    a = ap.parse_args()
    tiles = all_tiles()
    minx = min(t.x0 for t in tiles)
    maxy = max(t.y0 for t in tiles)
    (WEB / "mosaic").mkdir(parents=True, exist_ok=True)
    (WEB / "hires").mkdir(parents=True, exist_ok=True)

    groups: dict[tuple[int, int], list] = {}
    for t in tiles:
        c, r = round((t.x0 - minx) / TILE_M), round((maxy - t.y0) / TILE_M)
        groups.setdefault((r // N, c // N), []).append((r % N, c % N, t))
    chunks, total = [], 0
    for (i, j), items in sorted(groups.items()):
        canvas = Image.new("RGBA", (N * SRC, N * SRC), (0, 0, 0, 0))
        for r, c, t in items:
            src = WEB / "tiles" / t.name.replace(".tif", ".jpg")
            canvas.paste(rgba(Image.open(src)), (c * SRC, r * SRC))
        bb = canvas.getbbox()                     # crop empty margins
        canvas = canvas.crop(bb)
        x0 = minx + (j * N) * TILE_M + bb[0] / SRC * TILE_M
        y1 = maxy - (i * N) * TILE_M - bb[1] / SRC * TILE_M
        x1 = x0 + canvas.width / SRC * TILE_M
        y0 = y1 - canvas.height / SRC * TILE_M
        dst = WEB / "mosaic" / f"c_{i}_{j}.webp"
        canvas.save(dst, "WEBP", quality=a.quality, method=6)
        total += dst.stat().st_size
        chunks.append({"img": f"data/mosaic/{dst.name}", "bounds": [round(v, 2) for v in (x0, y0, x1, y1)]})
    hires = []
    for p in sorted((C.EXAMPLES / "images").glob("*.tif")):
        t = open_tile(p)
        dst = WEB / "hires" / t.name.replace(".tif", ".webp")
        rgba(Image.open(p)).save(dst, "WEBP", quality=78, method=6)
        total += dst.stat().st_size
        hires.append({"name": t.name, "img": f"data/hires/{dst.name}", "bounds": [round(v, 2) for v in t.bounds]})
    tl = [{"name": t.name, "bounds": [round(v, 2) for v in t.bounds]} for t in tiles]
    (WEB / "mosaic_index.json").write_text(json.dumps({"chunks": chunks, "hires": hires, "tiles": tl}))
    print(f"{len(chunks)} mosaic chunks + {len(hires)} hi-res tiles, {total / 1e6:.1f} MB")

    if Path(a.pred).exists():
        layers, summary = convert(Path(a.pred), C.TILES)
        out = WEB / "pred"
        out.mkdir(exist_ok=True)
        for name in ("rows", "interrows", "waste"):
            feats = layers.get(name, [])
            for f in feats:
                f["geometry"]["coordinates"] = round_coords(json.loads(json.dumps(f["geometry"]["coordinates"])))
            (out / f"{name}.geojson").write_text(json.dumps(fc(feats), separators=(",", ":")))
        (out / "canopies.json").write_text(json.dumps(compact_polys(layers.get("canopies", [])), separators=(",", ":")))
        (out / "canopies.geojson").unlink(missing_ok=True)
        summary["tiles_with_objects"] = len({f["properties"]["tile"] for f in layers.get("rows", [])})
        summary["source"] = Path(a.pred).name
        (out / "summary.json").write_text(json.dumps(summary, indent=1))
        sizes = {p.name: round(p.stat().st_size / 1e6, 2) for p in out.glob("*")}
        print("pred layers (MB):", sizes)


if __name__ == "__main__":
    main()
