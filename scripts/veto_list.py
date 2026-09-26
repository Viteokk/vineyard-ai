"""Tiles where the two detectors disagree completely -> out/veto_yolo11.json (used by scripts/model_veto.py): the
high-recall v1 classical detector (`pipeline.baseline --v1`) draws canopies there and the YOLO11 canopy model finds none
(meadow, scrub, ploughed fields, gardens; checked visually). Very young vineyards that only v3 finds (tiny plants the
model misses, e.g. r037_c024) are not in this list.
Input: the model's canopies on every tile, from the released weights (v0.2-weights):
  python -m pipeline.infer_multi --weights yolo11n-seg-vineyard-waste.pt --base out/baseline_all.xml \\
         --out out/model11_canopy_all.xml --scores out/waste_model11.json --canopy model --conf 0.25 --conf-review 0.15
Usage: python scripts/veto_list.py [MODEL.xml] [V1_CLASSICAL.xml]   (defaults: out/model11_canopy_all.xml, out/pre_global.xml)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat  # noqa: E402

src = sys.argv[1] if len(sys.argv) > 1 else str(C.OUT / "model11_canopy_all.xml")
cls = sys.argv[2] if len(sys.argv) > 2 else str(C.OUT / "pre_global.xml")
md, cl = read_cvat(src), read_cvat(cls)
can = lambda d, t: any(o["label"] == "vineyard" for o in d.get(t, []))
tiles = sorted(p.name for p in C.TILES.glob("siret3_r*_c*.tif"))
veto = [t for t in tiles if can(cl, t) and not can(md, t)]
(C.OUT / "veto_yolo11.json").write_text(json.dumps(veto, indent=0))
print(f"{len(veto)} tiles: v1 classical sees vines, YOLO11 none -> out/veto_yolo11.json")
