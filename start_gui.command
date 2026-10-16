#!/bin/zsh

cd "$(dirname "$0")" || exit 1

if [[ -x ".venv/bin/python3" ]]; then
  exec .venv/bin/python3 checkin_gui.py
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 checkin_gui.py
fi

echo "没找到 python3。"
echo "请先安装 Python 3，然后重新双击这个文件。"
read -k 1
