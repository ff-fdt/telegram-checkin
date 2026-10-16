# Telegram 自动签到

[English](README.md)

在本机管理 Telegram 定时签到：浏览器控制台、图形界面和命令行共用签到核心。
支持选择置顶会话、管理多个任务、统一每日时间、临时测试和 macOS 后台常驻。
命令行还支持按回复按钮文字或行列位置点击。

## 启动

需要 Python 3.10 及以上。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python web_ui.py
```

打开 `http://127.0.0.1:8766`，按下面的顺序配置：

1. 在 [my.telegram.org](https://my.telegram.org) 申请自己的 API ID 和 API hash。
2. 在账号设置里填手机号、API ID 和 API hash，发送验证码并完成登录。
3. 在 Telegram 里置顶目标 Bot、群或频道，回到控制台读取置顶会话。
4. 将选中的会话加入任务，填签到命令和统一签到时间。
5. 测试选中的任务；macOS 上点击“安装并启动后台”保持每日运行。

macOS 可双击 `start_web.command` 打开网页控制台，或用 `start_gui.command`
打开 Tkinter 图形界面。可选的 Telegram Desktop 会话导入组件安装命令：

```sh
.venv/bin/python -m pip install -r requirements-desktop.txt
```

## 命令行

```sh
cp .env.example .env
# 编辑 .env，填自己的账号和目标。
.venv/bin/python tg_checkin.py --once --dry-run
.venv/bin/python tg_checkin.py --once
.venv/bin/python tg_checkin.py
```

`--dry-run` 离线预览配置；`--once` 立即执行一次；最后一条命令常驻并按时执行。
也可用 `easy_checkin.py --setup` 交互填写配置。

Linux 上可分别运行 `web_ui.py --no-open` 和 `service_runner.py` 使用前台模式。
后台服务按钮使用部署者安装的三个 `telegram-checkin-*.service` systemd 单元。

## 数据位置

网页控制台的数据保存在 macOS 的 `~/Library/Application Support/TelegramCheckin`
或 Linux 的 `~/.local/share/telegram-checkin`；`TG_CHECKIN_HOME` 可指定其他目录。
命令行的 `.env` 与会话文件保存在当前工作目录。账号配置、会话、任务和日志请留在本机。

采用 MIT 许可证。
