import plistlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import subprocess
from zoneinfo import ZoneInfo

import local_runtime
import tg_checkin


class LocalRuntimeTests(unittest.TestCase):
    def test_save_and_load_env_values_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            values = dict(local_runtime.FIELD_DEFAULTS)
            values["TG_API_ID"] = "12345"
            values["TG_TARGET"] = "@demo_bot"

            with patch.object(local_runtime, "ENV_PATH", env_path):
                local_runtime.save_env_values(values)
                loaded = local_runtime.load_env_values()

        self.assertEqual(loaded["TG_API_ID"], "12345")
        self.assertEqual(loaded["TG_TARGET"], "@demo_bot")
        self.assertEqual(loaded["TG_SESSION"], "checkin")

    def test_parse_launchctl_status_dict_output(self) -> None:
        output = """
{
    "Label" = "local.telegram.checkin";
    "PID" = 1234;
    "LastExitStatus" = 0;
}
"""
        parsed = local_runtime.parse_launchctl_status(output)
        self.assertTrue(parsed["loaded"])
        self.assertEqual(parsed["pid"], 1234)
        self.assertEqual(parsed["last_exit_status"], 0)

    def test_parse_launchctl_status_tab_output(self) -> None:
        output = "-\t0\tlocal.telegram.checkin\n"
        parsed = local_runtime.parse_launchctl_status(output)
        self.assertTrue(parsed["loaded"])
        self.assertIsNone(parsed["pid"])
        self.assertEqual(parsed["last_exit_status"], 0)

    def test_parse_launchctl_status_accepts_watchdog_label(self) -> None:
        output = "1234\t0\tlocal.telegram.checkin.webwatch\n"
        parsed = local_runtime.parse_launchctl_status(
            output,
            expected_label=local_runtime.WEB_WATCHDOG_LABEL,
        )
        self.assertTrue(parsed["loaded"])
        self.assertEqual(parsed["pid"], 1234)

    def test_build_launch_agent_plist_contains_scheduler_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            stdout_path = Path(tmpdir) / "stdout.log"
            stderr_path = Path(tmpdir) / "stderr.log"

            with patch.object(local_runtime, "SERVICE_STDOUT_PATH", stdout_path):
                with patch.object(local_runtime, "SERVICE_STDERR_PATH", stderr_path):
                    with patch.object(local_runtime, "LOG_DIR", Path(tmpdir)):
                        payload = plistlib.loads(local_runtime.build_launch_agent_plist())

        self.assertEqual(payload["Label"], local_runtime.SERVICE_LABEL)
        self.assertTrue(payload["ProgramArguments"][-1].endswith("service_runner.py"))
        self.assertEqual(payload["WorkingDirectory"], str(local_runtime.DATA_DIR))

    def test_save_and_load_tasks_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            task_path = Path(tmpdir) / "tasks.json"
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
                    "time": "09:00",
                    "timezone": "Europe/London",
                    "jitter_seconds": "0",
                    "reply_wait_seconds": "8",
                    "button_text": "",
                    "button_row": "",
                    "button_col": "",
                }
            ]
            with patch.object(local_runtime, "TASKS_PATH", task_path):
                local_runtime.save_tasks(tasks)
                loaded = local_runtime.load_tasks()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["target"], "@demo_bot")
        self.assertTrue(loaded[0]["enabled"])
        self.assertEqual(loaded[0]["peer_access_hash"], "999")

    def test_validate_global_values_allows_blank_api_fields(self) -> None:
        values = dict(local_runtime.FIELD_DEFAULTS)
        values["TG_SESSION"] = "desktop-import"
        values["TG_PHONE"] = "+15555550100"

        validated = local_runtime.validate_global_values(values)

        self.assertEqual(validated["TG_API_ID"], "")
        self.assertEqual(validated["TG_API_HASH"], "")

    def test_task_to_config_values_injects_runtime_placeholders(self) -> None:
        values = dict(local_runtime.FIELD_DEFAULTS)
        task = {
            "id": "dialog:1",
            "title": "Demo Bot",
            "target": "@demo_bot",
            "target_kind": "bot",
            "peer_type": "user",
            "peer_id": "1",
            "peer_access_hash": "999",
            "enabled": True,
            "signin_text": "/checkin",
            "time": "09:00",
            "timezone": "Europe/London",
            "jitter_seconds": "0",
            "reply_wait_seconds": "8",
            "button_text": "",
            "button_row": "",
            "button_col": "",
        }

        config = tg_checkin.config_from_values(local_runtime.task_to_config_values(values, task))

        self.assertEqual(config.target, "@demo_bot")
        self.assertEqual(config.api_id, 1)
        self.assertEqual(config.api_hash, "placeholder_hash")
        self.assertEqual(config.peer_type, "user")
        self.assertEqual(config.peer_id, 1)
        self.assertEqual(config.peer_access_hash, 999)

    def test_clone_imported_session_uses_temporary_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            session_file = Path(tmpdir) / "checkin.session"
            json_file = Path(tmpdir) / "checkin.json"
            session_file.write_bytes(b"sqlite-placeholder")
            json_file.write_text('{"app_id": 1, "app_hash": "hash"}', encoding="utf-8")

            with patch("local_runtime.session_path", return_value=session_file):
                with patch("local_runtime.session_json_path", return_value=json_file):
                    temp_dir, temp_base, source_json = local_runtime.clone_imported_session({"TG_SESSION": "checkin"})
                    try:
                        self.assertNotEqual(Path(f"{temp_base}.session"), session_file)
                        self.assertTrue(Path(f"{temp_base}.session").exists())
                        self.assertEqual(Path(f"{temp_base}.session").read_bytes(), b"sqlite-placeholder")
                        self.assertEqual(source_json, str(json_file))
                    finally:
                        temp_dir.cleanup()

    def test_validate_global_values_restores_defaults_for_empty_fields(self) -> None:
        values = dict(local_runtime.FIELD_DEFAULTS)
        values["TG_SIGNIN_TEXT"] = ""
        values["TG_TIME"] = ""
        values["TG_TIMEZONE"] = ""
        values["TG_JITTER_SECONDS"] = ""
        values["TG_REPLY_WAIT_SECONDS"] = ""

        validated = local_runtime.validate_global_values(values)

        self.assertEqual(validated["TG_SIGNIN_TEXT"], "/checkin")
        self.assertEqual(validated["TG_TIME"], "09:00")
        self.assertEqual(validated["TG_TIMEZONE"], "SYSTEM")
        self.assertEqual(validated["TG_JITTER_SECONDS"], "0")
        self.assertEqual(validated["TG_REPLY_WAIT_SECONDS"], "8")

    def test_has_saved_session_accepts_tdesktop_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            values = dict(local_runtime.FIELD_DEFAULTS)
            values["TG_TDESKTOP_PATH"] = tmpdir
            self.assertTrue(local_runtime.has_saved_session(values))

    def test_import_from_tdesktop_populates_api_fields(self) -> None:
        class FakeClient:
            disconnected = None

            async def connect(self):
                return None

            async def is_user_authorized(self):
                return True

            async def get_me(self):
                return SimpleNamespace(phone="15555550101")

            async def disconnect(self):
                return None

        class FakeTDesktop:
            def __init__(self, path: str):
                self.path = path
                self.api = SimpleNamespace(api_id=2040, api_hash="desktop-hash")

            async def ToTelethon(self, session=None, flag=None):
                return FakeClient()

        values = dict(local_runtime.FIELD_DEFAULTS)
        values["TG_SESSION"] = "checkin"

        with patch("local_runtime.find_tdesktop_paths", return_value=[Path("/tmp/tdata")]):
            with patch("local_runtime.TDesktop", FakeTDesktop):
                with patch("local_runtime.OpenTeleClient", object()):
                    with patch("local_runtime.UseCurrentSession", object()):
                        import asyncio

                        updated = asyncio.run(local_runtime.import_from_tdesktop(values))

        self.assertEqual(updated["TG_API_ID"], "2040")
        self.assertEqual(updated["TG_API_HASH"], "desktop-hash")
        self.assertEqual(updated["TG_PHONE"], "+15555550101")

    def test_create_started_client_closes_client_when_start_fails(self) -> None:
        class FakeSession:
            def close(self):
                return None

        class FakeClient:
            disconnected = None

            def __init__(self, *_args, **_kwargs):
                self.session = FakeSession()
                self.closed = False

            async def start(self, phone=None):
                raise tg_checkin.errors.AuthKeyUnregisteredError(request=None)

            async def disconnect(self):
                self.closed = True

        values = dict(local_runtime.FIELD_DEFAULTS)
        values["TG_API_ID"] = "1"
        values["TG_API_HASH"] = "hash"
        created: list[FakeClient] = []

        def make_client(*args, **kwargs):
            client = FakeClient(*args, **kwargs)
            created.append(client)
            return client

        with patch("local_runtime.TelegramClient", side_effect=make_client):
            with self.assertRaises(tg_checkin.errors.AuthKeyUnregisteredError):
                import asyncio

                asyncio.run(local_runtime.create_started_client(values))

        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)

    def test_tail_logs_reads_operation_log_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            event_path = log_dir / "events.log"
            event_path.write_text("2026-05-20 21:08:33 | system | 已安装并启动。\n", encoding="utf-8")

            with patch.object(local_runtime, "LOG_DIR", log_dir):
                with patch.object(local_runtime, "EVENT_LOG_PATH", event_path):
                    self.assertEqual(local_runtime.tail_logs(), "2026-05-20 21:08:33 | system | 已安装并启动。")

    def test_get_launch_agent_status_reports_web_monitor(self) -> None:
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout='-\t0\tlocal.telegram.checkin\n', stderr="")
        with patch.object(local_runtime, "IS_LINUX", False):
            with patch.object(local_runtime, "launch_agent_path", return_value=Path("/tmp/local.telegram.checkin.plist")):
                with patch.object(local_runtime.Path, "exists", return_value=True):
                    with patch.object(local_runtime, "run_local_command", return_value=fake_result):
                        with patch.object(local_runtime, "is_web_port_open", return_value=False):
                            with patch.object(local_runtime, "web_server_pid", return_value=None):
                                with patch.object(local_runtime, "web_monitor_pid", return_value=3210):
                                    with patch.object(local_runtime, "load_scheduler_heartbeat", return_value={"phase": "idle"}):
                                        with patch.object(local_runtime, "scheduler_heartbeat_age_seconds", return_value=10.0):
                                            with patch.object(local_runtime, "login_item_names", return_value=[local_runtime.LOGIN_ITEM_NAME]):
                                                status = local_runtime.get_launch_agent_status()

        self.assertTrue(status["web_loaded"])
        self.assertEqual(status["web_monitor_pid"], 3210)
        self.assertEqual(status["web"]["monitor_pid"], 3210)
        self.assertFalse(status["web_port_open"])

    def test_install_and_start_launch_agent_cleans_legacy_web_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            install_path = Path(tmpdir) / "local.telegram.checkin.plist"
            legacy_web_path = Path(tmpdir) / "local.telegram.checkin.webui.plist"
            legacy_watchdog_path = Path(tmpdir) / "local.telegram.checkin.webwatch.plist"
            legacy_web_path.write_text("web", encoding="utf-8")
            legacy_watchdog_path.write_text("watch", encoding="utf-8")

            with patch.object(local_runtime, "IS_LINUX", False):
                with patch.object(local_runtime, "deploy_runtime"):
                    with patch.object(local_runtime, "install_launch_agent", return_value=install_path):
                        with patch.object(local_runtime, "install_web_watchdog_launch_agent", return_value=legacy_watchdog_path):
                            with patch.object(local_runtime, "restart_launch_agent") as restart_mock:
                                with patch.object(local_runtime, "run_local_command") as run_mock:
                                    with patch.object(local_runtime, "web_launch_agent_path", return_value=legacy_web_path):
                                        with patch.object(local_runtime, "remove_background_login_item"):
                                            with patch.object(local_runtime, "is_web_port_open", return_value=True):
                                                with patch.object(local_runtime, "scheduler_is_stale", return_value=False):
                                                    with patch.object(local_runtime, "record_event"):
                                                        result = local_runtime.install_and_start_launch_agent()

        self.assertEqual(result, str(install_path))
        self.assertEqual(restart_mock.call_count, 2)
        restart_mock.assert_any_call(local_runtime.launch_target(), install_path)
        restart_mock.assert_any_call(local_runtime.web_watchdog_launch_target(), legacy_watchdog_path)
        self.assertFalse(legacy_web_path.exists())
        self.assertEqual(run_mock.call_count, 2)

    def test_scheduler_heartbeat_round_trip_and_stale_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            heartbeat_path = Path(tmpdir) / ".scheduler_heartbeat.json"
            with patch.object(local_runtime, "SCHEDULER_HEARTBEAT_PATH", heartbeat_path):
                with patch("local_runtime.time.time", return_value=1000.0):
                    local_runtime.write_scheduler_heartbeat("idle", enabled_tasks=4)

                payload = local_runtime.load_scheduler_heartbeat()
                self.assertEqual(payload["phase"], "idle")
                self.assertEqual(payload["enabled_tasks"], 4)

                with patch("local_runtime.time.time", return_value=1100.0):
                    age = local_runtime.scheduler_heartbeat_age_seconds()
                self.assertEqual(age, 100.0)

                with patch("local_runtime.time.time", return_value=1300.0):
                    self.assertTrue(local_runtime.scheduler_is_stale(max_age_seconds=180.0))

    def test_scheduler_service_without_pid_is_stale(self) -> None:
        fake_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="-\t78\tlocal.telegram.checkin\n",
            stderr="",
        )
        with patch.object(local_runtime, "IS_LINUX", False):
            with patch.object(local_runtime.Path, "exists", return_value=True):
                with patch.object(local_runtime, "run_local_command", return_value=fake_result):
                    with patch.object(local_runtime, "load_scheduler_heartbeat", return_value={"phase": "idle"}):
                        with patch.object(local_runtime, "scheduler_heartbeat_age_seconds", return_value=10.0):
                            status = local_runtime.get_scheduler_service_status()

        self.assertTrue(status["loaded"])
        self.assertIsNone(status["pid"])
        self.assertTrue(status["stale"])

    def test_scheduler_service_reports_auth_error_from_heartbeat(self) -> None:
        fake_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="1234\t0\tlocal.telegram.checkin\n",
            stderr="",
        )
        with patch.object(local_runtime, "IS_LINUX", False):
            with patch.object(local_runtime.Path, "exists", return_value=True):
                with patch.object(local_runtime, "run_local_command", return_value=fake_result):
                    with patch.object(local_runtime, "load_scheduler_heartbeat", return_value={"phase": "auth-error"}):
                        with patch.object(local_runtime, "scheduler_heartbeat_age_seconds", return_value=10.0):
                            status = local_runtime.get_scheduler_service_status()

        self.assertTrue(status["auth_error"])
        self.assertFalse(status["stale"])

    def test_schedule_test_slot_uses_next_occurrence(self) -> None:
        values = dict(local_runtime.FIELD_DEFAULTS)
        values["TG_TIMEZONE"] = "SYSTEM"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".test_slot.json"
            fake_now = datetime(2026, 5, 29, 14, 24, tzinfo=ZoneInfo("Asia/Shanghai"))
            with patch.object(local_runtime, "TEST_SLOT_PATH", path):
                with patch("local_runtime.tg_checkin.detect_system_timezone", return_value=ZoneInfo("Asia/Shanghai")):
                    with patch("local_runtime.datetime") as mock_datetime:
                        mock_datetime.now.return_value = fake_now
                        payload = local_runtime.schedule_test_slot(values, "14:30")

        self.assertEqual(payload["time"], "14:30")
        self.assertEqual(payload["run_at"], "2026-05-29T14:30:00+08:00")

    def test_start_web_background_starts_monitor_even_when_port_is_already_open(self) -> None:
        with patch.object(local_runtime, "IS_LINUX", False):
            with patch.object(local_runtime, "web_monitor_pid", side_effect=[None, 4321, 4321]):
                with patch.object(local_runtime, "start_web_monitor_background") as monitor_mock:
                    with patch.object(local_runtime, "is_web_port_open", return_value=True):
                        local_runtime.start_web_background()

        monitor_mock.assert_called_once()

    def test_is_web_port_open_rejects_wrong_local_service(self) -> None:
        class FakeResponse:
            status = 404

            def read(self, _limit: int) -> bytes:
                return b"not found"

        class FakeConnection:
            def __init__(self, *_args, **_kwargs):
                pass

            def request(self, *_args, **_kwargs):
                return None

            def getresponse(self):
                return FakeResponse()

            def close(self):
                return None

        with patch.object(local_runtime.http.client, "HTTPConnection", FakeConnection):
            self.assertFalse(local_runtime.is_web_port_open())

    def test_is_web_port_open_accepts_health_endpoint(self) -> None:
        requests: list[str] = []

        class FakeResponse:
            status = 200

            def read(self, _limit: int) -> bytes:
                return b'{"ok": true, "app": "telegram-checkin"}'

        class FakeConnection:
            def __init__(self, *_args, **_kwargs):
                pass

            def request(self, _method: str, path: str):
                requests.append(path)

            def getresponse(self):
                return FakeResponse()

            def close(self):
                return None

        with patch.object(local_runtime.http.client, "HTTPConnection", FakeConnection):
            self.assertTrue(local_runtime.is_web_port_open())

        self.assertEqual(requests, ["/api/health"])


if __name__ == "__main__":
    unittest.main()
