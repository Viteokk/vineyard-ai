"""Build a YOLO-seg dataset (class 0 = vineyard canopy) from:
  - GOLD: organiser reference annotations (data/examples) — one tile for train (oversampled), one for val
  - PSEUDO: canopies predicted by the classical baseline on all other tiles

Tiles (2048 px) are cut into 640 px crops (stride 512) so vines keep their native size (~30 px).
Usage:  python train/make_dataset.py                 # all tiles (runs baseline if needed, ~5 min)
        python train/make_dataset.py --limit 20      # quick test
Output: dataset/{images,labels}/{train,val} + dataset/data.yaml  (override dir with --out)
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from shapely.geometry import Polygon, box
from shapely.validation import make_valid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat, write_cvat  # noqa: E402
from pipeline.tiles import open_tile  # noqa: E402

CROP, STRIDE, SIZE = 640, 512, 2048
GOLD_TRAIN = ["siret3_r021_c012.tif"]
GOLD_VAL = ["siret3_r006_c004.tif"]
GOLD_REPEAT = 10          # oversample the reference tile
NEG_SHARE = 0.10          # share of empty crops kept (teaches "no vine here")


def offsets():
    xs = list(range(0, SIZE - CROP + 1, STRIDE))
    if xs[-1] != SIZE - CROP:
        xs.append(SIZE - CROP)
    return [(x, y) for y in xs for x in xs]


def polys(objs):
    out = []
    for o in objs:
        if o["label"] != "vineyard" or len(o["points"]) < 3:
            continue
        p = Polygon(o["points"])
        p = p if p.is_valid else make_valid(p)
        out.append(p)
    return out


def crop_labels(ps, x0, y0):
    win = box(x0, y0, x0 + CROP, y0 + CROP)
    lines = []
    for p in ps:
        if not p.intersects(win):
            continue
        q = p.intersection(win)
        for g in getattr(q, "geoms", [q]):
            if g.geom_type != "Polygon" or g.area < 20:   # slivers
                continue
            c = np.asarray(g.exterior.coords)[:-1]
            c = np.clip((c - [x0, y0]) / CROP, 0, 1)
            lines.append("0 " + " ".join(f"{v:.5f}" for v in c.ravel()))
    return lines


def write_tile(out: Path, tile_path: Path, ps, split: str, repeat: int, rng, stats):
    arr = np.asarray(Image.open(tile_path).convert("RGB"))
    stem = tile_path.stem
    for (x0, y0) in offsets():
        crop = arr[y0:y0 + CROP, x0:x0 + CROP]
        if (crop.sum(2) < 30).mean() > 0.5:          # mostly black no-data
            continue
        lines = crop_labels(ps, x0, y0)
        if not lines and rng.random() > NEG_SHARE:
            continue
        for r in range(repeat):
            name = f"{stem}_{x0}_{y0}" + (f"_r{r}" if repeat > 1 else "")
            Image.fromarray(crop).save(out / "images" / split / f"{name}.jpg", quality=92)
            (out / "labels" / split / f"{name}.txt").write_text("\n".join(lines))
            stats[split] += 1
            stats[split + "_obj"] += len(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only N pseudo-label tiles (quick test)")
    ap.add_argument("--out", default=str(C.ROOT / "dataset"))
    ap.add_argument("--pseudo", default=str(C.OUT / "baseline_all.xml"),
                    help="CVAT with baseline predictions on all tiles (created if missing)")
    a = ap.parse_args()
    rng = random.Random(0)
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    for s in ("train", "val"):
        (out / "images" / s).mkdir(parents=True)
        (out / "labels" / s).mkdir(parents=True)

    gold = read_cvat(C.EXAMPLES / "annotations.xml")
    tiles = sorted(C.TILES.glob("siret3_r*_c*.tif"))
    pseudo_tiles = [t for t in tiles if t.name not in gold]
    if a.limit:
        pseudo_tiles = pseudo_tiles[: a.limit]

    pseudo_path = Path(a.pseudo)
    if pseudo_path.exists() and not a.limit:
        pseudo = read_cvat(pseudo_path)
    else:
        from pipeline.baseline import process_tile
        print(f"running baseline on {len(pseudo_tiles)} tiles for pseudo-labels ...")
        pseudo = {t.name: process_tile(open_tile(t)) for t in pseudo_tiles}
        if not a.limit:
            pseudo_path.parent.mkdir(parents=True, exist_ok=True)
            write_cvat(pseudo, pseudo_path)

    stats = {"train": 0, "val": 0, "train_obj": 0, "val_obj": 0}
    for n in GOLD_TRAIN:
        write_tile(out, C.TILES / n, polys(gold[n]), "train", GOLD_REPEAT, rng, stats)
    for n in GOLD_VAL:
        write_tile(out, C.TILES / n, polys(gold[n]), "val", 1, rng, stats)
    for t in pseudo_tiles:
        write_tile(out, t, polys(pseudo.get(t.name, [])), "train", 1, rng, stats)

    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: vineyard\n")
    print(f"train crops: {stats['train']} ({stats['train_obj']} canopies) | "
          f"val crops: {stats['val']} ({stats['val_obj']} canopies)")
    print(f"dataset ready: {out/'data.yaml'}")


if __name__ == "__main__":
    main()
