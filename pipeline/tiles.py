"""Tile I/O and georeferencing (pixel <-> EPSG:32635 metres).

Tiles are JPEG-compressed GeoTIFFs; georef is read from TIFF tags, no GDAL needed:
  33922 ModelTiepoint -> (.., .., .., X0, Y0, ..) = upper-left corner in UTM
  33550 ModelPixelScale -> (0.025, 0.025, 0)
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
NAME_RE = re.compile(r"siret3_r(\d{3})_c(\d{3})\.tif$")


@dataclass(frozen=True)
class Tile:
    name: str      # e.g. siret3_r018_c010.tif
    path: Path
    row: int       # grid row from file name
    col: int       # grid column from file name
    x0: float      # UTM X of upper-left corner
    y0: float      # UTM Y of upper-left corner
    res: float     # metres per pixel
    width: int
    height: int

    def px_to_utm(self, px) -> np.ndarray:
        """(N,2) pixel coords (x right, y down) -> (N,2) UTM metres."""
        p = np.asarray(px, dtype=float).reshape(-1, 2)
        return np.column_stack([self.x0 + p[:, 0] * self.res, self.y0 - p[:, 1] * self.res])

    def utm_to_px(self, xy) -> np.ndarray:
        q = np.asarray(xy, dtype=float).reshape(-1, 2)
        return np.column_stack([(q[:, 0] - self.x0) / self.res, (self.y0 - q[:, 1]) / self.res])

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(minx, miny, maxx, maxy) in UTM."""
        return (self.x0, self.y0 - self.height * self.res,
                self.x0 + self.width * self.res, self.y0)

    def read(self) -> np.ndarray:
        """RGB uint8 array (H, W, 3)."""
        return np.asarray(Image.open(self.path).convert("RGB"))


def open_tile(path: Path | str) -> Tile:
    path = Path(path)
    m = NAME_RE.search(path.name)
    if not m:
        raise ValueError(f"Unexpected tile name: {path.name}")
    with Image.open(path) as im:
        tags = im.tag_v2
        tie = tags[33922]
        scale = tags[33550]
        w, h = im.size
    return Tile(path.name, path, int(m.group(1)), int(m.group(2)),
                float(tie[3]), float(tie[4]), float(scale[0]), w, h)


@lru_cache(maxsize=1)
def all_tiles(folder: Path | str = C.TILES) -> tuple[Tile, ...]:
    tiles = tuple(open_tile(p) for p in sorted(Path(folder).glob("siret3_r*_c*.tif")))
    return tiles


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else C.TILES
    ts = all_tiles(folder)
    print(f"{len(ts)} tiles")
    for t in ts[:3]:
        print(t.name, t.bounds, t.px_to_utm([[0, 0], [2048, 2048]]).tolist())
