const state = {
  values: {},
  tasks: [],
  testSlot: null,
  dialogs: [],
  selectedDialogIds: new Set(),
  selectedTaskIds: new Set(),
  editingTaskId: null,
};

const valueFields = [
  "TG_API_ID",
  "TG_API_HASH",
  "TG_PHONE",
  "TG_SESSION",
  "TG_TDESKTOP_PATH",
  "TG_SIGNIN_TEXT",
  "TG_TIME",
  "TG_TIMEZONE",
  "TG_JITTER_SECONDS",
  "TG_REPLY_WAIT_SECONDS",
  "TG_BUTTON_TEXT",
  "TG_BUTTON_ROW",
  "TG_BUTTON_COL",
];

const taskFields = [
  "title",
  "target",
  "signin_text",
];

function setStatus(text) {
  document.getElementById("statusText").textContent = text;
}

function currentGlobalValue(field, fallback = "") {
  const input = document.getElementById(field);
  if (input && typeof input.value === "string") {
    const text = input.value.trim();
    if (text) return text;
  }
  return state.values[field] || fallback;
}

function currentUnifiedTime() {
  return currentGlobalValue("TG_TIME", "09:00");
}

function collectValues() {
  const values = {};
  valueFields.forEach((field) => {
    values[field] = document.getElementById(field).value.trim();
  });
  values.TG_BUTTON_TEXT = "";
  values.TG_BUTTON_ROW = "";
  values.TG_BUTTON_COL = "";
  return values;
}

function fillValues(values) {
  state.values = values;
  valueFields.forEach((field) => {
    const el = document.getElementById(field);
    if (el) el.value = values[field] || "";
  });
  const summaryTime = document.getElementById("taskSummaryTime");
  if (summaryTime) summaryTime.textContent = values.TG_TIME || "09:00";
}

function fillTestSlot(testSlot) {
  state.testSlot = testSlot || null;
  const input = document.getElementById("TEST_SLOT_TIME");
  if (input) input.value = (testSlot && testSlot.time) ? testSlot.time : "";
}

function renderStatus(snapshot) {
  const importedPath = snapshot.values.TG_TDESKTOP_PATH || "";
  const service = snapshot.service_status || {};
  document.getElementById("sessionText").textContent = service.auth_error
    ? "登录状态：Telegram 会话已失效，需要重新授权"
    : snapshot.session_ok
    ? `登录状态：${importedPath ? "已导入并保存会话" : "已保存会话"}`
    : "登录状态：还没登录";
  document.getElementById("sourceText").textContent = importedPath
    ? `登录方式：自动读取本机 Telegram`
    : "登录方式：手动 API 登录";
  const timezoneMode = snapshot.timezone_mode || "SYSTEM";
  const effectiveTimezone = snapshot.effective_timezone || timezoneMode;
  document.getElementById("timezoneText").textContent = timezoneMode === "SYSTEM"
    ? `当前时区：跟随电脑 (${effectiveTimezone})`
    : `当前时区：${effectiveTimezone}`;
  const testSlot = snapshot.test_slot || null;
  document.getElementById("testSlotText").textContent = testSlot && testSlot.run_at
    ? `临时测试：${testSlot.run_at}`
    : "临时测试：未设置";
  const taskText = service.auth_error
    ? "签到后台已暂停：Telegram 登录失效"
    : service.stale
    ? `签到后台无心跳，正在自修复${service.pid ? ` PID=${service.pid}` : ""}`
    : service.loaded
      ? `签到后台运行中${service.pid ? ` PID=${service.pid}` : ""}`
    : service.installed
      ? "签到后台未运行"
      : "签到后台未安装";
  const webMonitorText = service.web_monitor_pid
    ? `自修复监控运行中 PID=${service.web_monitor_pid}`
    : service.web_installed
      ? "自修复监控未运行"
      : "自修复监控未安装";
  const webText = service.web_port_open
    ? `网页服务运行中${service.web_pid ? ` PID=${service.web_pid}` : ""}`
    : service.web_loaded
      ? "网页服务恢复中"
      : service.web_installed
        ? "网页服务未运行"
        : "网页服务未安装";
  document.getElementById("serviceText").textContent = `后台状态：${taskText} / ${webMonitorText} / ${webText}`;
  const nearest = snapshot.nearest_run;
  document.getElementById("nextRunText").textContent = nearest
    ? `下次执行：${nearest.next_run}  |  ${nearest.title}`
    : "下次执行：当前没有可计算的任务";
}

