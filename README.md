# Telegram Check-in

[中文说明](README.zh-CN.md) · [MIT License](LICENSE)

A local Telegram check-in scheduler with a browser dashboard, a desktop GUI,
and a command-line interface. Sign in with your own Telegram account, choose
chats, and schedule messages and inline-button actions.

## Features

- Browser dashboard for account login, pinned chats, task editing, and logs.
- One daily schedule for dashboard tasks, following your computer's timezone
  or an IANA timezone you choose.
- One-time test schedules and manual runs.
- Inline button matching by text or zero-based row/column through the CLI.
- macOS background scheduler and web watchdog.
- Optional Telegram Desktop session import on macOS.
- Offline CLI configuration preview with `--dry-run`.

## Start the dashboard

Requires Python 3.10+. The desktop GUI also requires Tkinter.

```sh
git clone https://github.com/ff-fdt/telegram-checkin.git
cd telegram-checkin
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python web_ui.py
```

Open `http://127.0.0.1:8766`. Create an API ID and API hash at
[my.telegram.org](https://my.telegram.org), enter them with your phone number,
then request a login code and complete login. Fill in your two-step password
if your account uses one. Pin the bots, groups, or channels you want to use in
Telegram, load the pinned chats, and add the selected chats as tasks.

Set the check-in command and daily time, then test a selected task.
On macOS, click **安装并启动后台** to keep the scheduler running after the
terminal is closed. `start_web.command` opens the dashboard;
`start_gui.command` opens the Tkinter interface.

For optional Telegram Desktop import, install
`python -m pip install -r requirements-desktop.txt`, then click
**自动读取本机 Telegram**.

## Command-line use

```sh
cp .env.example .env
# Edit .env with your own account and target.
.venv/bin/python tg_checkin.py --once --dry-run
.venv/bin/python tg_checkin.py --once
.venv/bin/python tg_checkin.py
```

`--dry-run` performs an offline configuration preview. `--once` logs in and
runs immediately; the final command runs the daily scheduler. Use
`easy_checkin.py --setup` for interactive setup.

For Linux foreground dashboard scheduling, run `web_ui.py --no-open` in one
terminal and `service_runner.py` in another. The dashboard's background-service
buttons on Linux use the `telegram-checkin-{scheduler,web,watchdog}.service`
systemd units supplied by your deployment.

## Local data

Dashboard data is stored in `~/Library/Application Support/TelegramCheckin`
on macOS and `~/.local/share/telegram-checkin` on Linux. Set `TG_CHECKIN_HOME`
to choose another directory. CLI `.env` and session files stay in the current
working directory. Treat account configuration, session files, task lists, and
logs as private account data. The dashboard binds to loopback and accepts
requests from its own local origin.

## Development

```sh
.venv/bin/python -m unittest discover -v
```

The base dependencies are pinned in `requirements.lock`; install the optional
Desktop import dependencies separately. Third-party dependencies retain their
own licenses.
