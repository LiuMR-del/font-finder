"""Local HTTP server for the font finder UI."""
import base64
import gzip
import hashlib
import io
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fontTools import subset as ftsubset
from fontTools.ttLib import TTCollection
import numpy as np
from PIL import Image, ImageDraw

from . import online
from .common import BIN, CACHE, DATA, ROOT, WEB, load_config, read_json, write_json
from .lib import Library
from .match import Atlas, extract, split_lines
from .match import match as run_match
from .scan import file_hash, iter_font_files, scan

HIST_DIR = os.path.join(DATA, "history")
TTC_DIR = os.path.join(CACHE, "ttc")
SUBSET_DIR = os.path.join(CACHE, "subset")
for d in (HIST_DIR, TTC_DIR, SUBSET_DIR):
    os.makedirs(d, exist_ok=True)


def clean_image(mask, min_h=160, max_w=1600):
    """Black-on-white, padded copy of the extracted text for upload to font identification sites."""
    mask = np.logical_or.reduce(split_lines(mask))  # drops ornaments and lines cut by the crop edge
    ys, xs = np.nonzero(mask)
    m = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    pad = max(8, m.shape[0] // 6)
    im = Image.fromarray(np.pad(~m, pad, constant_values=True).astype("uint8") * 255)
    s = min(max(1.0, min_h / m.shape[0]), max_w / im.width)
    if s != 1.0:
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    return im


class App:
    def __init__(self):
        self.cfg = load_config()
        self.big = int(self.cfg.get("big_font_mb", 2) * 1024 * 1024)
        self.scan_lock = threading.Lock()
        self.status = "ready"
        self.last_added = 0
        if not os.path.exists(os.path.join(DATA, "index.json")):
            print("首次运行，正在扫描字体…")
            scan(verbose=True)
        self.lib = Library()
        self.atlas = Atlas(self.lib).build(verbose=True)
        self.version = 1
        self._fonts_json = None
        self.hist_lock = threading.Lock()

    # ------------------------------------------------------------------ library
    def rescan(self):
        with self.scan_lock:
            self.status = "scanning"
            try:
                before = len(self.lib.faces)
                scan(verbose=False)
                lib = Library()
                atlas = Atlas(lib).build()
                self.lib, self.atlas = lib, atlas
                self.last_added = len(lib.faces) - before
                self.version += 1
                self._fonts_json = None
            finally:
                self.status = "ready"

    def fonts_json(self):
        if self._fonts_json is None:
            faces = []
            for f in self.lib.faces:
                g = {k: f.get(k) for k in ("id", "family", "style", "full", "ps", "designer", "vendor", "version",
                                           "license", "license_url", "copyright", "path", "dups", "size", "added",
                                           "ext", "tags", "ranges", "is_ttc", "dafont", "cjk", "has_digits",
                                           "ink", "aspect", "ref")}
                g["google"] = bool(f.get("google"))
                g["big"] = f["size"] > self.big
                faces.append(g)
            body = json.dumps({"faces": faces, "version": self.version, "stats": self.lib.index.get("stats", {})},
                              ensure_ascii=False, separators=(",", ":")).encode()
            self._fonts_json = gzip.compress(body, 5)
        return self._fonts_json

    def font_file(self, fid, text):
        f = self.lib.by_id[fid]
        src = f["path"]
        if f.get("is_ttc"):
            src = os.path.join(TTC_DIR, f"{fid}.ttf")
            if not os.path.exists(src):
                TTCollection(f["path"]).fonts[f.get("index", 0)].save(src + ".tmp")
                os.replace(src + ".tmp", src)
        if f["size"] > self.big and text is not None:
            key = hashlib.md5(text.encode()).hexdigest()[:16]
            out = os.path.join(SUBSET_DIR, f"{fid}-{key}.woff2")
            if not os.path.exists(out):
                opts = ftsubset.Options()
                opts.flavor = "woff2"
                opts.layout_features = ["*"]
                opts.name_IDs = ["*"]
                opts.notdef_outline = True
                font = ftsubset.load_font(src, opts)
                sub = ftsubset.Subsetter(opts)
                sub.populate(text=text + " ")
                sub.subset(font)
                ftsubset.save_font(font, out + ".tmp", opts)
                os.replace(out + ".tmp", out)
            return out, "font/woff2"
        return src, {"otf": "font/otf"}.get(f["ext"], "font/ttf")

    def render_png(self, fid, text, size):
        if self.lib.by_id[fid].get("ref"):  # recognition-only: compose the stored glyph bitmaps
            r = self.lib.render(fid, text, size)
            m = r[0] if r else np.zeros((1, 1), bool)
            im = Image.new("LA", (m.shape[1] + 8, m.shape[0] + 8), (0, 0))
            im.putalpha(Image.fromarray(np.pad(m, 4).astype("uint8") * 255))
            buf = io.BytesIO()
            im.save(buf, "PNG")
            return buf.getvalue()
        f = self.lib.font(fid, size)
        l, t, r, b = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=f)
        im = Image.new("LA", (max(1, r - l + 8), max(1, b - t + 8)), (0, 0))
        ImageDraw.Draw(im).text((4 - l, 4 - t), text, font=f, fill=(0, 255))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()

    # ------------------------------------------------------------------ actions
    def install(self, fid):
        f = self.lib.by_id[fid]
        dst_dir = os.path.expanduser("~/Library/Fonts")
        os.makedirs(dst_dir, exist_ok=True)
        name, ext = os.path.splitext(os.path.basename(f["path"]))
        for cand in (name + ext, f"{name}-{f['hash'][:6]}{ext}"):
            dst = os.path.join(dst_dir, cand)
            if os.path.exists(dst):
                if file_hash(dst) == f["hash"]:
                    return {"ok": True, "msg": "已经安装过了", "path": dst}
                continue
            shutil.copy2(f["path"], dst)
            return {"ok": True, "msg": "已安装，在 PS/AI 里重新打开字体菜单即可使用", "path": dst}
        return {"ok": False, "msg": "字体目录里有同名但内容不同的文件，请手动处理"}

    def ocr(self, img):
        """Vision OCR; when it reads nothing (busy backgrounds), retry on the extracted black-on-white text."""
        items = self._ocr(img)
        if items:
            return items
        q = extract(img)
        if q is None:
            return []
        order = sorted(range(len(q["variants"])), key=lambda i: q["fills"][i] is None)  # colour masks first
        for i in order:
            got = [it for it in self._ocr(clean_image(q["variants"][i])) if it["text"].strip()]
            if got:
                return [{"text": " ".join(it["text"].strip() for it in got), "conf": min(it["conf"] for it in got),
                         "box": [0, 0, img.width, img.height], "cleaned": True}]
        return []

    def _ocr(self, img):
        exe = os.path.join(BIN, "ocr")
        if not os.path.exists(exe):
            src = os.path.join(ROOT, "fontfinder", "ocr.swift")
            os.makedirs(BIN, exist_ok=True)
            r = subprocess.run(["swiftc", "-O", src, "-o", exe], capture_output=True, text=True)
            if r.returncode != 0:
                return []
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t:
            img.save(t.name)
        try:
            out = subprocess.run([exe, t.name], capture_output=True, text=True, timeout=30).stdout
        finally:
            os.unlink(t.name)
        return json.loads(out or "[]")

    def match(self, img, text):
        q = extract(img)
        if q is None:
            return {"error": "没有分离出文字，请把框选范围收紧到文字周围再试"}
        res = run_match(self.lib, self.atlas, q, text, limit=40)
        mask = q["variants"][res["variant"]]
        colors = dict(q["colors"], fill=q["fills"][res["variant"]] or q["colors"]["fill"])
        buf = io.BytesIO()
        Image.fromarray((mask * 255).astype("uint8")).save(buf, "PNG")
        clean = io.BytesIO()
        clean_image(mask).save(clean, "PNG")
        hid = time.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(2).hex()
        th = img.copy()
        th.thumbnail((480, 240))
        th.convert("RGB").save(os.path.join(HIST_DIR, f"{hid}.png"))
        with self.hist_lock:
            hist = read_json("history.json", [])
            hist.append({"id": hid, "ts": int(time.time()), "text": text, "guess": res["guess"],
                         "top": [r["id"] for r in res["results"][:10]], "chosen": []})
            write_json("history.json", hist[-500:])
        res.update({"hid": hid, "colors": colors, "outlined": bool(q["colors"]["outline"]),
                    "mask": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
                    "clean": "data:image/png;base64," + base64.b64encode(clean.getvalue()).decode()})
        return res

    def choose(self, hid, fid, action, label=""):
        with self.hist_lock:
            hist = read_json("history.json", [])
            for h in hist:
                if h["id"] == hid:
                    h["chosen"].append({"id": fid, "action": action, "label": label, "ts": int(time.time())})
                    break
            write_json("history.json", hist)

    # ------------------------------------------------------------------ watcher
    def _signature(self):
        sig = []
        for p in iter_font_files(self.cfg["sources"]):
            try:
                st = os.stat(p)
                sig.append((p, st.st_size, int(st.st_mtime)))
            except OSError:
                pass
        return hash(tuple(sorted(sig)))

    def _unzip_inbox(self):
        inbox = self.cfg["inbox"]
        for fn in os.listdir(inbox):
            if not fn.lower().endswith(".zip"):
                continue
            dest = os.path.join(inbox, os.path.splitext(fn)[0])
            if os.path.exists(dest):
                continue
            try:
                with zipfile.ZipFile(os.path.join(inbox, fn)) as z:
                    os.makedirs(dest)
                    for info in z.infolist():
                        base = os.path.basename(info.filename)
                        if base.lower().endswith(online.FONT_EXT) and "__MACOSX" not in info.filename \
                                and not base.startswith("."):
                            with z.open(info) as s, open(os.path.join(dest, base), "wb") as o:
                                o.write(s.read())
            except (zipfile.BadZipFile, OSError):
                pass

    def watch(self, every=8):
        last = self._signature()
        while True:
            time.sleep(every)
            try:
                self._unzip_inbox()
                sig = self._signature()
                if sig != last:
                    time.sleep(2)
                    if self._signature() == sig:
                        self.rescan()
                        last = sig
            except Exception:
                traceback.print_exc()


