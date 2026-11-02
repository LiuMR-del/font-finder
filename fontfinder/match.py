"""Screenshot-to-font matching.

A query crop is binarised against its border colour, split into text lines and then character
groups, and compared with the same characters rendered in every local font (glyph shape +
proportions), plus a whole-line comparison that tolerates spacing differences. Multi-line crops
are scored line by line, with the words of the text distributed over the lines.
"""
import itertools
import os
import time
import warnings

import cv2
import numpy as np

from .common import DATA

# numpy 2.0 + Apple Accelerate reports bogus FP errors from float32 matmul.
warnings.filterwarnings("ignore", message=".*encountered in matmul")

N = 32
D = N * N
LINE_H = 40
ATLAS_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
CHAR_POS = {c: i for i, c in enumerate(ATLAS_CHARS)}
ATLAS_PATH = os.path.join(DATA, "atlas.npz")
# Score bands, from bench score distributions (2026-10-30): the right font at #1 scores >= 63 in
# 90% of cases (median ~90); when the font is not in the library the best score is < 86 in 90%.
BAND_HIGH, BAND_LOW = 0.88, 0.55


def band(score):
    return "high" if score >= BAND_HIGH else "mid" if score >= BAND_LOW else "low"
CONFUSABLE = {
    "0": "OoD", "O": "0oQD", "o": "0O", "1": "lIi|", "l": "1I|", "I": "1l|", "5": "Ss", "S": "5s",
    "2": "Zz", "Z": "2z", "8": "B", "B": "8", "6": "bG", "9": "gq", "g": "9", "7": "T",
}

# --------------------------------------------------------------------------- features


