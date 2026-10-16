import asyncio
import os
import unittest
from datetime import datetime
from unittest.mock import patch

import tg_checkin


class LoadConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(
            os.environ,
            {
                "TG_API_ID": "12345",
                "TG_API_HASH": "hash",
                "TG_PHONE": "+1000000000001",
                "TG_SESSION": "test-session",
                "TG_TARGET": "@demo_bot",
                "TG_SIGNIN_TEXT": "/checkin",
                "TG_TIME": "09:30",
                "TG_TIMEZONE": "Europe/London",
                "TG_JITTER_SECONDS": "15",
                "TG_REPLY_WAIT_SECONDS": "8",
            },
            clear=True,
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()

    def test_load_config_from_env(self) -> None:
        args = tg_checkin.build_parser().parse_args([])
        config = tg_checkin.load_config(args)

        self.assertEqual(config.api_id, 12345)
        self.assertEqual(config.api_hash, "hash")
        self.assertEqual(config.phone, "+1000000000001")
        self.assertEqual(config.session, "test-session")
        self.assertEqual(config.target, "@demo_bot")
        self.assertEqual(config.signin_text, "/checkin")
        self.assertEqual(config.run_time.hour, 9)
        self.assertEqual(config.run_time.minute, 30)
        self.assertEqual(config.timezone.key, "Europe/London")
        self.assertEqual(config.jitter_seconds, 15)
        self.assertEqual(config.reply_wait_seconds, 8)
        self.assertIsNone(config.proxy)

    def test_button_row_and_col_must_be_set_together(self) -> None:
        with patch.dict(os.environ, {"TG_BUTTON_ROW": "0"}, clear=False):
            args = tg_checkin.build_parser().parse_args([])
            with self.assertRaisesRegex(ValueError, "TG_BUTTON_ROW and TG_BUTTON_COL"):
                tg_checkin.load_config(args)

    def test_button_text_and_index_conflict(self) -> None:
        with patch.dict(
            os.environ,
            {"TG_BUTTON_TEXT": "Sign in", "TG_BUTTON_ROW": "0", "TG_BUTTON_COL": "0"},
            clear=False,
        ):
            args = tg_checkin.build_parser().parse_args([])
            with self.assertRaisesRegex(ValueError, "use either TG_BUTTON_TEXT or TG_BUTTON_ROW/COL"):
                tg_checkin.load_config(args)

    def test_proxy_settings_are_parsed_for_telethon(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TG_PROXY_TYPE": "socks5",
                "TG_PROXY_HOST": "127.0.0.1",
                "TG_PROXY_PORT": "40000",
            },
            clear=False,
        ):
            args = tg_checkin.build_parser().parse_args([])
            config = tg_checkin.load_config(args)

        self.assertEqual(config.proxy, ("socks5", "127.0.0.1", 40000, True))

    def test_parse_timezone_system_uses_detected_timezone(self) -> None:
        with patch("tg_checkin.detect_system_timezone_key", return_value="Asia/Shanghai"):
            timezone = tg_checkin.parse_timezone("SYSTEM")

        self.assertEqual(timezone.key, "Asia/Shanghai")


class NextRunAtTests(unittest.TestCase):
    def make_config(self) -> tg_checkin.Config:
        return tg_checkin.Config(
            api_id=1,
            api_hash="hash",
            phone=None,
            session="checkin",
            target="@demo_bot",
            signin_text="/checkin",
            button_text=None,
            button_row=None,
            button_col=None,
            peer_type=None,
            peer_id=None,
            peer_access_hash=None,
            run_time=tg_checkin.parse_time_string("09:00"),
            timezone=tg_checkin.parse_timezone("Europe/London"),
            jitter_seconds=0,
            reply_wait_seconds=8,
            proxy=None,
        )

    def test_next_run_uses_same_day_when_future(self) -> None:
        config = self.make_config()
        fake_now = datetime(2026, 5, 20, 8, 15, tzinfo=config.timezone)

        with patch("tg_checkin.datetime") as mock_datetime:
            mock_datetime.now.return_value = fake_now
            mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            run_at = tg_checkin.next_run_at(config)

        self.assertEqual(run_at, datetime(2026, 5, 20, 9, 0, tzinfo=config.timezone))

    def test_next_run_rolls_to_next_day_when_past(self) -> None:
        config = self.make_config()
        fake_now = datetime(2026, 5, 20, 10, 15, tzinfo=config.timezone)

        with patch("tg_checkin.datetime") as mock_datetime:
            mock_datetime.now.return_value = fake_now
            mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            run_at = tg_checkin.next_run_at(config)

        self.assertEqual(run_at, datetime(2026, 5, 21, 9, 0, tzinfo=config.timezone))


class DryRunTests(unittest.TestCase):
    def test_dry_run_does_not_create_telegram_client(self) -> None:
        env = {
            "TG_API_ID": "12345",
            "TG_API_HASH": "hash",
            "TG_TARGET": "@demo_bot",
            "TG_SIGNIN_TEXT": "/checkin",
            "TG_TIME": "09:30",
            "TG_TIMEZONE": "Europe/London",
            "TG_JITTER_SECONDS": "0",
            "TG_REPLY_WAIT_SECONDS": "8",
        }
        args = tg_checkin.build_parser().parse_args(["--once", "--dry-run"])

        with patch.dict(os.environ, env, clear=True):
            with patch("tg_checkin.TelegramClient") as mock_client:
                result = asyncio.run(tg_checkin.async_main(args))

        self.assertEqual(result, 0)
        mock_client.assert_not_called()


class ResolveTargetTests(unittest.TestCase):
    def test_resolve_target_uses_dialog_fallback_for_numeric_peer(self) -> None:
        class FakeDialog:
            def __init__(self):
                self.id = 1000000000001
                self.entity = object()

        class FakeClient:
            async def get_entity(self, target):
                raise ValueError(f"Cannot resolve {target}")

            async def iter_dialogs(self, limit=300):
                yield FakeDialog()

        config = tg_checkin.Config(
            api_id=1,
            api_hash="hash",
            phone=None,
            session="checkin",
            target=1000000000001,
            signin_text="/checkin",
            button_text=None,
            button_row=None,
            button_col=None,
            peer_type="user",
            peer_id=1000000000001,
            peer_access_hash=None,
            run_time=tg_checkin.parse_time_string("09:00"),
            timezone=tg_checkin.parse_timezone("Europe/London"),
            jitter_seconds=0,
            reply_wait_seconds=8,
            proxy=None,
        )

        entity = asyncio.run(tg_checkin.resolve_target_entity(FakeClient(), config))

        self.assertIsNotNone(entity)


if __name__ == "__main__":
    unittest.main()
