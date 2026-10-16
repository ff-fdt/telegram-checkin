from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import http.client
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from telethon import TelegramClient, errors
from telethon.sessions import MemorySession

try:
    from opentele2.api import UseCurrentSession
    from opentele2.td import TDesktop
    from opentele2.tl import TelegramClient as OpenTeleClient
except ImportError:  # Linux production uses a standard Telethon session.
    UseCurrentSession = None
    TDesktop = None
    OpenTeleClient = None

import tg_checkin


PROJECT_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = PROJECT_DIR
IS_LINUX = sys.platform.startswith("linux")
DEFAULT_APP_SUPPORT_DIR = (
    Path.home() / ".local" / "share" / "telegram-checkin"
    if IS_LINUX
    else Path.home() / "Library" / "Application Support" / "TelegramCheckin"
)
APP_SUPPORT_DIR = Path(os.environ.get("TG_CHECKIN_HOME", DEFAULT_APP_SUPPORT_DIR)).expanduser()
RUNTIME_DIR = Path(os.environ.get("TG_CHECKIN_RUNTIME", APP_SUPPORT_DIR / "runtime")).expanduser()
DATA_DIR = Path(os.environ.get("TG_CHECKIN_DATA", APP_SUPPORT_DIR / "data")).expanduser()
ENV_PATH = DATA_DIR / ".env"
TASKS_PATH = DATA_DIR / "tasks.json"
LOG_DIR = DATA_DIR / "logs"
SERVICE_STDOUT_PATH = LOG_DIR / "service.stdout.log"
SERVICE_STDERR_PATH = LOG_DIR / "service.stderr.log"
WEB_SERVICE_STDOUT_PATH = LOG_DIR / "webui.stdout.log"
WEB_SERVICE_STDERR_PATH = LOG_DIR / "webui.stderr.log"
WEB_WATCHDOG_STDOUT_PATH = LOG_DIR / "webwatch.stdout.log"
WEB_WATCHDOG_STDERR_PATH = LOG_DIR / "webwatch.stderr.log"
EVENT_LOG_PATH = LOG_DIR / "events.log"
LOGIN_STATE_PATH = DATA_DIR / ".login_state.json"
SCHEDULER_HEARTBEAT_PATH = DATA_DIR / ".scheduler_heartbeat.json"
SCHEDULER_STATE_PATH = DATA_DIR / ".scheduler_state.json"
TEST_SLOT_PATH = DATA_DIR / ".test_slot.json"
SERVICE_LABEL = "local.telegram.checkin"
WEB_SERVICE_LABEL = "local.telegram.checkin.webui"
WEB_WATCHDOG_LABEL = "local.telegram.checkin.webwatch"
SYSTEMD_SCHEDULER_UNIT = "telegram-checkin-scheduler.service"
SYSTEMD_WEB_UNIT = "telegram-checkin-web.service"
SYSTEMD_WATCHDOG_UNIT = "telegram-checkin-watchdog.service"
WEB_SERVICE_HOST = "127.0.0.1"
WEB_SERVICE_PORT = 8766
LOGIN_ITEM_NAME = "Telegram 自动签到后台"
LOGIN_ITEM_BUNDLE_ID = "local.telegram.checkin.loginitem"
LOGIN_ITEM_APP_PATH = PROJECT_DIR / f"{LOGIN_ITEM_NAME}.app"
SCHEDULER_STALE_SECONDS = 180.0

RUNTIME_FILE_NAMES = (
    "local_runtime.py",
    "service_runner.py",
    "tg_checkin.py",
    "web_launcher.py",
    "web_ui.py",
    "web_watchdog.py",
    "requirements.txt",
)
LEGACY_DATA_FILE_NAMES = (
    ".env",
    "tasks.json",
    ".login_state.json",
    ".scheduler_heartbeat.json",
    ".scheduler_state.json",
    ".test_slot.json",
)

FIELD_DEFAULTS = {
    "TG_API_ID": "",
    "TG_API_HASH": "",
    "TG_PHONE": "",
    "TG_SESSION": "checkin",
    "TG_TDESKTOP_PATH": "",
    "TG_TARGET": "",
    "TG_SIGNIN_TEXT": "/checkin",
    "TG_TIME": "09:00",
    "TG_TIMEZONE": "SYSTEM",
    "TG_JITTER_SECONDS": "0",
    "TG_REPLY_WAIT_SECONDS": "8",
    "TG_PROXY_TYPE": "",
    "TG_PROXY_HOST": "",
    "TG_PROXY_PORT": "",
    "TG_BUTTON_TEXT": "",
    "TG_BUTTON_ROW": "",
    "TG_BUTTON_COL": "",
}

FIELD_ORDER = list(FIELD_DEFAULTS)
TASK_DEFAULTS = {
    "id": "",
    "title": "",
    "target": "",
    "target_label": "",
    "target_kind": "other",
    "peer_type": "",
    "peer_id": "",
    "peer_access_hash": "",
    "enabled": True,
    "signin_text": "/checkin",
    "time": "09:00",
    "timezone": "SYSTEM",
    "jitter_seconds": "0",
    "reply_wait_seconds": "8",
    "button_text": "",
    "button_row": "",
    "button_col": "",
}


def ensure_data_layout() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATA_DIR.chmod(0o700)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if PROJECT_DIR == DATA_DIR or PROJECT_DIR == RUNTIME_DIR:
        return

    for name in LEGACY_DATA_FILE_NAMES:
        source = PROJECT_DIR / name
        destination = DATA_DIR / name
        if source.exists() and not destination.exists():
            shutil.copy2(source, destination)

    for pattern in ("*.session", "*.session-journal", "*.session-wal", "*.session-shm"):
        for source in PROJECT_DIR.glob(pattern):
            destination = DATA_DIR / source.name
            if not destination.exists():
                shutil.copy2(source, destination)

    legacy_log_dir = PROJECT_DIR / "logs"
    if legacy_log_dir.exists():
        for source in legacy_log_dir.iterdir():
            destination = LOG_DIR / source.name
            if source.is_file() and not destination.exists():
                shutil.copy2(source, destination)


def ensure_log_dir() -> None:
    ensure_data_layout()


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def deploy_runtime() -> Path:
    ensure_data_layout()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if PROJECT_DIR == RUNTIME_DIR:
        return RUNTIME_DIR

    for name in RUNTIME_FILE_NAMES:
        source = PROJECT_DIR / name
        if source.exists():
            shutil.copy2(source, RUNTIME_DIR / name)

    shutil.copytree(PROJECT_DIR / "webui", RUNTIME_DIR / "webui", dirs_exist_ok=True)
    runtime_python = RUNTIME_DIR / ".venv" / "bin" / "python3"
    if not runtime_python.exists():
        shutil.copytree(PROJECT_DIR / ".venv", RUNTIME_DIR / ".venv", symlinks=True)
    return RUNTIME_DIR


