import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import service_runner


class ServiceRunnerTests(unittest.TestCase):
    def test_collect_due_tasks_returns_task_after_scheduled_time(self) -> None:
        base_values = {
            "TG_API_ID": "1",
            "TG_API_HASH": "hash",
            "TG_PHONE": "+15555550100",
            "TG_SESSION": "checkin",
            "TG_TDESKTOP_PATH": "/tmp/tdata",
            "TG_SIGNIN_TEXT": "/checkin",
            "TG_TIME": "16:00",
            "TG_TIMEZONE": "Europe/London",
            "TG_JITTER_SECONDS": "0",
            "TG_REPLY_WAIT_SECONDS": "8",
            "TG_BUTTON_TEXT": "",
            "TG_BUTTON_ROW": "",
            "TG_BUTTON_COL": "",
        }
        tasks = [
            {
                "id": "dialog:1",
                "title": "Demo Bot",
                "target": "@demo_bot",
                "target_kind": "bot",
                "peer_type": "user",
                "peer_id": "1",
                "peer_access_hash": "999",
                "enabled": True,
                "signin_text": "/checkin",
                "time": "16:00",
                "timezone": "Europe/London",
                "jitter_seconds": "0",
                "reply_wait_seconds": "8",
                "button_text": "",
                "button_row": "",
                "button_col": "",
            }
        ]
        now = datetime(2026, 5, 21, 16, 5, tzinfo=ZoneInfo("Europe/London"))

        due = service_runner.collect_due_tasks(base_values, tasks, {}, now=now)

        self.assertEqual(len(due), 1)
        self.assertEqual(due[0][0]["id"], "dialog:1")
        self.assertEqual(due[0][2], "2026-05-21")

    def test_collect_due_tasks_skips_task_successful_today(self) -> None:
        base_values = {
            "TG_API_ID": "1",
            "TG_API_HASH": "hash",
            "TG_PHONE": "+15555550100",
            "TG_SESSION": "checkin",
            "TG_TDESKTOP_PATH": "/tmp/tdata",
            "TG_SIGNIN_TEXT": "/checkin",
            "TG_TIME": "16:00",
            "TG_TIMEZONE": "Europe/London",
            "TG_JITTER_SECONDS": "0",
            "TG_REPLY_WAIT_SECONDS": "8",
            "TG_BUTTON_TEXT": "",
            "TG_BUTTON_ROW": "",
            "TG_BUTTON_COL": "",
        }
        tasks = [
            {
                "id": "dialog:1",
                "title": "Demo Bot",
                "target": "@demo_bot",
                "target_kind": "bot",
                "peer_type": "user",
                "peer_id": "1",
                "peer_access_hash": "999",
                "enabled": True,
                "signin_text": "/checkin",
                "time": "16:00",
                "timezone": "Europe/London",
                "jitter_seconds": "0",
                "reply_wait_seconds": "8",
                "button_text": "",
                "button_row": "",
                "button_col": "",
            }
        ]
        now = datetime(2026, 5, 21, 16, 5, tzinfo=ZoneInfo("Europe/London"))
        state = {"dialog:1": {"last_success_date": "2026-05-21"}}

        due = service_runner.collect_due_tasks(base_values, tasks, state, now=now)

        self.assertEqual(due, [])

    def test_collect_due_tasks_does_not_treat_legacy_attempt_as_success(self) -> None:
        base_values = {
            "TG_API_ID": "1",
            "TG_API_HASH": "hash",
            "TG_PHONE": "",
            "TG_SESSION": "checkin",
            "TG_TDESKTOP_PATH": "",
            "TG_SIGNIN_TEXT": "/checkin",
            "TG_TIME": "16:00",
            "TG_TIMEZONE": "Asia/Shanghai",
            "TG_JITTER_SECONDS": "0",
            "TG_REPLY_WAIT_SECONDS": "8",
            "TG_BUTTON_TEXT": "",
            "TG_BUTTON_ROW": "",
            "TG_BUTTON_COL": "",
        }
        tasks = [
            {
                "id": "dialog:1",
                "title": "Demo",
                "target": "@demo",
                "target_kind": "bot",
                "peer_type": "user",
                "peer_id": "1",
                "peer_access_hash": "999",
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
        ]
        now = datetime(2026, 5, 21, 16, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
        state = {"dialog:1": {"last_attempt_date": "2026-05-21"}}

        due = service_runner.collect_due_tasks(base_values, tasks, state, now=now)

        self.assertEqual(len(due), 1)

    def test_scheduler_state_round_trip(self) -> None:
        payload = {"dialog:1": {"last_attempt_date": "2026-05-21", "updated_at": "2026-05-21T16:00:01"}}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".scheduler_state.json"
            with patch.object(service_runner, "SCHEDULER_STATE_PATH", path):
                service_runner.save_scheduler_state(payload)
                loaded = service_runner.load_scheduler_state()

        self.assertEqual(loaded, payload)

    def test_collect_due_tasks_retries_overdue_run_until_end_of_day(self) -> None:
        base_values = {
            "TG_API_ID": "1",
            "TG_API_HASH": "hash",
            "TG_PHONE": "+15555550100",
            "TG_SESSION": "checkin",
            "TG_TDESKTOP_PATH": "/tmp/tdata",
            "TG_SIGNIN_TEXT": "/checkin",
            "TG_TIME": "00:30",
            "TG_TIMEZONE": "SYSTEM",
            "TG_JITTER_SECONDS": "0",
            "TG_REPLY_WAIT_SECONDS": "8",
            "TG_BUTTON_TEXT": "",
            "TG_BUTTON_ROW": "",
            "TG_BUTTON_COL": "",
        }
        tasks = [
            {
                "id": "dialog:1",
                "title": "Demo Bot",
                "target": "@demo_bot",
                "target_kind": "bot",
                "peer_type": "user",
                "peer_id": "1",
                "peer_access_hash": "999",
                "enabled": True,
                "signin_text": "/checkin",
                "time": "00:30",
                "timezone": "SYSTEM",
                "jitter_seconds": "0",
                "reply_wait_seconds": "8",
                "button_text": "",
                "button_row": "",
                "button_col": "",
            }
        ]
        now = datetime(2026, 5, 29, 11, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

        due = service_runner.collect_due_tasks(base_values, tasks, {}, now=now)

        self.assertEqual(len(due), 1)
        self.assertEqual(due[0][0]["id"], "dialog:1")

    def test_should_log_connect_failure_only_once_per_hour(self) -> None:
        state: dict[str, dict[str, str]] = {}
        now = datetime(2026, 7, 5, 20, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".scheduler_state.json"
            with patch.object(service_runner, "SCHEDULER_STATE_PATH", path):
                first = service_runner.should_log_connect_failure(state, now=now)
                second = service_runner.should_log_connect_failure(state, now=now.replace(minute=30))
                third = service_runner.should_log_connect_failure(state, now=now.replace(hour=21))

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(third)

    def test_permanent_auth_error_detects_direct_and_chained_errors(self) -> None:
        direct = service_runner.tg_checkin.errors.AuthKeyDuplicatedError(request=None)
        wrapped = RuntimeError("connect failed")
        wrapped.__cause__ = service_runner.tg_checkin.errors.SessionRevokedError(request=None)

        self.assertTrue(service_runner.is_permanent_auth_error(direct))
        self.assertTrue(service_runner.is_permanent_auth_error(wrapped))
        self.assertFalse(service_runner.is_permanent_auth_error(ConnectionError("offline")))

    def test_record_connect_error_marks_auth_failure_and_backs_off(self) -> None:
        state: dict[str, dict[str, str]] = {}
        error = service_runner.tg_checkin.errors.AuthKeyUnregisteredError(request=None)
        with patch("service_runner.should_log_connect_failure", return_value=True):
            with patch("service_runner.write_scheduler_heartbeat") as heartbeat:
                with patch("service_runner.local_runtime.record_event") as event:
                    retry = service_runner.record_connect_error(error, state, 4, 4, "scheduled")

        self.assertEqual(retry, service_runner.AUTH_FAILURE_RETRY_SECONDS)
        heartbeat.assert_called_once_with(
            "auth-error",
            enabled_tasks=4,
            due_tasks=4,
            mode="scheduled",
            reason="auth-invalid",
            error_type="AuthKeyUnregisteredError",
        )
        self.assertIn("重新授权", event.call_args.args[0])

    def test_load_test_slot_due_returns_payload_within_grace_window(self) -> None:
        now = datetime(2026, 5, 29, 14, 24, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        tasks = [{"id": "dialog:1"}]
        payload = {
            "time": "14:24",
            "run_at": "2026-05-29T14:24:00+08:00",
            "active": True,
        }
        with patch("service_runner.local_runtime.load_test_slot", return_value=payload):
            result = service_runner.load_test_slot_due(tasks, now=now)

        self.assertIsNotNone(result)
        self.assertEqual(result["run_at_datetime"].isoformat(), "2026-05-29T14:24:00+08:00")

    def test_run_task_batch_keeps_running_after_one_task_fails(self) -> None:
        base_values = {
            "TG_API_ID": "1",
            "TG_API_HASH": "hash",
            "TG_PHONE": "",
            "TG_SESSION": "checkin",
            "TG_TDESKTOP_PATH": "",
            "TG_SIGNIN_TEXT": "/checkin",
            "TG_TIME": "20:00",
            "TG_TIMEZONE": "Asia/Shanghai",
            "TG_JITTER_SECONDS": "0",
            "TG_REPLY_WAIT_SECONDS": "8",
            "TG_BUTTON_TEXT": "",
            "TG_BUTTON_ROW": "",
            "TG_BUTTON_COL": "",
        }
        tasks = [
            {"id": "one", "target": "@one", "peer_type": "", "peer_id": "", "peer_access_hash": "", "signin_text": "/a"},
            {"id": "two", "target": "@two", "peer_type": "", "peer_id": "", "peer_access_hash": "", "signin_text": "/b"},
        ]
        calls = 0

        async def fake_checkin(_client, _config, dry_run=False):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("send failed")
            return service_runner.tg_checkin.CheckinOutcome(
                "sent",
                "已发送签到命令。",
                message_sent=True,
                completed=True,
            )

        with patch("service_runner.tg_checkin.perform_checkin", side_effect=fake_checkin):
            with patch("service_runner.local_runtime.record_event"):
                with patch("service_runner.write_scheduler_heartbeat"):
                    results = asyncio.run(service_runner.run_task_batch(object(), base_values, tasks, "测试"))

        self.assertEqual(calls, 2)
        self.assertFalse(results["one"].message_sent)
        self.assertTrue(results["two"].message_sent)

    def test_persist_task_results_only_marks_real_send_as_success(self) -> None:
        base_values = {
            "TG_API_ID": "1",
            "TG_API_HASH": "hash",
            "TG_PHONE": "",
            "TG_SESSION": "checkin",
            "TG_TDESKTOP_PATH": "",
            "TG_SIGNIN_TEXT": "/checkin",
            "TG_TIME": "20:00",
            "TG_TIMEZONE": "Asia/Shanghai",
            "TG_JITTER_SECONDS": "0",
            "TG_REPLY_WAIT_SECONDS": "8",
            "TG_BUTTON_TEXT": "",
            "TG_BUTTON_ROW": "",
            "TG_BUTTON_COL": "",
        }
        tasks = [
            {"id": "sent", "target": "@one", "peer_type": "", "peer_id": "", "peer_access_hash": "", "signin_text": "/a"},
            {"id": "failed", "target": "@two", "peer_type": "", "peer_id": "", "peer_access_hash": "", "signin_text": "/b"},
        ]
        results = {
            "sent": service_runner.TaskRunResult("sent", True, True, "sent"),
            "failed": service_runner.TaskRunResult("failed", False, False, "failed", "RuntimeError"),
        }
        state: dict[str, dict[str, str]] = {}
        now = datetime(2026, 5, 21, 20, 1, tzinfo=ZoneInfo("Asia/Shanghai"))

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".scheduler_state.json"
            with patch.object(service_runner, "SCHEDULER_STATE_PATH", path):
                service_runner.persist_task_results(base_values, tasks, results, state, now=now)

        self.assertEqual(state["sent"]["last_success_date"], "2026-05-21")
        self.assertNotIn("last_success_date", state["failed"])
        self.assertEqual(state["failed"]["last_failure_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
