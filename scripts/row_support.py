"""Quality check of the row axes on every tile: is each line really on a vine row?

For every `row` polyline of an annotation file, vegetation (ExG > 0.10, the detector's canopy threshold) is sampled
ON the line (±0.25 m) and on the two MID-LINES half a row spacing to each side. On a real vine row the line sits on
the canopies: on/mid >> 1 (typically 2-5). A line drawn across the rows, over trees, scrub or a ploughed field has
on/mid ~ 1. Per tile: length-weighted support = on / mid, and the share of rows with support < 1.3.
Output: out/row_support.json (sorted, worst first). Usage: python scripts/row_support.py [--inp out/pre_global.xml]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat  # noqa: E402

RES = 0.025


def exg_mask(img):
    f = img.astype(np.float32)
    s = f.sum(2) + 1e-6
    return ((2 * f[..., 1] - f[..., 0] - f[..., 2]) / s) > 0.10


def sample(mask, pts):
    h, w = mask.shape
    xs = np.clip(pts[:, 0].round().astype(int), 0, w - 1)
    ys = np.clip(pts[:, 1].round().astype(int), 0, h - 1)
    ok = (pts[:, 0] >= 0) & (pts[:, 0] < w) & (pts[:, 1] >= 0) & (pts[:, 1] < h)
    return mask[ys[ok], xs[ok]]


def line_points(poly, step_px=10):
    out = []
    for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step_px))
        t = np.linspace(0, 1, n, endpoint=False)
        out.append(np.column_stack([x0 + (x1 - x0) * t, y0 + (y1 - y0) * t]))
    out.append(np.array([poly[-1]]))
    return np.vstack(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", default=str(C.OUT / "pre_global.xml"))
    ap.add_argument("--out", default=str(C.OUT / "row_support.json"))
    a = ap.parse_args()
    ann = read_cvat(a.inp)
    half = 1.3 / RES                                   # half the median row spacing (2.6 m), in px
    band = [-10, -5, 0, 5, 10]                          # ±0.25 m across the line, in px
    res = []
    for tile, objs in sorted(ann.items()):
        rows = [o["points"] for o in objs if o["label"] == "row" and len(o["points"]) >= 2]
        if not rows:
            continue
        img = np.asarray(Image.open(C.TILES / tile).convert("RGB"))
        m = exg_mask(img)
        on_all, mid_all, L_all, bad = 0.0, 0.0, 0.0, 0
        for r in rows:
            p = np.asarray(r, float)
            d = p[-1] - p[0]
            L = float(np.hypot(*d))
            if L < 40:                                  # < 1 m: ignore
                continue
            nrm = np.array([-d[1], d[0]]) / L
            pts = line_points(p)
            on = np.concatenate([sample(m, pts + nrm * o) for o in band]).mean() if len(pts) else 0
            mid = np.concatenate([sample(m, pts + nrm * (s * half + o)) for s in (-1, 1) for o in band]).mean()
            sup = on / max(mid, 0.02)
            on_all += on * L; mid_all += mid * L; L_all += L
            bad += sup < 1.3
        if not L_all:
            continue
        res.append({"tile": tile, "rows": len(rows), "support": round((on_all / L_all) / max(mid_all / L_all, 0.02), 2),
                    "on": round(on_all / L_all, 3), "mid": round(mid_all / L_all, 3), "bad_share": round(bad / len(rows), 2),
                    "row_len_m": round(L_all * RES, 1)})
    res.sort(key=lambda r: r["support"])
    Path(a.out).write_text(json.dumps(res, indent=0))
    print(f"{len(res)} tiles with rows; support < 1.3: {sum(r['support'] < 1.3 for r in res)}, "
          f"1.3-1.8: {sum(1.3 <= r['support'] < 1.8 for r in res)}, >= 1.8: {sum(r['support'] >= 1.8 for r in res)} -> {a.out}")


if __name__ == "__main__":
    main()
