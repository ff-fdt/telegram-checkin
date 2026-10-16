from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values, set_key

import tg_checkin


SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"


def prompt_text(label: str, default: str | None = None, required: bool = False) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""
        print("这个字段必须填写。")


def prompt_yes_no(label: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{hint}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("请输入 y 或 n。")


def load_existing() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    values = dotenv_values(ENV_PATH)
    return {key: value for key, value in values.items() if value is not None}


def save_env(values: dict[str, str]) -> None:
    ENV_PATH.touch(exist_ok=True, mode=0o600)
    ENV_PATH.chmod(0o600)
    for key, value in values.items():
        set_key(str(ENV_PATH), key, value)


def collect_values() -> dict[str, str]:
    existing = load_existing()
    print("首次使用只需要填一遍，之后会直接复用 .env。")
    print("API_ID 和 API_HASH 去 https://my.telegram.org 的 API development tools 页面拿。")
    print()

    values: dict[str, str] = {}
    values["TG_API_ID"] = prompt_text("TG_API_ID", existing.get("TG_API_ID"), required=True)
    values["TG_API_HASH"] = prompt_text("TG_API_HASH", existing.get("TG_API_HASH"), required=True)
    values["TG_PHONE"] = prompt_text("TG_PHONE", existing.get("TG_PHONE"), required=True)
    values["TG_SESSION"] = prompt_text("TG_SESSION", existing.get("TG_SESSION", "checkin"), required=True)
    values["TG_TARGET"] = prompt_text("TG_TARGET", existing.get("TG_TARGET"), required=True)
    values["TG_SIGNIN_TEXT"] = prompt_text(
        "TG_SIGNIN_TEXT",
        existing.get("TG_SIGNIN_TEXT", "/checkin"),
        required=True,
    )
    values["TG_TIME"] = prompt_text("TG_TIME", existing.get("TG_TIME", "09:00"), required=True)
    values["TG_TIMEZONE"] = prompt_text(
        "TG_TIMEZONE",
        existing.get("TG_TIMEZONE", "SYSTEM"),
        required=True,
    )
    values["TG_JITTER_SECONDS"] = prompt_text(
        "TG_JITTER_SECONDS",
        existing.get("TG_JITTER_SECONDS", "0"),
        required=True,
    )
    values["TG_REPLY_WAIT_SECONDS"] = prompt_text(
        "TG_REPLY_WAIT_SECONDS",
        existing.get("TG_REPLY_WAIT_SECONDS", "8"),
        required=True,
    )

    if prompt_yes_no("回复后还要自动点按钮吗", default=bool(existing.get("TG_BUTTON_TEXT") or existing.get("TG_BUTTON_ROW"))):
        by_text_default = bool(existing.get("TG_BUTTON_TEXT")) or not existing.get("TG_BUTTON_ROW")
        if prompt_yes_no("按按钮文字匹配吗", default=by_text_default):
            values["TG_BUTTON_TEXT"] = prompt_text(
                "TG_BUTTON_TEXT",
                existing.get("TG_BUTTON_TEXT"),
                required=True,
            )
            values["TG_BUTTON_ROW"] = ""
            values["TG_BUTTON_COL"] = ""
        else:
            values["TG_BUTTON_ROW"] = prompt_text(
                "TG_BUTTON_ROW",
                existing.get("TG_BUTTON_ROW", "0"),
                required=True,
            )
            values["TG_BUTTON_COL"] = prompt_text(
                "TG_BUTTON_COL",
                existing.get("TG_BUTTON_COL", "0"),
                required=True,
            )
            values["TG_BUTTON_TEXT"] = ""
    else:
        values["TG_BUTTON_TEXT"] = ""
        values["TG_BUTTON_ROW"] = ""
        values["TG_BUTTON_COL"] = ""

    return values


def main() -> int:
    os.chdir(SCRIPT_DIR)

    reconfigure = "--setup" in sys.argv[1:] or not ENV_PATH.exists()
    if reconfigure:
        values = collect_values()
        save_env(values)
        print()
        print(f"配置已写入: {ENV_PATH}")
        print()

    passthrough = [arg for arg in sys.argv[1:] if arg != "--setup"]
    if not passthrough:
        passthrough = ["--once"]

    print(f"开始运行: python tg_checkin.py {' '.join(passthrough)}")
    return tg_checkin.main(passthrough)


if __name__ == "__main__":
    raise SystemExit(main())
