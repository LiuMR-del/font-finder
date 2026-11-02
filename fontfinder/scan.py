"""Scan font folders into data/index.json. Source folders are only ever read."""
import hashlib
import os
import re
import sys
import time

import numpy as np
from fontTools.ttLib import TTCollection, TTFont
from PIL import Image, ImageDraw, ImageFont

from .common import load_config, read_json, write_json

FONT_EXT = (".ttf", ".otf", ".ttc", ".otc")
GB_LEVEL1 = None

SYSTEM_FAMILIES = re.compile(
    r"^(Arial|Calibri|Cambria|Candara|Comic Sans|Consolas|Constantia|Corbel|Courier New|DengXian|"
    r"Ebrima|FangSong|Franklin Gothic|Gabriola|Gadugi|Georgia|HYZhongHei|Ink Free|Javanese|KaiTi|"
    r"Leelawadee|Lucida|MS Gothic|MS PGothic|MS UI Gothic|MV Boli|Malgun|Microsoft|MingLiU|PMingLiU|"
    r"Mongolian Baiti|Myanmar Text|Nirmala|NSimSun|SimSun|SimHei|Palatino Linotype|Sans Serif Collection|"
    r"Segoe|Sitka|Sylfaen|Symbol|Tahoma|Times New Roman|Trebuchet|Verdana|Webdings|Wingdings|"
    r"Yu Gothic|Bahnschrift|Impact|Marlett|HoloLens|Cascadia|Helvetica|PingFang|Hiragino|STHeiti|Menlo)",
    re.I,
)
PERSONAL = re.compile(
    r"personal|demo\b|trial|non[- ]?commercial|unlicensed|not for commercial|for evaluation|free version",
    re.I,
)
OPEN_LICENSE = re.compile(r"open font license|\bOFL\b|apache license|public domain|SIL\b|ubuntu font licen", re.I)
SCRIPT_WORDS = re.compile(
    r"script|hand|brush|signature|calligraph|writ|marker|pen\b|ink\b|scribble|vibes|allura|italianno|"
    r"parisienne|cursive|monoline|autograph|chalk|crayon|felt|kalam|caveat|pacifico",
    re.I,
)
DECOR_WORDS = re.compile(
    r"stencil|grunge|horror|creep|zombie|blood|distress|western|circus|christmas|halloween|"
    r"shadow|outline|inline|\b3d\b|retro|vintage|comic|cartoon|bubble|graffiti|pixel|\blcd\b|neon|"
    r"glitch|\bdeco\b|ornament|fancy|\bfun\b|candy|\bkids\b|jersey|varsity|college|athletic|"
    r"black ?letter|fraktur|medieval|old ?english|tattoo",
    re.I,
)
SPORT_WORDS = re.compile(r"jersey|varsity|college|athletic|sport|team|league|champion|football|basketball", re.I)
SERIF_PANOSE = set(range(2, 11))
SANS_PANOSE = {11, 12, 13, 15}


def gb_level1():
    global GB_LEVEL1
    if GB_LEVEL1 is None:
        chars = set()
        for hi in range(0xB0, 0xD8):
            for lo in range(0xA1, 0xFF):
                try:
                    chars.add(ord(bytes([hi, lo]).decode("gb2312")))
                except UnicodeDecodeError:
                    pass
        GB_LEVEL1 = chars
    return GB_LEVEL1


