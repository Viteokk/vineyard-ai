"""One YOLO-seg dataset, two classes: 0 = vineyard (canopy), 1 = waste -> one model for both object layers.

Sources (all open, licences checked):
  canopy  dataset/ from train/make_dataset.py: the organisers' reference tile + classical pseudo-labels on the
          Sireț3 tiles, 640 px crops at the native 2.5 cm/px. These crops are also the Sireț3 background for
          waste: white vine tubes, stakes, pale soil, stones are all in them and are not labelled as waste.
  waste   DroneWaste v1.0 (CC BY 4.0, zenodo 17045559, named in the challenge description): 640 px crops of
          drone orthomosaics of 17 dump sites, COCO masks.
          UAVVaste (CC BY 4.0, zenodo 8214061): 772 UAV photos of litter, COCO masks, resized x0.25 / x0.5 so litter has
          about the pixel size it has on the 2.5 cm/px tiles, then cut into 640 px crops.
Categories the annotation rules do NOT count as waste (vehicles, soil / excavation material, asphalt milling,
slag) are dropped; their images stay, so the model learns them as background.
Val (never in train): DroneWaste sites 13 and 17, the official UAVVaste val + test photos, the canopy val tile.

Usage:  python train/make_multi_dataset.py --raw ../waste_data [--out dataset_multi]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
from pathlib import Path

from PIL import Image
from shapely.geometry import Polygon, box
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parents[1]
CROP, STRIDE = 640, 512
NOT_WASTE = {"Vehicles", "Excavation materials", "Rubble", "Asphalt milling", "Foundry"}
DW_VAL_SITES = {"site13", "site17"}
UV_SCALES = (0.25, 0.5)  # photo resize factors (~1.6 and ~0.8 cm/px) to bring litter near the tiles' pixel size
BG_DW = 600              # DroneWaste crops without any waste kept as background (train)
UV_EMPTY_SHARE = 0.15    # chance x4 of keeping one empty crop per resized photo
MIN_AREA_PX = 12         # drop slivers left after cropping


def largest_poly(seg, bbox) -> Polygon:
    polys = []
    if isinstance(seg, list):
        for flat in seg:
            if len(flat) >= 6:
                p = make_valid(Polygon(list(zip(flat[0::2], flat[1::2]))))
                polys += [g for g in getattr(p, "geoms", [p]) if g.geom_type == "Polygon"]
    if not polys:
        x, y, w, h = bbox
        return box(x, y, x + w, y + h)
    return max(polys, key=lambda g: g.area)


def yolo_line(cls: int, poly: Polygon, ox: float, oy: float, w: int, h: int) -> str | None:
    if poly.is_empty or poly.area < MIN_AREA_PX:
        return None
    pts = list(poly.exterior.coords)[:-1]
    if len(pts) < 3:
        return None
    return f"{cls} " + " ".join(f"{min(max((x - ox) / w, 0), 1):.5f} {min(max((y - oy) / h, 0), 1):.5f}" for x, y in pts)


def link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    os.symlink(src.resolve(), dst)


def load_coco(path: Path):
    d = json.loads(path.read_text())
    cats = {c["id"]: c["name"] for c in d["categories"]}
    by = {}
    for a in d["annotations"]:
        by.setdefault(a["image_id"], []).append(a)
    return d["images"], by, cats


def dronewaste(raw: Path, out: Path, rng: random.Random) -> dict:
    imgs, by, cats = load_coco(raw / "dronewaste_v1.0.json")
    img_dir = next(p.parent for p in raw.rglob(imgs[0]["file_name"]))
    n = {"train": 0, "val": 0, "boxes": 0, "bg": 0, "dropped_not_waste": 0}
    empty = []
    for im in imgs:
        split = "val" if im["site"] in DW_VAL_SITES else "train"
        lines = []
        for a in by.get(im["id"], []):
            if cats[a["category_id"]] in NOT_WASTE:
                n["dropped_not_waste"] += 1
                continue
            ln = yolo_line(1, largest_poly(a.get("segmentation"), a["bbox"]), 0, 0, im["width"], im["height"])
            if ln:
                lines.append(ln)
        if not lines:
            empty.append((im, split))
            continue
        stem = "dw_" + Path(im["file_name"]).stem
        link(img_dir / im["file_name"], out / "images" / split / (stem + Path(im["file_name"]).suffix))
        (out / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + "\n")
        n[split] += 1
        n["boxes"] += len(lines)
    rng.shuffle(empty)
    for im, split in empty[:BG_DW]:
        stem = "dw_" + Path(im["file_name"]).stem
        link(img_dir / im["file_name"], out / "images" / split / (stem + Path(im["file_name"]).suffix))
        (out / "labels" / split / f"{stem}.txt").write_text("")
        n["bg"] += 1
    return n


def uavvaste(raw: Path, out: Path, rng: random.Random) -> dict:
    """Photos are ~0.4 cm/px (litter ~76 px wide); the tiles are 2.5 cm/px. Each photo is resized by UV_SCALES
    so litter lands at a similar pixel size, then cut into 640 px crops. Official train / val+test split."""
    root = next(p.parent.parent for p in raw.rglob("annotations.json") if "uav" in str(p).lower())
    imgs, by, _ = load_coco(root / "annotations" / "annotations.json")
    split_file = json.loads((root / "annotations" / "train_val_test_distribution_file.json").read_text())
    held = set(split_file.get("val", [])) | set(split_file.get("test", []))
    n = {"train": 0, "val": 0, "boxes": 0, "photos": 0, "missing": 0}
    for im in imgs:
        src = root / "images" / im["file_name"]
        if not src.exists():
            n["missing"] += 1
            continue
        split = "val" if im["file_name"] in held else "train"
        base = [largest_poly(a.get("segmentation"), a["bbox"]) for a in by.get(im["id"], [])]
        n["photos"] += 1
        photo = Image.open(src).convert("RGB")
        for sc in UV_SCALES:
            W, H = int(im["width"] * sc), int(im["height"] * sc)
            img = photo.resize((W, H), Image.BILINEAR)
            polys = [Polygon([(x * sc, y * sc) for x, y in p.exterior.coords]) for p in base]
            cw, ch = min(CROP, W), min(CROP, H)
            xs = list(range(0, W - cw + 1, STRIDE)) or [0]
            ys = list(range(0, H - ch + 1, STRIDE)) or [0]
            if xs[-1] != W - cw:
                xs.append(W - cw)
            if ys[-1] != H - ch:
                ys.append(H - ch)
            empties = []
            for oy in ys:
                for ox in xs:
                    win = box(ox, oy, ox + cw, oy + ch)
                    lines = []
                    for p in polys:
                        if not p.intersects(win):
                            continue
                        c = make_valid(p.intersection(win))
                        parts = [g for g in getattr(c, "geoms", [c]) if g.geom_type == "Polygon"]
                        if parts and max(parts, key=lambda g: g.area).area >= 0.4 * p.area:   # skip cut-off halves
                            ln = yolo_line(1, max(parts, key=lambda g: g.area), ox, oy, cw, ch)
                            if ln:
                                lines.append(ln)
                    if not lines:
                        empties.append((ox, oy))
                        continue
                    stem = f"uv_{Path(im['file_name']).stem}_s{int(sc * 100)}_{ox}_{oy}"
                    img.crop((ox, oy, ox + cw, oy + ch)).save(out / "images" / split / f"{stem}.jpg", quality=92)
                    (out / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + "\n")
                    n[split] += 1
                    n["boxes"] += len(lines)
            if empties and rng.random() < UV_EMPTY_SHARE * 4:
                ox, oy = rng.choice(empties)
                stem = f"uv_{Path(im['file_name']).stem}_s{int(sc * 100)}_{ox}_{oy}"
                img.crop((ox, oy, ox + cw, oy + ch)).save(out / "images" / split / f"{stem}.jpg", quality=92)
                (out / "labels" / split / f"{stem}.txt").write_text("")
    return n


def canopy(src: Path, out: Path) -> dict:
    n = {}
    for split in ("train", "val"):
        k = 0
        for img in sorted((src / "images" / split).iterdir()):
            lab = src / "labels" / split / (img.stem + ".txt")
            link(img, out / "images" / split / ("cp_" + img.name))
            (out / "labels" / split).mkdir(parents=True, exist_ok=True)
            shutil.copy2(lab, out / "labels" / split / ("cp_" + img.stem + ".txt")) if lab.exists() else \
                (out / "labels" / split / ("cp_" + img.stem + ".txt")).write_text("")
            k += 1
        n[split] = k
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(ROOT.parent / "waste_data"))
    ap.add_argument("--canopy", default=str(ROOT / "dataset"))
    ap.add_argument("--out", default=str(ROOT / "dataset_multi"))
    a = ap.parse_args()
    raw, out = Path(a.raw), Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    for s in ("train", "val"):
        (out / "images" / s).mkdir(parents=True)
        (out / "labels" / s).mkdir(parents=True)
    rng = random.Random(0)
    stats = {"canopy": canopy(Path(a.canopy), out), "dronewaste": dronewaste(raw, out, rng),
             "uavvaste": uavvaste(raw, out, rng)}
    (out / "data.yaml").write_text(f"path: {out.resolve()}\ntrain: images/train\nval: images/val\n"
                                   "names:\n  0: vineyard\n  1: waste\n")
    (out / "stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps(stats, indent=1))
    for s in ("train", "val"):
        print(s, len(list((out / "images" / s).iterdir())), "images")


if __name__ == "__main__":
    main()
