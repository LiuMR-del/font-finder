"""Hit-rate experiment: synthesise screenshot-like samples from local fonts and rank them back."""
import io
import json
import random
import subprocess
import sys
import tempfile
import time
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .common import BIN
from .lib import Library
from .match import Atlas, extract, match

TEXTS = ["23", "07", "JORDAN 23", "LAKERS", "Mike 10", "Smith", "88", "Team 2026", "Hello", "WOLVES"]
TEXTS2 = ["Jessica &\nMichael", "Happy\nBirthday", "Mr & Mrs\nSmith", "Team\n2026", "Best\nMom Ever"]
PALETTE = [((255, 255, 255), (20, 20, 20)), ((200, 30, 40), (255, 255, 255)), ((10, 40, 120), (250, 200, 30)),
           ((240, 240, 230), (0, 90, 50)), ((30, 30, 30), (230, 230, 230)), ((255, 220, 0), (120, 0, 0))]


def busy_background(w, h, fg, rng):
    """Illustration-like clutter: blurred colour blobs plus sharp sparkles, kept away from the text colour."""
    def colour():
        while True:
            c = tuple(rng.randrange(256) for _ in range(3))
            if sum((a - b) ** 2 for a, b in zip(c, fg)) > 120 ** 2:
                return c
    im = Image.new("RGB", (w, h), colour())
    d = ImageDraw.Draw(im)
    for _ in range(40):
        x, y, r = rng.randrange(w), rng.randrange(h), rng.randrange(h // 6 + 2, h // 2 + 3)
        d.ellipse((x - r, y - r, x + r, y + r), fill=colour())
    im = im.filter(ImageFilter.GaussianBlur(rng.uniform(2, 6)))
    d = ImageDraw.Draw(im)
    for _ in range(60):
        x, y, r = rng.randrange(w), rng.randrange(h), rng.randrange(1, max(2, h // 25))
        d.polygon([(x - r, y), (x, y - 3 * r), (x + r, y), (x, y + 3 * r)], fill=colour())
    return im


def synth(face, text, rng, hard, style="plain"):
    """style: plain (flat background), busy (illustration background), sticker (busy + double outline)."""
    bg, fg = PALETTE[rng.randrange(len(PALETTE))]
    size = rng.choice([60, 90, 140])
    f = ImageFont.truetype(face["path"], size, index=face.get("index", 0))
    stroke = rng.choice([0, 0, max(2, size // 18)]) if hard and style == "plain" else 0
    sc = (rng.randrange(256), rng.randrange(256), rng.randrange(256)) if stroke else None
    outer = max(3, size // 8) if style == "sticker" else 0
    kw = {"font": f, "align": "center", "spacing": size // 5 + outer}
    l, t, r, b = (int(v) for v in ImageDraw.Draw(Image.new("RGB", (1, 1))).multiline_textbbox(
        (0, 0), text, stroke_width=max(stroke, outer), **kw))
    pad = size // 3
    w, h = r - l + 2 * pad, b - t + 2 * pad
    if style == "plain":
        im = Image.new("RGB", (w, h), bg)
    else:
        fg = tuple(rng.randrange(256) for _ in range(3))
        im = busy_background(w, h, fg, rng)
        bg = im.getpixel((0, 0))
    d, xy = ImageDraw.Draw(im), (pad - l, pad - t)
    if style == "sticker":
        d.multiline_text(xy, text, fill=(255, 255, 255), stroke_width=outer, stroke_fill=(255, 255, 255), **kw)
        stroke, sc = max(2, size // 20), tuple(v // 3 for v in fg)
    d.multiline_text(xy, text, fill=fg, stroke_width=stroke, stroke_fill=sc, **kw)
    if hard:
        im = im.rotate(rng.uniform(-3, 3), resample=Image.BICUBIC, expand=False, fillcolor=bg)
        if rng.random() < 0.6:
            im = im.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 1.2)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=rng.choice([45, 65, 85]))
        im = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    return im


def ocr(im):
    with tempfile.NamedTemporaryFile(suffix=".png") as t:
        im.save(t.name)
        out = subprocess.run([f"{BIN}/ocr", t.name], capture_output=True, text=True, timeout=30).stdout
    items = json.loads(out or "[]")
    return " ".join(i["text"] for i in items)


def rank_of(lib, res, face):
    key = (face["family"].lower(), face["style"].lower())
    for i, r in enumerate(res["results"]):
        f = lib.by_id[r["id"]]
        if (f["family"].lower(), f["style"].lower()) == key:
            return i + 1
    return None


def main(n=50, seed=7, script=False):
    """script=True: only handwriting/script faces (wedding-card style tests)."""
    lib = Library()
    atlas = Atlas(lib).build()
    rng = random.Random(seed)
    pool = [f for f in lib.faces if f.get("path") and f["has_digits"] and f["has_upper"] and f["has_lower"]
            and "系统字体" not in f["tags"] and "符号" not in f["tags"]
            and (not script or "手写/书法" in f["tags"])]
    targets = rng.sample(pool, min(n, len(pool)))
    modes = ["干净+已知文字", "干扰+已知文字", "干扰+Vision识别", "干扰+不给文字", "两行+已知文字", "两行+不给文字",
             "花背景+已知文字", "描边贴纸+已知文字"]
    styles = {"花背景+已知文字": "busy", "描边贴纸+已知文字": "sticker"}
    ranks = {m: [] for m in modes}
    misses = []
    right, impostor = [], []  # known-text scores: correct font at #1, best other family (= "not in library")
    t0 = time.time()
    for face in targets:
        text = rng.choice(TEXTS)
        text2 = rng.choice([t for t in TEXTS2 if lib.covers(face["id"], t)] or ["Happy\nBirthday"])
        for m in modes:
            hard = m != "干净+已知文字"
            sample = text2 if m.startswith("两行") else text
            im = synth(face, sample, random.Random(zlib.crc32(f"{face['id']}{m}".encode())), hard,
                       styles.get(m, "plain"))
            q = extract(im)
            if q is None:
                ranks[m].append(None)
                continue
            t = " ".join(sample.split()) if "已知" in m else (ocr(im) if "Vision" in m else "")
            res = match(lib, atlas, q, t, limit=40)
            rk = rank_of(lib, res, face)
            ranks[m].append(rk)
            if "已知" in m:
                if rk == 1:
                    right.append(res["results"][0]["score"])
                others = [x["score"] for x in res["results"] if lib.by_id[x["id"]]["family"] != face["family"]]
                if others:
                    impostor.append(others[0])
            if m == "干扰+已知文字" and (rk is None or rk > 10):
                misses.append((face["family"], face["style"], text, rk))
    dt = time.time() - t0
    print(f"样本 {len(targets)} 个字体 × {len(modes)} 种条件，候选库 {len(lib.faces)} 个字形，"
          f"平均每次检索 {dt / len(targets) / len(modes):.2f}s")
    for m in modes:
        r = ranks[m]
        hit = lambda k: sum(1 for x in r if x is not None and x <= k) / len(r)
        print(f"  {m:14s} 第1名 {hit(1):5.0%}   前5 {hit(5):5.0%}   前10 {hit(10):5.0%}   前20 {hit(20):5.0%}")
    print("干扰+已知文字 未进前10：", misses)
    pct = lambda v: " ".join(f"p{q}={np.percentile(v, q) * 100:.0f}" for q in (10, 25, 50, 75, 90)) if v else "-"
    print(f"分数分布（已知文字）：正确字体排第1时 {pct(right)}")
    print(f"                      本地没有该字体时的最高分 {pct(impostor)}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 50, script="script" in sys.argv[2:])
