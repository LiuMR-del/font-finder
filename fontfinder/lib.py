"""In-memory font library: index lookup, coverage checks and Pillow rendering."""
import bisect
import threading

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .common import read_json
from .refs import Refs

RENDER_SIZE = 96


class Library:
    def __init__(self):
        self._lock = threading.RLock()
        self.reload()

    def reload(self):
        with self._lock:
            self.index = read_json("index.json", {"faces": [], "stats": {}})
            self.refs = Refs()
            local = {f["id"] for f in self.index["faces"]}
            # recognition-only faces whose real file is not in the local library
            self.faces = self.index["faces"] + [f for f in self.refs.faces if f["id"] not in local]
            self.by_id = {f["id"]: f for f in self.faces}
            self._ranges = {}
            self._fonts = {}
            self._renders = {}
            self.version = self.index.get("scanned_at", 0)

    def _range_arrays(self, fid):
        r = self._ranges.get(fid)
        if r is None:
            rs = self.by_id[fid]["ranges"]
            r = ([a for a, _ in rs], [b for _, b in rs])
            self._ranges[fid] = r
        return r

    def missing(self, fid, text):
        starts, ends = self._range_arrays(fid)
        out = []
        for ch in dict.fromkeys(text):
            if ch.isspace():
                continue
            cp = ord(ch)
            i = bisect.bisect_right(starts, cp) - 1
            if i < 0 or cp > ends[i]:
                out.append(ch)
        return out

    def covers(self, fid, text):
        return not self.missing(fid, text)

    def font(self, fid, size=RENDER_SIZE):
        key = (fid, size)
        f = self._fonts.get(key)
        if f is None:
            face = self.by_id[fid]
            f = ImageFont.truetype(face["path"], size, index=face.get("index", 0))
            self._fonts[key] = f
        return f

    def render(self, fid, text, size=RENDER_SIZE):
        """Tight ink mask of `text`, plus ink top/bottom measured upward from the baseline."""
        key = (fid, text, size)
        if key in self._renders:
            return self._renders[key]
        if self.by_id[fid].get("ref"):
            res = self.refs.render(self.by_id[fid], text, size)
            self._renders[key] = res
            return res
        with self._lock:
            try:
                f = self.font(fid, size)
                try:
                    length = f.getlength(text)
                except Exception:
                    length = size * len(text)
                w, h, y0 = int(length + size * 1.5) + 10, int(size * 3), int(size * 2)
                im = Image.new("L", (w, h), 0)
                ImageDraw.Draw(im).text((size // 2, y0), text, font=f, fill=255, anchor="ls")
                a = np.asarray(im) > 127
                ys, xs = np.nonzero(a)
                res = None
                if len(xs):
                    res = (a[ys.min():ys.max() + 1, xs.min():xs.max() + 1], y0 - ys.min(), y0 - ys.max() - 1)
            except Exception:
                res = None
        if len(self._renders) > 20000:
            self._renders.clear()
        self._renders[key] = res
        return res
