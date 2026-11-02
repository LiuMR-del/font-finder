#!/bin/bash
# 双击启动字体速查：首次运行会自动安装依赖、扫描字体（约 1 分钟）
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "首次运行：正在安装依赖…"
  /usr/bin/python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip &&
    .venv/bin/pip install -q fonttools pillow numpy brotli opencv-python-headless || { echo "依赖安装失败"; read -r; exit 1; }
fi
if [ ! -x bin/ocr ] && command -v swiftc >/dev/null; then
  mkdir -p bin && swiftc -O fontfinder/ocr.swift -o bin/ocr
fi
PORT=$(.venv/bin/python -c "import json;print(json.load(open('config.json')).get('port',8765))")
if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/status"; then
  open "http://127.0.0.1:$PORT/"; exit 0
fi
exec .venv/bin/python -m fontfinder.server --open 2> >(grep -v "extra bytes\|too much" >&2)