APP = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype=None, cache=True):
        with open(path, "rb") as f:
            data = f.read()
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        self._send(200, data, ctype, {"Cache-Control": "max-age=31536000" if cache else "no-cache"})

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _local(self):
        return self.client_address[0] in ("127.0.0.1", "::1")

    @staticmethod
    def _image(data_url):
        raw = base64.b64decode(data_url.split(",", 1)[-1])
        return Image.open(io.BytesIO(raw)).convert("RGB")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        p = u.path
        try:
            if p == "/" or p.startswith("/web/"):
                name = "index.html" if p == "/" else p[len("/web/"):]
                full = os.path.normpath(os.path.join(WEB, name))
                if not full.startswith(WEB) or not os.path.isfile(full):
                    return self._send(404, {"error": "not found"})
                return self._file(full, cache=False)
            if p == "/api/fonts":
                return self._send(200, APP.fonts_json(), headers={"Content-Encoding": "gzip"})
            if p == "/api/user":
                return self._send(200, read_json("user.json", {}))
            if p == "/api/status":
                return self._send(200, {"version": APP.version, "status": APP.status, "added": APP.last_added,
                                        "local": self._local()})
            if p.startswith("/font/"):
                fid = p[len("/font/"):]
                if fid not in APP.lib.by_id or APP.lib.by_id[fid].get("ref"):
                    return self._send(404, {"error": "no such font"})
                text = qs.get("text", [None])[0]
                path, ctype = APP.font_file(fid, text)
                return self._file(path, ctype)
            if p.startswith("/render/"):
                fid = p[len("/render/"):]
                text = qs.get("text", ["Aa"])[0][:60] or "Aa"
                size = min(200, int(qs.get("size", ["64"])[0]))
                return self._send(200, APP.render_png(fid, text, size), "image/png")
            if p == "/api/history":
                return self._send(200, list(reversed(read_json("history.json", []))))
            if p.startswith("/hist/"):
                full = os.path.join(HIST_DIR, os.path.basename(p))
                return self._file(full, "image/png") if os.path.isfile(full) else self._send(404, b"", "text/plain")
            if p == "/api/dafont/cats":
                return self._send(200, online.categories())
            return self._send(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": str(e)})

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        try:
            body = self._json_body()
            if p == "/api/user":
                write_json("user.json", body)
                return self._send(200, {"ok": True})
            if p in ("/api/reveal", "/api/install"):
                if not self._local():
                    return self._send(403, {"ok": False, "msg": "只能在运行工具的电脑上操作"})
                fid = body["id"]
                if APP.lib.by_id[fid].get("ref"):
                    return self._send(200, {"ok": False, "msg": "这个字体只附带了识别数据，没有字体文件，请自行获取授权后安装"})
                if p == "/api/reveal":
                    subprocess.run(["open", "-R", APP.lib.by_id[fid]["path"]])
                    return self._send(200, {"ok": True})
                res = APP.install(fid)
                if body.get("hid"):
                    APP.choose(body["hid"], fid, "安装")
                return self._send(200, res)
            if p == "/api/choose":
                APP.choose(body["hid"], body["id"], body.get("action", ""))
                return self._send(200, {"ok": True})
            if p == "/api/ocr":
                items = APP.ocr(self._image(body["image"]))
                return self._send(200, {"items": items})
            if p == "/api/match":
                return self._send(200, APP.match(self._image(body["image"]), body.get("text", "")))
            if p == "/api/rescan":
                APP.rescan()
                return self._send(200, {"ok": True, "version": APP.version})
            return self._send(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": str(e)})


def main():
    global APP
    APP = App()
    cfg = APP.cfg
    threading.Thread(target=APP.watch, daemon=True).start()
    host = "0.0.0.0" if cfg.get("lan") else "127.0.0.1"
    port = int(cfg.get("port", 8765))
    srv = ThreadingHTTPServer((host, port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"字体速查已启动：{url}  （关闭此窗口即停止）")
    if "--open" in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
