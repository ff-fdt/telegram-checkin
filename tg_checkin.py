from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from telethon import TelegramClient, errors
from telethon.tl.types import InputPeerChannel, InputPeerChat, InputPeerUser


LOGGER = logging.getLogger("tg-checkin")
SYSTEM_TIMEZONE_SENTINELS = {"SYSTEM", "LOCAL", "AUTO"}


@dataclass(slots=True)
class Config:
    api_id: int
    api_hash: str
    phone: str | None
    session: str
    target: str | int
    signin_text: str
    button_text: str | None
    button_row: int | None
    button_col: int | None
    peer_type: str | None
    peer_id: int | None
    peer_access_hash: int | None
    run_time: time
    timezone: ZoneInfo
    jitter_seconds: int
    reply_wait_seconds: int
    proxy: tuple[str, str, int, bool] | None


@dataclass(slots=True)
class CheckinOutcome:
    delivery: str
    description: str
    message_sent: bool = False
    completed: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram auto check-in script")
    parser.add_argument("--once", action="store_true", help="run once immediately")
    parser.add_argument("--dry-run", action="store_true", help="print what would run")
    parser.add_argument("--phone", help="telegram phone number, e.g. +8613900860001")
    parser.add_argument("--session", help="telethon session name, default: checkin")
    parser.add_argument("--target", help="username / t.me link / numeric chat id")
    parser.add_argument("--text", help="message text to send, default: /checkin")
    parser.add_argument("--button-text", help="exact inline button text to click")
    parser.add_argument("--button-row", type=int, help="inline button row index, 0-based")
    parser.add_argument("--button-col", type=int, help="inline button column index, 0-based")
    parser.add_argument("--time", help="daily run time, format HH:MM, default: 09:00")
    parser.add_argument("--timezone", help="IANA timezone, or SYSTEM to follow computer timezone")
    parser.add_argument("--jitter-seconds", type=int, help="random delay after schedule")
    parser.add_argument("--reply-wait-seconds", type=int, help="seconds to wait for reply buttons")
    parser.add_argument(
        "--log-level",
        default=os.getenv("TG_LOG_LEVEL", "INFO"),
        help="logging level, default: INFO",
    )
    return parser


def env_or_arg(args_value: object, env_name: str, default: object | None = None) -> object | None:
    if args_value is not None:
        return args_value
    env_value = os.getenv(env_name)
    if env_value not in (None, ""):
        return env_value
    return default


def require_str(value: object | None, name: str) -> str:
    if value in (None, ""):
        raise ValueError(f"missing required setting: {name}")
    return str(value)