def file_hash(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def to_ranges(codes):
    codes = sorted(codes)
    out = []
    for c in codes:
        if out and c == out[-1][1] + 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return out


def name_of(ft, *ids):
    n = ft["name"]
    for i in ids:
        v = n.getDebugName(i)
        if v:
            return v.strip()
    return ""


def measure(path, index, text):
    """Ink darkness and width of a sample string rendered at 100px."""
    try:
        f = ImageFont.truetype(path, 100, index=index)
        im = Image.new("L", (1400, 220), 0)
        ImageDraw.Draw(im).text((20, 30), text, font=f, fill=255)
        a = np.asarray(im) > 127
        ys, xs = np.nonzero(a)
        if len(xs) == 0:
            return None
        h = ys.max() - ys.min() + 1
        w = xs.max() - xs.min() + 1
        return {"ink": round(float(a.sum()) / (w * h), 4), "aspect": round(float(w) / h / len(text), 4)}
    except Exception:
        return None


def describe_face(ft, path, index):
    cmap = ft.getBestCmap() or {}
    codes = set(cmap)
    try:
        os2 = ft["OS/2"] if "OS/2" in ft else None
    except Exception:
        os2 = None
    family = name_of(ft, 16, 1) or os.path.splitext(os.path.basename(path))[0]
    style = name_of(ft, 17, 2) or "Regular"
    license_txt = " ".join(filter(None, [name_of(ft, 13), name_of(ft, 14)]))
    all_names = " ".join(filter(None, [name_of(ft, i) for i in (0, 1, 3, 4, 5, 10, 13)]))
    digits = all(ord(c) in codes for c in "0123456789")
    upper = all(ord(c) in codes for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    lower = all(ord(c) in codes for c in "abcdefghijklmnopqrstuvwxyz")
    cjk = sum(1 for c in codes if 0x4E00 <= c <= 0x9FFF)
    gb1 = len(codes & gb_level1()) if cjk else 0

    sample = "0123456789" if digits else ("HOBDRE" if upper else "")
    m = measure(path, index, sample) if sample else None
    cap = measure(path, index, "HOBDRE") if upper else None

    panose = None
    if os2 is not None and hasattr(os2, "panose"):
        p = os2.panose
        panose = [p.bFamilyType, p.bSerifStyle, p.bWeight, p.bProportion]
    return {
        "family": family,
        "style": style,
        "full": name_of(ft, 4) or f"{family} {style}",
        "ps": name_of(ft, 6),
        "designer": name_of(ft, 9),
        "vendor": name_of(ft, 8),
        "version": name_of(ft, 5),
        "copyright": name_of(ft, 0)[:300],
        "license": license_txt[:500],
        "license_url": name_of(ft, 14),
        "personal_only": bool(PERSONAL.search(all_names + " " + license_txt)),
        "open_license": bool(OPEN_LICENSE.search(license_txt)),
        "glyphs": len(ft.getGlyphOrder()),
        "has_digits": digits,
        "has_upper": upper,
        "has_lower": lower,
        "cjk": cjk,
        "cjk_gb1": gb1,
        "weight_class": getattr(os2, "usWeightClass", 400) if os2 is not None else 400,
        "width_class": getattr(os2, "usWidthClass", 5) if os2 is not None else 5,
        "italic": bool(os2 and (os2.fsSelection & 1)) or "italic" in style.lower() or "oblique" in style.lower(),
        "mono": bool("post" in ft and ft["post"].isFixedPitch),
        "panose": panose,
        "ink": m["ink"] if m else None,
        "aspect": (cap or m)["aspect"] if (cap or m) else None,
        "ranges": to_ranges(codes),
    }


def auto_tags(face, path):
    tags = []
    if face["has_digits"]:
        tags.append("含数字")
    if face["cjk_gb1"] >= 3000:
        tags.append("中文(完整)")
    elif face["cjk"] > 0:
        tags.append("中文(部分)")
    if face["has_upper"] and not face["has_lower"]:
        tags.append("仅大写")
    if not face["has_upper"] and not face["cjk"]:
        tags.append("无拉丁大写")

    ink = face["ink"]
    if ink is not None:
        if ink < 0.20:
            tags.append("细")
        elif ink < 0.35:
            tags.append("常规")
        elif ink < 0.50:
            tags.append("粗")
        else:
            tags.append("特粗")
    asp = face["aspect"]
    if asp is not None:
        if asp < 0.70:
            tags.append("窄体")
        elif asp > 1.05:
            tags.append("宽体")

    text = f'{face["family"]} {face["style"]} {face["full"]}'
    pan = face["panose"] or [0, 0, 0, 0]
    if face["mono"] or pan[3] == 9:
        tags.append("等宽")
    if pan[0] == 3 or SCRIPT_WORDS.search(text):
        tags.append("手写/书法")
    elif pan[0] == 4 or DECOR_WORDS.search(text):
        tags.append("装饰")
    elif pan[0] == 2 and pan[1] in SANS_PANOSE:
        tags.append("无衬线")
    elif pan[0] == 2 and pan[1] in SERIF_PANOSE:
        tags.append("衬线")
    if pan[0] == 5 or re.search(r"symbol|dingbat|icons|emoji|wingding|webding|marlett|MDL2", text, re.I):
        tags.append("符号")
    if face["italic"]:
        tags.append("斜体")
    if SPORT_WORDS.search(text):
        tags.append("运动风")

    is_sys = "SystemFonts" in path.split(os.sep) or bool(SYSTEM_FAMILIES.match(face["family"]))
    if is_sys:
        tags.append("系统字体")
    gf = face.get("google")
    if gf:
        tags.append("Google可商用")
        if gf.get("category") == "Handwriting" and "手写/书法" not in tags:
            tags.append("手写/书法")
        elif gf.get("category") == "Display" and not {"手写/书法", "装饰"} & set(tags):
            tags.append("装饰")
    df = face.get("dafont")
    df_lic = (df or {}).get("license", "").lower()
    if df:
        tags.append("dafont")
        tags.extend("dafont:" + c for c in df.get("cats", []))
    if (face["personal_only"] and not is_sys) or re.search(r"personal|demo|shareware|donationware", df_lic):
        tags.append("⚠个人使用")
    elif face["open_license"] or gf:
        tags.append("开源可商用")
    elif "100% free" in df_lic or "public domain" in df_lic:
        tags.append("免费可商用(dafont)")

    if (face["has_digits"] and ink is not None and ink >= 0.30 and "手写/书法" not in tags
            and "符号" not in tags and not face["italic"]):
        tags.append("适合号码")
    return tags


def iter_font_files(sources):
    for src in sources:
        if not os.path.isdir(src):
            continue
        for dp, dns, fns in os.walk(src):
            dns[:] = [d for d in dns if not d.startswith(".")]
            for fn in fns:
                if fn.lower().endswith(FONT_EXT) and not fn.startswith("._"):
                    yield os.path.join(dp, fn)


def dafont_sidecar(path, memo, name="_dafont.json"):
    """Metadata written next to fonts downloaded from dafont or Google Fonts (gfonts)."""
    key = (os.path.dirname(path), name)
    if key not in memo:
        p = os.path.join(key[0], name)
        memo[key] = read_json(p, None) if os.path.exists(p) else None
    return memo[key]


def scan(verbose=True):
    t0 = time.time()
    sidecars = {}
    cfg = load_config()
    old = read_json("index.json", {"faces": [], "files": {}})
    hash_cache = read_json("hash_cache.json", {})
    old_faces = {}
    for f in old.get("faces", []):
        old_faces.setdefault(f["hash"], []).append(f)

    files, new_cache, errors = {}, {}, []
    for path in iter_font_files(cfg["sources"]):
        try:
            st = os.stat(path)
        except OSError:
            continue
        key = f"{st.st_size}:{int(st.st_mtime)}"
        c = hash_cache.get(path)
        h = c[1] if c and c[0] == key else file_hash(path)
        new_cache[path] = [key, h]
        rec = files.setdefault(h, {"paths": [], "size": st.st_size, "added": int(st.st_mtime)})
        rec["paths"].append(path)

    faces, added = [], 0
    for h, rec in files.items():
        rec["paths"].sort(key=lambda p: (("SystemFonts" in p), len(p), p))
        if h in old_faces and old_faces[h][0].get("schema") == 2:
            for f in old_faces[h]:
                f["path"] = rec["paths"][0]
                f["dups"] = rec["paths"][1:]
                f["dafont"] = dafont_sidecar(f["path"], sidecars)
                f["google"] = dafont_sidecar(f["path"], sidecars, "_google.json")
                f["tags"] = auto_tags(f, f["path"])
                faces.append(f)
            continue
        path = rec["paths"][0]
        try:
            if path.lower().endswith((".ttc", ".otc")):
                fonts = TTCollection(path, lazy=True).fonts
            else:
                fonts = [TTFont(path, lazy=True)]
        except Exception as e:
            errors.append({"path": path, "error": str(e)[:200]})
            continue
        for i, ft in enumerate(fonts):
            try:
                face = describe_face(ft, path, i)
            except Exception as e:
                errors.append({"path": path, "index": i, "error": str(e)[:200]})
                continue
            face.update({
                "id": h[:12] + (f"-{i}" if len(fonts) > 1 else ""),
                "schema": 2,
                "hash": h,
                "index": i,
                "is_ttc": len(fonts) > 1 or path.lower().endswith((".ttc", ".otc")),
                "path": path,
                "dups": rec["paths"][1:],
                "size": rec["size"],
                "added": rec["added"],
                "ext": os.path.splitext(path)[1].lower().lstrip("."),
                "dafont": dafont_sidecar(path, sidecars),
                "google": dafont_sidecar(path, sidecars, "_google.json"),
            })
            face["tags"] = auto_tags(face, path)
            faces.append(face)
            added += 1

    faces.sort(key=lambda f: (f["family"].lower(), f["style"].lower()))
    index = {
        "scanned_at": int(time.time()),
        "sources": cfg["sources"],
        "faces": faces,
        "errors": errors,
        "stats": {
            "files": sum(len(r["paths"]) for r in files.values()),
            "unique_files": len(files),
            "duplicate_files": sum(len(r["paths"]) - 1 for r in files.values()),
            "faces": len(faces),
            "families": len({f["family"] for f in faces}),
            "new_faces": added,
        },
    }
    write_json("index.json", index)
    write_json("hash_cache.json", new_cache)
    if verbose:
        s = index["stats"]
        print(f"\n扫描完成，用时 {time.time() - t0:.1f}s：文件 {s['files']} 个，去重后 {s['unique_files']} 个，"
              f"重复 {s['duplicate_files']} 个；字形 {s['faces']} 个，家族 {s['families']} 个；"
              f"本次新增 {s['new_faces']} 个；损坏 {len(errors)} 个")
        for e in errors:
            print("  ✗", e["path"], e["error"])
    return index


if __name__ == "__main__":
    scan(verbose="-q" not in sys.argv)
