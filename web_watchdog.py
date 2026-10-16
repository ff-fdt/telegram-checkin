from __future__ import annotations

import argparse
import time

import local_runtime

WEB_REPAIR_RETRY_SECONDS = 60.0
SCHEDULER_REPAIR_RETRY_SECONDS = 60.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Keep local Telegram web console alive")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--loop", action="store_true", help="run forever and self-heal the web console")
    parser.add_argument("--interval", type=float, default=2.0, help="loop interval in seconds")
    return parser


def repair_web_service() -> tuple[bool, str]:
    result = local_runtime.run_web_launcher_background()
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "网页服务启动失败"
        return False, message
    for _ in range(20):
        if local_runtime.is_web_port_open():
            return True, "ok"
        time.sleep(0.2)
    return False, "网页服务启动超时。"


def repair_scheduler_service() -> tuple[bool, str]:
    try:
        local_runtime.restart_scheduler_service()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    for _ in range(20):
        status = local_runtime.get_scheduler_service_status()
        if status.get("loaded") and status.get("pid") and not status.get("stale"):
            return True, "ok"
        time.sleep(0.5)
    return False, "签到后台重启后仍然没有心跳。"


def run_once(record_detection: bool = True) -> int:
    failed = False
    if not local_runtime.is_web_port_open():
        if record_detection:
            local_runtime.record_event("检测到本地网页服务未运行，开始自修复。", source="watchdog")
        ok, message = repair_web_service()
        if ok:
            local_runtime.record_event("本地网页服务自修复完成。", source="watchdog")
        else:
            local_runtime.record_event(f"自修复失败：{message}", source="watchdog")
            failed = True

    status = local_runtime.get_scheduler_service_status()
    if status.get("installed") and status.get("stale"):
        if record_detection:
            local_runtime.record_event("检测到签到后台无心跳，开始自修复。", source="watchdog")
        ok, message = repair_scheduler_service()
        if ok:
            local_runtime.record_event("签到后台自修复完成。", source="watchdog")
        else:
            local_runtime.record_event(f"签到后台自修复失败：{message}", source="watchdog")
            failed = True
    return 1 if failed else 0


def run_loop(interval: float) -> int:
    seen_down = False
    failure_logged = False
    seen_scheduler_stale = False
    scheduler_failure_logged = False
    next_web_retry_at = 0.0
    next_scheduler_retry_at = 0.0
    while True:
        current_time = time.monotonic()
        if local_runtime.is_web_port_open():
            seen_down = False
            failure_logged = False
            next_web_retry_at = 0.0
        else:
            if not seen_down:
                local_runtime.record_event("检测到本地网页服务未运行，开始自修复。", source="watchdog")
                seen_down = True

            if current_time >= next_web_retry_at:
                ok, message = repair_web_service()
                next_web_retry_at = current_time + WEB_REPAIR_RETRY_SECONDS
                if ok:
                    local_runtime.record_event("本地网页服务自修复完成。", source="watchdog")
                    seen_down = False
                    failure_logged = False
                    next_web_retry_at = 0.0
                elif not failure_logged:
                    local_runtime.record_event(f"自修复失败：{message}", source="watchdog")
                    failure_logged = True

        scheduler_status = local_runtime.get_scheduler_service_status()
        if scheduler_status.get("installed") and scheduler_status.get("stale"):
            if not seen_scheduler_stale:
                local_runtime.record_event("检测到签到后台无心跳，开始自修复。", source="watchdog")
                seen_scheduler_stale = True

            if current_time >= next_scheduler_retry_at:
                ok, message = repair_scheduler_service()
                next_scheduler_retry_at = current_time + SCHEDULER_REPAIR_RETRY_SECONDS
                if ok:
                    local_runtime.record_event("签到后台自修复完成。", source="watchdog")
                    seen_scheduler_stale = False
                    scheduler_failure_logged = False
                    next_scheduler_retry_at = 0.0
                elif not scheduler_failure_logged:
                    local_runtime.record_event(f"签到后台自修复失败：{message}", source="watchdog")
                    scheduler_failure_logged = True
        else:
            seen_scheduler_stale = False
            scheduler_failure_logged = False
            next_scheduler_retry_at = 0.0
        time.sleep(interval)


def main() -> int:
    args = build_parser().parse_args()
    if args.loop:
        return run_loop(max(args.interval, 1.0))
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