def parse_int(value: object | None, name: str) -> int:
    raw = require_str(value, name)
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def parse_optional_int(value: object | None, name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def parse_proxy(values: Mapping[str, object]) -> tuple[str, str, int, bool] | None:
    proxy_type = str(values.get("TG_PROXY_TYPE") or "").strip().lower()
    if not proxy_type:
        return None
    if proxy_type not in {"socks5", "socks4", "http"}:
        raise ValueError("TG_PROXY_TYPE must be socks5, socks4, or http")
    proxy_host = require_str(values.get("TG_PROXY_HOST"), "TG_PROXY_HOST")
    proxy_port = parse_int(values.get("TG_PROXY_PORT"), "TG_PROXY_PORT")
    if not 1 <= proxy_port <= 65535:
        raise ValueError("TG_PROXY_PORT must be between 1 and 65535")
    return proxy_type, proxy_host, proxy_port, True


def parse_time_string(value: str) -> time:
    try:
        hour_str, minute_str = value.split(":", maxsplit=1)
        hour = int(hour_str)
        minute = int(minute_str)
        return time(hour=hour, minute=minute)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("time must use HH:MM format") from exc


def detect_system_timezone_key() -> str:
    tz_env = os.getenv("TZ", "").strip()
    if tz_env:
        try:
            ZoneInfo(tz_env)
            return tz_env
        except ZoneInfoNotFoundError:
            pass

    try:
        resolved = Path("/etc/localtime").resolve(strict=True)
        marker = "zoneinfo/"
        resolved_text = str(resolved)
        if marker in resolved_text:
            key = resolved_text.split(marker, 1)[1]
            ZoneInfo(key)
            return key
    except Exception:  # noqa: BLE001
        pass

    return "UTC"


def detect_system_timezone() -> ZoneInfo:
    return ZoneInfo(detect_system_timezone_key())


def parse_timezone(value: str | ZoneInfo) -> ZoneInfo:
    if isinstance(value, ZoneInfo):
        return value
    normalized = str(value).strip()
    if normalized.upper() in SYSTEM_TIMEZONE_SENTINELS:
        return detect_system_timezone()
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {value}") from exc


def timezone_display_name(value: str | ZoneInfo) -> str:
    timezone = parse_timezone(value)
    return getattr(timezone, "key", str(timezone))


def parse_target(value: str) -> str | int:
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def config_from_values(values: Mapping[str, object]) -> Config:
    api_id = parse_int(values.get("TG_API_ID"), "TG_API_ID")
    api_hash = require_str(values.get("TG_API_HASH"), "TG_API_HASH")
    phone = values.get("TG_PHONE")
    session = require_str(values.get("TG_SESSION", "checkin"), "TG_SESSION")
    target = parse_target(require_str(values.get("TG_TARGET"), "TG_TARGET"))
    signin_text = require_str(values.get("TG_SIGNIN_TEXT", "/checkin"), "TG_SIGNIN_TEXT")
    button_text = values.get("TG_BUTTON_TEXT")
    button_row = parse_optional_int(values.get("TG_BUTTON_ROW"), "TG_BUTTON_ROW")
    button_col = parse_optional_int(values.get("TG_BUTTON_COL"), "TG_BUTTON_COL")
    peer_type = str(values.get("TG_PEER_TYPE") or "").strip() or None
    peer_id = parse_optional_int(values.get("TG_PEER_ID"), "TG_PEER_ID")
    peer_access_hash = parse_optional_int(values.get("TG_PEER_ACCESS_HASH"), "TG_PEER_ACCESS_HASH")
    run_time = parse_time_string(require_str(values.get("TG_TIME", "09:00"), "TG_TIME"))
    timezone = parse_timezone(require_str(values.get("TG_TIMEZONE", "SYSTEM"), "TG_TIMEZONE"))
    jitter_seconds = parse_int(values.get("TG_JITTER_SECONDS", 0), "TG_JITTER_SECONDS")
    reply_wait_seconds = parse_int(
        values.get("TG_REPLY_WAIT_SECONDS", 8),
        "TG_REPLY_WAIT_SECONDS",
    )
    proxy = parse_proxy(values)

    if (button_row is None) ^ (button_col is None):
        raise ValueError("TG_BUTTON_ROW and TG_BUTTON_COL must be set together")
    if button_text and button_row is not None:
        raise ValueError("use either TG_BUTTON_TEXT or TG_BUTTON_ROW/COL, not both")
    if jitter_seconds < 0:
        raise ValueError("TG_JITTER_SECONDS must be >= 0")
    if reply_wait_seconds < 0:
        raise ValueError("TG_REPLY_WAIT_SECONDS must be >= 0")

    return Config(
        api_id=api_id,
        api_hash=api_hash,
        phone=str(phone) if phone else None,
        session=session,
        target=target,
        signin_text=signin_text,
        button_text=str(button_text) if button_text else None,
        button_row=button_row,
        button_col=button_col,
        peer_type=peer_type,
        peer_id=peer_id,
        peer_access_hash=peer_access_hash,
        run_time=run_time,
        timezone=timezone,
        jitter_seconds=jitter_seconds,
        reply_wait_seconds=reply_wait_seconds,
        proxy=proxy,
    )


def load_config(args: argparse.Namespace) -> Config:
    values = {
        "TG_API_ID": env_or_arg(None, "TG_API_ID"),
        "TG_API_HASH": env_or_arg(None, "TG_API_HASH"),
        "TG_PHONE": env_or_arg(args.phone, "TG_PHONE"),
        "TG_SESSION": env_or_arg(args.session, "TG_SESSION", "checkin"),
        "TG_TARGET": env_or_arg(args.target, "TG_TARGET"),
        "TG_SIGNIN_TEXT": env_or_arg(args.text, "TG_SIGNIN_TEXT", "/checkin"),
        "TG_BUTTON_TEXT": env_or_arg(args.button_text, "TG_BUTTON_TEXT"),
        "TG_BUTTON_ROW": env_or_arg(args.button_row, "TG_BUTTON_ROW"),
        "TG_BUTTON_COL": env_or_arg(args.button_col, "TG_BUTTON_COL"),
        "TG_TIME": env_or_arg(args.time, "TG_TIME", "09:00"),
        "TG_TIMEZONE": env_or_arg(args.timezone, "TG_TIMEZONE", "SYSTEM"),
        "TG_JITTER_SECONDS": env_or_arg(args.jitter_seconds, "TG_JITTER_SECONDS", 0),
        "TG_REPLY_WAIT_SECONDS": env_or_arg(args.reply_wait_seconds, "TG_REPLY_WAIT_SECONDS", 8),
        "TG_PROXY_TYPE": env_or_arg(None, "TG_PROXY_TYPE"),
        "TG_PROXY_HOST": env_or_arg(None, "TG_PROXY_HOST"),
        "TG_PROXY_PORT": env_or_arg(None, "TG_PROXY_PORT"),
    }
    return config_from_values(values)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def next_run_at(config: Config) -> datetime:
    now = datetime.now(config.timezone)
    run_at = now.replace(
        hour=config.run_time.hour,
        minute=config.run_time.minute,
        second=0,
        microsecond=0,
    )
    if run_at <= now:
        run_at += timedelta(days=1)
    if config.jitter_seconds:
        run_at += timedelta(seconds=random.randint(0, config.jitter_seconds))
    return run_at


def log_dry_run_plan(config: Config, once: bool) -> None:
    button_enabled = config.button_text is not None or config.button_row is not None

    if once:
        LOGGER.info(
            "dry-run only: immediate check-in configured button=%s timezone=%s",
            button_enabled,
            config.timezone.key,
        )
        return

    scheduled_at = next_run_at(config)
    LOGGER.info(
        "dry-run only: next scheduled run at %s button=%s",
        scheduled_at.isoformat(),
        button_enabled,
    )


def collect_button_labels(buttons: Sequence[Sequence[object]] | None) -> list[str]:
    labels: list[str] = []
    if not buttons:
        return labels
    for row_index, row in enumerate(buttons):
        for col_index, button in enumerate(row):
            labels.append(f"[{row_index},{col_index}] {getattr(button, 'text', '<no text>')}")
    return labels


async def wait_for_button_message(client: TelegramClient, entity: object, sent_message_id: int, timeout_seconds: int):
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        messages = await client.get_messages(entity, limit=6)
        for message in messages:
            if message.id <= sent_message_id:
                continue
            if message.out:
                continue
            if getattr(message, "buttons", None):
                return message
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(1)


async def maybe_click_button(client: TelegramClient, entity: object, sent_message_id: int, config: Config) -> None:
    if config.button_text is None and config.button_row is None:
        return
    if config.reply_wait_seconds == 0:
        raise RuntimeError("button click is enabled, but TG_REPLY_WAIT_SECONDS is 0")

    LOGGER.info("waiting up to %s seconds for inline buttons", config.reply_wait_seconds)
    button_message = await wait_for_button_message(client, entity, sent_message_id, config.reply_wait_seconds)
    if button_message is None:
        raise RuntimeError("no reply with inline buttons arrived before timeout")

    try:
        if config.button_text is not None:
            await button_message.click(text=config.button_text)
            LOGGER.info("clicked button by text: %s", config.button_text)
        else:
            await button_message.click(i=config.button_row, j=config.button_col)
            LOGGER.info("clicked button by index: [%s,%s]", config.button_row, config.button_col)
    except Exception as exc:  # noqa: BLE001
        labels = collect_button_labels(getattr(button_message, "buttons", None))
        if labels:
            LOGGER.error("available buttons: %s", " | ".join(labels))
        raise RuntimeError(f"failed to click button: {exc}") from exc


def build_input_peer(config: Config) -> object | None:
    if config.peer_type == "user" and config.peer_id is not None and config.peer_access_hash is not None:
        return InputPeerUser(config.peer_id, config.peer_access_hash)
    if config.peer_type == "channel" and config.peer_id is not None and config.peer_access_hash is not None:
        return InputPeerChannel(config.peer_id, config.peer_access_hash)
    if config.peer_type == "chat" and config.peer_id is not None:
        return InputPeerChat(config.peer_id)
    return None


async def resolve_dialog_entity(client: TelegramClient, peer_id: int | None) -> object | None:
    if peer_id is None:
        return None
    async for dialog in client.iter_dialogs(limit=300):
        if int(dialog.id) == peer_id:
            return dialog.entity
    return None


async def resolve_target_entity(client: TelegramClient, config: Config) -> object:
    try:
        return await client.get_entity(config.target)
    except Exception as first_exc:  # noqa: BLE001
        input_peer = build_input_peer(config)
        if input_peer is not None:
            try:
                return await client.get_entity(input_peer)
            except Exception:  # noqa: BLE001
                pass

        dialog_entity = await resolve_dialog_entity(client, config.peer_id)
        if dialog_entity is not None:
            return dialog_entity

        raise RuntimeError("无法解析任务目标") from first_exc


async def perform_checkin(client: TelegramClient, config: Config, dry_run: bool = False) -> CheckinOutcome:
    if dry_run:
        LOGGER.info("dry-run only: no Telegram entity resolution or send")
        return CheckinOutcome("dry-run", "仅演练，不发送。", message_sent=False, completed=False)

    entity = await resolve_target_entity(client, config)

    try:
        sent = await client.send_message(entity, config.signin_text)
        LOGGER.info("sign-in message sent")
        if config.button_text is None and config.button_row is None:
            return CheckinOutcome("sent", "已发送签到命令。", message_sent=True, completed=True)
        try:
            await maybe_click_button(client, entity, sent.id, config)
        except Exception:  # noqa: BLE001
            LOGGER.exception("post-send button action failed")
            return CheckinOutcome(
                "sent-post-action-failed",
                "已发送签到命令，但后续按钮操作失败。",
                message_sent=True,
                completed=False,
            )
        return CheckinOutcome(
            "sent-and-clicked",
            "已发送签到命令并点击按钮。",
            message_sent=True,
            completed=True,
        )
    except errors.FloodWaitError as exc:
        LOGGER.warning("telegram asked to wait %s seconds", exc.seconds)
        await asyncio.sleep(exc.seconds)
        raise


async def run_scheduler(client: TelegramClient, config: Config, dry_run: bool) -> None:
    while True:
        run_at = next_run_at(config)
        wait_seconds = max((run_at - datetime.now(config.timezone)).total_seconds(), 0)
        LOGGER.info("next run at %s", run_at.isoformat())
        await asyncio.sleep(wait_seconds)
        try:
            await perform_checkin(client, config, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("check-in run failed: %s", exc)


async def async_main(args: argparse.Namespace) -> int:
    load_dotenv()
    try:
        config = load_config(args)
    except ValueError as exc:
        LOGGER.error(str(exc))
        return 2

    if args.dry_run:
        log_dry_run_plan(config, once=args.once)
        return 0

    client = TelegramClient(config.session, config.api_id, config.api_hash, proxy=config.proxy)
    await client.start(phone=config.phone)
    LOGGER.info("telegram session is ready")

    if args.once:
        await perform_checkin(client, config, dry_run=args.dry_run)
    else:
        await run_scheduler(client, config, dry_run=args.dry_run)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        LOGGER.info("stopped by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
