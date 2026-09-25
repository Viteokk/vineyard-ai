"""Downsample the 311 GeoTIFF tiles to light JPEGs for the web map (0.1 m/px, 512x512) + index.json.

The web map uses Leaflet CRS.Simple directly in UTM metres (EPSG:32635), so the imagery and all
GeoJSON layers line up exactly without reprojection. index.json: [{name, bounds:[minx,miny,maxx,maxy]}]
Usage: python scripts/make_web_tiles.py [--size 512]
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.tiles import all_tiles  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=512)
    a = ap.parse_args()
    out = C.ROOT / "web" / "data" / "tiles"
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for t in all_tiles():
        jpg = out / t.name.replace(".tif", ".jpg")
        if not jpg.exists():
            Image.open(t.path).convert("RGB").resize((a.size, a.size), Image.LANCZOS).save(jpg, quality=80)
        index.append({"name": t.name, "img": f"data/tiles/{jpg.name}", "bounds": [round(v, 2) for v in t.bounds]})
    (out.parent / "tiles_index.json").write_text(json.dumps(index))
    minx = min(i["bounds"][0] for i in index); miny = min(i["bounds"][1] for i in index)
    maxx = max(i["bounds"][2] for i in index); maxy = max(i["bounds"][3] for i in index)
    print(f"{len(index)} web tiles; extent UTM: {minx},{miny} -> {maxx},{maxy}")


if __name__ == "__main__":
    main()