function renderDialogs() {
  const body = document.getElementById("dialogsBody");
  body.innerHTML = "";
  state.dialogs.forEach((dialog) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td class="tiny"><input type="checkbox" data-dialog-id="${dialog.id}" ${state.selectedDialogIds.has(dialog.id) ? "checked" : ""}></td>
      <td class="small">${dialog.target_kind}</td>
      <td>${dialog.title}</td>
      <td>${dialog.target}</td>
    `;
    body.appendChild(row);
  });
  body.querySelectorAll('input[data-dialog-id]').forEach((input) => {
    input.addEventListener("change", (event) => {
      const id = event.target.dataset.dialogId;
      if (event.target.checked) state.selectedDialogIds.add(id);
      else state.selectedDialogIds.delete(id);
    });
  });
}

function renderTasks() {
  const body = document.getElementById("tasksBody");
  body.innerHTML = "";
  state.tasks.forEach((task) => {
    const row = document.createElement("tr");
    if (task.id === state.editingTaskId) row.classList.add("active");
    row.innerHTML = `
      <td class="tiny"><input type="checkbox" data-task-id="${task.id}" ${state.selectedTaskIds.has(task.id) ? "checked" : ""}></td>
      <td class="tiny">${task.enabled ? "是" : "否"}</td>
      <td>${task.title || task.target}</td>
      <td>${task.signin_text}</td>
      <td>${task.target}</td>
    `;
    row.addEventListener("click", (event) => {
      if (event.target.tagName === "INPUT") return;
      state.editingTaskId = task.id;
      fillTaskEditor(task);
      renderTasks();
    });
    body.appendChild(row);
  });
  body.querySelectorAll('input[data-task-id]').forEach((input) => {
    input.addEventListener("change", (event) => {
      const id = event.target.dataset.taskId;
      if (event.target.checked) state.selectedTaskIds.add(id);
      else state.selectedTaskIds.delete(id);
    });
  });
}

function fillTaskEditor(task) {
  taskFields.forEach((field) => {
    document.getElementById(`task_${field}`).value = task[field] || "";
  });
  document.getElementById("task_target_kind").value = task.target_kind || "";
  document.getElementById("task_time").value = currentUnifiedTime();
  document.getElementById("task_timezone").value = task.timezone || state.values.TG_TIMEZONE || "SYSTEM";
  document.getElementById("task_jitter_seconds").value = task.jitter_seconds || state.values.TG_JITTER_SECONDS || "0";
  document.getElementById("task_reply_wait_seconds").value = task.reply_wait_seconds || state.values.TG_REPLY_WAIT_SECONDS || "8";
  document.getElementById("task_button_text").value = task.button_text || "";
  document.getElementById("task_button_row").value = task.button_row || "";
  document.getElementById("task_button_col").value = task.button_col || "";
  document.getElementById("task_enabled").checked = !!task.enabled;
  document.getElementById("taskSummaryTitle").textContent = task.title || task.target || "-";
  document.getElementById("taskSummaryTarget").textContent = task.target || "-";
  document.getElementById("taskSummaryTime").textContent = currentUnifiedTime();
}

function clearTaskEditor() {
  state.editingTaskId = null;
  taskFields.forEach((field) => {
    document.getElementById(`task_${field}`).value = "";
  });
  document.getElementById("task_target_kind").value = "";
  document.getElementById("task_time").value = "";
  document.getElementById("task_timezone").value = "";
  document.getElementById("task_jitter_seconds").value = "";
  document.getElementById("task_reply_wait_seconds").value = "";
  document.getElementById("task_button_text").value = "";
  document.getElementById("task_button_row").value = "";
  document.getElementById("task_button_col").value = "";
  document.getElementById("task_enabled").checked = true;
  document.getElementById("taskSummaryTitle").textContent = "先点上面某个任务";
  document.getElementById("taskSummaryTarget").textContent = "-";
  document.getElementById("taskSummaryTime").textContent = currentUnifiedTime();
}

function collectTaskEditor() {
  if (!state.editingTaskId) throw new Error("请先在任务列表里点选一个任务。");
  const source = state.tasks.find((item) => item.id === state.editingTaskId);
  const task = { ...source };
  taskFields.forEach((field) => {
    task[field] = document.getElementById(`task_${field}`).value.trim();
  });
  task.target_kind = document.getElementById("task_target_kind").value.trim();
  task.time = currentUnifiedTime();
  task.timezone = state.values.TG_TIMEZONE || "SYSTEM";
  task.jitter_seconds = state.values.TG_JITTER_SECONDS || "0";
  task.reply_wait_seconds = state.values.TG_REPLY_WAIT_SECONDS || "8";
  task.button_text = state.values.TG_BUTTON_TEXT || "";
  task.button_row = state.values.TG_BUTTON_ROW || "";
  task.button_col = state.values.TG_BUTTON_COL || "";
  task.enabled = document.getElementById("task_enabled").checked;
  return task;
}

function updateState(snapshot) {
  fillValues(snapshot.values);
  fillTestSlot(snapshot.test_slot || null);
  state.tasks = snapshot.tasks || [];
  renderStatus(snapshot);
  renderTasks();
}

async function api(path, payload = null, method = "POST") {
  const options = { method, headers: {}, cache: "no-store" };
  if (payload !== null) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(payload);
  }
  let res;
  try {
    res = await fetch(path, options);
  } catch (error) {
    throw new Error("本地服务没在运行。请运行 web_ui.py 或重新双击“start_web.command”。");
  }
  let data;
  try {
    data = await res.json();
  } catch (error) {
    throw new Error("本地服务返回了无效响应，请重新打开本地控制台。");
  }
  if (!data.ok) throw new Error(data.error || "请求失败");
  return data;
}

async function loadState() {
  const data = await api("/api/state", null, "GET");
  updateState(data.state);
}

async function saveConfig() {
  setStatus("正在保存配置…");
  const data = await api("/api/config/save", { values: collectValues() });
  updateState(data.state);
  setStatus(data.message);
}

async function saveTestSlot() {
  const time = document.getElementById("TEST_SLOT_TIME").value.trim();
  if (!time) throw new Error("请先填写临时测试时间。");
  setStatus("正在保存临时测试时间…");
  const data = await api("/api/test-slot/save", { values: collectValues(), time });
  updateState(data.state);
  setStatus(data.message);
}

async function clearTestSlot() {
  setStatus("正在清除临时测试时间…");
  const data = await api("/api/test-slot/clear", {});
  updateState(data.state);
  setStatus(data.message);
}

async function autoImportDesktop() {
  setStatus("正在自动读取本机 Telegram…");
  const data = await api("/api/account/auto-import", { values: collectValues() });
  updateState(data.state);
  state.dialogs = data.dialogs || [];
  state.selectedDialogIds.clear();
  renderDialogs();
  document.getElementById("LOGIN_CODE").value = "";
  document.getElementById("LOGIN_PASSWORD").value = "";
  setStatus(data.message);
}

async function loadDialogs() {
  setStatus("正在读取置顶会话列表…");
  const data = await api("/api/dialogs/load", { values: collectValues() });
  state.dialogs = data.dialogs || [];
  state.selectedDialogIds.clear();
  renderDialogs();
  updateState(data.state);
  setStatus(data.message);
}

async function importDialogs() {
  const selected = state.dialogs.filter((dialog) => state.selectedDialogIds.has(dialog.id));
  if (!selected.length) throw new Error("先在会话列表里选中至少一个目标。");
  setStatus("正在加入任务…");
  const data = await api("/api/tasks/import", { values: collectValues(), dialogs: selected });
  updateState(data.state);
  setStatus(data.message);
}

async function saveTask() {
  const task = collectTaskEditor();
  setStatus("正在保存任务…");
  const data = await api("/api/tasks/save", { values: collectValues(), task });
  updateState(data.state);
  setStatus(data.message);
}

async function deleteTasks() {
  if (!state.selectedTaskIds.size) throw new Error("先选中至少一个任务。");
  const data = await api("/api/tasks/delete", { ids: [...state.selectedTaskIds] });
  state.selectedTaskIds.clear();
  clearTaskEditor();
  updateState(data.state);
  setStatus(data.message);
}

async function toggleTasks() {
  if (!state.selectedTaskIds.size) throw new Error("先选中至少一个任务。");
  const data = await api("/api/tasks/toggle", { ids: [...state.selectedTaskIds] });
  updateState(data.state);
  setStatus(data.message);
}

async function runTasks(ids) {
  if (!ids.length) throw new Error("没有可运行的任务。");
  setStatus("正在执行任务…");
  const data = await api("/api/tasks/run", { values: collectValues(), ids });
  updateState(data.state);
  if (data.logs) document.getElementById("logsBox").textContent = data.logs;
  setStatus(data.message);
}

async function serviceAction(path, message) {
  setStatus(message);
  const data = await api(path, {});
  updateState(data.state);
  setStatus(data.message);
}

async function refreshLogs() {
  const data = await api("/api/logs", null, "GET");
  document.getElementById("logsBox").textContent = data.logs || "";
}

async function sendLoginCode() {
  const data = await api("/api/login/send-code", { values: collectValues() });
  updateState(data.state);
  setStatus(data.message);
}

async function completeLogin() {
  const passwordInput = document.getElementById("LOGIN_PASSWORD");
  const codeInput = document.getElementById("LOGIN_CODE");
  try {
    const data = await api("/api/login/complete", {
      values: collectValues(), code: codeInput.value.trim(), password: passwordInput.value,
    });
    updateState(data.state);
    setStatus(data.message);
  } finally {
    passwordInput.value = "";
    codeInput.value = "";
  }
}

function bindEvents() {
  document.getElementById("sendCodeBtn").addEventListener("click", guarded(sendLoginCode));
  document.getElementById("completeLoginBtn").addEventListener("click", guarded(completeLogin));
  document.getElementById("autoImportBtn").addEventListener("click", guarded(autoImportDesktop));
  document.getElementById("saveConfigBtn").addEventListener("click", guarded(saveConfig));
  document.getElementById("saveTestSlotBtn").addEventListener("click", guarded(saveTestSlot));
  document.getElementById("clearTestSlotBtn").addEventListener("click", guarded(clearTestSlot));
  document.getElementById("loadDialogsBtn").addEventListener("click", guarded(loadDialogs));
  document.getElementById("importDialogsBtn").addEventListener("click", guarded(importDialogs));
  document.getElementById("saveTaskBtn").addEventListener("click", guarded(saveTask));
  document.getElementById("deleteTasksBtn").addEventListener("click", guarded(deleteTasks));
  document.getElementById("toggleTasksBtn").addEventListener("click", guarded(toggleTasks));
  document.getElementById("runSelectedBtn").addEventListener("click", guarded(() => runTasks([...state.selectedTaskIds])));
  document.getElementById("runAllBtn").addEventListener("click", guarded(() => runTasks(state.tasks.filter((task) => task.enabled).map((task) => task.id))));
  document.getElementById("installServiceBtn").addEventListener("click", guarded(() => serviceAction("/api/service/install-start", "正在安装并启动后台…")));
  document.getElementById("stopServiceBtn").addEventListener("click", guarded(() => serviceAction("/api/service/stop", "正在停止后台…")));
  document.getElementById("uninstallServiceBtn").addEventListener("click", guarded(() => serviceAction("/api/service/uninstall", "正在卸载后台…")));
  document.getElementById("refreshStateBtn").addEventListener("click", guarded(loadState));
  document.getElementById("refreshLogsBtn").addEventListener("click", guarded(refreshLogs));
  document.getElementById("TG_TIME").addEventListener("input", (event) => {
    state.values.TG_TIME = event.target.value.trim() || "09:00";
    document.getElementById("taskSummaryTime").textContent = currentUnifiedTime();
  });

  document.getElementById("dialogsCheckAll").addEventListener("change", (event) => {
    state.selectedDialogIds = event.target.checked
      ? new Set(state.dialogs.map((dialog) => dialog.id))
      : new Set();
    renderDialogs();
  });

  document.getElementById("tasksCheckAll").addEventListener("change", (event) => {
    state.selectedTaskIds = event.target.checked
      ? new Set(state.tasks.map((task) => task.id))
      : new Set();
    renderTasks();
  });
}

function guarded(fn) {
  return async () => {
    try {
      await fn();
      await refreshLogs();
    } catch (error) {
      setStatus(error.message);
      alert(error.message);
    }
  };
}

async function init() {
  bindEvents();
  await loadState();
  await refreshLogs();
  clearTaskEditor();
}

init().catch((error) => {
  setStatus(error.message);
  alert(error.message);
});
