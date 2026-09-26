"""Fine-tune YOLOv8-seg on the vineyard canopy dataset.

Usage:  python train/train_yolo.py                       # full run (50 epochs, early stop)
        python train/train_yolo.py --data dataset_multi/data.yaml --model runs/vineyard/canopy/weights/best.pt \
            --name multi --epochs 25 --patience 6              # one model, 2 classes: vineyard + waste
        python train/train_yolo.py --epochs 1 --fraction 0.1   # 2-minute smoke test
Watch:  python train/watch.py          (in a 2nd terminal)
Output: runs/vineyard/<name>/weights/best.pt, results.csv, results.png, val_batch*_pred.jpg
"""
import argparse
import time
from pathlib import Path

import torch
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "dataset" / "data.yaml"))
    ap.add_argument("--model", default="yolov8n-seg.pt", help="pretrained start point (auto-downloaded)")
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--fraction", type=float, default=1.0, help="use only part of the train set (tests)")
    ap.add_argument("--name", default="canopy")
    ap.add_argument("--patience", type=int, default=10, help="stop if val mAP does not improve for N epochs")
    a = ap.parse_args()

    device = "mps" if torch.backends.mps.is_available() else ("0" if torch.cuda.is_available() else "cpu")
    print(f"device: {device}  model: {a.model}  data: {a.data}")
    t0 = time.time()
    model = YOLO(a.model)
    model.train(
        data=a.data, imgsz=a.imgsz, epochs=a.epochs, batch=a.batch, device=device,
        patience=a.patience,
        max_det=300,           # many canopies per crop
        fraction=a.fraction,
        project=str(ROOT / "runs" / "vineyard"), name=a.name, exist_ok=True,
        degrees=90, flipud=0.5, fliplr=0.5,   # aerial imagery: any orientation is valid
        mosaic=1.0, hsv_h=0.01, hsv_s=0.4, hsv_v=0.3,
        plots=True, workers=4, seed=0,
    )
    run = ROOT / "runs" / "vineyard" / a.name
    print(f"\ntraining time: {(time.time() - t0) / 60:.1f} min on {device}")
    print(f"best weights: {run / 'weights' / 'best.pt'}")
    print(f"curves:       {run / 'results.png'}")


if __name__ == "__main__":
    main()
