"""Local re-implementation of the official scoring, on tiles that have a reference (data/examples).

Implements (per the challenge description):
  canopy   25%: 0.6 * class IoU + 0.4 * F1 of canopies matched 1-to-1 at IoU >= 0.5
  waste    10%: box F1, 1-to-1 at IoU >= 0.3
  axes      8%: F1; pred & ref axis match when EACH lies >= 80% within 0.4 m of the other
  attrs     5%: mean(accuracy, macro-F1) over every REFERENCE object (missing = wrong)
  grouping  2%: vineyard_id consistency (pairwise, ID values irrelevant)
  counts   10%: blocks, rows, canopy area, inter-row area (tol 15%), total row length (tol 10%)
Route (25%) and engineering (15%) are scored elsewhere; `partial` is normalised to the 60% covered here.

Usage:  python -m pipeline.eval --pred out/pred.xml [--ref data/examples/annotations.xml]
"""
from __future__ import annotations

import argparse
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union
from shapely.validation import make_valid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import read_cvat  # noqa: E402

M = C.PX  # metres per pixel


# ---------- geometry helpers (all in metres) ----------
def _poly(o) -> Polygon:
    p = Polygon(np.asarray(o["points"]) * M)
    return p if p.is_valid else make_valid(p)


def _line(o) -> LineString:
    return LineString(np.asarray(o["points"]) * M)


def _box(o):
    return box(o["xtl"] * M, o["ytl"] * M, o["xbr"] * M, o["ybr"] * M)


def _iou(a, b) -> float:
    if not a.intersects(b):
        return 0.0
    inter = a.intersection(b).area
    return inter / (a.area + b.area - inter + 1e-12)


def match_iou(pred, ref, thr: float) -> tuple[int, list[tuple[int, int]]]:
    """1-to-1 matching maximising IoU; returns (#TP, pairs)."""
    if not pred or not ref:
        return 0, []
    iou = np.zeros((len(pred), len(ref)))
    for i, p in enumerate(pred):
        pb = p.bounds
        for j, r in enumerate(ref):
            rb = r.bounds
            if pb[0] > rb[2] or rb[0] > pb[2] or pb[1] > rb[3] or rb[1] > pb[3]:
                continue
            iou[i, j] = _iou(p, r)
    rows, cols = linear_sum_assignment(-iou)
    pairs = [(i, j) for i, j in zip(rows, cols) if iou[i, j] >= thr]
    return len(pairs), pairs


def f1(tp: int, n_pred: int, n_ref: int) -> float:
    if n_pred == 0 and n_ref == 0:
        return 1.0
    p = tp / n_pred if n_pred else 0.0
    r = tp / n_ref if n_ref else 0.0
    return 2 * p * r / (p + r) if p + r else 0.0


def _within(a: LineString, b: LineString, tol=0.4, step=0.05) -> float:
    """Share of a's length lying within tol metres of b."""
    n = max(2, int(a.length / step) + 1)
    pts = [a.interpolate(t, normalized=True) for t in np.linspace(0, 1, n)]
    return float(np.mean([b.distance(p) <= tol for p in pts]))


def match_axes(pred, ref) -> list[tuple[int, int]]:
    if not pred or not ref:
        return []
    ok = np.zeros((len(pred), len(ref)))
    for i, p in enumerate(pred):
        for j, r in enumerate(ref):
            if p.distance(r) > 0.4:
                continue
            a, b = _within(p, r), _within(r, p)
            if a >= 0.8 and b >= 0.8:
                ok[i, j] = a + b
    rows, cols = linear_sum_assignment(-ok)
    return [(i, j) for i, j in zip(rows, cols) if ok[i, j] > 0]


def acc_macro_f1(y_true: list[str], y_pred: list[str | None]) -> float:
    if not y_true:
        return 1.0
    acc = np.mean([t == p for t, p in zip(y_true, y_pred)])
    f1s = []
    for c in sorted(set(y_true)):
        tp = sum(t == c and p == c for t, p in zip(y_true, y_pred))
        fp = sum(t != c and p == c for t, p in zip(y_true, y_pred))
        fn = sum(t == c and p != c for t, p in zip(y_true, y_pred))
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return float((acc + np.mean(f1s)) / 2)


def count_score(pred: float, ref: float, tol: float) -> float:
    if ref == 0:
        return 1.0 if pred == 0 else 0.0
    return max(0.0, 1 - abs(pred - ref) / ref / tol)


