"""Live view of a YOLO training run: prints a table that refreshes every 20 s.

Usage: python train/watch.py [--name canopy]      (Ctrl+C to stop watching; training continues)
Columns: box/seg loss (should go DOWN), mask precision/recall/mAP50/mAP50-95 (should go UP).
"""
import argparse
import csv
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="canopy")
    ap.add_argument("--every", type=int, default=20)
    a = ap.parse_args()
    f = ROOT / "runs" / "vineyard" / a.name / "results.csv"
    best = 0.0
    while True:
        print("\033[2J\033[H", end="")          # clear screen
        print(f"watching {f}\n")
        if not f.exists():
            print("no results yet (first epoch still running)...")
        else:
            rows = list(csv.DictReader(open(f)))
            rows = [{k.strip(): v for k, v in r.items()} for r in rows]
            print(f"{'ep':>3} {'seg_loss':>9} {'val_seg':>8} {'P(mask)':>8} {'R(mask)':>8} {'mAP50':>7} {'mAP50-95':>9}")
            for r in rows[-25:]:
                m50 = float(r.get("metrics/mAP50(M)", 0))
                best = max(best, m50)
                print(f"{int(float(r['epoch'])):>3} {float(r['train/seg_loss']):>9.3f} {float(r['val/seg_loss']):>8.3f} "
                      f"{float(r['metrics/precision(M)']):>8.3f} {float(r['metrics/recall(M)']):>8.3f} "
                      f"{m50:>7.3f} {float(r['metrics/mAP50-95(M)']):>9.3f}")
            print(f"\nepochs done: {len(rows)} | best mask mAP50 so far: {best:.3f}")
        time.sleep(a.every)


if __name__ == "__main__":
    main()
