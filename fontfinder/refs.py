"""Recognition-only references for fonts whose files may not be redistributed.

System, commercial and personal-use fonts are shipped in the public repository only as glyph
bitmaps of a small character set (plus advances and metadata) - enough to recognise them in a
screenshot and render an approximate preview, but not a usable font. Redistributable fonts
(OFL/Apache etc.) are copied into ./fonts instead.

    .venv/bin/python -m fontfinder.refs      # export from the local library (maintainer only)
"""
import json
import os
import shutil

import numpy as np
from PIL import Image, ImageDraw

from .common import ROOT

REF_DIR = os.path.join(ROOT, "refs")
FONTS_DIR = os.path.join(ROOT, "fonts")
CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz&'.,-!?#@+/:()"
SIZE = 96  # = lib.RENDER_SIZE
META_KEYS = ("id", "family", "style", "full", "ps", "designer", "vendor", "version", "license", "license_url",
             "copyright", "personal_only", "open_license", "glyphs", "has_digits", "has_upper", "has_lower",
             "cjk", "cjk_gb1", "weight_class", "width_class", "italic", "mono", "panose", "ink", "aspect",
             "hash", "tags", "dafont")
REF_TAG = "仅供识别"


def redistributable(face):
    """May the font file itself be published? Only open licences; system/personal/unknown may not."""
    tags = face.get("tags", [])
    if face.get("google"):
        return True
    if "系统字体" in tags or "⚠个人使用" in tags or face.get("personal_only"):
        return False
    return bool(face.get("open_license"))


# --------------------------------------------------------------------------- export


def _glyphs(font, chars):
    out = []
    for ch in chars:
        im = Image.new("L", (SIZE * 3, SIZE * 3), 0)
        ox, oy = SIZE, SIZE * 2
        ImageDraw.Draw(im).text((ox, oy), ch, font=font, fill=255, anchor="ls")
        a = np.asarray(im) > 127
        ys, xs = np.nonzero(a)
        adv = float(font.getlength(ch))
        if not len(xs):
            continue
        out.append((ch, a[ys.min():ys.max() + 1, xs.min():xs.max() + 1], int(xs.min() - ox), int(oy - ys.min()), adv))
    return out


def export(lib, verbose=True):
    """Copy redistributable non-Google fonts to ./fonts and write ./refs for the rest."""
    from PIL import ImageFont
    os.makedirs(FONTS_DIR, exist_ok=True)
    os.makedirs(REF_DIR, exist_ok=True)
    copied, faces, table, blob = 0, [], [], []
    offset, seen_paths = 0, set()
    for f in lib.faces:
        if f.get("ref") or f.get("google") or not f.get("path"):
            continue
        if redistributable(f):
            if f["path"] in seen_paths or f["path"].startswith(FONTS_DIR + os.sep):
                continue
            seen_paths.add(f["path"])
            name = os.path.basename(f["path"])
            dst = os.path.join(FONTS_DIR, name)
            if os.path.exists(dst) and os.path.getsize(dst) != f["size"]:
                stem, ext = os.path.splitext(name)
                dst = os.path.join(FONTS_DIR, f"{stem}-{f['hash'][:6]}{ext}")
            if not os.path.exists(dst):
                shutil.copy2(f["path"], dst)
                copied += 1
            continue
        try:
            font = ImageFont.truetype(f["path"], SIZE, index=f.get("index", 0))
            chars = [c for c in CHARS if not lib.missing(f["id"], c)]
            glyphs = _glyphs(font, chars)
            space = float(font.getlength(" "))
        except Exception:
            continue
        if not glyphs:
            continue
        fi = len(faces)
        meta = {k: f.get(k) for k in META_KEYS}
        meta.update({"ref": True, "space": space, "tags": [t for t in f.get("tags", []) if t != REF_TAG] + [REF_TAG],
                     "ranges": _ranges(ord(g[0]) for g in glyphs)})
        faces.append(meta)
        for ch, m, dx, top, adv in glyphs:
            bits = np.packbits(m.ravel())
            table.append((fi, ord(ch), m.shape[1], m.shape[0], dx, top, adv, offset))
            blob.append(bits)
            offset += len(bits)
    t = np.array(table, dtype=np.float64)
    np.savez_compressed(os.path.join(REF_DIR, "glyphs.npz"), table=t.astype(np.int64)[:, :6].astype(np.int32),
                        adv=t[:, 6].astype(np.float32), offset=t[:, 7].astype(np.int64),
                        bits=np.concatenate(blob) if blob else np.zeros(0, np.uint8))
    with open(os.path.join(REF_DIR, "faces.json"), "w", encoding="utf-8") as fh:
        json.dump(faces, fh, ensure_ascii=False, separators=(",", ":"))
    if verbose:
        print(f"可分发字体复制到 fonts/：新增 {copied} 个；仅供识别的字体：{len(faces)} 个，字形 {len(table)} 个")
    return copied, len(faces)


