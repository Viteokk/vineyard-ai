"""Contact sheet of the model's waste boxes (out/waste_model.json from pipeline.infer_multi), best score first.

Each cell: 6.4 m crop at full resolution around the box, box in red, rank + score + tile underneath.
Used to pick --conf-waste by eye: where the boxes stop being clear litter.
Usage:  python scripts/waste_sheet.py [--top 80] [--out out/preview/waste_model_sheet.jpg]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402

CELL, SIDE = 160, 256          # output cell px, crop side in tile px (6.4 m)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", default=str(C.OUT / "waste_model.json"))
    ap.add_argument("--top", type=int, default=80)
    ap.add_argument("--cols", type=int, default=10)
    ap.add_argument("--out", default=str(C.OUT / "preview" / "waste_model_sheet.jpg"))
    a = ap.parse_args()
    boxes = json.loads(Path(a.scores).read_text())[: a.top]
    rows = (len(boxes) + a.cols - 1) // a.cols
    sheet = Image.new("RGB", (a.cols * CELL, rows * (CELL + 16)), "black")
    d = ImageDraw.Draw(sheet)
    cache = {}
    for i, b in enumerate(boxes):
        if b["tile"] not in cache:
            cache = {b["tile"]: Image.open(C.TILES / b["tile"]).convert("RGB")}
        im = cache[b["tile"]]
        x0, y0, x1, y1 = b["px"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        L, T = int(cx - SIDE / 2), int(cy - SIDE / 2)
        crop = im.crop((L, T, L + SIDE, T + SIDE)).resize((CELL, CELL))
        k = CELL / SIDE
        ImageDraw.Draw(crop).rectangle([(x0 - L) * k - 2, (y0 - T) * k - 2, (x1 - L) * k + 2, (y1 - T) * k + 2], outline="red", width=2)
        gx, gy = (i % a.cols) * CELL, (i // a.cols) * (CELL + 16)
        sheet.paste(crop, (gx, gy))
        d.text((gx + 3, gy + CELL + 2), f"#{i + 1} {b['score']:.2f} {b['tile'][7:16]}", fill="white")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(a.out, quality=88)
    print(f"{len(boxes)} boxes -> {a.out}")


if __name__ == "__main__":
    main()
