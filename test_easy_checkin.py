import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import easy_checkin


class EasyCheckinTests(unittest.TestCase):
    def test_setup_writes_env_and_defaults_to_once(self) -> None:
        answers = [
            "12345",
            "hash",
            "+15555550100",
            "checkin",
            "@demo_bot",
            "/checkin",
            "09:00",
            "Europe/London",
            "0",
            "8",
            "n",
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            script_dir = Path(tmpdir)

            with patch.object(easy_checkin, "ENV_PATH", env_path):
                with patch.object(easy_checkin, "SCRIPT_DIR", script_dir):
                    with patch("builtins.input", side_effect=answers):
                        with patch.object(sys, "argv", ["easy_checkin.py", "--setup"]):
                            with patch("easy_checkin.tg_checkin.main", return_value=0) as mock_main:
                                result = easy_checkin.main()
                                self.assertEqual(result, 0)
                                self.assertTrue(env_path.exists())
                                self.assertIn("TG_API_ID='12345'", env_path.read_text())
                                self.assertIn("TG_TARGET='@demo_bot'", env_path.read_text())
                                mock_main.assert_called_once_with(["--once"])

    def test_passthrough_args_are_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("TG_API_ID='1'\n")
            script_dir = Path(tmpdir)

            with patch.object(easy_checkin, "ENV_PATH", env_path):
                with patch.object(easy_checkin, "SCRIPT_DIR", script_dir):
                    with patch.object(sys, "argv", ["easy_checkin.py", "--dry-run", "--once"]):
                        with patch("easy_checkin.tg_checkin.main", return_value=0) as mock_main:
                            result = easy_checkin.main()

        self.assertEqual(result, 0)
        mock_main.assert_called_once_with(["--dry-run", "--once"])


if __name__ == "__main__":
    unittest.main()
