"""Classical-CV baseline: rows -> canopies -> inter-row areas -> attributes, per tile (pixel space).

Idea: find the dominant row orientation (sharpest across-row vegetation profile), rotate the tile so rows
are horizontal, place row axes on a drifting periodic grid (robust to grass strips), fit each axis and run
it out to the imagery edge when the vines reach it. Canopies = crown pixels inside the +-0.3 m band of an
axis (kills inter-row weeds/grass); inter-row areas = strips between the canopy bands of neighbouring rows;
row_structure from the longest vine-free stretch, interrow_cover from the % of vegetated pixels.
The +-0.3 m band, full-tile rows and strips mirror how the organisers' reference is drawn (data/examples).
IDs are tile-local here (T<r>_<c>); global block/row IDs are assigned later over the whole mosaic.

Usage:  python -m pipeline.baseline --tiles data/examples/images --out out/baseline.xml
        python -m pipeline.eval --pred out/baseline.xml
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import binary_fill_holes, grey_opening, uniform_filter1d
from shapely.geometry import LineString, Polygon, box
from shapely.ops import transform as shapely_transform
from shapely.validation import make_valid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as C  # noqa: E402
from pipeline.cvat_io import write_cvat  # noqa: E402
from pipeline.tiles import Tile, open_tile  # noqa: E402

RES = C.PX                   # 0.025 m/px
DS = 4                       # downsample for orientation search -> 0.1 m/px


@dataclass
class P:  # tunable parameters (metres unless noted)
    exg_canopy: float = 0.10     # ExG threshold for canopy pixels
    exg_cover: float = 0.08      # ExG threshold for "vegetation" pixels in inter-row cover (0.05 counts
                                 # sparse weeds + soil tint: reference "mixed" strips would read >75%)
    period_min: float = 2.0      # row spacing band
    period_max: float = 3.4
    min_period_score: float = 0.12
    min_row_veg: float = 3.0     # metres of vegetation needed to accept a row
    min_rows: int = 3            # a vineyard needs >= 3 rows
    gap_disrupted: float = 5.5   # longest gap (m) above this -> disrupted. Rule says >= 5 m; the margin
                                 # absorbs canopy outlines that end slightly short of the real vine
    # canopies
    exg_crown: float = 0.12      # ExG threshold for crown pixels
    open_m: float = 0.175        # morphological opening of crown pixels (removes specks)
    close_m: float = 0.10        # morphological closing of crown pixels
    merge_m: float = 0.30        # bridge along-row gaps up to this length (fragments of one plant)
    dilate_m: float = 0.05       # outline growth (reference is traced loosely)
    min_canopy_m2: float = 0.12
    tree_spill: float = 0.7      # a row is NOT a vine row if crown pixels beside the band >= this share ...
    tree_med: float = 0.8        # ... and its median canopy area >= this (m2): orchards, tree lines, scrub
    tree_spill_strong: float = 0.85  # inside a vineyard tile only clear tree rows are dropped
    tree_med_strong: float = 1.5
    side_in: float = 0.6         # side band (m from the axis) used to measure crown spill
    side_out: float = 1.1
    split_len: float = 99.0      # blobs longer than this along the row are split; off by default: the
                                 # reference keeps connected blobs whole (p90 length 3-5 m) ...
    split_piece: float = 1.2     # ... into pieces of about this length
    cover_bare: float = 0.25
    cover_veg: float = 0.75
    max_neighbour: float = 1.6   # rows further apart than this x period are not neighbours
    # row axes
    tophat: float = 0.45         # opening width (x period) removing broad grass strips from the profile
    row_search: float = 0.35     # re-centering window around the predicted grid position
    row_min_peak: float = 0.02   # min top-hat response to trust a re-centered position
    fit_band: float = 0.45       # initial band for the per-row line fit
    max_slope: float = 0.03      # residual slope allowed in the rotated frame
    canopy_half: float = 0.30    # canopy band half-width around the axis (reference convention)
    edge_snap: float = 4.0       # extend a row to the imagery edge if its first/last vine is this close
    # v3: orientation and row support (v1 behaviour: --v1)
    orient_mode: str = "support" # "support": of the strongest periodic directions, keep the one whose rows sit on the
                                 # most vegetation compared with the space between them; "period" (v1): the most
                                 # periodic direction (a ploughed field, a tree line or wheel tracks could win)
    orient_k: int = 6            # candidate directions (local maxima of periodicity, >= 6 deg apart)
    min_contrast: float = 0.05   # on-row minus between-row vegetation share needed to call it a vineyard
    row_support: bool = True     # drop rows / trim row ends that are not on vines
    sup_ratio: float = 1.3       # a row needs on/between vegetation >= this (vine rows: median 7, wrong lines ~1.0)
    sup_diff: float = 0.08       # ... and on - between >= this
    trim_win: float = 0.0        # window (m) to trim row ends over fields / houses; 0 = off (it cost 0.01 on the
                                 # reference, where rows run to the imagery edge; overshoots are fixed in Marcaj)
    trim_diff: float = 0.03      # an end is trimmed only where there is hardly any vegetation on the line (field,
    trim_on: float = 0.05        # road, roof) or dense green everywhere with no more on the line than between
    trim_dense: float = 0.4      # the lines (trees, meadow); grassy vineyards keep their green on the line
    trim_min: float = 6.0        # ... and only if that stretch is at least this long
    trim_run: float = 4.0        # the kept part starts / ends with at least this much continuous vine support
    second_pass: bool = True     # v3: a second vineyard with another row direction on the same tile
    second_min_free: float = 0.15  # ... searched in what the first pass left unexplained (share of the tile)
    second_min_angle: float = 15.0 # ... only if its direction differs by at least this (deg)


# ---------------------------------------------------------------- helpers
def exg(img: np.ndarray) -> np.ndarray:
    f = img.astype(np.float32)
    s = f.sum(2) + 1e-6
    return (2 * f[..., 1] - f[..., 0] - f[..., 2]) / s


def valid_mask(img: np.ndarray) -> np.ndarray:
    """Imagery vs black no-data. Only large dark regions count as no-data (vine shadows are dark too)."""
    nodata = (img.max(2) < 12).astype(np.uint8)
    nodata = cv2.morphologyEx(nodata, cv2.MORPH_OPEN, np.ones((25, 25), np.uint8))
    return nodata == 0


def rot_matrix(shape, angle_deg):
    """Rotation about the centre with an expanded canvas; returns (M, out_size)."""
    h, w = shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(np.ceil(h * sin + w * cos)), int(np.ceil(h * cos + w * sin))
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    return M, (nw, nh)


def apply(M, pts):
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    return pts @ M[:, :2].T + M[:, 2]


def periodicity(profile: np.ndarray, px: float, pmin: float, pmax: float):
    """Share of spectral power in the row-spacing band, and the dominant period (m)."""
    p = profile - uniform_filter1d(profile, size=max(3, int(8 / px)))
    spec = np.abs(np.fft.rfft(p)) ** 2
    freqs = np.fft.rfftfreq(len(p), d=px)          # cycles per metre
    band = (freqs >= 1 / pmax) & (freqs <= 1 / pmin)
    if not band.any() or spec[1:].sum() == 0:
        return 0.0, 0.0
    k = np.argmax(np.where(band, spec, 0))
    return float(spec[band].sum() / spec[1:].sum()), float(1 / freqs[k])


def find_orientation(veg: np.ndarray, valid: np.ndarray, p: P, avoid: float | None = None):
    small = cv2.resize(veg.astype(np.float32), None, fx=1 / DS, fy=1 / DS, interpolation=cv2.INTER_AREA)
    vsmall = cv2.resize(valid.astype(np.float32), None, fx=1 / DS, fy=1 / DS, interpolation=cv2.INTER_AREA)
    px = RES * DS

    def score(a):
        M, size = rot_matrix(small.shape, a)
        r = cv2.warpAffine(small, M, size)
        v = cv2.warpAffine(vsmall, M, size)
        prof = r.sum(1) / np.maximum(v.sum(1), 1)
        prof = prof[v.sum(1) > 20]
        if len(prof) < 50:
            return 0.0, 0.0
        return periodicity(prof, px, p.period_min, p.period_max)

    coarse = [(score(a)[0], a) for a in np.arange(0, 180, 2.0)]
    if avoid is not None:                           # second pass: another direction than the first vineyard
        far = lambda a: min(abs(a - avoid) % 180, 180 - abs(a - avoid) % 180) >= p.second_min_angle
        coarse = [(sc if far(a) else 0.0, a) for sc, a in coarse]
    s, best = max(coarse)
    if p.orient_mode == "period":                   # v1
        a, period = refine_orientation(veg, valid, best, p)
        return a, s, period
    # v3: local maxima of periodicity are candidates; keep the one with the highest on-row vs between-row contrast
    n = len(coarse)
    peaks = sorted((coarse[i] for i in range(n) if coarse[i][0] >= coarse[i - 1][0] and coarse[i][0] >= coarse[(i + 1) % n][0]), reverse=True)
    cands = []
    for sc, a0 in peaks:
        if sc < 0.5 * p.min_period_score:
            break
        if all(min(abs(a0 - c), 180 - abs(a0 - c)) >= 6 for _, c in cands):
            cands.append((sc, a0))
        if len(cands) >= p.orient_k:
            break
    best_c = None
    for sc, a0 in cands or [(s, best)]:
        a, period = refine_orientation(veg, valid, a0, p)
        c = row_contrast(small, vsmall, a, period, px)
        if best_c is None or c > best_c[0]:
            best_c = (c, a, period, sc)
    c, a, period, sc = best_c
    return a, (sc if c >= p.min_contrast else 0.0), period


def row_contrast(small: np.ndarray, vsmall: np.ndarray, a: float, period: float, px: float) -> float:
    """Best (over grid phase) difference between the vegetation share on the row lines and half a period away.
    Vines: 0.2-0.5; lines drawn across the rows, over scrub or a ploughed field: ~0."""
    M, size = rot_matrix(small.shape, a)
    r, v = cv2.warpAffine(small, M, size), cv2.warpAffine(vsmall, M, size)
    vs = v.sum(1)
    keep = vs > 0.3 * vs.max()
    prof = (r.sum(1) / np.maximum(vs, 1))
    per = period / px
    if keep.sum() < 3 * per:
        return 0.0
    idx = np.arange(len(prof))
    best = 0.0
    for ph in np.arange(0, per, max(per / 12, 1.0)):
        on = np.round(np.arange(ph, len(prof), per)).astype(int)
        mid = np.round(np.arange(ph + per / 2, len(prof), per)).astype(int)
        on, mid = on[(on < len(prof))], mid[(mid < len(prof))]
        on, mid = on[keep[on]], mid[keep[mid]]
        if len(on) < 3 or len(mid) < 3:
            continue
        best = max(best, float(prof[on].mean() - prof[mid].mean()))
    return best


def refine_orientation(veg: np.ndarray, valid: np.ndarray, a0: float, p: P, ds: int = 2):
    """Fine angle = the one giving the sharpest across-row profile (max std of the detrended profile);
    period from a zero-padded spectrum (plain FFT bins are ~0.1 m wide at 2.7 m)."""
    px = RES * ds
    small = cv2.resize(veg.astype(np.float32), None, fx=1 / ds, fy=1 / ds, interpolation=cv2.INTER_AREA)
    vsmall = cv2.resize(valid.astype(np.float32), None, fx=1 / ds, fy=1 / ds, interpolation=cv2.INTER_AREA)

    def profile(a):
        M, size = rot_matrix(small.shape, a)
        r, v = cv2.warpAffine(small, M, size), cv2.warpAffine(vsmall, M, size)
        vs = v.sum(1)
        prof = (r.sum(1) / np.maximum(vs, 1))[vs > 0.3 * vs.max()]
        return prof - uniform_filter1d(prof, size=int(8 / px)) if len(prof) > 50 else np.zeros(1)

    a = max(np.arange(a0 - 3, a0 + 3.01, 0.1), key=lambda x: profile(x).std())
    pr = profile(a)
    n = len(pr) * 8
    spec = np.abs(np.fft.rfft(pr * np.hanning(len(pr)), n=n)) ** 2
    fr = np.fft.rfftfreq(n, d=px)
    band = (fr >= 1 / p.period_max) & (fr <= 1 / p.period_min)
    k = int(np.argmax(np.where(band, spec, 0)))
    return float(a), float(1 / fr[k]) if fr[k] > 0 else 2.7


@dataclass
class Row:
    y: float                      # axis position in rotated frame (full-res px)
    x0: float
    x1: float
    slope: float = 0.0            # small residual slope in rotated frame
    vines: list = field(default_factory=list)  # (xa, xb) along-row extents of detected canopies
    spill: float = 0.0            # crown pixels beside the canopy band relative to inside it (trees spill)

    xc: float = 0.0               # x about which the slope is expressed
    open0: bool = False           # row runs out of the imagery (not a vine) at x0 / x1
    open1: bool = False

    def yat(self, x):
        return self.y + self.slope * (x - self.xc)

    def longest_gap(self) -> float:
        """Longest vine-free stretch along the row in this tile (px), row ends included — the reference
        marks a row disrupted also when the missing vines are at its start/end."""
        end, gap = self.x0, 0.0
        for a, b in sorted(self.vines):
            gap = max(gap, a - end)
            end = max(end, b)
        return max(gap, self.x1 - end)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index runs where mask is True."""
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def grid_positions(th: np.ndarray, ok: np.ndarray, period_px: float, p: P) -> list[float]:
    """Row positions along the across-row profile `th` (top-hatted vegetation profile).

    Rows are (nearly) equally spaced, so instead of free peak picking (which locks onto grass strips) we
    anchor on the best-supported grid phase and walk outwards, re-centering each row within a small window
    and letting the spacing drift slowly.
    """
    n = len(th)
    def gridpts(ph):  # float arange may overshoot `n` by rounding
        g = np.arange(ph, n, period_px).astype(int)
        return g[g < n]

    phases = np.arange(int(period_px))
    ph = phases[int(np.argmax([th[gridpts(ph)].sum() for ph in phases]))]
    grid = gridpts(ph)
    start = float(grid[np.argmax(th[grid])])
    win = int(p.row_search / RES)

    def walk(y, step):
        out, per = [], period_px
        while True:
            y_pred = y + step * per
            if not (0 <= y_pred < n):
                return out
            lo, hi = int(max(0, y_pred - win)), int(min(n, y_pred + win + 1))
            seg = th[lo:hi]
            real = seg.size > 0 and seg.max() > p.row_min_peak
            if real:
                y_new = float(lo + np.argmax(seg))
            else:                       # irregular spacing (lane, missing row): look further ahead
                a, b = sorted((y + step * 0.6 * per, y + step * 1.6 * per))
                lo, hi = int(max(0, a)), int(min(n, b + 1))
                seg = th[lo:hi]
                real = seg.size > 0 and seg.max() > p.row_min_peak
                y_new = float(lo + np.argmax(seg)) if real else y_pred
            if real and abs(y_new - y) <= p.period_max / RES:
                per = float(np.clip(0.8 * per + 0.2 * abs(y_new - y), p.period_min / RES, p.period_max / RES))
            if real:
                out.append(y_new)
            y = y_new

    ys = walk(start, -1)[::-1] + [start] + walk(start, +1)
    return [y for y in ys if ok[int(y)]]