def record_event(message: str, source: str = "system") -> None:
    ensure_log_dir()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with EVENT_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} | {source} | {message}\n")


def write_scheduler_heartbeat(phase: str, **extra: Any) -> None:
    ensure_data_layout()
    payload: dict[str, Any] = {
        "updated_at": time.time(),
        "updated_at_iso": datetime.now().isoformat(),
        "phase": phase,
    }
    payload.update(extra)
    atomic_write_text(SCHEDULER_HEARTBEAT_PATH, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def load_scheduler_heartbeat() -> dict[str, Any]:
    ensure_data_layout()
    if not SCHEDULER_HEARTBEAT_PATH.exists():
        return {}
    try:
        payload = json.loads(SCHEDULER_HEARTBEAT_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return payload if isinstance(payload, dict) else {}


def scheduler_heartbeat_age_seconds(now_ts: float | None = None) -> float | None:
    payload = load_scheduler_heartbeat()
    updated_at = payload.get("updated_at")
    if updated_at in (None, ""):
        return None
    try:
        updated_at_value = float(updated_at)
    except (TypeError, ValueError):
        return None
    current = time.time() if now_ts is None else now_ts
    return max(current - updated_at_value, 0.0)


def scheduler_is_stale(max_age_seconds: float = SCHEDULER_STALE_SECONDS) -> bool:
    age = scheduler_heartbeat_age_seconds()
    return age is None or age > max_age_seconds


def load_env_values() -> dict[str, str]:
    ensure_data_layout()
    values = dict(FIELD_DEFAULTS)
    if ENV_PATH.exists():
        raw = dotenv_values(ENV_PATH)
        for key in FIELD_ORDER:
            value = raw.get(key)
            if value is not None:
                values[key] = value
    return values


def load_test_slot() -> dict[str, Any]:
    ensure_data_layout()
    if not TEST_SLOT_PATH.exists():
        return {}
    try:
        payload = json.loads(TEST_SLOT_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return payload if isinstance(payload, dict) else {}


def save_test_slot(payload: dict[str, Any]) -> None:
    ensure_data_layout()
    atomic_write_text(TEST_SLOT_PATH, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def clear_test_slot() -> None:
    if TEST_SLOT_PATH.exists():
        TEST_SLOT_PATH.unlink()


def schedule_test_slot(values: dict[str, str], time_text: str) -> dict[str, Any]:
    normalized = validate_global_values(values)
    timezone_name = normalized.get("TG_TIMEZONE", FIELD_DEFAULTS["TG_TIMEZONE"]) or FIELD_DEFAULTS["TG_TIMEZONE"]
    timezone = tg_checkin.parse_timezone(timezone_name)
    run_time = tg_checkin.parse_time_string(time_text.strip())
    now = datetime.now(timezone)
    run_at = now.replace(hour=run_time.hour, minute=run_time.minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += timedelta(days=1)
    payload = {
        "time": time_text.strip(),
        "run_at": run_at.isoformat(),
        "timezone_mode": timezone_name,
        "effective_timezone": tg_checkin.timezone_display_name(timezone),
        "created_at": datetime.now().isoformat(),
        "active": True,
    }
    save_test_slot(payload)
    return payload


def save_env_values(values: dict[str, str]) -> None:
    ensure_data_layout()
    lines: list[str] = []
    for key in FIELD_ORDER:
        value = values.get(key, "")
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f"{key}='{escaped}'")
    atomic_write_text(ENV_PATH, "\n".join(lines) + "\n")


def values_with_runtime_defaults(values: dict[str, str], target_fallback: str | None = None) -> dict[str, str]:
    payload = dict(FIELD_DEFAULTS)
    for key, value in values.items():
        payload[key] = "" if value is None else str(value)

    payload["TG_SESSION"] = payload.get("TG_SESSION") or FIELD_DEFAULTS["TG_SESSION"]
    payload["TG_SIGNIN_TEXT"] = payload.get("TG_SIGNIN_TEXT") or FIELD_DEFAULTS["TG_SIGNIN_TEXT"]
    payload["TG_TIME"] = payload.get("TG_TIME") or FIELD_DEFAULTS["TG_TIME"]
    payload["TG_TIMEZONE"] = payload.get("TG_TIMEZONE") or FIELD_DEFAULTS["TG_TIMEZONE"]
    payload["TG_JITTER_SECONDS"] = payload.get("TG_JITTER_SECONDS") or FIELD_DEFAULTS["TG_JITTER_SECONDS"]
    payload["TG_REPLY_WAIT_SECONDS"] = payload.get("TG_REPLY_WAIT_SECONDS") or FIELD_DEFAULTS["TG_REPLY_WAIT_SECONDS"]
    if not payload.get("TG_API_ID"):
        payload["TG_API_ID"] = "1"
    if not payload.get("TG_API_HASH"):
        payload["TG_API_HASH"] = "placeholder_hash"
    if target_fallback is not None and not payload.get("TG_TARGET"):
        payload["TG_TARGET"] = target_fallback
    return payload


def build_config(values: dict[str, str], target_fallback: str | None = None) -> tg_checkin.Config:
    return tg_checkin.config_from_values(values_with_runtime_defaults(values, target_fallback=target_fallback))


def ensure_manual_api_credentials(values: dict[str, str]) -> None:
    if has_tdesktop_session(values) or has_session_json(values):
        return
    api_id = str(values.get("TG_API_ID", "") or "").strip()
    api_hash = str(values.get("TG_API_HASH", "") or "").strip()
    if api_id and api_hash:
        return
    raise ValueError("请先点“自动读取本机 Telegram”，或者手动填写 API ID / API HASH。")


def validate_global_values(values: dict[str, str]) -> dict[str, str]:
    normalized = dict(values)
    normalized["TG_SESSION"] = normalized.get("TG_SESSION", "").strip() or FIELD_DEFAULTS["TG_SESSION"]
    normalized["TG_SIGNIN_TEXT"] = normalized.get("TG_SIGNIN_TEXT", "").strip() or FIELD_DEFAULTS["TG_SIGNIN_TEXT"]
    normalized["TG_TIME"] = normalized.get("TG_TIME", "").strip() or FIELD_DEFAULTS["TG_TIME"]
    normalized["TG_TIMEZONE"] = normalized.get("TG_TIMEZONE", "").strip() or FIELD_DEFAULTS["TG_TIMEZONE"]
    normalized["TG_JITTER_SECONDS"] = normalized.get("TG_JITTER_SECONDS", "").strip() or FIELD_DEFAULTS["TG_JITTER_SECONDS"]
    normalized["TG_REPLY_WAIT_SECONDS"] = normalized.get("TG_REPLY_WAIT_SECONDS", "").strip() or FIELD_DEFAULTS["TG_REPLY_WAIT_SECONDS"]
    build_config(normalized, target_fallback="__dummy__")
    return normalized


def default_task_from_values(
    values: dict[str, str],
    target: str,
    title: str,
    kind: str,
    task_id: str,
    peer_type: str = "",
    peer_id: str = "",
    peer_access_hash: str = "",
) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": title,
        "target": target,
        "target_label": title,
        "target_kind": kind,
        "peer_type": peer_type,
        "peer_id": peer_id,
        "peer_access_hash": peer_access_hash,
        "enabled": True,
        "signin_text": values.get("TG_SIGNIN_TEXT", FIELD_DEFAULTS["TG_SIGNIN_TEXT"]).strip() or "/checkin",
        "time": values.get("TG_TIME", FIELD_DEFAULTS["TG_TIME"]).strip() or "09:00",
        "timezone": values.get("TG_TIMEZONE", FIELD_DEFAULTS["TG_TIMEZONE"]).strip() or "SYSTEM",
        "jitter_seconds": values.get("TG_JITTER_SECONDS", FIELD_DEFAULTS["TG_JITTER_SECONDS"]).strip() or "0",
        "reply_wait_seconds": values.get("TG_REPLY_WAIT_SECONDS", FIELD_DEFAULTS["TG_REPLY_WAIT_SECONDS"]).strip() or "8",
        "button_text": values.get("TG_BUTTON_TEXT", "").strip(),
        "button_row": values.get("TG_BUTTON_ROW", "").strip(),
        "button_col": values.get("TG_BUTTON_COL", "").strip(),
    }


def normalize_task(task: dict[str, Any]) -> dict[str, Any]:
    merged = dict(TASK_DEFAULTS)
    merged.update(task)
    merged["id"] = str(merged["id"] or "")
    merged["title"] = str(merged["title"] or "")
    merged["target"] = str(merged["target"] or "")
    merged["target_label"] = str(merged["target_label"] or merged["title"] or merged["target"])
    merged["target_kind"] = str(merged["target_kind"] or "other")
    merged["peer_type"] = str(merged.get("peer_type", "") or "")
    merged["peer_id"] = str(merged.get("peer_id", "") or "")
    merged["peer_access_hash"] = str(merged.get("peer_access_hash", "") or "")
    if not merged["peer_id"] and merged["id"].startswith("dialog:"):
        merged["peer_id"] = merged["id"].split(":", 1)[1]
    if not merged["peer_type"]:
        if merged["target_kind"] == "bot":
            merged["peer_type"] = "user"
        elif merged["target_kind"] == "channel":
            merged["peer_type"] = "channel"
        elif merged["target_kind"] == "group":
            merged["peer_type"] = "chat"
    merged["enabled"] = bool(merged["enabled"])
    for key in (
        "signin_text",
        "time",
        "timezone",
        "jitter_seconds",
        "reply_wait_seconds",
        "button_text",
        "button_row",
        "button_col",
    ):
        merged[key] = str(merged.get(key, "") or "")
    return merged


def load_tasks() -> list[dict[str, Any]]:
    ensure_data_layout()
    if not TASKS_PATH.exists():
        return []
    data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    return [normalize_task(item) for item in data if isinstance(item, dict)]


def save_tasks(tasks: list[dict[str, Any]]) -> None:
    ensure_data_layout()
    normalized = [normalize_task(task) for task in tasks]
    atomic_write_text(TASKS_PATH, json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")


def task_to_config_values(base_values: dict[str, str], task: dict[str, Any]) -> dict[str, str]:
    merged = values_with_runtime_defaults(base_values)
    merged["TG_TARGET"] = str(task["target"])
    merged["TG_SIGNIN_TEXT"] = str(task["signin_text"])
    merged["TG_PEER_TYPE"] = str(task.get("peer_type", "") or "")
    merged["TG_PEER_ID"] = str(task.get("peer_id", "") or "")
    merged["TG_PEER_ACCESS_HASH"] = str(task.get("peer_access_hash", "") or "")
    return merged


def validate_task(base_values: dict[str, str], task: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_task(task)
    if not normalized["target"]:
        raise ValueError("任务目标不能为空。")
    build_config(task_to_config_values(base_values, normalized))
    return normalized


def get_effective_tasks(base_values: dict[str, str]) -> list[dict[str, Any]]:
    tasks = load_tasks()
    if tasks:
        return tasks
    legacy_target = base_values.get("TG_TARGET", "").strip()
    if not legacy_target:
        return []
    return [
        default_task_from_values(
            base_values,
            target=legacy_target,
            title=legacy_target,
            kind="legacy",
            task_id=f"legacy:{legacy_target}",
        )
    ]


def session_base_path(session_name: str) -> Path:
    ensure_data_layout()
    return DATA_DIR / session_name


def session_path(session_name: str) -> Path:
    ensure_data_layout()
    return DATA_DIR / f"{session_name}.session"


def session_json_path(session_name: str) -> Path:
    ensure_data_layout()
    return DATA_DIR / f"{session_name}.json"


def has_saved_session(values: dict[str, str]) -> bool:
    if has_tdesktop_session(values):
        return True
    session_name = values.get("TG_SESSION", FIELD_DEFAULTS["TG_SESSION"]).strip() or FIELD_DEFAULTS["TG_SESSION"]
    return session_path(session_name).exists()


def has_session_json(values: dict[str, str]) -> bool:
    session_name = values.get("TG_SESSION", FIELD_DEFAULTS["TG_SESSION"]).strip() or FIELD_DEFAULTS["TG_SESSION"]
    return session_path(session_name).exists() and session_json_path(session_name).exists()


def has_tdesktop_session(values: dict[str, str]) -> bool:
    path = values.get("TG_TDESKTOP_PATH", "").strip()
    return bool(path and Path(path).expanduser().exists())


def clone_imported_session(values: dict[str, str]) -> tuple[tempfile.TemporaryDirectory[str], str, str]:
    session_name = values.get("TG_SESSION", FIELD_DEFAULTS["TG_SESSION"]).strip() or FIELD_DEFAULTS["TG_SESSION"]
    source_session = session_path(session_name)
    source_json = session_json_path(session_name)
    temp_dir = tempfile.TemporaryDirectory(prefix="tg-checkin-session-")
    temp_base = str(Path(temp_dir.name) / session_name)
    shutil.copy2(source_session, f"{temp_base}.session")
    return temp_dir, temp_base, str(source_json)


def session_sidecar_paths(session_name: str) -> list[Path]:
    base = session_path(session_name)
    return [Path(f"{base}-journal"), Path(f"{base}-wal"), Path(f"{base}-shm")]


def install_imported_session(session_name: str, imported_base: str, imported_json: str) -> None:
    target_session = session_path(session_name)
    target_json = session_json_path(session_name)
    source_session = Path(f"{imported_base}.session")
    source_json = Path(imported_json)

    for sidecar in session_sidecar_paths(session_name):
        if sidecar.exists():
            sidecar.unlink()

    shutil.copy2(source_session, target_session)
    shutil.copy2(source_json, target_json)


def remove_session_artifacts(session_name: str) -> None:
    for path in [session_path(session_name), session_json_path(session_name), *session_sidecar_paths(session_name)]:
        if path.exists():
            path.unlink()


def python_executable(root: Path | None = None) -> str:
    code_root = root or PROJECT_DIR
    candidates = [
        code_root / ".venv" / "bin" / "python3",
        code_root / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def runtime_python_executable() -> str:
    return python_executable(RUNTIME_DIR)


def require_opentele() -> None:
    if TDesktop is None or OpenTeleClient is None or UseCurrentSession is None:
        raise RuntimeError("当前运行环境未安装 Telegram Desktop 会话转换组件。")


def find_tdesktop_paths() -> list[Path]:
    candidates = [
        Path.home() / "Library" / "Application Support" / "Telegram Desktop" / "tdata",
        Path.home() / "Library" / "Containers" / "ru.keepcoder.Telegram" / "Data" / "Library" / "Application Support" / "Telegram Desktop" / "tdata",
    ]
    found: list[Path] = []
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            resolved = candidate.resolve()
            if resolved not in found:
                found.append(resolved)
    return found


async def create_connected_client(values: dict[str, str]):
    if has_tdesktop_session(values):
        require_opentele()
        tdesktop_path = values.get("TG_TDESKTOP_PATH", "").strip()
        tdesk = TDesktop(str(Path(tdesktop_path).expanduser()))
        client = None
        try:
            client = await tdesk.ToTelethon(session=MemorySession(), flag=UseCurrentSession)
            client._tg_tdesktop = tdesk
            await client.connect()
            return client
        except Exception:
            if client is not None:
                await close_client(client)
            raise

    if has_session_json(values):
        require_opentele()
        temp_dir, temp_base, json_path = clone_imported_session(values)
        client = None
        try:
            client = await OpenTeleClient.FromSessionJson(temp_base, json_path=json_path)
            client._tg_temp_session_dir = temp_dir
            await client.connect()
            return client
        except Exception:
            if client is not None:
                await close_client(client)
            else:
                temp_dir.cleanup()
            raise

    ensure_manual_api_credentials(values)
    config = build_config(values, target_fallback="__dummy__")
    client = TelegramClient(config.session, config.api_id, config.api_hash, proxy=config.proxy)
    try:
        await client.connect()
        return client
    except Exception:
        await close_client(client)
        raise


async def create_started_client(values: dict[str, str]):
    if has_tdesktop_session(values):
        client = await create_connected_client(values)
        if not await client.is_user_authorized():
            await close_client(client)
            raise ValueError("本机 Telegram 会话不可用，请重新打开 Telegram Desktop 后再试。")
        return client

    if has_session_json(values):
        client = await create_connected_client(values)
        if not await client.is_user_authorized():
            await close_client(client)
            raise ValueError("导入的 Telegram Desktop 会话不可用，请重新自动导入一次。")
        return client

    ensure_manual_api_credentials(values)
    config = build_config(values, target_fallback="__dummy__")
    client = TelegramClient(config.session, config.api_id, config.api_hash, proxy=config.proxy)
    try:
        await client.start(phone=config.phone)
        return client
    except Exception:
        await close_client(client)
        raise


async def close_client(client) -> None:
    try:
        disconnect = client.disconnect()
        if disconnect:
            await disconnect
        disconnected = getattr(client, "disconnected", None)
        if disconnected is not None:
            await disconnected
    finally:
        session = getattr(client, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
        temp_dir = getattr(client, "_tg_temp_session_dir", None)
        if temp_dir is not None:
            try:
                temp_dir.cleanup()
            except Exception:  # noqa: BLE001
                pass


async def import_from_tdesktop(values: dict[str, str]) -> dict[str, str]:
    require_opentele()
    configured = values.get("TG_TDESKTOP_PATH", "").strip()
    paths: list[Path] = []
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            paths.append(candidate.resolve())

    for path in find_tdesktop_paths():
        if path not in paths:
            paths.append(path)

    if not paths:
        raise ValueError("没找到 Telegram Desktop 的 tdata 目录。")

    session_name = values.get("TG_SESSION", FIELD_DEFAULTS["TG_SESSION"]).strip() or FIELD_DEFAULTS["TG_SESSION"]
    last_error: Exception | None = None

    for tdata_path in paths:
        try:
            tdesk = TDesktop(str(tdata_path))
            client = await tdesk.ToTelethon(session=MemorySession(), flag=UseCurrentSession)
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    raise ValueError("导入成功但会话未授权。")
                me = await client.get_me()
            finally:
                await close_client(client)

            updated = dict(values)
            updated["TG_TDESKTOP_PATH"] = str(tdata_path)
            updated["TG_SESSION"] = session_name
            updated["TG_API_ID"] = str(tdesk.api.api_id)
            updated["TG_API_HASH"] = str(tdesk.api.api_hash)
            updated["TG_SIGNIN_TEXT"] = updated.get("TG_SIGNIN_TEXT", "").strip() or FIELD_DEFAULTS["TG_SIGNIN_TEXT"]
            updated["TG_TIME"] = updated.get("TG_TIME", "").strip() or FIELD_DEFAULTS["TG_TIME"]
            updated["TG_TIMEZONE"] = updated.get("TG_TIMEZONE", "").strip() or FIELD_DEFAULTS["TG_TIMEZONE"]
            updated["TG_JITTER_SECONDS"] = updated.get("TG_JITTER_SECONDS", "").strip() or FIELD_DEFAULTS["TG_JITTER_SECONDS"]
            updated["TG_REPLY_WAIT_SECONDS"] = updated.get("TG_REPLY_WAIT_SECONDS", "").strip() or FIELD_DEFAULTS["TG_REPLY_WAIT_SECONDS"]
            remove_session_artifacts(session_name)
            if getattr(me, "phone", None):
                phone = str(me.phone)
                updated["TG_PHONE"] = phone if phone.startswith("+") else f"+{phone}"
            return updated
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    raise ValueError(f"自动导入失败：{last_error}")


def launch_agent_path() -> Path:
    if IS_LINUX:
        return Path("/etc/systemd/system") / SYSTEMD_SCHEDULER_UNIT
    return Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"


def web_launch_agent_path() -> Path:
    if IS_LINUX:
        return Path("/etc/systemd/system") / SYSTEMD_WEB_UNIT
    return Path.home() / "Library" / "LaunchAgents" / f"{WEB_SERVICE_LABEL}.plist"


def launch_domain() -> str:
    return f"gui/{os.getuid()}"


def launch_target() -> str:
    return f"{launch_domain()}/{SERVICE_LABEL}"


def web_launch_target() -> str:
    return f"{launch_domain()}/{WEB_SERVICE_LABEL}"


def web_watchdog_launch_agent_path() -> Path:
    if IS_LINUX:
        return Path("/etc/systemd/system") / SYSTEMD_WATCHDOG_UNIT
    return Path.home() / "Library" / "LaunchAgents" / f"{WEB_WATCHDOG_LABEL}.plist"


def web_watchdog_launch_target() -> str:
    return f"{launch_domain()}/{WEB_WATCHDOG_LABEL}"


def parse_systemd_status(output: str, unit: str) -> dict[str, Any]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    pid_text = values.get("MainPID", "0")
    try:
        pid = int(pid_text)
    except ValueError:
        pid = 0
    exit_text = values.get("ExecMainStatus", "")
    try:
        exit_status = int(exit_text) if exit_text else None
    except ValueError:
        exit_status = None
    load_state = values.get("LoadState", "not-found")
    active_state = values.get("ActiveState", "inactive")
    return {
        "installed": load_state != "not-found",
        "loaded": active_state == "active",
        "pid": pid or None,
        "last_exit_status": exit_status,
        "unit": unit,
        "active_state": active_state,
        "sub_state": values.get("SubState", "unknown"),
    }


def get_systemd_service_status(unit: str) -> dict[str, Any]:
    result = run_local_command(
        [
            "systemctl",
            "show",
            unit,
            "--property=LoadState,ActiveState,SubState,MainPID,ExecMainStatus",
        ],
        check=False,
    )
    if result.returncode != 0:
        return {
            "installed": False,
            "loaded": False,
            "pid": None,
            "last_exit_status": None,
            "unit": unit,
            "message": result.stderr.strip() or result.stdout.strip(),
        }
    return parse_systemd_status(result.stdout, unit)


def systemctl_control_args(*args: str) -> list[str]:
    prefix = ["systemctl"] if os.geteuid() == 0 else ["sudo", "-n", "systemctl"]
    return [*prefix, *args]


def build_launch_agent_plist(
    label: str = SERVICE_LABEL,
    program_arguments: list[str] | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
    keep_alive: bool = True,
    run_at_load: bool = True,
    start_interval: int | None = None,
    working_directory: Path | None = None,
) -> bytes:
    ensure_log_dir()
    args = program_arguments or [runtime_python_executable(), str(RUNTIME_DIR / "service_runner.py")]
    payload = {
        "Label": label,
        "ProgramArguments": args,
        "WorkingDirectory": str(working_directory or DATA_DIR),
        "RunAtLoad": run_at_load,
        "KeepAlive": keep_alive,
        "ThrottleInterval": 30,
        "StandardOutPath": str(stdout_path or SERVICE_STDOUT_PATH),
        "StandardErrorPath": str(stderr_path or SERVICE_STDERR_PATH),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
        },
    }
    if start_interval is not None:
        payload["StartInterval"] = start_interval
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML)


def install_launch_agent() -> Path:
    deploy_runtime()
    path = launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_launch_agent_plist())
    return path


def install_web_launch_agent() -> Path:
    deploy_runtime()
    path = web_launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        build_launch_agent_plist(
            label=WEB_SERVICE_LABEL,
            program_arguments=[runtime_python_executable(), str(RUNTIME_DIR / "web_ui.py"), "--no-open"],
            stdout_path=WEB_SERVICE_STDOUT_PATH,
            stderr_path=WEB_SERVICE_STDERR_PATH,
        )
    )
    return path


def install_web_watchdog_launch_agent() -> Path:
    deploy_runtime()
    path = web_watchdog_launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        build_launch_agent_plist(
            label=WEB_WATCHDOG_LABEL,
            program_arguments=[runtime_python_executable(), str(RUNTIME_DIR / "web_watchdog.py"), "--loop"],
            stdout_path=WEB_WATCHDOG_STDOUT_PATH,
            stderr_path=WEB_WATCHDOG_STDERR_PATH,
        )
    )
    return path


def ensure_background_login_item_app() -> Path:
    app_path = LOGIN_ITEM_APP_PATH
    macos_dir = app_path / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)

    plist_path = app_path / "Contents" / "Info.plist"
    plist_payload = {
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleExecutable": "TelegramCheckinBackgroundLauncher",
        "CFBundleIdentifier": LOGIN_ITEM_BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": LOGIN_ITEM_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
    }
    plist_path.write_bytes(plistlib.dumps(plist_payload, fmt=plistlib.FMT_XML))

    launcher_path = macos_dir / "TelegramCheckinBackgroundLauncher"
    launcher_path.write_text(
        "#!/bin/zsh\n\n"
        "APP_ROOT=\"$(cd \"$(dirname \"$0\")/../../..\" && pwd)\"\n"
        "WATCHDOG=\"$APP_ROOT/web_watchdog.py\"\n\n"
        "if [[ -x \"$APP_ROOT/.venv/bin/python3\" ]]; then\n"
        "  exec \"$APP_ROOT/.venv/bin/python3\" \"$WATCHDOG\" --loop\n"
        "fi\n\n"
        "if command -v python3 >/dev/null 2>&1; then\n"
        "  exec python3 \"$WATCHDOG\" --loop\n"
        "fi\n\n"
        "exit 1\n",
        encoding="utf-8",
    )
    launcher_path.chmod(0o755)
    return app_path


def run_osascript(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, cwd=DATA_DIR)


def login_item_names() -> list[str]:
    result = run_osascript('tell application "System Events" to get the name of every login item')
    if result.returncode != 0:
        return []
    output = result.stdout.strip()
    if not output:
        return []
    return [item.strip() for item in output.split(",") if item.strip()]


def install_background_login_item() -> Path:
    app_path = ensure_background_login_item_app()
    name_text = json.dumps(LOGIN_ITEM_NAME, ensure_ascii=False)
    path_text = json.dumps(str(app_path), ensure_ascii=False)
    script = (
        'tell application "System Events"\n'
        f"if exists login item {name_text} then\n"
        f"delete login item {name_text}\n"
        "end if\n"
        f"make login item at end with properties {{name:{name_text}, path:{path_text}, hidden:true}}\n"
        "end tell"
    )
    result = run_osascript(script)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "登录项安装失败"
        raise RuntimeError(message)
    return app_path


def remove_background_login_item() -> None:
    name_text = json.dumps(LOGIN_ITEM_NAME, ensure_ascii=False)
    script = (
        'tell application "System Events"\n'
        f"if exists login item {name_text} then\n"
        f"delete login item {name_text}\n"
        "end if\n"
        "end tell"
    )
    run_osascript(script)


def is_web_port_open() -> bool:
    connection = None
    try:
        connection = http.client.HTTPConnection(WEB_SERVICE_HOST, WEB_SERVICE_PORT, timeout=2.0)
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        if response.status != 200:
            return False
        payload = response.read(256)
        has_ok = b'"ok": true' in payload or b'"ok":true' in payload
        has_app = b'"app": "telegram-checkin"' in payload or b'"app":"telegram-checkin"' in payload
        return has_ok and has_app
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            if connection is not None:
                connection.close()
        except Exception:  # noqa: BLE001
            pass


def web_server_pid() -> int | None:
    result = run_local_command(["pgrep", "-f", "web_ui.py --no-open"], check=False)
    if result.returncode != 0:
        return None
    line = result.stdout.strip().splitlines()
    if not line:
        return None
    try:
        return int(line[0].strip())
    except ValueError:
        return None


def web_monitor_pid() -> int | None:
    result = run_local_command(["pgrep", "-f", "web_watchdog.py --loop"], check=False)
    if result.returncode != 0:
        return None
    line = result.stdout.strip().splitlines()
    if not line:
        return None
    try:
        return int(line[0].strip())
    except ValueError:
        return None


def run_web_launcher_background() -> subprocess.CompletedProcess[str]:
    if IS_LINUX:
        return run_local_command(systemctl_control_args("restart", SYSTEMD_WEB_UNIT), check=False)
    code_root = RUNTIME_DIR if (RUNTIME_DIR / "web_launcher.py").exists() else PROJECT_DIR
    return subprocess.run(
        [python_executable(code_root), str(code_root / "web_launcher.py"), "--background"],
        capture_output=True,
        text=True,
        cwd=DATA_DIR,
    )


def start_web_monitor_background() -> subprocess.Popen:
    if IS_LINUX:
        raise RuntimeError("Linux 网页监控由 systemd 托管。")
    ensure_log_dir()
    code_root = RUNTIME_DIR if (RUNTIME_DIR / "web_watchdog.py").exists() else PROJECT_DIR
    stdout_handle = WEB_WATCHDOG_STDOUT_PATH.open("a", encoding="utf-8")
    stderr_handle = WEB_WATCHDOG_STDERR_PATH.open("a", encoding="utf-8")
    return subprocess.Popen(
        [python_executable(code_root), str(code_root / "web_watchdog.py"), "--loop"],
        cwd=DATA_DIR,
        stdout=stdout_handle,
        stderr=stderr_handle,
        start_new_session=True,
        close_fds=True,
    )


def start_web_background() -> None:
    if IS_LINUX:
        result = run_local_command(
            systemctl_control_args("start", SYSTEMD_WEB_UNIT, SYSTEMD_WATCHDOG_UNIT),
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "网页服务启动失败。")
        for _ in range(40):
            if is_web_port_open():
                return
            time.sleep(0.2)
        raise RuntimeError("网页服务启动超时。")
    if web_monitor_pid() is None:
        start_web_monitor_background()
        for _ in range(20):
            if web_monitor_pid() is not None:
                break
            time.sleep(0.2)
    if web_monitor_pid() is None:
        raise RuntimeError("网页自修复进程启动失败。")
    if is_web_port_open():
        return
    for _ in range(40):
        if is_web_port_open():
            return
        time.sleep(0.2)
    raise RuntimeError("网页服务启动超时。")


def stop_web_background() -> None:
    if IS_LINUX:
        run_local_command(systemctl_control_args("stop", SYSTEMD_WATCHDOG_UNIT, SYSTEMD_WEB_UNIT), check=False)
        return
    run_local_command(["pkill", "-f", "web_ui.py --no-open"], check=False)
    run_local_command(["pkill", "-f", "web_watchdog.py --loop"], check=False)


def run_local_command(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    ensure_data_layout()
    result = subprocess.run(args, capture_output=True, text=True, cwd=DATA_DIR)
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise RuntimeError(message)
    return result


def restart_launch_agent(target: str, path: Path) -> None:
    run_local_command(["launchctl", "bootout", launch_domain(), str(path)], check=False)
    result = run_local_command(["launchctl", "bootstrap", launch_domain(), str(path)], check=False)
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip()
        loaded = run_local_command(["launchctl", "print", target], check=False)
        if loaded.returncode != 0:
            raise RuntimeError(output or "launchctl bootstrap failed")
        run_local_command(["launchctl", "kickstart", target], check=False)


def restart_scheduler_service() -> None:
    if IS_LINUX:
        result = run_local_command(systemctl_control_args("restart", SYSTEMD_SCHEDULER_UNIT), check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "签到后台重启失败。")
        return
    path = launch_agent_path()
    if not path.exists():
        raise RuntimeError("签到后台尚未安装。")
    result = run_local_command(["launchctl", "kickstart", "-k", launch_target()], check=False)
    if result.returncode == 0:
        return
    restart_launch_agent(launch_target(), path)


def install_and_start_launch_agent() -> str:
    if IS_LINUX:
        result = run_local_command(
            systemctl_control_args(
                "enable",
                "--now",
                SYSTEMD_SCHEDULER_UNIT,
                SYSTEMD_WEB_UNIT,
                SYSTEMD_WATCHDOG_UNIT,
            ),
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "后台启动失败。")
        record_event("已启动签到后台、自修复进程和网页服务。", source="system")
        return str(launch_agent_path())
    deploy_runtime()
    path = install_launch_agent()
    watchdog_path = install_web_watchdog_launch_agent()
    remove_background_login_item()
    run_local_command(["pkill", "-f", "web_watchdog.py --loop"], check=False)
    restart_launch_agent(launch_target(), path)
    restart_launch_agent(web_watchdog_launch_target(), watchdog_path)
    run_local_command(["launchctl", "bootout", web_launch_target()], check=False)
    web_path = web_launch_agent_path()
    if web_path.exists():
        web_path.unlink()
    for _ in range(40):
        if is_web_port_open() and not scheduler_is_stale():
            break
        time.sleep(0.2)
    record_event("已迁移并启动签到后台、独立自修复进程和网页服务。", source="system")
    return str(path)


def stop_launch_agent() -> None:
    if IS_LINUX:
        run_local_command(
            systemctl_control_args("stop", SYSTEMD_WATCHDOG_UNIT, SYSTEMD_WEB_UNIT, SYSTEMD_SCHEDULER_UNIT),
            check=False,
        )
        record_event("已停止签到后台和本地网页服务。", source="system")
        return
    run_local_command(["launchctl", "bootout", launch_target()], check=False)
    run_local_command(["launchctl", "bootout", web_launch_target()], check=False)
    run_local_command(["launchctl", "bootout", web_watchdog_launch_target()], check=False)
    stop_web_background()
    record_event("已停止签到后台和本地网页服务。", source="system")


def uninstall_launch_agent() -> None:
    if IS_LINUX:
        run_local_command(
            systemctl_control_args(
                "disable",
                "--now",
                SYSTEMD_WATCHDOG_UNIT,
                SYSTEMD_WEB_UNIT,
                SYSTEMD_SCHEDULER_UNIT,
            ),
            check=False,
        )
        record_event("已停用签到后台、自修复进程和网页服务。", source="system")
        return
    stop_launch_agent()
    remove_background_login_item()
    for path in (launch_agent_path(), web_launch_agent_path(), web_watchdog_launch_agent_path()):
        if path.exists():
            path.unlink()
    record_event("已卸载签到后台、自修复进程和网页登录项。", source="system")


def parse_launchctl_status(output: str, expected_label: str = SERVICE_LABEL) -> dict[str, Any]:
    status: dict[str, Any] = {"loaded": False, "pid": None, "last_exit_status": None}
    if not output.strip():
        return status

    if output.lstrip().startswith("{"):
        status["loaded"] = True
        pid_match = re.search(r'"PID"\s*=\s*(\d+);', output)
        exit_match = re.search(r'"LastExitStatus"\s*=\s*(\d+);', output)
        if pid_match:
            status["pid"] = int(pid_match.group(1))
        if exit_match:
            status["last_exit_status"] = int(exit_match.group(1))
        return status

    columns = output.strip().split("\t")
    if len(columns) == 3 and columns[2] == expected_label:
        status["loaded"] = True
        status["pid"] = None if columns[0] == "-" else int(columns[0])
        status["last_exit_status"] = None if columns[1] == "-" else int(columns[1])
    return status


def get_scheduler_service_status() -> dict[str, Any]:
    service_path = launch_agent_path()
    if IS_LINUX:
        service_status = get_systemd_service_status(SYSTEMD_SCHEDULER_UNIT)
        service_status["plist_path"] = str(service_path)
        heartbeat = load_scheduler_heartbeat()
        heartbeat_age = scheduler_heartbeat_age_seconds()
        service_status["heartbeat"] = heartbeat
        service_status["heartbeat_age_seconds"] = heartbeat_age
        service_status["auth_error"] = heartbeat.get("phase") == "auth-error"
        service_status["stale"] = bool(
            service_status["installed"]
            and (
                not service_status["loaded"]
                or service_status["pid"] is None
                or heartbeat_age is None
                or heartbeat_age > SCHEDULER_STALE_SECONDS
            )
        )
        return service_status

    service_status: dict[str, Any] = {
        "installed": service_path.exists(),
        "loaded": False,
        "pid": None,
        "last_exit_status": None,
        "plist_path": str(service_path),
    }
    result = run_local_command(["launchctl", "list", SERVICE_LABEL], check=False)
    if result.returncode == 0:
        service_status.update(parse_launchctl_status(result.stdout))
    else:
        service_status["message"] = result.stderr.strip() or result.stdout.strip()
    heartbeat = load_scheduler_heartbeat()
    heartbeat_age = scheduler_heartbeat_age_seconds()
    scheduler_stale = bool(
        service_status["installed"]
        and (
            not service_status["loaded"]
            or service_status["pid"] is None
            or heartbeat_age is None
            or heartbeat_age > SCHEDULER_STALE_SECONDS
        )
    )
    service_status["heartbeat"] = heartbeat
    service_status["heartbeat_age_seconds"] = heartbeat_age
    service_status["auth_error"] = heartbeat.get("phase") == "auth-error"
    service_status["stale"] = scheduler_stale
    return service_status


def get_launch_agent_status() -> dict[str, Any]:
    service_status = get_scheduler_service_status()
    service_path = launch_agent_path()
    heartbeat = service_status["heartbeat"]
    heartbeat_age = service_status["heartbeat_age_seconds"]
    scheduler_stale = service_status["stale"]

    watchdog_path = web_watchdog_launch_agent_path()
    web_port_open = is_web_port_open()

    if IS_LINUX:
        web_status = get_systemd_service_status(SYSTEMD_WEB_UNIT)
        watchdog_status = get_systemd_service_status(SYSTEMD_WATCHDOG_UNIT)
        web_pid = web_status.get("pid")
        web_monitor = watchdog_status.get("pid")
        watchdog_installed = bool(watchdog_status.get("installed"))
        web_loaded = bool(web_status.get("loaded") and web_port_open)
        return {
            "installed": service_status["installed"],
            "loaded": service_status["loaded"],
            "pid": service_status["pid"],
            "last_exit_status": service_status["last_exit_status"],
            "heartbeat": heartbeat,
            "heartbeat_age_seconds": heartbeat_age,
            "stale": scheduler_stale,
            "auth_error": bool(service_status.get("auth_error")),
            "plist_path": str(service_path),
            "service": service_status,
            "web": {
                "installed": bool(web_status.get("installed")),
                "loaded": web_loaded,
                "pid": web_pid,
                "monitor_pid": web_monitor,
                "healthy": web_port_open,
                "plist_path": str(web_launch_agent_path()),
            },
            "web_installed": bool(web_status.get("installed")),
            "web_loaded": web_loaded,
            "web_pid": web_pid,
            "web_monitor_pid": web_monitor,
            "web_port_open": web_port_open,
            "web_plist_path": str(web_launch_agent_path()),
            "watchdog_installed": watchdog_installed,
        }

    web_pid = web_server_pid()
    web_monitor = web_monitor_pid()
    watchdog_result = run_local_command(["launchctl", "list", WEB_WATCHDOG_LABEL], check=False)
    watchdog_status = parse_launchctl_status(
        watchdog_result.stdout,
        expected_label=WEB_WATCHDOG_LABEL,
    ) if watchdog_result.returncode == 0 else {
        "loaded": False,
        "pid": None,
        "last_exit_status": None,
    }
    watchdog_installed = watchdog_path.exists()

    return {
        "installed": service_status["installed"],
        "loaded": service_status["loaded"],
        "pid": service_status["pid"],
        "last_exit_status": service_status["last_exit_status"],
        "heartbeat": heartbeat,
        "heartbeat_age_seconds": heartbeat_age,
        "stale": scheduler_stale,
        "auth_error": bool(service_status.get("auth_error")),
        "plist_path": str(service_path),
        "service": service_status,
        "web": {
            "installed": watchdog_installed,
            "loaded": bool(watchdog_status.get("loaded") or web_monitor or web_port_open),
            "pid": web_pid,
            "monitor_pid": watchdog_status.get("pid") or web_monitor,
            "healthy": web_port_open,
            "plist_path": str(watchdog_path),
        },
        "web_installed": watchdog_installed,
        "web_loaded": bool(watchdog_status.get("loaded") or web_monitor or web_port_open),
        "web_pid": web_pid,
        "web_monitor_pid": watchdog_status.get("pid") or web_monitor,
        "web_port_open": web_port_open,
        "web_plist_path": str(watchdog_path),
        "watchdog_installed": watchdog_installed,
    }


def tail_logs(limit: int = 120) -> str:
    ensure_log_dir()
    if not EVENT_LOG_PATH.exists():
        return "暂无操作日志。"
    lines = EVENT_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    recent = lines[-limit:]
    return "\n".join(recent).strip() or "暂无操作日志。"


def save_login_state(values: dict[str, str], phone_code_hash: str) -> None:
    LOGIN_STATE_PATH.write_text(
        json.dumps(
            {
                "session": values.get("TG_SESSION", ""),
                "phone": values.get("TG_PHONE", ""),
                "phone_code_hash": phone_code_hash,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_login_state() -> dict[str, str]:
    if not LOGIN_STATE_PATH.exists():
        return {}
    return json.loads(LOGIN_STATE_PATH.read_text(encoding="utf-8"))


def clear_login_state() -> None:
    if LOGIN_STATE_PATH.exists():
        LOGIN_STATE_PATH.unlink()


async def send_login_code(values: dict[str, str]) -> None:
    if has_tdesktop_session(values):
        return
    if has_session_json(values):
        return
    ensure_manual_api_credentials(values)
    config = build_config(values, target_fallback="__dummy__")
    if not config.phone:
        raise ValueError("缺少手机号。")

    client = TelegramClient(config.session, config.api_id, config.api_hash, proxy=config.proxy)
    await client.connect()
    try:
        if await client.is_user_authorized():
            return
        result = await client.send_code_request(config.phone)
        save_login_state(values, result.phone_code_hash)
    finally:
        await client.disconnect()


async def complete_login(values: dict[str, str], code: str, password: str) -> str:
    if has_tdesktop_session(values):
        client = await create_connected_client(values)
        try:
            me = await client.get_me()
            return me.username or me.first_name or str(me.id)
        finally:
            await close_client(client)

    if has_session_json(values):
        client = await create_connected_client(values)
        try:
            me = await client.get_me()
            return me.username or me.first_name or str(me.id)
        finally:
            await close_client(client)

    ensure_manual_api_credentials(values)
    config = build_config(values, target_fallback="__dummy__")
    if not config.phone:
        raise ValueError("缺少手机号。")

    state = load_login_state()
    client = TelegramClient(config.session, config.api_id, config.api_hash, proxy=config.proxy)
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            clear_login_state()
            return me.username or me.first_name or str(me.id)

        phone_code_hash = state.get("phone_code_hash")
        if not phone_code_hash:
            raise ValueError("请先点一次“发送登录验证码”。")

        try:
            await client.sign_in(phone=config.phone, code=code, phone_code_hash=phone_code_hash)
        except errors.SessionPasswordNeededError:
            if not password:
                raise ValueError("这个账号开启了二步验证，请填写二步验证密码。") from None
            await client.sign_in(password=password)

        me = await client.get_me()
        clear_login_state()
        return me.username or me.first_name or str(me.id)
    finally:
        await close_client(client)


async def list_available_dialogs(values: dict[str, str], limit: int = 300) -> list[dict[str, Any]]:
    client = await create_connected_client(validate_global_values(values))
    try:
        if not await client.is_user_authorized():
            raise ValueError("当前还没有登录，请先完成登录。")

        dialogs: list[dict[str, Any]] = []
        async for dialog in client.iter_dialogs(limit=limit):
            if not bool(getattr(dialog, "pinned", False)):
                continue
            entity = dialog.entity
            title = dialog.name or ""
            username = getattr(entity, "username", "") or ""
            is_bot = bool(getattr(entity, "bot", False))
            if is_bot:
                kind = "bot"
            elif dialog.is_group:
                kind = "group"
            elif dialog.is_channel:
                kind = "channel"
            else:
                continue

            target = f"@{username}" if username else str(dialog.id)
            label = title or target
            peer_type = "user" if is_bot else ("channel" if dialog.is_channel else "chat")
            peer_access_hash = str(getattr(entity, "access_hash", "") or "")
            dialogs.append(
                {
                    "id": str(dialog.id),
                    "title": label,
                    "target": target,
                    "target_label": label,
                    "target_kind": kind,
                    "peer_type": peer_type,
                    "peer_id": str(dialog.id),
                    "peer_access_hash": peer_access_hash,
                    "username": username,
                }
            )
        return dialogs
    finally:
        await close_client(client)


def run_script(args: list[str]) -> subprocess.CompletedProcess[str]:
    ensure_log_dir()
    return subprocess.run(
        [python_executable(), str(PROJECT_DIR / "tg_checkin.py"), *args],
        capture_output=True,
        text=True,
        cwd=DATA_DIR,
    )


def run_service_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    ensure_log_dir()
    return subprocess.run(
        [python_executable(), str(PROJECT_DIR / "service_runner.py"), *args],
        capture_output=True,
        text=True,
        cwd=DATA_DIR,
    )
