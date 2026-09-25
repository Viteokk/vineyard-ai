"""Draw predictions (and optionally the reference) over the tile image for visual QA.

Colours: canopy = magenta outline, row axis = yellow, inter-row = cyan (bare_soil) / orange (mixed) /
green (vegetation), waste = red box. Reference (if given) is drawn in white on the left half of a
side-by-side image.

Usage:  python -m pipeline.preview --pred out/baseline.xml --tiles data/examples/images --out out/preview
        [--ref data/examples/annotations.xml] [--scale 0.5]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.cvat_io import read_cvat  # noqa: E402
from pipeline.tiles import open_tile  # noqa: E402

COVER_BGR = {"bare_soil": (255, 255, 0), "mixed": (0, 165, 255), "vegetation": (0, 200, 0),
             "unassessable": (128, 128, 128)}


def draw(img: np.ndarray, objs: list[dict], s: float) -> np.ndarray:
    out = img.copy()
    fill = out.copy()
    for o in objs:
        if o["label"] == "interrow_area":
            pts = np.round(np.asarray(o["points"]) * s).astype(np.int32)
            cv2.fillPoly(fill, [pts], COVER_BGR.get(o["attrs"].get("interrow_cover"), (128, 128, 128)))
    out = cv2.addWeighted(fill, 0.25, out, 0.75, 0)
    for o in objs:
        if o["type"] == "box":
            p1 = (int(o["xtl"] * s), int(o["ytl"] * s))
            p2 = (int(o["xbr"] * s), int(o["ybr"] * s))
            cv2.rectangle(out, p1, p2, (0, 0, 255), 2)
            continue
        pts = np.round(np.asarray(o["points"]) * s).astype(np.int32)
        if o["label"] == "vineyard":
            cv2.polylines(out, [pts], True, (255, 0, 255), 1, cv2.LINE_AA)
        elif o["label"] == "row":
            col = (0, 0, 255) if o["attrs"].get("row_structure") == "disrupted" else (0, 255, 255)
            cv2.polylines(out, [pts], False, col, 2, cv2.LINE_AA)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True)
    ap.add_argument("--tiles", required=True)
    ap.add_argument("--ref", default=None)
    ap.add_argument("--out", default="out/preview")
    ap.add_argument("--scale", type=float, default=0.5)
    a = ap.parse_args()
    pred = read_cvat(a.pred)
    ref = read_cvat(a.ref) if a.ref else {}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    for name, objs in pred.items():
        path = Path(a.tiles) / name
        if not path.exists():
            continue
        img = cv2.cvtColor(open_tile(path).read(), cv2.COLOR_RGB2BGR)
        img = cv2.resize(img, None, fx=a.scale, fy=a.scale, interpolation=cv2.INTER_AREA)
        panel = draw(img, objs, a.scale)
        if name in ref:
            left = draw(img, ref[name], a.scale)
            for im, txt in ((left, "REFERENCE"), (panel, "PREDICTION")):
                cv2.putText(im, txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 5)
                cv2.putText(im, txt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            panel = np.hstack([left, np.full((img.shape[0], 6, 3), 255, np.uint8), panel])
        dst = Path(a.out) / (Path(name).stem + ".jpg")
        cv2.imwrite(str(dst), panel, [cv2.IMWRITE_JPEG_QUALITY, 85])
        print(dst)


if __name__ == "__main__":
    main()