def _tight(m):
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return None
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _unit(v):
    v = v - v.mean()
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def glyph_feat(m):
    """Shape vector + (aspect, ink, stroke/height) for one character mask."""
    m = _tight(m)
    if m is None:
        return None
    h, w = m.shape
    m8 = m.astype(np.uint8)
    dt = cv2.distanceTransform(np.pad(m8, 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
    stroke = 2.0 * float(dt[m].mean())
    s = (N - 4) / max(h, w)
    nh, nw = max(1, round(h * s)), max(1, round(w * s))
    g = cv2.resize(m.astype(np.float32), (nw, nh), interpolation=cv2.INTER_AREA)
    c = np.zeros((N, N), np.float32)
    y0, x0 = (N - nh) // 2, (N - nw) // 2
    c[y0:y0 + nh, x0:x0 + nw] = g
    c = cv2.GaussianBlur(c, (0, 0), 1.0)
    return _unit(c.ravel()), w / h, float(m.mean()), stroke / h, h


def line_vec(m, width):
    m = _tight(m)
    g = cv2.resize(m.astype(np.float32), (width, LINE_H), interpolation=cv2.INTER_AREA)
    g = cv2.GaussianBlur(g, (0, 0), 1.2)
    return _unit(g.ravel())


# --------------------------------------------------------------------------- query side


def _clean(mask, rel=0.004):
    n, lbl, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if n <= 1:
        return mask
    areas = st[1:, cv2.CC_STAT_AREA]
    keep = np.zeros(n, bool)
    keep[1:] = areas >= max(3, rel * areas.max())
    return keep[lbl]


def _rgb_hex(rgb):
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb)


def extract(img):
    """img: PIL image of the cropped text. Returns candidate masks (silhouette, maybe fill-only,
    colour-cluster, hole-filled), the text colour each implies (None = use colors.fill) and colours."""
    a = np.asarray(img.convert("RGB"))
    h, w = a.shape[:2]
    scale = 1.0
    if max(h, w) > 1400:
        scale = 1400 / max(h, w)
    elif h < 90:
        scale = min(4.0, 120 / h)
    if scale != 1.0:
        a = cv2.resize(a, (max(1, round(w * scale)), max(1, round(h * scale))),
                       interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    lab = cv2.cvtColor(a, cv2.COLOR_RGB2LAB).astype(np.float32)
    b = max(1, min(a.shape[:2]) // 40)
    border = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3),
                             lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3)])
    bg = np.median(border, 0)
    dist = np.linalg.norm(lab - bg, axis=2)
    if dist.max() < 12:
        return None
    d8 = np.clip(dist * 255 / dist.max(), 0, 255).astype(np.uint8)
    thr, _ = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = _clean(d8 > thr)
    if mask.sum() < 20:
        return None

    border_rgb = np.concatenate([a[:b].reshape(-1, 3), a[-b:].reshape(-1, 3)])
    colors = {"bg": _rgb_hex(np.median(border_rgb, 0)), "fill": _rgb_hex(np.median(a[mask], 0)),
              "outline": None, "outline_rel": 0}
    variants = [mask]

    px = lab[mask]
    if len(px) > 200:
        sample = px[np.random.default_rng(0).choice(len(px), min(len(px), 20000), replace=False)]
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, _, cent = cv2.kmeans(sample, 2, None, crit, 3, cv2.KMEANS_PP_CENTERS)
        if np.linalg.norm(cent[0] - cent[1]) > 40:
            lbl = np.argmin(((px[:, None, :] - cent[None]) ** 2).sum(2), 1)
            full = np.full(mask.shape, -1, np.int8)
            full[mask] = lbl
            text_h = _tight(mask).shape[0]
            it = max(1, round(text_h * 0.02))
            near_bg = cv2.dilate((~mask).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=it).astype(bool)
            adj = [float((near_bg & (full == k)).sum()) / max(1, (full == k).sum()) for k in (0, 1)]
            out_k = int(np.argmax(adj))
            fill_k = 1 - out_k
            n_out, n_fill = (full == out_k).sum(), (full == fill_k).sum()
            if adj[out_k] > 0.45 and adj[fill_k] < 0.3 and n_out > 0.25 * mask.sum() and n_fill > 0.2 * mask.sum():
                fill = _clean(full == fill_k, 0.01)
                if fill.sum() > 20:
                    variants.append(fill)
                    edge = mask & ~cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
                    colors["fill"] = _rgb_hex(np.median(a[full == fill_k], 0))
                    colors["outline"] = _rgb_hex(np.median(a[full == out_k], 0))
                    colors["outline_rel"] = round(float(n_out) / max(1, edge.sum()) / text_h, 4)
    fills = [None] * len(variants)

    cm = color_mask(lab)
    if cm is not None:
        m, rgb = cm[0], cv2.cvtColor(np.uint8([[cm[1]]]), cv2.COLOR_LAB2RGB)[0, 0]
        if all(_iou(m, v) < 0.9 for v in variants):
            variants.append(m)
            fills.append(_rgb_hex(rgb))
    for v, f in list(zip(variants, fills)):
        k = max(3, round(_tight(v).shape[0] * 0.04)) | 1
        closed = cv2.morphologyEx(v.astype(np.uint8), cv2.MORPH_CLOSE,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))).astype(bool)
        solid = _fill_holes(closed)
        if solid.sum() > 1.5 * v.sum():  # outline-only lettering (bubble / sticker styles)
            variants.append(solid)
            fills.append(f)
    return {"variants": variants, "fills": fills, "colors": colors, "scale": scale}


def _iou(a, b):
    return (a & b).sum() / max(1, (a | b).sum())


def _fill_holes(m):
    f = np.pad(m, 1).astype(np.uint8)
    cv2.floodFill(f, None, (0, 0), 2)
    return (f != 2)[1:-1, 1:-1]


