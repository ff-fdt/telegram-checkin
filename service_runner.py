from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import local_runtime
import tg_checkin
from telethon import errors as telethon_errors


LOGGER = logging.getLogger("tg-multi-checkin")
POLL_SECONDS = 15
AUTH_FAILURE_RETRY_SECONDS = 5 * 60
TEST_SLOT_GRACE_SECONDS = 15 * 60
CONNECT_TIMEOUT_SECONDS = 60
TASK_TIMEOUT_SECONDS = 90
CLOSE_TIMEOUT_SECONDS = 15
SCHEDULER_STATE_PATH = local_runtime.SCHEDULER_STATE_PATH


@dataclass(slots=True)
class TaskRunResult:
    task_id: str
    message_sent: bool
    completed: bool
    delivery: str
    error: str | None = None


PERMANENT_AUTH_ERRORS = (
    telethon_errors.AuthKeyDuplicatedError,
    telethon_errors.AuthKeyUnregisteredError,
    telethon_errors.SessionRevokedError,
    telethon_errors.UserDeactivatedError,
    telethon_errors.UserDeactivatedBanError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Telegram multi-target auto check-in service")
    parser.add_argument("--once", action="store_true", help="run selected tasks once immediately")
    parser.add_argument("--dry-run", action="store_true", help="print plans without sending")
    parser.add_argument("--task-id", action="append", default=[], help="task id to run; can be used multiple times")
    parser.add_argument("--log-level", default="INFO", help="logging level")
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def load_enabled_tasks(task_ids: list[str] | None = None) -> tuple[dict[str, str], list[dict]]:
    base_values = local_runtime.load_env_values()
    local_runtime.validate_global_values(base_values)
    tasks = local_runtime.get_effective_tasks(base_values)
    if task_ids:
        selected = set(task_ids)
        tasks = [task for task in tasks if task["id"] in selected]
    else:
        tasks = [task for task in tasks if task.get("enabled")]
    if not tasks:
        raise ValueError("没有可运行的任务。请先在 GUI 里添加至少一个启用中的任务。")
    return base_values, tasks


def task_title(task: dict) -> str:
    return task.get("title") or task.get("target_label") or task.get("target") or task.get("id", "未知任务")


def task_log_ref(task: dict) -> str:
    digest = hashlib.sha256(str(task.get("id", "unknown")).encode("utf-8")).hexdigest()[:8]
    return f"任务#{digest}"


def load_scheduler_state() -> dict[str, dict[str, str]]:
    if not SCHEDULER_STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(SCHEDULER_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items() if isinstance(value, dict)}


def save_scheduler_state(state: dict[str, dict[str, str]]) -> None:
    local_runtime.atomic_write_text(
        SCHEDULER_STATE_PATH,
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
    )


def scheduled_run_for_today(config: tg_checkin.Config, now: datetime | None = None) -> datetime:
    tz = config.timezone
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    return current.replace(
        hour=config.run_time.hour,
        minute=config.run_time.minute,
        second=0,
        microsecond=0,
    )


def task_day_key(config: tg_checkin.Config, now: datetime | None = None) -> str:
    tz = config.timezone
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    return current.date().isoformat()


def collect_due_tasks(
    base_values: dict[str, str],
    tasks: list[dict],
    scheduler_state: dict[str, dict[str, str]],
    now: datetime | None = None,
) -> list[tuple[dict, tg_checkin.Config, str]]:
    due: list[tuple[dict, tg_checkin.Config, str]] = []
    for task in tasks:
        config = tg_checkin.config_from_values(local_runtime.task_to_config_values(base_values, task))
        current_day = task_day_key(config, now=now)
        if scheduler_state.get(task["id"], {}).get("last_success_date") == current_day:
            continue
        scheduled_at = scheduled_run_for_today(config, now=now)
        current = now.astimezone(scheduled_at.tzinfo) if now is not None else datetime.now(scheduled_at.tzinfo)
        if current >= scheduled_at:
            due.append((task, config, current_day))
    return due


def load_test_slot_due(tasks: list[dict], now: datetime | None = None) -> dict | None:
    payload = local_runtime.load_test_slot()
    if not payload or not payload.get("active") or not tasks:
        return None
    run_at_raw = str(payload.get("run_at", "") or "")
    if not run_at_raw:
        return None
    try:
        run_at = datetime.fromisoformat(run_at_raw)
    except ValueError:
        local_runtime.clear_test_slot()
        local_runtime.record_event("临时测试时间格式无效，已清除。", source="runner")
        return None
    current = now.astimezone(run_at.tzinfo) if now is not None else datetime.now(run_at.tzinfo)
    lateness_seconds = (current - run_at).total_seconds()
    if lateness_seconds < 0:
        return None
    if lateness_seconds > TEST_SLOT_GRACE_SECONDS:
        local_runtime.clear_test_slot()
        local_runtime.record_event("临时测试时间已过期，已自动清除。", source="runner")
        return None
    payload["run_at_datetime"] = run_at
    return payload


def write_scheduler_heartbeat(phase: str, **extra: object) -> None:
    local_runtime.write_scheduler_heartbeat(phase, **extra)


async def close_client_safely(client) -> None:
    try:
        await asyncio.wait_for(local_runtime.close_client(client), timeout=CLOSE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        LOGGER.warning("telegram client close timed out")


def outcome_message(prefix: str, task_ref: str, outcome: tg_checkin.CheckinOutcome) -> str:
    return f"{prefix}{outcome.description.rstrip('。')}：{task_ref}"


def should_log_connect_failure(scheduler_state: dict[str, dict[str, str]], now: datetime | None = None) -> bool:
    current = now or datetime.now()
    hour_key = current.strftime("%Y-%m-%dT%H")
    meta = scheduler_state.get("__meta__", {})
    if meta.get("last_connect_failure_hour") == hour_key:
        return False
    meta["last_connect_failure_hour"] = hour_key
    scheduler_state["__meta__"] = meta
    save_scheduler_state(scheduler_state)
    return True


def is_permanent_auth_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, PERMANENT_AUTH_ERRORS):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def record_connect_error(
    exc: BaseException,
    scheduler_state: dict[str, dict[str, str]],
    enabled_tasks: int,
    due_tasks: int,
    mode: str,
) -> int:
    permanent = is_permanent_auth_error(exc)
    reason = "auth-invalid" if permanent else "connect-failed"
    phase = "auth-error" if permanent else "idle"
    write_scheduler_heartbeat(
        phase,
        enabled_tasks=enabled_tasks,
        due_tasks=due_tasks,
        mode=mode,
        reason=reason,
        error_type=type(exc).__name__,
    )

    if should_log_connect_failure(scheduler_state):
        if permanent:
            LOGGER.error("Telegram session authorization is invalid: %s", type(exc).__name__)
            local_runtime.record_event(
                "Telegram 登录已失效，签到暂停，必须重新授权 VPS 独立会话。",
                source="runner",
            )
        else:
            LOGGER.error("Telegram connection failed: %s", type(exc).__name__, exc_info=exc)
            local_runtime.record_event("Telegram 连接失败，将继续重试。", source="runner")

    return AUTH_FAILURE_RETRY_SECONDS if permanent else POLL_SECONDS


async def run_task_batch(
    client,
    base_values: dict[str, str],
    tasks: list[dict],
    event_prefix: str,
) -> dict[str, TaskRunResult]:
    results: dict[str, TaskRunResult] = {}
    for task in tasks:
        config = tg_checkin.config_from_values(local_runtime.task_to_config_values(base_values, task))
        task_ref = task_log_ref(task)
        try:
            LOGGER.info("开始执行%s", task_ref)
            write_scheduler_heartbeat(
                "running",
                enabled_tasks=len(tasks),
                due_tasks=len(tasks),
                task_ref=task_ref,
                mode=event_prefix,
            )
            outcome = await asyncio.wait_for(
                tg_checkin.perform_checkin(client, config, dry_run=False),
                timeout=TASK_TIMEOUT_SECONDS,
            )
            results[task["id"]] = TaskRunResult(
                task_id=task["id"],
                message_sent=outcome.message_sent,
                completed=outcome.completed,
                delivery=outcome.delivery,
            )
            local_runtime.record_event(outcome_message(event_prefix, task_ref, outcome), source="runner")
        except asyncio.TimeoutError:
            results[task["id"]] = TaskRunResult(task["id"], False, False, "timeout", "timeout")
            LOGGER.exception("%s超时", task_ref)
            local_runtime.record_event(f"{event_prefix}任务超时：{task_ref}", source="runner")
        except Exception as exc:  # noqa: BLE001
            results[task["id"]] = TaskRunResult(
                task["id"],
                False,
                False,
                "failed",
                type(exc).__name__,
            )
            LOGGER.exception("%s失败", task_ref)
            local_runtime.record_event(f"{event_prefix}任务失败：{task_ref}", source="runner")
    return results


def persist_task_results(
    base_values: dict[str, str],
    tasks: list[dict],
    results: dict[str, TaskRunResult],
    scheduler_state: dict[str, dict[str, str]],
    now: datetime | None = None,
) -> None:
    for task in tasks:
        result = results.get(task["id"])
        if result is None:
            continue
        config = tg_checkin.config_from_values(local_runtime.task_to_config_values(base_values, task))
        current_day = task_day_key(config, now=now)
        updated_at = datetime.now(config.timezone).isoformat()
        task_state = dict(scheduler_state.get(task["id"], {}))
        task_state["last_attempt_at"] = updated_at
        task_state["last_result"] = result.delivery
        if result.message_sent:
            task_state["last_success_date"] = current_day
            task_state["last_success_at"] = updated_at
            task_state["last_attempt_date"] = current_day
            task_state.pop("last_failure_at", None)
            task_state.pop("last_failure_type", None)
        else:
            task_state["last_failure_at"] = updated_at
            task_state["last_failure_type"] = result.error or result.delivery
        scheduler_state[task["id"]] = task_state
        save_scheduler_state(scheduler_state)


async def run_once(base_values: dict[str, str], tasks: list[dict], dry_run: bool) -> int:
    if dry_run:
        for task in tasks:
            config = tg_checkin.config_from_values(local_runtime.task_to_config_values(base_values, task))
            LOGGER.info("校验%s", task_log_ref(task))
            tg_checkin.log_dry_run_plan(config, once=True)
        return 0

    client = await asyncio.wait_for(local_runtime.create_started_client(base_values), timeout=CONNECT_TIMEOUT_SECONDS)
    LOGGER.info("telegram session is ready")
    local_runtime.record_event(f"手动触发 {len(tasks)} 个任务。", source="runner")
    try:
        results = await run_task_batch(client, base_values, tasks, "手动")
        scheduler_state = load_scheduler_state()
        persist_task_results(base_values, tasks, results, scheduler_state)
        return 1 if any(not result.completed for result in results.values()) else 0
    finally:
        await close_client_safely(client)


async def run_scheduler(base_values: dict[str, str], tasks: list[dict]) -> int:
    local_runtime.record_event(f"签到后台已启动，共监控 {len(tasks)} 个任务。", source="runner")
    scheduler_state = load_scheduler_state()
    write_scheduler_heartbeat("boot", enabled_tasks=len(tasks))

    while True:
        try:
            base_values, tasks = load_enabled_tasks()
        except ValueError:
            write_scheduler_heartbeat("idle", enabled_tasks=0, reason="no-enabled-tasks")
            await asyncio.sleep(POLL_SECONDS)
            continue

        test_slot = load_test_slot_due(tasks)
        if test_slot is not None:
            try:
                write_scheduler_heartbeat("connecting", enabled_tasks=len(tasks), due_tasks=len(tasks), mode="test-slot")
                client = await asyncio.wait_for(
                    local_runtime.create_started_client(base_values),
                    timeout=CONNECT_TIMEOUT_SECONDS,
                )
                LOGGER.info("telegram session is ready")
                write_scheduler_heartbeat("connected", enabled_tasks=len(tasks), due_tasks=len(tasks), mode="test-slot")
            except asyncio.TimeoutError:
                LOGGER.exception("临时测试连接超时")
                local_runtime.record_event("临时测试连接超时。", source="runner")
                await asyncio.sleep(POLL_SECONDS)
                continue
            except Exception as exc:  # noqa: BLE001
                retry_seconds = record_connect_error(
                    exc,
                    scheduler_state,
                    enabled_tasks=len(tasks),
                    due_tasks=len(tasks),
                    mode="test-slot",
                )
                await asyncio.sleep(retry_seconds)
                continue

            local_runtime.record_event(
                f"开始执行临时测试时间：{test_slot.get('run_at_datetime').isoformat()}",
                source="runner",
            )
            try:
                results = await run_task_batch(client, base_values, tasks, "临时测试")
                persist_task_results(base_values, tasks, results, scheduler_state)
            finally:
                await close_client_safely(client)
                local_runtime.clear_test_slot()
                local_runtime.record_event("临时测试时间已执行并清除。", source="runner")
                write_scheduler_heartbeat("idle", enabled_tasks=len(tasks), due_tasks=0)
            await asyncio.sleep(POLL_SECONDS)
            continue

        due = collect_due_tasks(base_values, tasks, scheduler_state)
        if not due:
            write_scheduler_heartbeat("idle", enabled_tasks=len(tasks), due_tasks=0)
            await asyncio.sleep(POLL_SECONDS)
            continue

        try:
            write_scheduler_heartbeat("connecting", enabled_tasks=len(tasks), due_tasks=len(due))
            client = await asyncio.wait_for(
                local_runtime.create_started_client(base_values),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
            LOGGER.info("telegram session is ready")
            write_scheduler_heartbeat("connected", enabled_tasks=len(tasks), due_tasks=len(due))
        except asyncio.TimeoutError:
            LOGGER.exception("定时任务连接超时")
            if should_log_connect_failure(scheduler_state):
                local_runtime.record_event("定时任务连接超时，将继续重试到今天结束。", source="runner")
            write_scheduler_heartbeat("idle", enabled_tasks=len(tasks), due_tasks=0, reason="connect-timeout")
            await asyncio.sleep(POLL_SECONDS)
            continue
        except Exception as exc:  # noqa: BLE001
            retry_seconds = record_connect_error(
                exc,
                scheduler_state,
                enabled_tasks=len(tasks),
                due_tasks=len(due),
                mode="scheduled",
            )
            await asyncio.sleep(retry_seconds)
            continue

        try:
            due_tasks = [task for task, _config, _day in due]
            results = await run_task_batch(client, base_values, due_tasks, "定时")
            persist_task_results(base_values, due_tasks, results, scheduler_state)
        finally:
            await close_client_safely(client)
            write_scheduler_heartbeat("idle", enabled_tasks=len(tasks), due_tasks=0)

        await asyncio.sleep(POLL_SECONDS)


async def async_main(args: argparse.Namespace) -> int:
    base_values, tasks = load_enabled_tasks(args.task_id)
    if args.once:
        return await run_once(base_values, tasks, dry_run=args.dry_run)
    if args.dry_run:
        return await run_once(base_values, tasks, dry_run=True)
    return await run_scheduler(base_values, tasks)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.log_level)
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        LOGGER.info("stopped by user")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
