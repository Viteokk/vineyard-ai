"""Central paths & constants. Override raw data location with env VINEYARD_RAW."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Raw challenge package (the folder with 01_tiles, 02_route, ...). Default: parent of the repo.
RAW = Path(os.environ.get("VINEYARD_RAW", ROOT.parent))
DATA = ROOT / "data"          # extracted, git-ignored
TILES = DATA / "tiles"        # 311 GeoTIFF tiles, original names
ROUTE_IN = DATA / "route"     # start / passages / forbidden / study_area geojson
EXAMPLES = DATA / "examples"  # annotations.xml + images/ of the 2 reference tiles
OUT = ROOT / "out"            # intermediate outputs, git-ignored

CRS = "EPSG:32635"
PX = 0.025          # metres per pixel
TILE_PX = 2048      # tile size in pixels (51.2 m)

LABELS = ("vineyard", "waste", "row", "interrow_area")
ROW_STRUCTURE = ("regular", "disrupted", "unassessable")
INTERROW_COVER = ("bare_soil", "vegetation", "mixed", "unassessable")
