#!/bin/bash
# 手动重新扫描（平时不需要：工具运行时会自动发现新字体）
cd "$(dirname "$0")" || exit 1
PORT=$(.venv/bin/python -c "import json;print(json.load(open('config.json')).get('port',8765))")
if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/status"; then
  curl -s -X POST -d '{}' "http://127.0.0.1:$PORT/api/rescan" && echo && echo "已通知运行中的字体速查重新扫描"
else
  .venv/bin/python -m fontfinder.scan 2> >(grep -v "extra bytes\|too much" >&2)
fi
read -r -p "按回车关闭"