def band_frac(rv: np.ndarray, rvalid: np.ndarray, row: "Row", xs: np.ndarray, dy0: float, hb: int, H: int):
    """Per x: share of vegetation pixels in the +-hb band around the row axis shifted by dy0 (px), and where the band
    lies on imagery."""
    yl = np.round(row.yat(xs) + dy0).astype(int)
    inside = (yl - hb >= 0) & (yl + hb < H)
    tot = np.zeros(len(xs), np.float32)
    val = np.zeros(len(xs), np.float32)
    for dy in range(-hb, hb + 1, max(1, hb // 3)):
        yq = np.clip(yl + dy, 0, H - 1)
        vv = inside & (rvalid[yq, xs] > 0)
        tot += (rv[yq, xs] > 0) & vv
        val += vv
    ok = val > 0
    return np.where(ok, tot / np.maximum(val, 1), 0.0), ok


def detect_rows(rv: np.ndarray, rvalid: np.ndarray, period: float, p: P) -> list[Row]:
    H, W = rv.shape
    cnt = rvalid.sum(1)
    prof = rv.sum(1).astype(np.float32) / np.maximum(cnt, 1)
    prof = uniform_filter1d(prof, size=int(0.2 / RES))
    th = prof - grey_opening(prof, size=int(p.tophat * period / RES))   # keep narrow peaks only
    ys = grid_positions(th, cnt > 0.1 / RES, period / RES, p)

    band = int(p.fit_band / RES)
    rows: list[Row] = []
    for y0 in ys:
        yi = int(round(y0))
        lo = max(0, yi - band)
        strip = rv[lo: yi + band + 1]
        yy, xx = np.nonzero(strip)
        if len(xx) * RES * RES < p.min_row_veg * 0.1:
            continue
        yy = yy + lo
        xc, slope, yfit = float(np.median(xx)), 0.0, float(y0)
        for tol in (p.fit_band, 0.25, 0.15):              # iteratively re-weighted line fit
            res = yy - (yfit + slope * (xx - xc))
            keep = np.abs(res) <= tol / RES
            if keep.sum() < 200:
                break
            A = np.column_stack([xx[keep] - xc, np.ones(keep.sum())])
            sol, *_ = np.linalg.lstsq(A, yy[keep], rcond=None)
            if abs(sol[0]) > p.max_slope:
                break
            slope, yfit = float(sol[0]), float(sol[1])
        row = Row(yfit, 0.0, float(W - 1), slope, xc=xc)

        # occupancy along the fitted axis (canopy-width band), ignoring specks
        xs = np.arange(W)
        yline = np.round(row.yat(xs)).astype(int)
        inside = (yline >= 0) & (yline < H)
        occ = np.zeros(W, bool)
        vline = np.zeros(W, bool)
        hb = int(p.canopy_half / RES)
        for dy in range(-hb, hb + 1):
            yq = np.clip(yline + dy, 0, H - 1)
            occ |= inside & (rv[yq, xs] > 0)
        vline[inside] = rvalid[yline[inside], xs[inside]] > 0
        occ &= vline
        occ = uniform_filter1d(occ.astype(np.float32), size=int(0.1 / RES)) > 0.5
        chord = vline.sum() * RES                     # imagery length along this axis
        if not occ.any() or occ.sum() * RES < min(p.min_row_veg, 0.4 * chord):
            continue
        on = np.flatnonzero(occ)
        x0, x1 = int(on[0]), int(on[-1])
        if p.row_support:                            # v3: is this line on vines (vs half a period to either side)?
            frac = lambda dyc: band_frac(rv, rvalid, row, xs, dyc, hb, H)
            f_on, ok_on = frac(0.0)
            f_a, ok_a = frac(-period / 2 / RES)
            f_b, ok_b = frac(+period / 2 / RES)
            both = ok_a.astype(np.float32) + ok_b.astype(np.float32)
            f_mid = np.where(both > 0, (f_a * ok_a + f_b * ok_b) / np.maximum(both, 1), 0.0)
            span = np.zeros(W, bool)
            span[x0:x1 + 1] = True
            use = ok_on & (both > 0) & span
            if use.sum() * RES >= min(p.min_row_veg, 0.4 * chord):   # same length rule as above (corner rows)
                won, wmid = float(f_on[use].mean()), float(f_mid[use].mean())
                if won / max(wmid, 0.02) < p.sup_ratio or won - wmid < p.sup_diff:
                    continue                          # not a vine row: across the real rows, over scrub / a field
            if p.trim_win > 0:                        # trim ends that clearly run over fields / yards / grass
                w = int(p.trim_win / RES) | 1
                known = (ok_on & (both > 0)).astype(np.float32)
                cnt = np.maximum(uniform_filter1d(known, size=w), 1e-6)
                lon = uniform_filter1d(np.where(known > 0, f_on, 0).astype(np.float32), size=w) / cnt
                lmid = uniform_filter1d(np.where(known > 0, f_mid, 0).astype(np.float32), size=w) / cnt
                lk = uniform_filter1d(known, size=w)
                bad = (lk > 0.5) & ((lon < p.trim_on) | ((lon - lmid < p.trim_diff) & (lmid > p.trim_dense)))
                good = span & ~bad
                if (x1 - x0) * RES < 2 * p.trim_run:      # short rows (tile corners) are kept as they are
                    good = span.copy()
                runs = [(a, b) for a, b in _runs(good) if (b - a) * RES >= min(p.trim_run, (x1 - x0 + 1) * RES)]
                gi = np.array([x for a, b in (runs[:1] + runs[-1:]) for x in (a, b - 1)]) if runs else np.array([], int)
                if gi.size == 0:
                    continue
                tmin = p.trim_min / RES                # only long stretches: short ones are shadows / missing vines
                if gi[0] - x0 >= tmin:
                    x0 = int(gi[0])
                if x1 - gi[-1] >= tmin:
                    x1 = int(gi[-1])
        # vines usually reach the tile edge: snap row ends to the imagery edge if close enough
        vr = [(a, b) for a, b in _runs(vline) if a <= x0 < b] or [(0, W)]
        va, vb = vr[0]
        if (x0 - va) * RES <= p.edge_snap:
            x0, row.open0 = va, True
        if (vb - 1 - x1) * RES <= p.edge_snap:
            x1, row.open1 = vb - 1, True
        row.x0, row.x1 = float(x0), float(x1)
        rows.append(row)
    rows.sort(key=lambda r: r.y)
    return rows


def _split_long(comp: np.ndarray, p: P) -> list[np.ndarray]:
    """Split a blob longer than split_len (along x) at column-count minima into ~split_piece parts."""
    w = comp.shape[1]
    if w * RES <= p.split_len:
        return [comp]
    colsum = uniform_filter1d(comp.sum(0).astype(np.float32), size=int(0.2 / RES))
    ncut = max(1, int(round(w * RES / p.split_piece)) - 1)
    cuts = []
    for c in range(1, ncut + 1):
        t = int(c * w / (ncut + 1))
        lo, hi = max(1, t - int(0.4 / RES)), min(w - 1, t + int(0.4 / RES))
        cuts.append(lo + int(np.argmin(colsum[lo:hi])) if hi > lo else t)
    pieces, prev = [], 0
    for c in cuts + [w]:
        part = np.zeros_like(comp)
        part[:, prev:c] = comp[:, prev:c]
        pieces.append(part)
        prev = c
    return pieces


def row_canopies(rc: np.ndarray, row: Row, Minv, tile_box, p: P) -> list[Polygon]:
    """Canopy polygons (tile px) for one row, from the rotated crown mask `rc`."""
    H, W = rc.shape
    hb = p.canopy_half / RES
    x0, x1 = int(row.x0), int(row.x1) + 1
    ya, yb = row.yat(x0), row.yat(x1)
    top = max(0, int(np.floor(min(ya, yb) - hb)) - 2)
    bot = min(H, int(np.ceil(max(ya, yb) + hb)) + 3)
    crop = rc[top:bot, x0:x1]
    yy = np.arange(top, bot)[:, None]
    xx = np.arange(x0, x1)[None, :]
    band = (np.abs(yy - row.yat(xx)) <= hb).astype(np.uint8)
    cm = crop & band
    ext = int(np.ceil(p.side_out / RES)) + 2
    top2, bot2 = max(0, top - ext), min(H, bot + ext)
    crop2 = rc[top2:bot2, x0:x1]
    yy2 = np.arange(top2, bot2)[:, None]
    d2 = np.abs(yy2 - row.yat(xx))
    side = (d2 >= p.side_in / RES) & (d2 <= p.side_out / RES)
    inb = d2 <= hb
    row.spill = float(crop2[side].mean() / max(crop2[inb].mean(), 1e-3)) if side.any() else 0.0
    if not cm.any():
        return []
    if p.open_m > 0:                                # drop weed specks / thin grass
        k = int(round(p.open_m / RES)) | 1
        cm = cv2.morphologyEx(cm, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    k = int(round(p.close_m / RES)) | 1
    cm = cv2.morphologyEx(cm, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    if p.merge_m > 0:                               # bridge small along-row gaps between fragments
        cm = cv2.morphologyEx(cm, cv2.MORPH_CLOSE, np.ones((1, int(p.merge_m / RES) | 1), np.uint8))
    if p.dilate_m > 0:                              # reference outlines are drawn loosely
        d = int(round(p.dilate_m / RES)) * 2 + 1
        cm = cv2.dilate(cm, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d)))
    cm &= band
    out = []
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cm, connectivity=8)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area * RES * RES < p.min_canopy_m2:
            continue
        comp = binary_fill_holes(lab[y:y + h, x:x + w] == i)
        for part in _split_long(comp, p):
            if part.sum() * RES * RES < p.min_canopy_m2:
                continue
            cs, _ = cv2.findContours(part.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            c = cv2.approxPolyDP(max(cs, key=cv2.contourArea), 1.0, True).reshape(-1, 2).astype(np.float64)
            if len(c) < 3:
                continue
            poly = Polygon(apply(Minv, c + [x + x0, y + top]))
            poly = (poly if poly.is_valid else make_valid(poly)).intersection(tile_box)
            if poly.geom_type != "Polygon":
                poly = max(getattr(poly, "geoms", []), key=lambda g: g.area, default=Polygon())
            if poly.is_empty or poly.geom_type != "Polygon" or poly.area * RES * RES < p.min_canopy_m2:
                continue
            out.append(poly)
            cols = np.flatnonzero(part.any(0))
            row.vines.append((x0 + x + cols[0], x0 + x + cols[-1] + 1))
    return out


def load_forbidden(path: Path = C.ROUTE_IN / "forbidden.geojson"):
    """Organiser forbidden zones (buildings +1 m, village core, compounds) as one UTM geometry, or None."""
    if not path.exists():
        return None
    import json
    from shapely.geometry import shape
    from shapely.ops import unary_union
    return unary_union([shape(f["geometry"]) for f in json.loads(path.read_text())["features"]])


def drop_forbidden(objs: list[dict], tile: Tile, forbidden, p: P) -> list[dict]:
    """Remove objects lying mostly inside forbidden zones: nothing there is a vine (checked visually on the
    4 affected tiles: house yards, compounds). If fewer than min_rows rows survive, the tile has no vineyard."""
    if forbidden is None or objs == [] or not forbidden.intersects(box(*tile.bounds)):
        return objs
    zone = forbidden.intersection(box(*tile.bounds))
    zpx = shapely_transform(lambda x, y, z=None: tuple(tile.utm_to_px(np.column_stack([x, y])).T), zone)
    keep = []
    for o in objs:
        g = LineString(o["points"]) if o["type"] == "polyline" else Polygon(o["points"])
        g = g if g.is_valid else make_valid(g)
        inside = g.intersection(zpx)
        share = inside.length / g.length if o["type"] == "polyline" else inside.area / max(g.area, 1e-9)
        if share <= 0.5:
            keep.append(o)
    if sum(o["label"] == "row" for o in keep) < p.min_rows:
        return []
    return keep


def vine_rows(rows: list[Row], row_polys: list[list[Polygon]], period: float, p: P, debug=None) -> list[bool]:
    """Keep rows that look like grapevines, in runs of >= min_rows neighbouring rows.

    Vines here are narrow (~0.6 m) with small canopies (median ~0.5 m2). Orchard / tree-line / scrub crowns are
    2-4 m wide, so their vegetation spills far beyond the +-0.3 m canopy band, and the clipped blobs are big.
    Measured on the reference and on visually checked tiles; rules only - no manual labels of Siret3.
    """
    ok = []
    for row, polys in zip(rows, row_polys):
        L = max((row.x1 - row.x0) * RES, 1e-6)
        areas = np.array([g.area for g in polys]) * RES * RES if polys else np.zeros(1)
        med, dens = float(np.median(areas)), len(polys) / L
        # tree/orchard/meadow row: vegetation spills well beyond the canopy band AND the "canopies" are big.
        # (continuous mature vine strips have big blobs but no spill; vines on grass spill but stay small)
        good = not (row.spill >= p.tree_spill and med >= p.tree_med)
        ok.append(good)
        if debug is not None:
            debug.append({"y": round(row.y), "len_m": round(L, 1), "n": len(polys), "med": round(med, 3),
                          "p90": round(float(np.percentile(areas, 90)), 3), "dens": round(dens, 3), "spill": round(row.spill, 3), "vine": good})
    # runs of neighbouring vine rows; isolated / short runs are not a vineyard
    keep = [False] * len(rows)
    i = 0
    while i < len(rows):
        if not ok[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(rows) and ok[j + 1] and (rows[j + 1].y - rows[j].y) * RES <= p.max_neighbour * period:
            j += 1
        if j - i + 1 >= p.min_rows:
            keep[i:j + 1] = [True] * (j - i + 1)
        i = j + 1
    if not any(keep):
        return keep                                   # no vineyard on this tile
    # Vineyard present: be conservative. Drawing a missed row in Marcaj is slow, deleting a false one is
    # fast, so within a vineyard tile only clear tree rows go.
    return [not (r.spill >= p.tree_spill_strong and float(np.median(np.array([g.area for g in ps]) * RES * RES
                                                                          if ps else [0])) >= p.tree_med_strong)
            for r, ps in zip(rows, row_polys)]


# ---------------------------------------------------------------- main per-tile routine
def process_tile(tile: Tile, p: P = P(), debug: list | None = None) -> list[dict]:
    img = tile.read()
    valid = valid_mask(img)
    e = exg(img)
    veg = (e > p.exg_canopy) & valid
    veg = cv2.morphologyEx(veg.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    veg = cv2.morphologyEx(veg, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    vid = f"T{tile.row:03d}_{tile.col:03d}"
    objs, angle, period = _detect(tile, img, e, veg, valid, p, debug, vid)
    if objs and p.second_pass and p.orient_mode != "period":
        # v3: what the first vineyard does not explain (its rows +- half a spacing, grown by 1 m) may hold a second
        # vineyard with another row direction (block corners, neighbouring plots)
        foot = np.zeros(valid.shape, np.uint8)
        wpx = int(round((period + 2.0) / RES))
        for o in objs:
            if o["label"] == "row":
                cv2.polylines(foot, [np.round(np.asarray(o["points"])).astype(np.int32)], False, 1, thickness=wpx)
        free = valid & (foot == 0)
        if free.mean() >= p.second_min_free:
            objs2, _, _ = _detect(tile, img, e, (veg.astype(bool) & free).astype(np.uint8), free, p, None, vid + "b", avoid=angle)
            objs += objs2
    return objs


def _detect(tile: Tile, img, e, veg, valid, p: P, debug, vid: str, avoid: float | None = None):
    """One row direction on (part of) a tile -> (objects, angle, period)."""
    angle, pscore, period = find_orientation(veg, valid, p, avoid)
    if pscore < p.min_period_score:
        return [], angle, period

    M, size = rot_matrix(veg.shape, angle)
    Minv = cv2.invertAffineTransform(M)
    rv = cv2.warpAffine(veg, M, size, flags=cv2.INTER_NEAREST)
    rvalid = cv2.warpAffine(valid.astype(np.uint8), M, size, flags=cv2.INTER_NEAREST)

    # ---- row axes: periodic grid over the across-row profile, then a robust line fit per row
    rows = detect_rows(rv, rvalid, period, p)
    if len(rows) < p.min_rows:
        return [], angle, period

    # ---- canopies: vegetation inside the canopy band of each row, one polygon per plant
    objs: list[dict] = []
    rc = cv2.warpAffine((e > p.exg_crown).astype(np.uint8) & valid.astype(np.uint8), M, size,
                        flags=cv2.INTER_NEAREST)
    tile_box = box(0, 0, tile.width, tile.height)
    row_polys = [row_canopies(rc, row, Minv, tile_box, p) for row in rows]
    keep = vine_rows(rows, row_polys, period, p, debug)
    rows = [r for r, k in zip(rows, keep) if k]
    if len(rows) < p.min_rows:
        return [], angle, period
    for polys, k in zip(row_polys, keep):
        if not k:
            continue
        for poly in polys:
            objs.append({"label": "vineyard", "type": "polygon",
                         "points": list(poly.exterior.coords)[:-1], "attrs": {"vineyard_id": vid}})

    # ---- row polylines + row_structure
    for k, row in enumerate(rows):
        line = LineString(apply(Minv, [[row.x0, row.yat(row.x0)], [row.x1, row.yat(row.x1)]]))
        line = line.intersection(box(0, 0, tile.width, tile.height))
        if line.is_empty or line.geom_type != "LineString" or line.length * RES < 1.0:
            continue
        pts = np.asarray(line.coords)
        structure = "disrupted" if row.longest_gap() * RES > p.gap_disrupted else "regular"
        objs.append({"label": "row", "type": "polyline", "points": pts.tolist(),
                     "attrs": {"vineyard_id": vid, "row_id": f"{vid}-R{k + 1:02d}",
                               "row_structure": structure}})

    # ---- inter-row areas: strip from canopy band edge to canopy band edge of neighbouring rows,
    #      ending at the shorter row (or running out to the tile edge when both rows do)
    for a, b in zip(rows, rows[1:]):
        if (b.yat(b.xc) - a.yat(a.xc)) * RES > p.max_neighbour * period:
            continue                                  # not neighbours (missing row / block edge)
        x0 = 0.0 if (a.open0 and b.open0) else max(a.x0, b.x0)          # rotated canvas holds the tile
        x1 = size[0] - 1.0 if (a.open1 and b.open1) else min(a.x1, b.x1)
        if x1 - x0 < 1.0 / RES:
            continue
        ha, hb = p.canopy_half / RES, p.canopy_half / RES
        quad = [[x0, a.yat(x0) + ha], [x1, a.yat(x1) + ha], [x1, b.yat(x1) - hb], [x0, b.yat(x0) - hb]]
        poly = make_valid(Polygon(apply(Minv, quad))).intersection(tile_box)
        if poly.geom_type != "Polygon":
            poly = max(getattr(poly, "geoms", []), key=lambda g: g.area, default=Polygon())
        if poly.is_empty or poly.geom_type != "Polygon" or poly.area * RES * RES < 0.5:
            continue
        # cover: share of vegetated pixels inside the polygon
        mask = np.zeros((tile.height, tile.width), np.uint8)
        cv2.fillPoly(mask, [np.round(np.asarray(poly.exterior.coords)).astype(np.int32)], 1)
        m = mask.astype(bool) & valid
        frac = float((e[m] > p.exg_cover).mean()) if m.any() else 0.0
        cover = "bare_soil" if frac < p.cover_bare else ("vegetation" if frac > p.cover_veg else "mixed")
        objs.append({"label": "interrow_area", "type": "polygon",
                     "points": list(poly.exterior.coords)[:-1],
                     "attrs": {"vineyard_id": vid, "interrow_cover": cover}})
    return objs, angle, period


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiles", default=str(C.TILES))
    ap.add_argument("--out", default=str(C.OUT / "baseline.xml"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-forbidden", action="store_true", help="keep objects inside organiser forbidden zones")
    ap.add_argument("--v1", action="store_true", help="v1 behaviour (as uploaded to Marcaj): most periodic direction, no row support")
    a = ap.parse_args()
    params = P(orient_mode="period", row_support=False) if a.v1 else P()
    paths = sorted(Path(a.tiles).glob("siret3_r*_c*.tif"))
    if a.limit:
        paths = paths[: a.limit]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    t0, res = time.time(), {}
    forbidden = None if a.no_forbidden else load_forbidden()
    for pth in paths:
        t = time.time()
        try:
            tile = open_tile(pth)
            res[pth.name] = drop_forbidden(process_tile(tile, params), tile, forbidden, params)
        except Exception as ex:  # one bad tile must not kill a 311-tile run; export it empty
            print(f"{pth.name}: FAILED {type(ex).__name__}: {ex}", file=sys.stderr)
            res[pth.name] = []
        n = {k: sum(o["label"] == k for o in res[pth.name]) for k in C.LABELS}
        print(f"{pth.name}: {n}  {time.time() - t:.1f}s")
    write_cvat(res, a.out)
    print(f"{len(paths)} tiles in {time.time() - t0:.1f}s -> {a.out}")


if __name__ == "__main__":
    main()