def _ranges(codes):
    codes = sorted(set(codes))
    out = []
    for c in codes:
        if out and c == out[-1][1] + 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return out


# --------------------------------------------------------------------------- runtime


class Refs:
    """Loaded reference glyphs; renders text by placing the stored bitmaps (no kerning)."""

    def __init__(self):
        self.faces, self.glyphs = [], {}
        fj, gz = os.path.join(REF_DIR, "faces.json"), os.path.join(REF_DIR, "glyphs.npz")
        if not (os.path.exists(fj) and os.path.exists(gz)):
            return
        with open(fj, encoding="utf-8") as fh:
            self.faces = json.load(fh)
        with np.load(gz) as z:
            table, adv, offset, bits = z["table"], z["adv"], z["offset"], z["bits"]
        for (fi, code, w, h, dx, top), a, off in zip(table, adv, offset):
            n = (int(w) * int(h) + 7) // 8
            self.glyphs[(self.faces[fi]["id"], chr(code))] = (bits[off:off + n], int(w), int(h), int(dx), int(top),
                                                               float(a))
        for f in self.faces:
            f.update({"path": None, "dups": [], "size": 0, "ext": "", "is_ttc": False, "index": 0, "schema": 2})

    def _glyph(self, fid, ch):
        g = self.glyphs.get((fid, ch))
        if g is None:
            return None
        bits, w, h, dx, top, adv = g
        return np.unpackbits(bits)[:w * h].reshape(h, w).astype(bool), dx, top, adv

    def render(self, face, text, size=SIZE):
        """Same contract as Library.render: (tight mask, ink top, ink bottom) measured up from the baseline."""
        placed, pen = [], 0.0
        for ch in text:
            if ch == " ":
                pen += face.get("space", SIZE * 0.3)
                continue
            g = self._glyph(face["id"], ch)
            if g is None:
                continue
            m, dx, top, adv = g
            placed.append((m, int(round(pen)) + dx, top))
            pen += adv
        if not placed:
            return None
        x0 = min(x for _, x, _ in placed)
        x1 = max(x + m.shape[1] for m, x, _ in placed)
        t = max(top for _, _, top in placed)
        b = min(top - m.shape[0] for m, _, top in placed)
        out = np.zeros((t - b, x1 - x0), bool)
        for m, x, top in placed:
            out[t - top:t - top + m.shape[0], x - x0:x - x0 + m.shape[1]] |= m
        if size != SIZE:
            s = size / SIZE
            im = Image.fromarray(out.astype(np.uint8) * 255).resize(
                (max(1, round(out.shape[1] * s)), max(1, round(out.shape[0] * s))), Image.BILINEAR)
            return np.asarray(im) > 127, round(t * s), round(b * s)
        return out, t, b


if __name__ == "__main__":
    from .lib import Library
    export(Library())
