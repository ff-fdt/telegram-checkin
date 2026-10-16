from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import traceback
import webbrowser
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import local_runtime
import tg_checkin


SCRIPT_DIR = Path(__file__).resolve().parent
STATIC_DIR = SCRIPT_DIR / "webui"
HOST = "127.0.0.1"
PORT = 8766
SCHEDULER_STATE_PATH = local_runtime.SCHEDULER_STATE_PATH


def load_scheduler_state() -> dict[str, dict]:
    if not SCHEDULER_STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(SCHEDULER_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return payload if isinstance(payload, dict) else {}


def next_eligible_run(task: dict, values: dict[str, str], scheduler_state: dict[str, dict]) -> datetime:
    config = tg_checkin.config_from_values(local_runtime.task_to_config_values(values, task))
    now = datetime.now(config.timezone)
    run_at = now.replace(
        hour=config.run_time.hour,
        minute=config.run_time.minute,
        second=0,
        microsecond=0,
    )
    last_success_date = str(scheduler_state.get(task["id"], {}).get("last_success_date", "") or "")
    current_day = now.date().isoformat()
    if run_at <= now or last_success_date == current_day:
        run_at += timedelta(days=1)
    return run_at


def build_state_payload() -> dict:
    values = local_runtime.load_env_values()
    tasks = local_runtime.get_effective_tasks(values)
    session_ok = local_runtime.has_saved_session(values)
    timezone_mode = values.get("TG_TIMEZONE", "") or local_runtime.FIELD_DEFAULTS["TG_TIMEZONE"]
    effective_timezone = tg_checkin.timezone_display_name(timezone_mode)
    scheduler_state = load_scheduler_state()

    next_runs = []
    for task in tasks:
        if not task.get("enabled"):
            continue
        try:
            run_at = next_eligible_run(task, values, scheduler_state)
            next_runs.append(
                {
                    "task_id": task["id"],
                    "title": task.get("title") or task.get("target"),
                    "next_run": run_at.isoformat(),
                }
            )
        except Exception as exc:  # noqa: BLE001
            next_runs.append(
                {
                    "task_id": task["id"],
                    "title": task.get("title") or task.get("target"),
                    "error": str(exc),
                }
            )

    enabled = [item for item in next_runs if "next_run" in item]
    nearest = min(enabled, key=lambda item: item["next_run"]) if enabled else None

    return {
        "values": values,
        "tasks": tasks,
        "test_slot": local_runtime.load_test_slot(),
        "session_ok": session_ok,
        "timezone_mode": timezone_mode,
        "effective_timezone": effective_timezone,
        "service_status": local_runtime.get_launch_agent_status(),
        "next_runs": next_runs,
        "nearest_run": nearest,
    }


def resolve_request_values(payload_values: dict | None) -> dict[str, str]:
    values = local_runtime.load_env_values()
    protected_if_blank = {
        "TG_API_ID",
        "TG_API_HASH",
        "TG_PHONE",
        "TG_SESSION",
        "TG_TDESKTOP_PATH",
    }
    if payload_values:
        for key, value in payload_values.items():
            name = str(key)
            text = "" if value is None else str(value)
            if text == "" and name in protected_if_blank and values.get(name):
                continue
            values[name] = text
    return local_runtime.validate_global_values(values)


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "TelegramCheckinWeb/1.0"

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            hosts.update({"127.0.0.1", "localhost"})
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        if (
            host not in hosts
            or (origin is not None and origin != f"http://{host}")
            or self.headers.get("Sec-Fetch-Site") == "cross-site"
        ):
            self.close_connection = True
            self.send_error(HTTPStatus.FORBIDDEN, "Use the local dashboard origin")
            return False
        return True

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_HEAD(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self.serve_static("index.html", head_only=True)
            return
        if path == "/styles.css":
            self.serve_static("styles.css", head_only=True)
            return
        if path == "/app.js":
            self.serve_static("app.js", head_only=True)
            return
        if path == "/favicon.png":
            self.serve_static("favicon.png", head_only=True)
            return
        if path == "/favicon.svg":
            self.serve_static("favicon.svg", head_only=True)
            return
        if path == "/api/health":
            self.write_json({"ok": True, "app": "telegram-checkin"}, head_only=True)
            return
        if path == "/api/state":
            self.write_json({"ok": True}, head_only=True)
            return
        if path == "/api/logs":
            self.write_json({"ok": True}, head_only=True)
            return
        self.write_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND, head_only=True)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self.serve_static("index.html")
            return
        if path == "/styles.css":
            self.serve_static("styles.css")
            return
        if path == "/app.js":
            self.serve_static("app.js")
            return
        if path == "/favicon.png":
            self.serve_static("favicon.png")
            return
        if path == "/favicon.svg":
            self.serve_static("favicon.svg")
            return
        if path == "/api/health":
            self.write_json({"ok": True, "app": "telegram-checkin"})
            return
        if path == "/api/state":
            self.write_json({"ok": True, "state": build_state_payload()})
            return
        if path == "/api/logs":
            self.write_json({"ok": True, "logs": local_runtime.tail_logs()})
            return
        self.write_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            data = self.read_json()
            if path == "/api/config/save":
                values = resolve_request_values(data.get("values"))
                local_runtime.save_env_values(values)
                local_runtime.record_event(f"已保存统一签到时间：{values.get('TG_TIME', '')}", source="ui")
                self.write_json({"ok": True, "message": "配置已保存。", "state": build_state_payload()})
                return
            if path == "/api/test-slot/save":
                values = resolve_request_values(data.get("values"))
                local_runtime.save_env_values(values)
                time_text = str(data.get("time", "") or "").strip()
                if not time_text:
                    raise ValueError("请先填写临时测试时间。")
                payload = local_runtime.schedule_test_slot(values, time_text)
                local_runtime.record_event(f"已设置临时测试时间：{payload.get('run_at')}", source="ui")
                self.write_json({"ok": True, "message": "临时测试时间已保存。", "state": build_state_payload()})
                return
            if path == "/api/test-slot/clear":
                local_runtime.clear_test_slot()
                local_runtime.record_event("已清除临时测试时间。", source="ui")
                self.write_json({"ok": True, "message": "临时测试时间已清除。", "state": build_state_payload()})
                return
            if path == "/api/account/auto-import":
                values = resolve_request_values(data.get("values"))
                updated = asyncio.run(local_runtime.import_from_tdesktop(values))
                local_runtime.save_env_values(updated)
                dialogs = asyncio.run(local_runtime.list_available_dialogs(updated))
                local_runtime.record_event(f"已自动导入本机 Telegram，并读取 {len(dialogs)} 个置顶目标。", source="ui")
                self.write_json(
                    {
                        "ok": True,
                        "message": f"已自动导入本机 Telegram，已读取 {len(dialogs)} 个置顶目标。",
                        "dialogs": dialogs,
                        "state": build_state_payload(),
                    }
                )
                return
            if path == "/api/login/send-code":
                values = resolve_request_values(data.get("values"))
                local_runtime.save_env_values(values)
                asyncio.run(local_runtime.send_login_code(values))
                self.write_json({"ok": True, "message": "验证码已发送，请查看 Telegram。", "state": build_state_payload()})
                return
            if path == "/api/login/complete":
                values = resolve_request_values(data.get("values"))
                local_runtime.save_env_values(values)
                username = asyncio.run(local_runtime.complete_login(values, data.get("code", ""), data.get("password", "")))
                self.write_json({"ok": True, "message": f"登录完成：{username}", "state": build_state_payload()})
                return
            if path == "/api/dialogs/load":
                values = resolve_request_values(data.get("values"))
                dialogs = asyncio.run(local_runtime.list_available_dialogs(values))
                local_runtime.record_event(f"已读取 {len(dialogs)} 个置顶目标。", source="ui")
                self.write_json({"ok": True, "message": f"已读取 {len(dialogs)} 个置顶目标。", "dialogs": dialogs, "state": build_state_payload()})
                return
            if path == "/api/tasks/import":
                values = resolve_request_values(data.get("values"))
                dialogs = data.get("dialogs", [])
                tasks = local_runtime.load_tasks()
                known = {task["id"] for task in tasks}
                added = 0
                for dialog in dialogs:
                    task_id = f"dialog:{dialog['id']}"
                    if task_id in known:
                        continue
                    tasks.append(
                        local_runtime.default_task_from_values(
                            values,
                            target=dialog["target"],
                            title=dialog["title"],
                            kind=dialog["target_kind"],
                            task_id=task_id,
                            peer_type=str(dialog.get("peer_type", "") or ""),
                            peer_id=str(dialog.get("peer_id", "") or ""),
                            peer_access_hash=str(dialog.get("peer_access_hash", "") or ""),
                        )
                    )
                    known.add(task_id)
                    added += 1
                local_runtime.save_tasks(tasks)
                local_runtime.record_event(f"新增 {added} 个任务。", source="ui")
                self.write_json({"ok": True, "message": f"新增 {added} 个任务。", "state": build_state_payload()})
                return
            if path == "/api/tasks/save":
                values = resolve_request_values(data.get("values"))
                local_runtime.save_env_values(values)
                task = local_runtime.validate_task(values, data["task"])
                tasks = local_runtime.load_tasks()
                updated = False
                for index, current in enumerate(tasks):
                    if current["id"] == task["id"]:
                        tasks[index] = task
                        updated = True
                        break
                if not updated:
                    tasks.append(task)
                local_runtime.save_tasks(tasks)
                local_runtime.record_event(f"已保存任务：{task.get('title') or task.get('target')}", source="ui")
                self.write_json({"ok": True, "message": "任务已保存。", "state": build_state_payload()})
                return
            if path == "/api/tasks/delete":
                ids = set(data.get("ids", []))
                tasks = [task for task in local_runtime.load_tasks() if task["id"] not in ids]
                local_runtime.save_tasks(tasks)
                local_runtime.record_event(f"已删除 {len(ids)} 个任务。", source="ui")
                self.write_json({"ok": True, "message": f"已移除 {len(ids)} 个任务。", "state": build_state_payload()})
                return
            if path == "/api/tasks/toggle":
                ids = set(data.get("ids", []))
                tasks = local_runtime.load_tasks()
                for task in tasks:
                    if task["id"] in ids:
                        task["enabled"] = not bool(task.get("enabled"))
                local_runtime.save_tasks(tasks)
                local_runtime.record_event(f"已切换 {len(ids)} 个任务的启用状态。", source="ui")
                self.write_json({"ok": True, "message": "已切换任务状态。", "state": build_state_payload()})
                return
            if path == "/api/tasks/run":
                values = resolve_request_values(data.get("values"))
                local_runtime.save_env_values(values)
                task_ids = data.get("ids", [])
                args = ["--once"]
                for task_id in task_ids:
                    args.extend(["--task-id", task_id])
                result = local_runtime.run_service_command(args)
                output = (result.stdout + "\n" + result.stderr).strip()
                if result.returncode != 0:
                    lines = [line.strip() for line in output.splitlines() if line.strip()]
                    raise RuntimeError(lines[-1] if lines else f"执行失败，退出码 {result.returncode}")
                local_runtime.record_event(f"已手动触发 {len(task_ids)} 个任务。", source="ui")
                self.write_json(
                    {
                        "ok": True,
                        "message": f"已手动触发 {len(task_ids)} 个任务。",
                        "state": build_state_payload(),
                        "logs": local_runtime.tail_logs(),
                    }
                )
                return
            if path == "/api/service/install-start":
                local_runtime.install_and_start_launch_agent()
                self.write_json({"ok": True, "message": "后台已安装并启动。", "state": build_state_payload()})
                return
            if path == "/api/service/stop":
                local_runtime.stop_launch_agent()
                self.write_json({"ok": True, "message": "后台已停止。", "state": build_state_payload()})
                return
            if path == "/api/service/uninstall":
                local_runtime.uninstall_launch_agent()
                self.write_json({"ok": True, "message": "后台已卸载。", "state": build_state_payload()})
                return
            self.write_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            local_runtime.record_event(f"操作失败：{exc}", source="ui")
            self.write_json(
                {
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
                status=HTTPStatus.BAD_REQUEST,
            )

    def serve_static(self, name: str, head_only: bool = False) -> None:
        path = STATIC_DIR / name
        if not path.exists():
            self.write_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND, head_only=head_only)
            return
        content = path.read_bytes()
        mime, _ = mimetypes.guess_type(path.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_no_cache_headers()
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                return

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def write_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK, head_only: bool = False) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_no_cache_headers()
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                return

    def send_no_cache_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Telegram check-in web UI")
    parser.add_argument("--host", default=HOST, choices=("127.0.0.1", "localhost"), help="local host to bind")
    parser.add_argument("--port", type=int, default=PORT, help="port to bind")
    parser.add_argument("--no-open", action="store_true", help="do not open browser automatically")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"本地控制台已启动：{url}")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
