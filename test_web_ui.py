import unittest
from unittest.mock import patch

import web_ui


class WebUiTests(unittest.TestCase):
    def test_resolve_request_values_uses_saved_values_as_base(self) -> None:
        saved = {
            "TG_API_ID": "",
            "TG_API_HASH": "",
            "TG_PHONE": "+15555550100",
            "TG_SESSION": "checkin",
            "TG_TDESKTOP_PATH": "/tmp/tdata",
            "TG_SIGNIN_TEXT": "/checkin",
            "TG_TIME": "09:00",
            "TG_TIMEZONE": "Europe/London",
            "TG_JITTER_SECONDS": "0",
            "TG_REPLY_WAIT_SECONDS": "8",
            "TG_BUTTON_TEXT": "",
            "TG_BUTTON_ROW": "",
            "TG_BUTTON_COL": "",
        }

        with patch("web_ui.local_runtime.load_env_values", return_value=saved):
            values = web_ui.resolve_request_values({})

        self.assertEqual(values["TG_TDESKTOP_PATH"], "/tmp/tdata")
        self.assertEqual(values["TG_TIME"], "09:00")

    def test_resolve_request_values_keeps_imported_credentials_when_payload_is_blank(self) -> None:
        saved = {
            "TG_API_ID": "2040",
            "TG_API_HASH": "desktop-hash",
            "TG_PHONE": "+15555550100",
            "TG_SESSION": "checkin",
            "TG_TDESKTOP_PATH": "/tmp/tdata",
            "TG_SIGNIN_TEXT": "/checkin",
            "TG_TIME": "09:00",
            "TG_TIMEZONE": "Europe/London",
            "TG_JITTER_SECONDS": "0",
            "TG_REPLY_WAIT_SECONDS": "8",
            "TG_BUTTON_TEXT": "",
            "TG_BUTTON_ROW": "",
            "TG_BUTTON_COL": "",
        }

        with patch("web_ui.local_runtime.load_env_values", return_value=saved):
            values = web_ui.resolve_request_values({"TG_API_ID": "", "TG_API_HASH": "", "TG_TDESKTOP_PATH": ""})

        self.assertEqual(values["TG_API_ID"], "2040")
        self.assertEqual(values["TG_API_HASH"], "desktop-hash")
        self.assertEqual(values["TG_TDESKTOP_PATH"], "/tmp/tdata")

    def test_build_state_payload_includes_test_slot(self) -> None:
        values = {
            "TG_API_ID": "1",
            "TG_API_HASH": "hash",
            "TG_PHONE": "+15555550100",
            "TG_SESSION": "checkin",
            "TG_SIGNIN_TEXT": "/checkin",
            "TG_TIME": "09:00",
            "TG_TIMEZONE": "Europe/London",
            "TG_JITTER_SECONDS": "0",
            "TG_REPLY_WAIT_SECONDS": "8",
            "TG_BUTTON_TEXT": "",
            "TG_BUTTON_ROW": "",
            "TG_BUTTON_COL": "",
        }
        tasks = [
            {
                "id": "one",
                "title": "Task One",
                "target": "@one",
                "target_kind": "bot",
                "enabled": True,
                "signin_text": "/one",
                "time": "09:00",
                "timezone": "Europe/London",
                "jitter_seconds": "0",
                "reply_wait_seconds": "8",
                "button_text": "",
                "button_row": "",
                "button_col": "",
            }
        ]

        with patch("web_ui.local_runtime.load_env_values", return_value=values):
            with patch("web_ui.local_runtime.get_effective_tasks", return_value=tasks):
                with patch("web_ui.local_runtime.has_saved_session", return_value=True):
                    with patch("web_ui.local_runtime.get_launch_agent_status", return_value={"loaded": False, "installed": False}):
                        with patch("web_ui.local_runtime.load_test_slot", return_value={"time": "14:30", "run_at": "2026-05-29T14:30:00+08:00"}):
                            payload = web_ui.build_state_payload()

        self.assertTrue(payload["session_ok"])
        self.assertEqual(payload["tasks"][0]["id"], "one")
        self.assertEqual(payload["nearest_run"]["task_id"], "one")
        self.assertEqual(payload["test_slot"]["time"], "14:30")

    def test_build_state_payload_includes_nearest_run(self) -> None:
        values = {
            "TG_API_ID": "1",
            "TG_API_HASH": "hash",
            "TG_PHONE": "+15555550100",
            "TG_SESSION": "checkin",
            "TG_SIGNIN_TEXT": "/checkin",
            "TG_TIME": "09:00",
            "TG_TIMEZONE": "Europe/London",
            "TG_JITTER_SECONDS": "0",
            "TG_REPLY_WAIT_SECONDS": "8",
            "TG_BUTTON_TEXT": "",
            "TG_BUTTON_ROW": "",
            "TG_BUTTON_COL": "",
        }
        tasks = [
            {
                "id": "one",
                "title": "Task One",
                "target": "@one",
                "target_kind": "bot",
                "enabled": True,
                "signin_text": "/one",
                "time": "09:00",
                "timezone": "Europe/London",
                "jitter_seconds": "0",
                "reply_wait_seconds": "8",
                "button_text": "",
                "button_row": "",
                "button_col": "",
            }
        ]

        with patch("web_ui.local_runtime.load_env_values", return_value=values):
            with patch("web_ui.local_runtime.get_effective_tasks", return_value=tasks):
                with patch("web_ui.local_runtime.has_saved_session", return_value=True):
                    with patch("web_ui.local_runtime.get_launch_agent_status", return_value={"loaded": False, "installed": False}):
                        with patch("web_ui.local_runtime.load_test_slot", return_value={}):
                            payload = web_ui.build_state_payload()

        self.assertTrue(payload["session_ok"])
        self.assertEqual(payload["tasks"][0]["id"], "one")
        self.assertEqual(payload["nearest_run"]["task_id"], "one")

    def test_build_state_payload_handles_desktop_import_mode_without_api_credentials(self) -> None:
        values = {
            "TG_API_ID": "",
            "TG_API_HASH": "",
            "TG_PHONE": "+15555550100",
            "TG_SESSION": "desktop-import",
            "TG_TDESKTOP_PATH": "/home/example/TelegramDesktop/tdata",
            "TG_SIGNIN_TEXT": "/checkin",
            "TG_TIME": "09:00",
            "TG_TIMEZONE": "Europe/London",
            "TG_JITTER_SECONDS": "0",
            "TG_REPLY_WAIT_SECONDS": "8",
            "TG_BUTTON_TEXT": "",
            "TG_BUTTON_ROW": "",
            "TG_BUTTON_COL": "",
        }
        tasks = [
            {
                "id": "one",
                "title": "Task One",
                "target": "@one",
                "target_kind": "bot",
                "enabled": True,
                "signin_text": "/one",
                "time": "09:00",
                "timezone": "Europe/London",
                "jitter_seconds": "0",
                "reply_wait_seconds": "8",
                "button_text": "",
                "button_row": "",
                "button_col": "",
            }
        ]

        with patch("web_ui.local_runtime.load_env_values", return_value=values):
            with patch("web_ui.local_runtime.get_effective_tasks", return_value=tasks):
                with patch("web_ui.local_runtime.has_saved_session", return_value=True):
                    with patch("web_ui.local_runtime.get_launch_agent_status", return_value={"loaded": False, "installed": False}):
                        with patch("web_ui.local_runtime.load_test_slot", return_value={}):
                            payload = web_ui.build_state_payload()

        self.assertTrue(payload["session_ok"])
        self.assertEqual(payload["values"]["TG_API_ID"], "")
        self.assertEqual(payload["nearest_run"]["task_id"], "one")


if __name__ == "__main__":
    unittest.main()
