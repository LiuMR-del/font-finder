import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "cache")
BIN = os.path.join(ROOT, "bin")
WEB = os.path.join(ROOT, "web")

for d in (DATA, CACHE):
    os.makedirs(d, exist_ok=True)


def load_config():
    """config.json (shared) plus optional config.local.json (per machine, not in git): its "sources"
    are appended, other keys override."""
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    local = os.path.join(ROOT, "config.local.json")
    if os.path.exists(local):
        with open(local, encoding="utf-8") as f:
            extra = json.load(f)
        cfg["sources"] = cfg.get("sources", []) + extra.pop("sources", [])
        cfg.update(extra)
    cfg["sources"] = [_abs(p) for p in cfg.get("sources", [])]
    cfg["inbox"] = _abs(cfg.get("inbox", "./新下载字体"))
    os.makedirs(cfg["inbox"], exist_ok=True)
    return cfg


def _abs(p):
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(ROOT, p))


_lock = threading.Lock()


def read_json(name, default):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(name, obj):
    path = os.path.join(DATA, name)
    tmp = path + ".tmp"
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
