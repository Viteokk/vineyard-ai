"""Marcaj upload ZIPs (CVAT for images 1.1): annotations.xml + images/<original tile name>, validated.

The organiser part ZIPs 1-4 are already 93-94 MB with the images alone, so each part is split into ZIPs of
at most --max-mb (default 60 MB, safely under the 90 MB limit whether it means MB or MiB). Every ZIP carries
the annotations of exactly its own images. Images are copied byte-for-byte (stored, not recompressed).

Checks before writing (the run stops on any error):
  - all tiles of data/parts.json present exactly once (311), names unchanged, every image file exists
  - labels / geometry types / attribute names and values exactly as in the annotation rules (lower-case)
  - vineyard_id never empty on vineyard / row / interrow_area; row has row_id and row_structure
  - polygons >= 3 points, polylines >= 2 points, all coordinates inside the 2048 x 2048 tile

Upload order in Marcaj: one ZIP at a time, read each import report, then check Files = 311 before Publish.
Usage:  python -m pipeline.export_cvat --inp out/pre_global.xml [--out out/upload] [--max-mb 60]
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat, write_cvat  # noqa: E402

SIZE = 2048
SPEC = {  # label -> (geometry type, required attributes with allowed values or None for free text)
    "vineyard": ("polygon", {"vineyard_id": None}),
    "waste": ("box", {"vineyard_id": None}),
    "row": ("polyline", {"vineyard_id": None, "row_id": None, "row_structure": set(C.ROW_STRUCTURE)}),
    "interrow_area": ("polygon", {"vineyard_id": None, "interrow_cover": set(C.INTERROW_COVER)}),
}
MAY_BE_EMPTY = {("waste", "vineyard_id")}          # waste further than 10 m from a block has no block


def validate(ann: dict[str, list[dict]], parts: dict[str, list[str]], tiles_dir: Path) -> list[str]:
    errors = []
    names = [n for ns in parts.values() for n in ns]
    if len(names) != len(set(names)):
        errors.append("a tile appears in more than one part")
    for n in names:
        if not (tiles_dir / n).exists():
            errors.append(f"missing image file {n}")
    extra = set(ann) - set(names)
    if extra:
        errors.append(f"{len(extra)} annotated images are not in parts.json, e.g. {sorted(extra)[:3]}")
    for n, objs in ann.items():
        for i, o in enumerate(objs):
            where = f"{n} #{i} {o.get('label')}"
            if o.get("label") not in SPEC:
                errors.append(f"{where}: unknown label")
                continue
            geom, attrs = SPEC[o["label"]]
            if o["type"] != geom:
                errors.append(f"{where}: type {o['type']}, expected {geom}")
            got = o.get("attrs", {})
            for a, allowed in attrs.items():
                v = got.get(a)
                if v is None or (v == "" and (o["label"], a) not in MAY_BE_EMPTY):
                    errors.append(f"{where}: attribute {a} missing/empty")
                elif allowed is not None and v not in allowed:
                    errors.append(f"{where}: {a}={v!r} not in {sorted(allowed)}")
            for a in got:
                if a not in attrs:
                    errors.append(f"{where}: unexpected attribute {a}")
            if geom == "box":
                pts = [(o["xtl"], o["ytl"]), (o["xbr"], o["ybr"])]
            else:
                pts = o["points"]
                need = 3 if geom == "polygon" else 2
                if len(pts) < need:
                    errors.append(f"{where}: {len(pts)} points")
            if any(not (-0.5 <= x <= SIZE + 0.5 and -0.5 <= y <= SIZE + 0.5) for x, y in pts):
                errors.append(f"{where}: coordinates outside the tile")
    return errors


def split(names: list[str], tiles_dir: Path, max_bytes: int) -> list[list[str]]:
    """Greedy split in file order; leaves ~3 MB headroom per ZIP for annotations.xml."""
    groups, cur, size = [], [], 0
    for n in names:
        s = (tiles_dir / n).stat().st_size
        if cur and size + s > max_bytes - 3_000_000:
            groups.append(cur)
            cur, size = [], 0
        cur.append(n)
        size += s
    if cur:
        groups.append(cur)
    return groups


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", default=str(C.OUT / "pre_global.xml"))
    ap.add_argument("--out", default=str(C.OUT / "upload"))
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--max-mb", type=float, default=60.0)
    ap.add_argument("--only", default="", help="comma-separated tile names: one test ZIP with just these "
                                               "(for the Marcaj dry run), e.g. the two example tiles")
    ap.add_argument("--use-examples", action="store_true",
                    help="replace our predictions on the two official example tiles with the organisers' reference "
                         "annotations (data/examples/annotations.xml). Only after the mentors confirm it is allowed.")
    a = ap.parse_args()
    tiles_dir, out = Path(a.tiles), Path(a.out)
    parts = json.loads((C.DATA / "parts.json").read_text())
    ann = read_cvat(a.inp)
    if a.use_examples:
        ref = read_cvat(C.EXAMPLES / "annotations.xml")
        for n, objs in ref.items():
            ann[n] = objs
        print(f"using the official reference annotations on {len(ref)} example tiles: {', '.join(sorted(ref))}")
    if a.only:
        only = [n.strip() for n in a.only.split(",") if n.strip()]
        parts = {"test_" + "_".join(n[7:16] for n in only): only}
        ann = {n: ann.get(n, []) for n in only}
    errors = validate(ann, parts, tiles_dir)
    if errors:
        print(f"{len(errors)} validation errors, nothing written:")
        for e in errors[:40]:
            print("  " + e)
        sys.exit(1)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.zip"):
        old.unlink()
    total_imgs, total_objs, rows = 0, 0, []
    max_bytes = int(a.max_mb * 1_000_000)
    for part, names in parts.items():
        groups = split(sorted(names), tiles_dir, max_bytes)
        for gi, group in enumerate(groups):
            suffix = "" if len(groups) == 1 else f"_{chr(ord('a') + gi)}"
            zpath = out / f"{part}{suffix}.zip"
            xml = out / "annotations.xml"
            write_cvat({n: ann.get(n, []) for n in group}, xml, name=f"vineyard-ai {part}{suffix}")
            with zipfile.ZipFile(zpath, "w") as z:
                z.write(xml, "annotations.xml", compress_type=zipfile.ZIP_DEFLATED)
                for n in group:
                    z.write(tiles_dir / n, f"images/{n}", compress_type=zipfile.ZIP_STORED)
            xml.unlink()
            nobj = sum(len(ann.get(n, [])) for n in group)
            mb = zpath.stat().st_size
            if mb > max_bytes:
                sys.exit(f"{zpath.name} is {mb / 1e6:.1f} MB > {a.max_mb} MB")
            total_imgs += len(group)
            total_objs += nobj
            rows.append((zpath.name, len(group), nobj, mb))
    for name, ni, no, mb in rows:
        print(f"  {name:48s} {ni:3d} images  {no:6d} objects  {mb / 1e6:5.1f} MB ({mb / 2**20:5.1f} MiB)")
    print(f"{len(rows)} ZIPs, {total_imgs} images, {total_objs} objects -> {out}")
    if total_imgs != sum(len(v) for v in parts.values()):
        sys.exit("image count mismatch")


if __name__ == "__main__":
    main()
