from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import local_runtime


HOST = "127.0.0.1"
PORT = 8766
URL = f"http://{HOST}:{PORT}"
SCRIPT_DIR = Path(__file__).resolve().parent
WEB_STDOUT = local_runtime.LOG_DIR / "webui.stdout.log"
WEB_STDERR = local_runtime.LOG_DIR / "webui.stderr.log"


def start_server() -> subprocess.Popen:
    local_runtime.ensure_log_dir()
    stdout_handle = WEB_STDOUT.open("a", encoding="utf-8")
    stderr_handle = WEB_STDERR.open("a", encoding="utf-8")
    return subprocess.Popen(
        [local_runtime.python_executable(), str(SCRIPT_DIR / "web_ui.py"), "--no-open"],
        cwd=SCRIPT_DIR,
        stdout=stdout_handle,
        stderr=stderr_handle,
        start_new_session=True,
        close_fds=True,
    )


def wait_until_ready(host: str, port: int, retries: int = 40, delay: float = 0.2) -> bool:
    for _ in range(retries):
        if local_runtime.is_web_port_open():
            return True
        time.sleep(delay)
    return False


def read_log_tail(path: Path, limit: int = 2000) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-limit:].strip()


def startup_failure_message() -> str:
    stderr_text = read_log_tail(WEB_STDERR)
    if "PermissionError: [Errno 1] Operation not permitted" in stderr_text:
        return "本地服务启动失败：当前环境不允许监听 127.0.0.1 端口。请直接在终端里运行 start_web.command。"
    if "Address already in use" in stderr_text:
        return f"本地服务启动失败：127.0.0.1:{PORT} 已被其他程序占用。"
    if stderr_text:
        last_line = stderr_text.splitlines()[-1].strip()
        return f"本地服务启动失败：{last_line}"
    return "本地服务启动失败，请查看 logs/webui.stderr.log。"


def show_alert(message: str) -> None:
    script = (
        f"display alert {json.dumps('Telegram 自动签到', ensure_ascii=False)} "
        f"message {json.dumps(message, ensure_ascii=False)} as critical"
    )
    subprocess.run(["osascript", "-e", script], check=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start Telegram local web console")
    parser.add_argument("--background", action="store_true", help="start server only, do not open browser")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not local_runtime.is_web_port_open():
        process = start_server()
        if not wait_until_ready(HOST, PORT):
            process.terminate()
            message = startup_failure_message()
            print(message, file=sys.stderr)
            if not args.background:
                show_alert(message)
            return 1
        local_runtime.record_event("本地网页服务已启动。", source="web")
    if not args.background:
        webbrowser.open(URL)
        local_runtime.record_event("已打开本地控制台页面。", source="web")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
