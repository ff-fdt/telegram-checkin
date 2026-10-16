#!/bin/zsh

cd "$(dirname "$0")" || exit 1

PYTHON_CMD=""

if [[ -x ".venv/bin/python3" ]]; then
  PYTHON_CMD=".venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="python3"
fi

if [[ -z "$PYTHON_CMD" ]]; then
  echo "没找到 python3。"
  echo "请先安装 Python 3，然后重新双击这个文件。"
  read -k 1
  exit 1
fi

"$PYTHON_CMD" web_launcher.py
STATUS=$?

if [[ $STATUS -ne 0 ]]; then
  echo
  echo "启动失败。"
  if [[ -f "logs/webui.stderr.log" ]]; then
    echo
    echo "===== logs/webui.stderr.log ====="
    tail -n 40 "logs/webui.stderr.log"
  fi
  echo
  echo "按任意键关闭窗口。"
  read -k 1
fi

exit $STATUS
