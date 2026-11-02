"""Download Google Fonts' Latin Display + Handwriting families into ./google-fonts (OFL/Apache/UFL,
commercial use allowed). Re-running only fetches what is missing.

    .venv/bin/python -m fontfinder.gfonts
"""
import concurrent.futures
import json
import os
import re
import sys
import urllib.request

from .common import ROOT

DEST = os.path.join(ROOT, "google-fonts")
META_URL = "https://fonts.google.com/metadata/fonts"
TREE_URL = "https://api.github.com/repos/google/fonts/git/trees/main?recursive=1"
RAW = "https://raw.githubusercontent.com/google/fonts/main/"
CATEGORIES = ("Display", "Handwriting")
LICENSE_FILES = ("OFL.txt", "LICENSE.txt", "UFL.txt")


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "fontfinder"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _key(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def plan():
    """[(family meta, [repo paths])] for the wanted families: upright files, variable font preferred."""
    meta = json.loads(_get(META_URL))
    tree = json.loads(_get(TREE_URL, 120))
    dirs = {}
    for e in tree["tree"]:
        m = re.match(r"(ofl|apache|ufl)/([^/]+)/([^/]+)$", e["path"])
        if m:
            dirs.setdefault(m.group(2), []).append(e["path"])
    out = []
    for f in meta["familyMetadataList"]:
        if f["category"] not in CATEGORIES or "latin" not in f.get("subsets", []) or f.get("isNoto"):
            continue
        paths = dirs.get(_key(f["family"]), [])
        fonts = [p for p in paths if p.endswith(".ttf")]
        fonts = [p for p in fonts if "Italic" not in p] or fonts
        pick = ([p for p in fonts if "[" in p] or [p for p in fonts if re.search(r"-(Regular|Bold)\.ttf$", p)]
                or fonts[:1])
        if pick:
            lic = [p for p in paths if os.path.basename(p) in LICENSE_FILES]
            out.append((f, pick + lic[:1]))
    return out


def download(verbose=True):
    items = plan()
    jobs = []
    for fam, paths in items:
        d = os.path.join(DEST, paths[0].split("/")[1])
        os.makedirs(d, exist_ok=True)
        side = os.path.join(d, "_google.json")
        if not os.path.exists(side):
            with open(side, "w", encoding="utf-8") as fh:
                json.dump({"family": fam["family"], "category": fam["category"],
                           "license": paths[0].split("/")[0].upper(),
                           "url": "https://fonts.google.com/specimen/" + fam["family"].replace(" ", "+")},
                          fh, ensure_ascii=False)
        for p in paths:
            dst = os.path.join(d, os.path.basename(p))
            if not os.path.exists(dst):
                jobs.append((p, dst))

    def fetch(job):
        p, dst = job
        try:
            data = _get(RAW + urllib.request.quote(p))
        except Exception as e:
            return f"{p}: {e}"
        with open(dst + ".tmp", "wb") as fh:
            fh.write(data)
        os.replace(dst + ".tmp", dst)
        return None

    errors = []
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        for i, err in enumerate(ex.map(fetch, jobs), 1):
            if err:
                errors.append(err)
            if verbose and (i % 100 == 0 or i == len(jobs)):
                print(f"  {i}/{len(jobs)}", flush=True)
    if verbose:
        print(f"Google Fonts：{len(items)} 个家族，本次下载 {len(jobs) - len(errors)} 个文件，失败 {len(errors)} 个")
        for e in errors[:10]:
            print("  失败：", e)
    return errors


if __name__ == "__main__":
    sys.exit(1 if download() else 0)
