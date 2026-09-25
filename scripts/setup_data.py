"""Extract the challenge package into ./data (run once per machine).

Usage:  python scripts/setup_data.py            # raw package = parent folder of repo
        VINEYARD_RAW=/path/to/package python scripts/setup_data.py
"""
import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402


def main() -> None:
    raw = C.RAW
    zips = sorted((raw / "01_tiles").glob("siret3_challenge_tiles_part*of5.zip"))
    if len(zips) != 5:
        sys.exit(f"Expected 5 tile ZIPs in {raw/'01_tiles'}, found {len(zips)}. Set VINEYARD_RAW.")

    C.TILES.mkdir(parents=True, exist_ok=True)
    parts = {}
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".tif")]
            for n in names:
                target = C.TILES / Path(n).name
                if not target.exists():
                    with zf.open(n) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            parts[z.stem] = sorted(Path(n).name for n in names)
        print(f"{z.name}: {len(names)} tiles")

    # remember original part membership -> export one CVAT ZIP per part
    (C.DATA / "parts.json").write_text(json.dumps(parts, indent=1))

    C.ROUTE_IN.mkdir(parents=True, exist_ok=True)
    for f in (raw / "02_route").glob("*.geojson"):
        shutil.copy2(f, C.ROUTE_IN / f.name)

    C.EXAMPLES.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw / "05_examples" / "siret3_examples_cvat.zip") as zf:
        zf.extractall(C.EXAMPLES)

    n = len(list(C.TILES.glob("*.tif")))
    print(f"tiles: {n} (expected 311)")
    if n != 311:
        sys.exit("Tile count mismatch!")


if __name__ == "__main__":
    main()