def color_mask(lab, k=6):
    """Text as the colour cluster sitting in the middle of the crop and avoiding its border.

    Complements the border-distance mask, which breaks down on busy photo/illustration
    backgrounds. Returns (mask, Lab colour) or None when no cluster looks like text."""
    h, w = lab.shape[:2]
    px = cv2.GaussianBlur(lab, (3, 3), 0).reshape(-1, 3)
    sample = px[np.random.default_rng(0).choice(len(px), min(len(px), 40000), replace=False)]
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, _, cent = cv2.kmeans(sample, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    lbl = np.argmin(((px[:, None, :] - cent[None]) ** 2).sum(2), 1).reshape(h, w)
    b = max(2, min(h, w) // 20)
    border = np.zeros((h, w), bool)
    border[:b] = border[-b:] = True
    border[:, :b] = border[:, -b:] = True
    yy, xx = np.mgrid[0:h, 0:w]
    center = (np.abs(yy - h / 2) < h * 0.3) & (np.abs(xx - w / 2) < w * 0.42)
    best = None
    for c in range(k):
        m = lbl == c
        if m.sum() < 0.02 * h * w:
            continue
        s = ((m & center).sum() / center.sum() - 2 * (m & border).sum() / border.sum()
             + 0.5 * _clean(m, 0.05).sum() / m.sum())
        if best is None or s > best[0]:
            best = (s, c)
    if best is None or best[0] < 0.3:
        return None
    c0 = cent[best[1]]
    gap = min(np.linalg.norm(cent[c] - c0) for c in range(k) if c != best[1])
    dist = np.linalg.norm(px - c0, axis=1).reshape(h, w)
    m = _clean(dist < min(25.0, 0.5 * gap), 0.02)
    return (m, c0) if m.sum() >= 20 else None


def segment(mask):
    """Split a line mask into character groups (components merged when they overlap horizontally)."""
    n, lbl, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    comps = [[st[i, 0], st[i, 0] + st[i, 2], [i]] for i in range(1, n)]
    comps.sort(key=lambda c: c[0])
    groups = []
    for x0, x1, ids in comps:
        if groups:
            gx0, gx1, gids = groups[-1]
            ov = min(gx1, x1) - max(gx0, x0)
            if ov > 0.4 * min(gx1 - gx0, x1 - x0):
                groups[-1] = [min(gx0, x0), max(gx1, x1), gids + ids]
                continue
        groups.append([x0, x1, ids])
    out = []
    for x0, x1, ids in groups:
        gm = np.isin(lbl[:, x0:x1], ids)
        ys = np.nonzero(gm.any(1))[0]
        out.append({"mask": gm, "top": int(ys.min()), "bot": int(ys.max()) + 1})
    return out


def drop_ornaments(m, max_area=0.05, max_gap=0.5):
    """Remove small marks floating clear above or below the text body (leaves, stars), keeping
    i-dots and accents, which sit close to the letter below/above them."""
    p = m.sum(1)
    body = np.nonzero(p >= 0.5 * p.max())[0]
    b0, b1 = int(body.min()), int(body.max())
    gap_lim = max_gap * (b1 - b0 + 1)
    n, lbl, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    small = st[:, cv2.CC_STAT_AREA] <= max_area * st[1:, cv2.CC_STAT_AREA].sum()
    alive = np.ones(n, bool)
    alive[0] = False
    changed = True
    while changed:
        changed = False
        for i in np.nonzero(alive & small)[0]:
            x, y, w, h = st[i, :4]
            others = alive[lbl[:, x:x + w]] & (lbl[:, x:x + w] != i)
            if y + h <= b0:
                rows = np.nonzero(others[y + h:].any(1))[0]
                gap = rows[0] if len(rows) else np.inf
            elif y > b1:
                rows = np.nonzero(others[:y].any(1))[0]
                gap = y - 1 - rows[-1] if len(rows) else np.inf
            else:
                continue
            if gap > gap_lim:
                alive[i], changed = False, True
    return alive[lbl]


def split_lines(mask, depth=0.3, min_ink=0.08):
    """Split a crop into text lines at deep valleys of the row ink profile.

    Components go whole to the line holding their centre, so a descender dipping into the next
    line stays with its letter. Small ornaments stay attached to the nearest line; a short band
    sliced by the crop edge (half of a neighbouring line) is dropped."""
    H = mask.shape[0]
    prof = mask.sum(1).astype(np.float32)
    total = float(prof.sum())
    k = max(1, H // 40)
    ps = np.convolve(prof, np.ones(2 * k + 1, np.float32) / (2 * k + 1), mode="same")
    cuts = []

    def rec(a, b):
        seg, ink = ps[a:b], np.cumsum(prof[a:b])
        if len(seg) < 3:
            return
        pre = np.maximum.accumulate(seg)
        suf = np.maximum.accumulate(seg[::-1])[::-1]
        ok = (seg < depth * np.minimum(pre, suf)) & (ink >= min_ink * total) & (ink[-1] - ink >= min_ink * total)
        if not ok.any():
            return
        y = a + int(np.argmin(np.where(ok, seg, np.inf)))
        cuts.append(y)
        rec(a, y)
        rec(y + 1, b)

    rec(0, H)
    if not cuts:
        return [drop_ornaments(mask)]
    cuts.sort()
    n, lbl, st, cen = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    band = np.searchsorted(cuts, cen[1:, 1], side="right")
    lines = [np.isin(lbl, np.nonzero(band == i)[0] + 1) for i in range(len(cuts) + 1)]
    lines = [m for m in lines if m.any()]
    heights = [_tight(m).shape[0] for m in lines]
    tall = max(heights)

    def sliced(m, row, h):
        r = m.sum(1)
        return h < 0.5 * tall and r[row] >= 0.5 * r.max()

    if len(lines) > 1 and sliced(lines[0], 0, heights[0]):
        lines, heights = lines[1:], heights[1:]
    if len(lines) > 1 and sliced(lines[-1], H - 1, heights[-1]):
        lines, heights = lines[:-1], heights[:-1]
    return [drop_ornaments(m) for m in lines]


def assign_words(text, widths):
    """Split text into len(widths) consecutive lines whose lengths best follow the measured widths."""
    words = text.split()
    n = len(widths)
    if n == 1:
        return [" ".join(words)]
    if len(words) < n or n > 6:
        return None
    rw = np.array(widths, np.float64) / sum(widths)
    best, best_cost = None, None
    for cut in itertools.combinations(range(1, len(words)), n - 1):
        parts = [" ".join(words[i:j]) for i, j in zip((0,) + cut, cut + (len(words),))]
        rt = np.array([len(p) for p in parts], np.float64)
        cost = float(np.abs(np.log(rw / (rt / rt.sum()))).sum())
        if best_cost is None or cost < best_cost:
            best, best_cost = parts, cost
    return best


def query_features(mask):
    groups = segment(mask)
    feats = []
    for g in groups:
        f = glyph_feat(g["mask"])
        if f is None:
            continue
        feats.append({"vec": f[0], "aspect": f[1], "ink": f[2], "stroke": f[3], "h": f[4],
                      "top": g["top"], "bot": g["bot"]})
    if feats:
        lt = min(f["top"] for f in feats)
        lb = max(f["bot"] for f in feats)
        lh = max(1, lb - lt)
        for f in feats:
            f["t"] = (f["top"] - lt) / lh
            f["b"] = (lb - f["bot"]) / lh
    tm = _tight(mask)
    return {"glyphs": feats, "line_aspect": tm.shape[1] / tm.shape[0], "mask": mask}


# --------------------------------------------------------------------------- atlas


class Atlas:
    """Pre-rendered features of ATLAS_CHARS for every face."""

    def __init__(self, lib):
        self.lib = lib
        self.ids, self.row = [], {}
        self.vec = np.zeros((0, len(ATLAS_CHARS), D), np.float32)
        self.feat = np.zeros((0, len(ATLAS_CHARS), 5), np.float32)
        self.valid = np.zeros((0, len(ATLAS_CHARS)), bool)

    def _face_rows(self, fid):
        vec = np.zeros((len(ATLAS_CHARS), D), np.float16)
        feat = np.zeros((len(ATLAS_CHARS), 5), np.float32)
        valid = np.zeros(len(ATLAS_CHARS), bool)
        miss = set(self.lib.missing(fid, ATLAS_CHARS))
        for i, ch in enumerate(ATLAS_CHARS):
            if ch in miss:
                continue
            r = self.lib.render(fid, ch)
            if r is None:
                continue
            f = glyph_feat(r[0])
            vec[i] = f[0]
            feat[i] = (f[1], f[2], f[3], r[1], r[2])  # aspect, ink, stroke/h, top, bottom (baseline-up)
            valid[i] = True
        return vec, feat, valid

    def build(self, verbose=False):
        t0 = time.time()
        old = {}
        if os.path.exists(ATLAS_PATH):
            # Each z[key] access re-reads the whole array from the archive: read each once.
            with np.load(ATLAS_PATH, allow_pickle=False) as z:
                ovec, ofeat, ovalid, oids = z["vec"], z["feat"], z["valid"], z["ids"].tolist()
            if ovec.shape[2] == D:
                for i, fid in enumerate(oids):
                    old[fid] = (ovec[i], ofeat[i], ovalid[i])
        ids = [f["id"] for f in self.lib.faces]
        vec = np.zeros((len(ids), len(ATLAS_CHARS), D), np.float16)
        feat = np.zeros((len(ids), len(ATLAS_CHARS), 5), np.float32)
        valid = np.zeros((len(ids), len(ATLAS_CHARS)), bool)
        new = 0
        for i, fid in enumerate(ids):
            rows = old.get(fid)
            if rows is None:
                rows = self._face_rows(fid)
                new += 1
            vec[i], feat[i], valid[i] = rows
        if new or len(old) != len(ids):
            np.savez(ATLAS_PATH, ids=np.array(ids), vec=vec, feat=feat, valid=valid)
        self.ids, self.row = ids, {fid: i for i, fid in enumerate(ids)}
        self.vec, self.feat, self.valid = vec.astype(np.float32), feat, valid
        if verbose:
            print(f"字形图集：{len(ids)} 个字形，新渲染 {new} 个，用时 {time.time() - t0:.1f}s")
        return self


# --------------------------------------------------------------------------- scoring


def _glyph_penalty(q, aspect, ink, stroke):
    return (0.6 * np.abs(np.log(np.maximum(aspect, 1e-3) / q["aspect"]))
            + 1.2 * np.abs(ink - q["ink"]) + 2.5 * np.abs(stroke - q["stroke"]))


def _char_feats(atlas, lib, fids, ch):
    """(vec [F,D], feat [F,5], valid [F]) for one character across fids, rendering if not in atlas."""
    rows = [atlas.row.get(fid) for fid in fids]
    if ch in CHAR_POS and all(r is not None for r in rows):
        ci = CHAR_POS[ch]
        r = np.array(rows)
        return atlas.vec[r, ci], atlas.feat[r, ci], atlas.valid[r, ci]
    vec = np.zeros((len(fids), D), np.float32)
    feat = np.zeros((len(fids), 5), np.float32)
    valid = np.zeros(len(fids), bool)
    for i, fid in enumerate(fids):
        if lib.missing(fid, ch):
            continue
        r = lib.render(fid, ch)
        if r is None:
            continue
        f = glyph_feat(r[0])
        vec[i], feat[i], valid[i] = f[0], (f[1], f[2], f[3], r[1], r[2]), True
    return vec, feat, valid


def score_glyphs_known(atlas, lib, fids, qf, chars):
    """Per-character comparison when the text is known and segmentation matched its length."""
    per = []
    cache = {}
    for ch in set(chars):
        cache[ch] = _char_feats(atlas, lib, fids, ch)
    tops = np.stack([cache[c][1][:, 3] for c in chars], 1)
    bots = np.stack([cache[c][1][:, 4] for c in chars], 1)
    lt, lb = tops.max(1), bots.min(1)
    lh = np.maximum(lt - lb, 1)
    for k, (q, ch) in enumerate(zip(qf["glyphs"], chars)):
        best = None
        for alt_i, c in enumerate([ch] + list(CONFUSABLE.get(ch, ""))):
            if c not in cache:
                cache[c] = _char_feats(atlas, lib, fids, c)
            vec, feat, valid = cache[c]
            s = vec @ q["vec"] - _glyph_penalty(q, feat[:, 0], feat[:, 1], feat[:, 2])
            if alt_i == 0:
                t = (lt - feat[:, 3]) / lh
                b = (feat[:, 4] - lb) / lh
                s = s - 0.8 * (np.abs(t - q["t"]) + np.abs(b - q["b"]))
            else:
                s = s - 0.25
            s = np.where(valid, s, -2.0)
            best = s if best is None else np.maximum(best, s)
        per.append(best)
    return np.mean(per, 0)


def score_glyphs_any(atlas, fids, qf):
    """No text known: each query character takes its best-matching atlas character per font."""
    r = np.array([atlas.row[f] for f in fids])
    vec, feat, valid = atlas.vec[r], atlas.feat[r], atlas.valid[r]
    F, C = valid.shape
    flat = vec.reshape(F * C, D)
    per, guess = [], []
    for q in qf["glyphs"]:
        s = (flat @ q["vec"]).reshape(F, C)
        s = s - _glyph_penalty(q, feat[:, :, 0], feat[:, :, 1], feat[:, :, 2])
        s = np.where(valid, s, -2.0)
        per.append(s.max(1))
        guess.append(s.argmax(1))
    return np.mean(per, 0), np.stack(guess, 1)


def score_line(lib, fids, qf, text):
    qa = qf["line_aspect"]
    width = int(np.clip(round(LINE_H * qa), 8, 800))
    qv = line_vec(qf["mask"], width)
    out = np.full(len(fids), -2.0, np.float32)
    for i, fid in enumerate(fids):
        r = lib.render(fid, text)
        if r is None:
            continue
        m = r[0]
        ca = m.shape[1] / m.shape[0]
        out[i] = float(line_vec(m, width) @ qv) - 0.8 * abs(np.log(ca / qa))
    return out


def match(lib, atlas, query, text="", limit=40):
    """query: result of extract(). Returns ranked [{id, score}], a guessed text when none given,
    and the index of the mask variant that produced the best match."""
    text = " ".join((text or "").split())
    chars = [c for c in text if not c.isspace()]
    if chars:
        fids = [f["id"] for f in lib.faces if lib.covers(f["id"], text)]
    else:
        fids = [f["id"] for f in lib.faces if atlas.row.get(f["id"]) is not None
                and atlas.valid[atlas.row[f["id"]]].sum() >= 10]
    if not fids:
        return {"results": [], "guess": "", "candidates": 0}

    best, which, guesses = None, None, {}
    for vi, mask in enumerate(query["variants"]):
        qfs = [qf for qf in map(query_features, split_lines(mask)) if qf["glyphs"]]
        if not qfs:
            continue
        if chars:
            n_words = len(text.split())
            if len(qfs) > n_words:  # extra "lines" are ornaments (flourishes, dividers)
                keep = sorted(np.argsort([-qf["mask"].sum() for qf in qfs])[:n_words])
                qfs = [qfs[i] for i in keep]
            parts = assign_words(text, [_tight(qf["mask"]).shape[1] for qf in qfs])
            if parts is None:
                qfs, parts = [query_features(mask)], [text]
            s, total = 0.0, 0
            for qf, part in zip(qfs, parts):
                pc = [c for c in part if not c.isspace()]
                line = score_line(lib, fids, qf, part)
                if len(qf["glyphs"]) == len(pc):
                    line = 0.55 * score_glyphs_known(atlas, lib, fids, qf, pc) + 0.45 * line
                s, total = s + len(pc) * line, total + len(pc)
            s = s / total
        else:
            s, g = score_glyphs_any(atlas, fids, {"glyphs": [q for qf in qfs for q in qf["glyphs"]]})
            top, k, words = int(np.argmax(s)), 0, []
            for qf in qfs:
                words.append("".join(ATLAS_CHARS[c] for c in g[top][k:k + len(qf["glyphs"])]))
                k += len(qf["glyphs"])
            guesses[vi] = " ".join(words)
        if best is None:
            best, which = s, np.full(len(fids), vi)
        else:
            which = np.where(s > best, vi, which)
            best = np.maximum(best, s)
    if best is None:
        return {"results": [], "guess": "", "candidates": len(fids), "variant": 0}

    order = np.argsort(-best)
    results, seen = [], set()
    for i in order:
        f = lib.by_id[fids[i]]
        key = (f["family"].lower(), f["style"].lower())
        if key in seen:
            continue
        seen.add(key)
        results.append({"id": f["id"], "score": round(float(best[i]), 4), "band": band(float(best[i]))})
        if len(results) >= limit:
            break
    vi = int(which[order[0]])
    return {"results": results, "guess": guesses.get(vi, ""), "candidates": len(fids), "variant": vi}
