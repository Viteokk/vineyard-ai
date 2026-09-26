"""Empty the tiles where the YOLO11 canopy model finds no vine at all (out/emptied_by_model.json, 72 tiles: meadow,
scrub, ploughed fields, gardens), whatever the classical detector drew there. Used for the v3 pre-annotations
(classical rows on vegetation AND the model sees vines). Usage: python scripts/model_veto.py IN.xml OUT.xml"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat, write_cvat  # noqa: E402

inp, out = sys.argv[1], sys.argv[2]
veto = set(json.loads((C.OUT / "emptied_by_model.json").read_text()))
d = read_cvat(inp)
n = sum(1 for t in veto if d.get(t))
for t in veto:
    d[t] = []
write_cvat(d, out)
print(f"model veto: {n} tiles emptied ({len(veto)} in the list) -> {out}")
