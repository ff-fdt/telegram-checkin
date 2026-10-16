from __future__ import annotations

import asyncio
import queue
import threading
import traceback
import webbrowser
from datetime import datetime
from tkinter import BooleanVar, StringVar, Tk, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import local_runtime
import tg_checkin


class CheckinApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Telegram 自动签到")
        self.root.geometry("1180x920")
        self.root.minsize(1080, 820)

        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False

        values = local_runtime.load_env_values()
        self.global_keys = [
            "TG_API_ID",
            "TG_API_HASH",
            "TG_PHONE",
            "TG_SESSION",
            "TG_SIGNIN_TEXT",
            "TG_TIME",
            "TG_TIMEZONE",
            "TG_JITTER_SECONDS",
            "TG_REPLY_WAIT_SECONDS",
            "TG_BUTTON_TEXT",
            "TG_BUTTON_ROW",
            "TG_BUTTON_COL",
        ]
        self.global_vars = {key: StringVar(value=values.get(key, "")) for key in self.global_keys}
        self.default_button_mode = StringVar(value=self.detect_default_button_mode(values))

        self.task_vars = {
            "title": StringVar(),
            "target": StringVar(),
            "target_kind": StringVar(),
            "signin_text": StringVar(),
            "time": StringVar(),
            "timezone": StringVar(),
            "jitter_seconds": StringVar(),
            "reply_wait_seconds": StringVar(),
            "button_text": StringVar(),
            "button_row": StringVar(),
            "button_col": StringVar(),
        }
        self.task_enabled_var = BooleanVar(value=True)
        self.task_button_mode = StringVar(value="none")
        self.code_var = StringVar()
        self.password_var = StringVar()
        self.status_var = StringVar(value="准备就绪")
        self.session_var = StringVar(value="登录状态：未检查")
        self.service_var = StringVar(value="后台状态：未检查")
        self.next_run_var = StringVar(value="下次执行：未检查")

        self.dialogs: list[dict] = []
        self.dialog_by_iid: dict[str, dict] = {}
        self.tasks = local_runtime.get_effective_tasks(values)
        self.task_by_id: dict[str, dict] = {}
        self.selected_task_id: str | None = None

        self.build_ui()
        self.populate_task_tree()
        self.refresh_runtime_state()
        self.root.after(200, self.process_queue)

    def detect_default_button_mode(self, values: dict[str, str]) -> str:
        if values.get("TG_BUTTON_TEXT"):
            return "text"
        if values.get("TG_BUTTON_ROW") and values.get("TG_BUTTON_COL"):
            return "index"
        return "none"

    def build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=1)

        ttk.Label(
            container,
            text="Telegram 自动签到本地控制台",
            font=("SF Pro Display", 24, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            container,
            text="本地读取你的 Telegram 会话列表，勾选多个 Bot / 群组后统一托管后台自动签到。",
        ).grid(row=1, column=0, sticky="w", pady=(4, 10))

        status = ttk.LabelFrame(container, text="状态", padding=12)
        status.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        for col in range(3):
            status.columnconfigure(col, weight=1)
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Label(status, textvariable=self.session_var).grid(row=0, column=1, sticky="w")
        ttk.Label(status, textvariable=self.service_var).grid(row=0, column=2, sticky="w")
        ttk.Label(status, textvariable=self.next_run_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        notebook = ttk.Notebook(container)
        notebook.grid(row=3, column=0, sticky="nsew")
        container.rowconfigure(3, weight=1)

        account_tab = ttk.Frame(notebook, padding=12)
        dialogs_tab = ttk.Frame(notebook, padding=12)
        tasks_tab = ttk.Frame(notebook, padding=12)
        logs_tab = ttk.Frame(notebook, padding=12)
        notebook.add(account_tab, text="账号与默认值")
        notebook.add(dialogs_tab, text="读取会话列表")
        notebook.add(tasks_tab, text="任务管理")
        notebook.add(logs_tab, text="后台日志")

        self.build_account_tab(account_tab)
        self.build_dialogs_tab(dialogs_tab)
        self.build_tasks_tab(tasks_tab)
        self.build_logs_tab(logs_tab)

    def build_account_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)

        account = ttk.LabelFrame(parent, text="账号配置", padding=12)
        account.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 12))
        account.columnconfigure(1, weight=1)
        fields = [
            ("TG_API_ID", "API ID"),
            ("TG_API_HASH", "API HASH"),
            ("TG_PHONE", "手机号"),
            ("TG_SESSION", "本地会话名"),
        ]
        for row, (key, label) in enumerate(fields):
            ttk.Label(account, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(account, textvariable=self.global_vars[key], show="*" if key == "TG_API_HASH" else "").grid(row=row, column=1, sticky="ew", pady=4)

        ttk.Button(account, text="保存账号配置", command=self.save_global_config).grid(row=len(fields), column=0, columnspan=2, sticky="ew", pady=(12, 4))
        ttk.Button(account, text="打开 Telegram API 页面", command=lambda: webbrowser.open("https://my.telegram.org")).grid(row=len(fields) + 1, column=0, columnspan=2, sticky="ew", pady=4)

        defaults = ttk.LabelFrame(parent, text="新任务默认值", padding=12)
        defaults.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 12))
        defaults.columnconfigure(1, weight=1)
        default_fields = [
            ("TG_SIGNIN_TEXT", "默认签到消息"),
            ("TG_TIME", "默认执行时间"),
            ("TG_TIMEZONE", "默认时区"),
            ("TG_JITTER_SECONDS", "默认随机延迟秒数"),
            ("TG_REPLY_WAIT_SECONDS", "默认按钮等待秒数"),
        ]
        for row, (key, label) in enumerate(default_fields):
            ttk.Label(defaults, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(defaults, textvariable=self.global_vars[key]).grid(row=row, column=1, sticky="ew", pady=4)

        ttk.Label(defaults, text="默认按钮模式").grid(row=5, column=0, sticky="w", pady=(10, 4))
        radio_wrap = ttk.Frame(defaults)
        radio_wrap.grid(row=5, column=1, sticky="ew")
        ttk.Radiobutton(radio_wrap, text="不点按钮", variable=self.default_button_mode, value="none").pack(anchor="w")
        ttk.Radiobutton(radio_wrap, text="按文字", variable=self.default_button_mode, value="text").pack(anchor="w")
        ttk.Radiobutton(radio_wrap, text="按位置", variable=self.default_button_mode, value="index").pack(anchor="w")

        ttk.Label(defaults, text="默认按钮文字").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Entry(defaults, textvariable=self.global_vars["TG_BUTTON_TEXT"]).grid(row=6, column=1, sticky="ew", pady=4)
        ttk.Label(defaults, text="默认按钮行").grid(row=7, column=0, sticky="w", pady=4)
        ttk.Entry(defaults, textvariable=self.global_vars["TG_BUTTON_ROW"]).grid(row=7, column=1, sticky="ew", pady=4)
        ttk.Label(defaults, text="默认按钮列").grid(row=8, column=0, sticky="w", pady=4)
        ttk.Entry(defaults, textvariable=self.global_vars["TG_BUTTON_COL"]).grid(row=8, column=1, sticky="ew", pady=4)
        ttk.Button(defaults, text="保存默认值", command=self.save_global_config).grid(row=9, column=0, columnspan=2, sticky="ew", pady=(12, 4))

        login = ttk.LabelFrame(parent, text="首次登录", padding=12)
        login.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        login.columnconfigure(1, weight=1)
        ttk.Label(login, text="验证码").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(login, textvariable=self.code_var).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(login, text="二步验证密码").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(login, textvariable=self.password_var, show="*").grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(login, text="1. 发送登录验证码", command=self.send_login_code).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        ttk.Button(login, text="2. 完成登录", command=self.finish_login).grid(row=3, column=0, columnspan=2, sticky="ew", pady=4)

        service = ttk.LabelFrame(parent, text="后台常驻", padding=12)
        service.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        service.columnconfigure(0, weight=1)
        service.columnconfigure(1, weight=1)
        ttk.Button(service, text="安装并启动后台", command=self.install_and_start_service).grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=4)
        ttk.Button(service, text="停止后台", command=self.stop_service).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(service, text="卸载后台", command=self.uninstall_service).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=4)
        ttk.Button(service, text="刷新后台状态", command=self.refresh_runtime_state).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(service, text="先完成登录，再去读取会话列表并添加任务。后台由 launchd 托管，开机后也会继续跑。", wraplength=420).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def build_dialogs_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(toolbar, text="读取 Telegram 会话列表", command=self.load_dialogs).pack(side="left")
        ttk.Button(toolbar, text="把选中项加入任务", command=self.add_selected_dialogs).pack(side="left", padx=(8, 0))

        tree_frame = ttk.Frame(parent)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("kind", "title", "target")
        self.dialog_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        self.dialog_tree.heading("kind", text="类型")
        self.dialog_tree.heading("title", text="名称")
        self.dialog_tree.heading("target", text="目标值")
        self.dialog_tree.column("kind", width=120, anchor="center")
        self.dialog_tree.column("title", width=360)
        self.dialog_tree.column("target", width=260)
        self.dialog_tree.grid(row=0, column=0, sticky="nsew")
        dialog_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.dialog_tree.yview)
        dialog_scroll.grid(row=0, column=1, sticky="ns")
        self.dialog_tree.configure(yscrollcommand=dialog_scroll.set)

    def build_tasks_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(0, weight=1)

        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(left)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(toolbar, text="移除选中任务", command=self.remove_selected_tasks).pack(side="left")
        ttk.Button(toolbar, text="切换启用状态", command=self.toggle_selected_tasks).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="立即运行选中任务", command=self.run_selected_tasks_now).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="立即运行全部启用任务", command=self.run_all_tasks_now).pack(side="left", padx=(8, 0))

        task_frame = ttk.Frame(left)
        task_frame.grid(row=1, column=0, sticky="nsew")
        task_frame.columnconfigure(0, weight=1)
        task_frame.rowconfigure(0, weight=1)

        columns = ("enabled", "kind", "title", "time", "signin_text", "target")
        self.task_tree = ttk.Treeview(task_frame, columns=columns, show="headings", selectmode="extended")
        self.task_tree.heading("enabled", text="启用")
        self.task_tree.heading("kind", text="类型")
        self.task_tree.heading("title", text="任务名")
        self.task_tree.heading("time", text="时间")
        self.task_tree.heading("signin_text", text="签到命令")
        self.task_tree.heading("target", text="目标")
        self.task_tree.column("enabled", width=70, anchor="center")
        self.task_tree.column("kind", width=90, anchor="center")
        self.task_tree.column("title", width=220)
        self.task_tree.column("time", width=90, anchor="center")
        self.task_tree.column("signin_text", width=160)
        self.task_tree.column("target", width=220)
        self.task_tree.grid(row=0, column=0, sticky="nsew")
        task_scroll = ttk.Scrollbar(task_frame, orient="vertical", command=self.task_tree.yview)
        task_scroll.grid(row=0, column=1, sticky="ns")
        self.task_tree.configure(yscrollcommand=task_scroll.set)
        self.task_tree.bind("<<TreeviewSelect>>", self.on_task_select)

        editor = ttk.LabelFrame(parent, text="任务编辑器", padding=12)
        editor.grid(row=0, column=1, sticky="nsew")
        editor.columnconfigure(1, weight=1)

        ttk.Label(editor, text="任务名").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(editor, textvariable=self.task_vars["title"]).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(editor, text="目标").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(editor, textvariable=self.task_vars["target"]).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(editor, text="类型").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(editor, textvariable=self.task_vars["target_kind"]).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Checkbutton(editor, text="启用这个任务", variable=self.task_enabled_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 4))

        field_rows = [
            ("signin_text", "签到命令"),
            ("time", "执行时间"),
            ("timezone", "时区"),
            ("jitter_seconds", "随机延迟秒数"),
            ("reply_wait_seconds", "按钮等待秒数"),
        ]
        start = 4
        for offset, (key, label) in enumerate(field_rows):
            row = start + offset
            ttk.Label(editor, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(editor, textvariable=self.task_vars[key]).grid(row=row, column=1, sticky="ew", pady=4)

        ttk.Label(editor, text="按钮模式").grid(row=9, column=0, sticky="w", pady=(10, 4))
        modes = ttk.Frame(editor)
        modes.grid(row=9, column=1, sticky="ew")
        ttk.Radiobutton(modes, text="不点按钮", variable=self.task_button_mode, value="none").pack(anchor="w")
        ttk.Radiobutton(modes, text="按文字", variable=self.task_button_mode, value="text").pack(anchor="w")
        ttk.Radiobutton(modes, text="按位置", variable=self.task_button_mode, value="index").pack(anchor="w")

        ttk.Label(editor, text="按钮文字").grid(row=10, column=0, sticky="w", pady=4)
        ttk.Entry(editor, textvariable=self.task_vars["button_text"]).grid(row=10, column=1, sticky="ew", pady=4)
        ttk.Label(editor, text="按钮行").grid(row=11, column=0, sticky="w", pady=4)
        ttk.Entry(editor, textvariable=self.task_vars["button_row"]).grid(row=11, column=1, sticky="ew", pady=4)
        ttk.Label(editor, text="按钮列").grid(row=12, column=0, sticky="w", pady=4)
        ttk.Entry(editor, textvariable=self.task_vars["button_col"]).grid(row=12, column=1, sticky="ew", pady=4)
        ttk.Button(editor, text="保存当前任务", command=self.save_selected_task).grid(row=13, column=0, columnspan=2, sticky="ew", pady=(12, 4))

    def build_logs_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self.log_box = ScrolledText(parent, height=25, wrap="word", font=("SF Mono", 11))
        self.log_box.grid(row=0, column=0, sticky="nsew")
        self.log_box.insert("1.0", local_runtime.tail_logs())
        self.log_box.configure(state="disabled")
        ttk.Button(parent, text="刷新日志", command=self.refresh_logs).grid(row=1, column=0, sticky="e", pady=(8, 0))

    def current_global_values(self) -> dict[str, str]:
        values = {key: var.get().strip() for key, var in self.global_vars.items()}
        if self.default_button_mode.get() == "none":
            values["TG_BUTTON_TEXT"] = ""
            values["TG_BUTTON_ROW"] = ""
            values["TG_BUTTON_COL"] = ""
        elif self.default_button_mode.get() == "text":
            values["TG_BUTTON_ROW"] = ""
            values["TG_BUTTON_COL"] = ""
        elif self.default_button_mode.get() == "index":
            values["TG_BUTTON_TEXT"] = ""
        return values

    def validate_global(self) -> dict[str, str]:
        values = self.current_global_values()
        return local_runtime.validate_global_values(values)

    def save_global_config(self) -> None:
        try:
            values = self.validate_global()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("配置错误", str(exc))
            return
        local_runtime.save_env_values(values)
        self.set_status("账号和默认值已保存。")
        self.refresh_runtime_state()

    def send_login_code(self) -> None:
        try:
            values = self.validate_global()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("配置错误", str(exc))
            return
        local_runtime.save_env_values(values)
        self.run_worker(
            "正在发送登录验证码…",
            lambda: (
                asyncio.run(local_runtime.send_login_code(values)),
                "验证码已发送，请查看 Telegram。",
            )[1],
        )

    def finish_login(self) -> None:
        try:
            values = self.validate_global()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("配置错误", str(exc))
            return

        code = self.code_var.get().strip()
        password = self.password_var.get().strip()
        local_runtime.save_env_values(values)

        def task() -> str:
            username = asyncio.run(local_runtime.complete_login(values, code, password))
            return f"登录完成：{username}"

        self.run_worker("正在完成登录…", task, clear_auth=True)

    def load_dialogs(self) -> None:
        try:
            values = self.validate_global()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("配置错误", str(exc))
            return
        local_runtime.save_env_values(values)

        def task() -> list[dict]:
            return asyncio.run(local_runtime.list_available_dialogs(values))

        self.run_worker("正在读取会话列表…", task, success_handler=self.handle_dialogs_loaded)

    def handle_dialogs_loaded(self, dialogs: list[dict]) -> str:
        self.dialogs = dialogs
        self.dialog_by_iid = {f"dialog:{item['id']}": item for item in dialogs}
        for item in self.dialog_tree.get_children():
            self.dialog_tree.delete(item)
        for dialog in dialogs:
            iid = f"dialog:{dialog['id']}"
            self.dialog_tree.insert("", "end", iid=iid, values=(dialog["target_kind"], dialog["title"], dialog["target"]))
        return f"已读取 {len(dialogs)} 个 Bot / 群组 / 频道。"

    def add_selected_dialogs(self) -> None:
        try:
            values = self.validate_global()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("配置错误", str(exc))
            return
        selected = self.dialog_tree.selection()
        if not selected:
            messagebox.showinfo("未选择", "先在会话列表里选中至少一个目标。")
            return

        existing_ids = {task["id"] for task in self.tasks}
        added = 0
        for iid in selected:
            dialog = self.dialog_by_iid.get(iid)
            if not dialog:
                continue
            task_id = f"dialog:{dialog['id']}"
            if task_id in existing_ids:
                continue
            task = local_runtime.default_task_from_values(
                values,
                target=dialog["target"],
                title=dialog["title"],
                kind=dialog["target_kind"],
                task_id=task_id,
            )
            self.tasks.append(task)
            existing_ids.add(task_id)
            added += 1

        if not added:
            messagebox.showinfo("没有新增", "选中的目标已经都在任务列表里了。")
            return

        self.persist_tasks()
        self.populate_task_tree()
        self.refresh_runtime_state()
        self.set_status(f"新增了 {added} 个任务。")

    def populate_task_tree(self) -> None:
        self.task_by_id = {task["id"]: task for task in self.tasks}
        for iid in self.task_tree.get_children():
            self.task_tree.delete(iid)
        for task in self.tasks:
            self.task_tree.insert(
                "",
                "end",
                iid=task["id"],
                values=(
                    "是" if task.get("enabled") else "否",
                    task.get("target_kind", ""),
                    task.get("title", ""),
                    task.get("time", ""),
                    task.get("signin_text", ""),
                    task.get("target", ""),
                ),
            )

    def on_task_select(self, _event=None) -> None:
        selected = self.task_tree.selection()
        if len(selected) != 1:
            self.selected_task_id = None
            return
        task_id = selected[0]
        task = self.task_by_id.get(task_id)
        if not task:
            return
        self.selected_task_id = task_id
        self.task_vars["title"].set(task.get("title", ""))
        self.task_vars["target"].set(task.get("target", ""))
        self.task_vars["target_kind"].set(task.get("target_kind", ""))
        self.task_enabled_var.set(bool(task.get("enabled")))
        for key in ("signin_text", "time", "timezone", "jitter_seconds", "reply_wait_seconds", "button_text", "button_row", "button_col"):
            self.task_vars[key].set(str(task.get(key, "")))
        if task.get("button_text"):
            self.task_button_mode.set("text")
        elif task.get("button_row") and task.get("button_col"):
            self.task_button_mode.set("index")
        else:
            self.task_button_mode.set("none")

    def task_from_editor(self) -> dict:
        if not self.selected_task_id:
            raise ValueError("请先在任务列表里选中一个任务。")
        task = dict(self.task_by_id[self.selected_task_id])
        task["title"] = self.task_vars["title"].get().strip()
        task["target"] = self.task_vars["target"].get().strip()
        task["target_kind"] = self.task_vars["target_kind"].get().strip() or "other"
        task["enabled"] = self.task_enabled_var.get()
        for key in ("signin_text", "time", "timezone", "jitter_seconds", "reply_wait_seconds", "button_text", "button_row", "button_col"):
            task[key] = self.task_vars[key].get().strip()
        if self.task_button_mode.get() == "none":
            task["button_text"] = ""
            task["button_row"] = ""
            task["button_col"] = ""
        elif self.task_button_mode.get() == "text":
            task["button_row"] = ""
            task["button_col"] = ""
        elif self.task_button_mode.get() == "index":
            task["button_text"] = ""
        return task

    def save_selected_task(self) -> None:
        try:
            values = self.validate_global()
            task = local_runtime.validate_task(values, self.task_from_editor())
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("任务错误", str(exc))
            return

        for index, current in enumerate(self.tasks):
            if current["id"] == task["id"]:
                self.tasks[index] = task
                break
        self.persist_tasks()
        self.populate_task_tree()
        self.task_tree.selection_set(task["id"])
        self.refresh_runtime_state()
        self.set_status(f"任务已保存：{task['title'] or task['target']}")

    def remove_selected_tasks(self) -> None:
        selected = list(self.task_tree.selection())
        if not selected:
            messagebox.showinfo("未选择", "先选中至少一个任务。")
            return
        self.tasks = [task for task in self.tasks if task["id"] not in selected]
        self.persist_tasks()
        self.populate_task_tree()
        self.refresh_runtime_state()
        self.set_status(f"已移除 {len(selected)} 个任务。")

    def toggle_selected_tasks(self) -> None:
        selected = list(self.task_tree.selection())
        if not selected:
            messagebox.showinfo("未选择", "先选中至少一个任务。")
            return
        selected_set = set(selected)
        for task in self.tasks:
            if task["id"] in selected_set:
                task["enabled"] = not bool(task.get("enabled"))
        self.persist_tasks()
        self.populate_task_tree()
        self.refresh_runtime_state()
        self.set_status("已切换选中任务的启用状态。")

    def run_selected_tasks_now(self) -> None:
        selected = list(self.task_tree.selection())
        if not selected:
            messagebox.showinfo("未选择", "先选中至少一个任务。")
            return
        self.run_tasks_now(selected)

    def run_all_tasks_now(self) -> None:
        task_ids = [task["id"] for task in self.tasks if task.get("enabled")]
        if not task_ids:
            messagebox.showinfo("没有任务", "当前没有启用中的任务。")
            return
        self.run_tasks_now(task_ids)

    def run_tasks_now(self, task_ids: list[str]) -> None:
        try:
            values = self.validate_global()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("配置错误", str(exc))
            return

        local_runtime.save_env_values(values)
        self.persist_tasks()

        def task() -> str:
            args = ["--once"]
            for task_id in task_ids:
                args.extend(["--task-id", task_id])
            result = local_runtime.run_service_command(args)
            output = (result.stdout + "\n" + result.stderr).strip()
            if result.returncode != 0:
                raise RuntimeError(output or f"执行失败，退出码 {result.returncode}")
            return output or "执行完成。"

        self.run_worker("正在执行任务…", task)

    def install_and_start_service(self) -> None:
        try:
            values = self.validate_global()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("配置错误", str(exc))
            return
        if not local_runtime.has_saved_session(values):
            messagebox.showwarning("还没登录", "先完成登录，生成本地 session 文件，再安装后台。")
            return
        enabled_count = sum(1 for task in self.tasks if task.get("enabled"))
        if not enabled_count:
            messagebox.showwarning("没有任务", "先从会话列表里添加至少一个启用中的任务。")
            return
        local_runtime.save_env_values(values)
        self.persist_tasks()
        self.run_worker("正在安装并启动后台…", local_runtime.install_and_start_launch_agent)

    def stop_service(self) -> None:
        self.run_worker("正在停止后台…", lambda: (local_runtime.stop_launch_agent(), "后台已停止。")[1])

    def uninstall_service(self) -> None:
        self.run_worker("正在卸载后台…", lambda: (local_runtime.uninstall_launch_agent(), "后台已卸载。")[1])

    def persist_tasks(self) -> None:
        local_runtime.save_tasks(self.tasks)

    def run_worker(self, busy_text: str, func, success_handler=None, clear_auth: bool = False) -> None:
        if self.busy:
            messagebox.showinfo("请稍等", "有任务还在执行，请先等它跑完。")
            return
        self.busy = True
        self.set_status(busy_text)

        def worker() -> None:
            try:
                result = func()
                self.queue.put(("success", (result, success_handler, clear_auth)))
            except Exception as exc:  # noqa: BLE001
                self.queue.put(("error", f"{exc}\n\n{traceback.format_exc()}"))

        threading.Thread(target=worker, daemon=True).start()

    def process_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                self.busy = False
                if kind == "success":
                    result, handler, clear_auth = payload
                    message = handler(result) if handler else str(result)
                    if clear_auth:
                        self.code_var.set("")
                        self.password_var.set("")
                    self.set_status(message)
                    self.refresh_runtime_state()
                else:
                    self.set_status("操作失败")
                    messagebox.showerror("执行失败", str(payload))
                    self.refresh_runtime_state()
        except queue.Empty:
            pass
        finally:
            self.root.after(200, self.process_queue)

    def refresh_runtime_state(self) -> None:
        values = self.current_global_values()
        self.session_var.set(f"登录状态：{'已保存会话' if local_runtime.has_saved_session(values) else '还没登录'}")

        enabled_tasks = [task for task in self.tasks if task.get("enabled")]
        if enabled_tasks:
            try:
                next_task = min(
                    enabled_tasks,
                    key=lambda task: tg_checkin.next_run_at(
                        tg_checkin.config_from_values(local_runtime.task_to_config_values(values, task))
                    ),
                )
                next_run = tg_checkin.next_run_at(
                    tg_checkin.config_from_values(local_runtime.task_to_config_values(values, next_task))
                )
                self.next_run_var.set(f"下次执行：{next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}  |  {next_task['title'] or next_task['target']}")
            except Exception:
                self.next_run_var.set("下次执行：有任务，但配置还没填完整")
        else:
            self.next_run_var.set("下次执行：当前没有启用中的任务")

        try:
            status = local_runtime.get_launch_agent_status()
            if status.get("loaded"):
                pid = status.get("pid")
                suffix = f"  PID={pid}" if pid else ""
                self.service_var.set(f"后台状态：运行中{suffix}")
            elif status.get("installed"):
                self.service_var.set("后台状态：已安装，但当前未运行")
            else:
                self.service_var.set("后台状态：未安装")
        except Exception as exc:  # noqa: BLE001
            self.service_var.set(f"后台状态：检查失败 - {exc}")

        self.refresh_logs()

    def refresh_logs(self) -> None:
        content = local_runtime.tail_logs()
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.insert("1.0", content)
        self.log_box.configure(state="disabled")

    def set_status(self, text: str) -> None:
        self.status_var.set(f"{datetime.now().strftime('%H:%M:%S')}  {text}")


def main() -> int:
    root = Tk()
    ttk.Style().theme_use("clam")
    CheckinApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