# ---------- scoring ----------
def score(pred_objs: dict, ref_objs: dict, verbose: bool = True) -> dict:
    tot = dict(inter=0.0, union=0.0, c_tp=0, c_np=0, c_nr=0, w_tp=0, w_np=0, w_nr=0,
               a_tp=0, a_np=0, a_nr=0)
    rs_true, rs_pred, ic_true, ic_pred = [], [], [], []
    group_pairs_ok, group_pairs = 0, 0
    agg = {k: {"blocks": set(), "rows": set(), "canopy": 0.0, "interrow": 0.0, "rowlen": 0.0}
           for k in ("pred", "ref")}

    for name, ref in ref_objs.items():
        pred = pred_objs.get(name, [])
        by = lambda objs, lab: [o for o in objs if o["label"] == lab]  # noqa: E731

        # canopy
        pc, rc = [_poly(o) for o in by(pred, "vineyard")], [_poly(o) for o in by(ref, "vineyard")]
        pu, ru = unary_union(pc) if pc else Polygon(), unary_union(rc) if rc else Polygon()
        tot["inter"] += pu.intersection(ru).area
        tot["union"] += pu.union(ru).area
        tp, cpairs = match_iou(pc, rc, 0.5)
        tot["c_tp"] += tp; tot["c_np"] += len(pc); tot["c_nr"] += len(rc)

        # waste
        pw, rw = [_box(o) for o in by(pred, "waste")], [_box(o) for o in by(ref, "waste")]
        tp, _ = match_iou(pw, rw, 0.3)
        tot["w_tp"] += tp; tot["w_np"] += len(pw); tot["w_nr"] += len(rw)

        # axes + row_structure
        pro, rro = by(pred, "row"), by(ref, "row")
        pairs = match_axes([_line(o) for o in pro], [_line(o) for o in rro])
        tot["a_tp"] += len(pairs); tot["a_np"] += len(pro); tot["a_nr"] += len(rro)
        m = {j: i for i, j in pairs}
        for j, r in enumerate(rro):
            rs_true.append(r["attrs"].get("row_structure"))
            rs_pred.append(pro[m[j]]["attrs"].get("row_structure") if j in m else None)

        # inter-row cover (match polygons at IoU >= 0.5)
        pio, rio = by(pred, "interrow_area"), by(ref, "interrow_area")
        _, ipairs = match_iou([_poly(o) for o in pio], [_poly(o) for o in rio], 0.5)
        m = {j: i for i, j in ipairs}
        for j, r in enumerate(rio):
            ic_true.append(r["attrs"].get("interrow_cover"))
            ic_pred.append(pio[m[j]]["attrs"].get("interrow_cover") if j in m else None)

        # grouping: pairs of matched canopies — same ref block <=> same pred block
        pv, rv = by(pred, "vineyard"), by(ref, "vineyard")
        ids = [(pv[i]["attrs"].get("vineyard_id"), rv[j]["attrs"].get("vineyard_id")) for i, j in cpairs]
        sample = ids[:: max(1, len(ids) // 150)]
        for (p1, r1), (p2, r2) in combinations(sample, 2):
            group_pairs += 1
            group_pairs_ok += ((p1 == p2) == (r1 == r2)) and bool(p1)

        # counts / measurements
        for key, objs, polys in (("pred", pred, pc), ("ref", ref, rc)):
            a = agg[key]
            for o in objs:
                if o["attrs"].get("vineyard_id"):
                    a["blocks"].add(o["attrs"]["vineyard_id"])
                if o["label"] == "row":
                    a["rows"].add(o["attrs"].get("row_id"))
                    a["rowlen"] += _line(o).length
                if o["label"] == "interrow_area":
                    a["interrow"] += _poly(o).area
            a["canopy"] += (unary_union(polys).area if polys else 0.0)

    iou = tot["inter"] / tot["union"] if tot["union"] else 1.0
    cf1 = f1(tot["c_tp"], tot["c_np"], tot["c_nr"])
    s = {
        "canopy": 0.6 * iou + 0.4 * cf1,
        "waste": f1(tot["w_tp"], tot["w_np"], tot["w_nr"]),
        "axes": f1(tot["a_tp"], tot["a_np"], tot["a_nr"]),
        "attrs": (acc_macro_f1(rs_true, rs_pred) + acc_macro_f1(ic_true, ic_pred)) / 2,
        "grouping": group_pairs_ok / group_pairs if group_pairs else 0.0,
    }
    P, R = agg["pred"], agg["ref"]
    counts = {
        "blocks": count_score(len(P["blocks"]), len(R["blocks"]), 0.15),
        "rows": count_score(len(P["rows"]), len(R["rows"]), 0.15),
        "canopy_area": count_score(P["canopy"], R["canopy"], 0.15),
        "interrow_area": count_score(P["interrow"], R["interrow"], 0.15),
        "row_length": count_score(P["rowlen"], R["rowlen"], 0.10),
    }
    s["counts"] = float(np.mean(list(counts.values())))
    weights = {"canopy": 25, "waste": 10, "axes": 8, "attrs": 5, "grouping": 2, "counts": 10}
    s["partial"] = sum(s[k] * w for k, w in weights.items()) / sum(weights.values())

    if verbose:
        print(f"canopy   IoU={iou:.3f}  F1={cf1:.3f}  (pred {tot['c_np']} / ref {tot['c_nr']}, TP {tot['c_tp']})"
              f"  -> {s['canopy']:.3f}")
        print(f"waste    F1={s['waste']:.3f}  (pred {tot['w_np']} / ref {tot['w_nr']})")
        print(f"axes     F1={s['axes']:.3f}  (pred {tot['a_np']} / ref {tot['a_nr']}, TP {tot['a_tp']})")
        print(f"attrs    {s['attrs']:.3f}  (row_structure n={len(rs_true)}, interrow_cover n={len(ic_true)})")
        print(f"grouping {s['grouping']:.3f}")
        print("counts   " + "  ".join(f"{k}={v:.2f}" for k, v in counts.items()))
        print(f"         pred: blocks {len(P['blocks'])}, rows {len(P['rows'])}, canopy {P['canopy']:.1f} m2, "
              f"interrow {P['interrow']:.1f} m2, rowlen {P['rowlen']:.1f} m")
        print(f"         ref : blocks {len(R['blocks'])}, rows {len(R['rows'])}, canopy {R['canopy']:.1f} m2, "
              f"interrow {R['interrow']:.1f} m2, rowlen {R['rowlen']:.1f} m")
        print(f"PARTIAL SCORE (60% of total covered here): {s['partial']:.3f}")
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True, help="CVAT xml with predictions")
    ap.add_argument("--ref", default=str(C.EXAMPLES / "annotations.xml"))
    a = ap.parse_args()
    score(read_cvat(a.pred), read_cvat(a.ref))


if __name__ == "__main__":
    main()
