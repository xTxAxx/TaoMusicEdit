// app.js —— 主应用逻辑：工作区 / 输出区 / 文件树 / 检测·裁剪任务编排 / SSE 进度 / 反馈
"use strict";

const state = {
  workspace: "",
  outputDir: "",
  currentVideo: null,      // 当前选中视频绝对路径
  video: null,             // 当前视频元信息（duration/fps/nb_frames），供参数范围校验
  jobs: { detect: null, trim: null, batch: null },  // 正在运行的主流程任务 job_id（单文件检测/裁剪、批量）
  retryQueues: { detect: [], trim: [] },  // 重试排队：每列待派发的任务（等待中徽章）
  retryRunning: { detect: new Set(), trim: new Set() },  // 列 -> 运行中的重试任务集合（并发上限用）
  sel: new Set(),          // 批处理选择的视频路径集合
  selAnchor: null,         // shift 区选的锚点路径
  batchDetectResults: {},  // 批量检测结果缓存 { path: result }
  batchRuns: new Set(),    // 本会话实际执行过检测的文件路径（区分「缓存匹配」与「真实运行」）
  tasks: { detect: [], trim: [] },  // 任务中心：颜色检测 / 视频裁剪 两列的任务
  jobTasks: new Map(),     // job_id -> { kind, col, batch, tasks: [], jobId }：SSE 结果定位到任务
  groups: new Map(),       // job_id -> 批量折叠组 { parent, tasks, expanded }
  filters: { detect: "all", trim: "all" },  // 各列筛选：all / running / done / fail
  _taskSeq: 0,
};

// ---------------- 工具 ----------------
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function basename(p) {
  return String(p).split(/[\\/]/).pop();
}

function setStatus(text) {
  const el = document.getElementById("topStatus");
  if (el) el.textContent = text;
}

// ---------------- 工作区 ----------------
async function refreshWorkspace() {
  const res = await fetch("/api/workspace");
  const data = await res.json();
  if (data.path) {
    document.getElementById("workspacePath").value = data.path;
    await loadWorkspace(data.path);
  }
}

async function loadWorkspace(path) {
  if (!path) return;
  const prevWs = state.workspace;
  const res = await fetch("/api/workspace", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await res.json();
  if (!data.ok) return;
  state.workspace = data.path;
  document.getElementById("workspacePath").value = data.path;
  const info = document.getElementById("workspaceInfo");
  info.innerHTML =
    `<span class="ok">✓ 已载入</span> · 目录可写：` +
    (data.writable ? '<span class="ok">是</span>' : '<span class="bad">否</span>');
  renderFileTree(data.tree);
  // 切换了工作区则清空批处理选择，否则重建树后恢复选择高亮
  if (prevWs && prevWs !== data.path) clearSelection();
  else renderSelection();
  setStatus("工作区已载入，请选择视频");
  scheduleSave();
}

// ---------------- 文件树 ----------------
// 仅渲染视频文件（非视频一律隐藏）
function renderFileTree(nodes) {
  const container = document.getElementById("fileTree");
  const ul = document.createElement("ul");
  ul.className = "tree";
  (nodes || []).forEach((n) => {
    const li = renderNode(n);
    if (li) ul.appendChild(li);
  });
  container.innerHTML = "";
  if (!ul.childNodes.length) {
    const empty = document.createElement("div");
    empty.className = "placeholder";
    empty.textContent = "（该工作区没有视频文件）";
    container.appendChild(empty);
  } else {
    container.appendChild(ul);
  }
}

function renderNode(node) {
  if (node.type === "dir") {
    const ul = document.createElement("ul");
    let count = 0;
    (node.children || []).forEach((c) => {
      const li = renderNode(c);
      if (li) { ul.appendChild(li); count++; }
    });
    if (count === 0) return null;
    const li = document.createElement("li");
    li.className = "tree-dir open";
    const row = document.createElement("div");
    row.className = "tree-row dir";
    row.innerHTML =
      '<span class="caret"></span><span class="tree-name">' + esc(node.name) +
      '</span><span class="tree-count">' + count + "</span>";
    li.appendChild(row);
    li.appendChild(ul);
    row.addEventListener("click", () => li.classList.toggle("open"));
    return li;
  }
  // 仅渲染视频文件（非视频一律隐藏）；下方分支据此不再做 is_video 判断
  if (!node.is_video) return null;
  const li = document.createElement("li");
  li.className = "tree-file video";
  const row = document.createElement("div");
  row.className = "tree-row file";
  row.dataset.path = node.path;
  const ext = (node.ext || "").replace(".", "").toUpperCase();
  row.innerHTML =
    '<span class="tree-dot vid"></span>' +
    '<span class="tree-name">' + esc(node.name) + "</span>" +
    '<span class="tree-meta">' + ext + "</span>";
  li.appendChild(row);
  row.addEventListener("click", (e) => {
    if (e.ctrlKey || e.metaKey) { toggleSelect(node.path); return; }  // Ctrl：单选切换
    if (e.shiftKey) { rangeSelect(node.path); return; }               // Shift：区间选择
    selectOnly(node.path);                                            // 普通点击：单选并载入播放器
    selectVideo(node.path, row);
  });
  return li;
}

// ---------------- 批处理选择 ----------------
// 按 DOM 顺序收集当前工作区所有视频文件行
function videoRows() {
  const out = [];
  document.querySelectorAll("#fileTree .tree-file.video").forEach((li) => {
    const row = li.querySelector(".tree-row.file");
    const p = row ? row.dataset.path : "";
    if (p) out.push({ path: p, el: li });
  });
  return out;
}

function selectOnly(path) {
  state.sel.clear();
  state.sel.add(path);
  state.selAnchor = path;
  renderSelection();
}

function toggleSelect(path) {
  if (state.sel.has(path)) state.sel.delete(path);
  else state.sel.add(path);
  state.selAnchor = path;
  renderSelection();
}

function rangeSelect(path) {
  const rows = videoRows();
  if (!rows.length) return;
  const idx = rows.findIndex((r) => r.path === path);
  if (idx < 0) return;
  let anchor = rows.findIndex((r) => r.path === state.selAnchor);
  if (anchor < 0) anchor = idx;
  const lo = Math.min(anchor, idx);
  const hi = Math.max(anchor, idx);
  state.sel.clear();
  for (let i = lo; i <= hi; i++) state.sel.add(rows[i].path);
  state.selAnchor = path;
  renderSelection();
}

function selectAll() {
  state.sel.clear();
  const rows = videoRows();
  rows.forEach((r) => state.sel.add(r.path));
  state.selAnchor = rows.length ? rows[0].path : null;
  renderSelection();
}

function clearSelection() {
  state.sel.clear();
  state.selAnchor = null;
  renderSelection();
}

function renderSelection() {
  const rows = videoRows();
  const set = state.sel;
  rows.forEach((r) => r.el.classList.toggle("selected", set.has(r.path)));
  const el = document.getElementById("selCount");
  if (el) el.textContent = "已选 " + state.sel.size + " 个";
  updateActions();
}

async function selectVideo(vpath, rowEl) {
  document.querySelectorAll("#fileTree .tree-row.file.active").forEach((r) => r.classList.remove("active"));
  if (rowEl) rowEl.classList.add("active");
  state.currentVideo = vpath;
  setStatus("正在加载视频…");
  try {
    await Player.loadVideo(vpath);
    state.video = Player.getInfo();  // 供 validateTrim 做范围校验（时长/帧率/总帧数）
    applyBatchResultForVideo(vpath);
    setStatus("视频已加载，可开始检测或裁剪");
  } catch (e) {
    state.video = null;
    setStatus("视频加载失败");
  }
  updateActions();
}

// 单独打开视频时，自动匹配该视频的检测结果：
// 优先用本次会话的批量检测结果，否则查询后端持久化缓存（刷新 / 重启后依然有效），
// 填入 trimmer 裁剪起点 + 在播放器加载检测标记并在 panelDetect 中展示。
async function applyBatchResultForVideo(vpath) {
  let r = state.batchDetectResults[vpath] || null;
  let cached = true;
  if (!r) {
    r = await fetchCachedDetect(vpath);
    if (r === null || state.currentVideo !== vpath) return;  // 无缓存或已切换视频
  } else {
    // 结果已在会话内（可能来自启动时恢复的持久化缓存）：
    // 仅当本会话真正执行过该文件的检测时，才视为「批量检测结果」，否则视为「缓存」
    cached = !state.batchRuns.has(vpath);
  }
  state.batchDetectResults[vpath] = r;
  renderDetectResult(r, cached ? "缓存" : null);
  if (r.detected && r.frame !== null && r.frame !== undefined) {
    setParam("start_mode", "frame");
    setParam("start_value", r.frame + 1);
    Player.setDetected(r, vpath);
    const row = rowOf("start_value");
    if (row) {
      const tip = document.createElement("div");
      tip.className = "param-tip";
      tip.textContent =
        "已读取" + (cached ? "缓存" : "批量") + "检测结果：帧 #" + (r.frame + 1) + " @ " +
        (r.timestamp !== null && r.timestamp !== undefined ? r.timestamp.toFixed(3) : "?") + "s（可修改）";
      if (!row.querySelector(".param-tip")) row.appendChild(tip);
    }
    setStatus("已载入检测结果，可查看或裁剪");
  } else {
    Player.clearDetected();
    setStatus("该视频已有缓存检测结果（未命中）");
  }
  updateActions();
}

// 查询后端持久化检测缓存（单个视频）；无缓存或网络异常返回 null
async function fetchCachedDetect(vpath) {
  try {
    const res = await fetch("/api/detect-cache?path=" + encodeURIComponent(vpath));
    const d = await res.json();
    if (d.ok && d.hit && d.result) return d.result;
  } catch (e) { /* 按无缓存处理 */ }
  return null;
}

// 启动时恢复全部检测缓存到 state.batchDetectResults，
// 使「批量裁剪」在页面刷新后无需重新检测即可直接使用
async function hydrateDetectCache() {
  try {
    const res = await fetch("/api/detect-cache");
    const d = await res.json();
    if (d.ok && d.entries) Object.assign(state.batchDetectResults, d.entries);
  } catch (e) { /* 恢复失败不影响正常使用 */ }
}

// ---------------- 检测缓存设置（开关 / 条目上限 / 清理 / 清空） ----------------

// 刷新缓存统计文本（条数由后端返回，避免本地与持久化数据不一致）
function renderCacheStats(count) {
  const el = document.getElementById("cacheStats");
  if (el) el.textContent = "缓存条目：" + count + " 条";
}

// 从后端拉取最新配置并刷新统计（no-store + 时间戳双保险，避免浏览器缓存旧计数）
async function refreshCacheConfig() {
  try {
    const res = await fetch("/api/detect-cache/config?t=" + Date.now(),
                            { cache: "no-store" });
    const d = await res.json();
    if (d.ok) renderCacheStats(d.count);
    return d;
  } catch (e) { return null; }
}

// 保存缓存配置（enabled / limit 变更时立即持久化到 settings.json）
async function saveCacheConfig(patch) {
  try {
    const res = await fetch("/api/detect-cache/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    const d = await res.json();
    if (d.ok) renderCacheStats(d.count);
  } catch (e) { /* 忽略：保存失败不阻塞本地交互 */ }
}

// 绑定缓存设置控件；saved 为 /api/settings 返回值（含 detect_cache 组）
function initCacheSettingsUI(saved) {
  const chkEnabled = document.getElementById("param-cache_enabled");
  const inpLimit = document.getElementById("param-cache_limit");
  if (!chkEnabled || !inpLimit) return;

  // 用保存的设置初始化控件（无保存值时用默认：启用 / 不限）
  const dc = (saved && saved.detect_cache) || {};
  chkEnabled.checked = dc.enabled !== false;
  inpLimit.value = String(dc.limit != null ? dc.limit : 0);
  void refreshCacheConfig();

  let limitTimer = null;
  chkEnabled.addEventListener("change", () => {
    saveCacheConfig({ enabled: chkEnabled.checked });
  });
  inpLimit.addEventListener("change", () => {
    clearTimeout(limitTimer);
    limitTimer = setTimeout(() => {
      const n = Math.max(0, Math.floor(Number(inpLimit.value) || 0));
      inpLimit.value = String(n); // 归一化非法输入
      saveCacheConfig({ limit: n });
    }, 400);
  });

  // 手动刷新缓存统计（检测/批量检测不会自动更新计数，按需点击刷新）
  document.getElementById("btnRefreshCacheStats")?.addEventListener("click", () => {
    void refreshCacheConfig();
  });

  // 跳过已缓存：勾选即持久化到 settings.json，重启后恢复
  const skipCached = document.getElementById("skipCached");
  if (skipCached) {
    skipCached.checked = !!(saved && saved.detect_cache && saved.detect_cache.skip_cached);
    skipCached.addEventListener("change", () => {
      saveCacheConfig({ skip_cached: skipCached.checked });
    });
  }

  document.getElementById("btnPruneCache")?.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/detect-cache/prune", { method: "POST" });
      const d = await res.json();
      if (d.ok) {
        // 同步移除本地批处理表中对应视频的结果
        for (const p of d.paths || []) delete state.batchDetectResults[p];
        renderCacheStats(d.count);
      }
    } catch (e) { /* 忽略 */ }
  });

  document.getElementById("btnClearCache")?.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/detect-cache/clear", { method: "POST" });
      const d = await res.json();
      if (d.ok) {
        state.batchDetectResults = {};
        renderCacheStats(0);
      }
    } catch (e) { /* 忽略 */ }
  });
}

// ---------------- 批处理设置（并发数） ----------------
// 并发数 0 表示自动（按 CPU 核数）；改动即随参数面板一起持久化到 settings.json
function initBatchWorkersUI(saved) {
  const inp = document.getElementById("param-batch_workers");
  if (!inp) return;
  const w = (saved && saved.batch && saved.batch.workers) || 0;
  inp.value = String(w);
  inp.addEventListener("change", () => {
    const n = Math.max(0, Math.min(16, Math.floor(Number(inp.value) || 0)));
    inp.value = String(n);
    scheduleSave();
  });
}

// ---------------- 输出目录 ----------------
async function refreshOutput() {
  const res = await fetch("/api/output-dir");
  const data = await res.json();
  if (data.path) {
    state.outputDir = data.path;
    document.getElementById("outputPath").value = data.path;
    renderOutputInfo(data);
  }
}

async function setOutputDir(path) {
  if (!path) return;
  const res = await fetch("/api/output-dir", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await res.json();
  if (!data.ok) {
    renderOutputInfo({ path, writable: false, message: data.message });
    return;
  }
  state.outputDir = data.path;
  document.getElementById("outputPath").value = data.path;
  renderOutputInfo(data);
  scheduleSave();
  updateActions();
}

function renderOutputInfo(data) {
  const el = document.getElementById("outputInfo");
  if (!data.writable) {
    el.innerHTML =
      '<span class="bad">✗ 不可写</span> <span class="muted-text">' +
      esc(data.message || "该目录不可写") + "</span>";
  } else {
    el.innerHTML = '<span class="ok">✓ 可写</span> · 检测与裁剪结果将保存到该目录';
  }
}

// ---------------- 目录浏览弹窗 ----------------
let dirTarget = "workspace";
let dirCurrent = "";

function openDirModal(target) {
  dirTarget = target;
  const start = target === "workspace"
    ? (state.workspace || "")
    : (state.outputDir || "");
  document.getElementById("dirModal").hidden = false;
  document.getElementById("dirModalTitle").textContent =
    target === "workspace" ? "选择工作目录" : "选择输出目录";
  loadDirList(start);
}

function closeDirModal() {
  document.getElementById("dirModal").hidden = true;
}

async function loadDirList(p) {
  if (!p) p = "";
  const res = await fetch("/api/list-dir?path=" + encodeURIComponent(p));
  const data = await res.json();
  if (!data.ok) return;
  dirCurrent = data.path;
  document.getElementById("dirCurrent").textContent = data.path;
  const list = document.getElementById("dirList");
  list.innerHTML = "";
  if (data.parent && data.parent !== data.path) {
    const up = document.createElement("div");
    up.className = "dir-item up";
    up.textContent = ".. · 上级目录";
    up.addEventListener("click", () => loadDirList(data.parent));
    list.appendChild(up);
  }
  if (!data.subdirs.length) {
    const empty = document.createElement("div");
    empty.className = "dir-empty";
    empty.textContent = "（该目录下没有子目录）";
    list.appendChild(empty);
  }
  data.subdirs.forEach((s) => {
    const item = document.createElement("div");
    item.className = "dir-item";
    item.innerHTML = '<span class="caret">▸</span><span class="tree-name">' + esc(s) + "</span>";
    item.addEventListener("click", () => loadDirList(joinPath(dirCurrent, s)));
    list.appendChild(item);
  });
}

function joinPath(base, name) {
  return String(base).replace(/[\\/]+$/, "") + "/" + name;
}

function chooseCurrentDir() {
  if (!dirCurrent) return;
  closeDirModal();
  if (dirTarget === "workspace") {
    loadWorkspace(dirCurrent);
  } else {
    setOutputDir(dirCurrent);
  }
}

// ---------------- 检测流程 ----------------
async function runDetect() {
  // 前置校验不通过时静默返回（失败反馈由任务中心 / 状态栏承担，不再弹右下角提示）
  if (state.jobs.detect) return;
  if (!state.currentVideo) return;
  const v = validateDetect(state);
  if (v.errors.length) return;
  setStatus("检测中…");
  const task = addTask("detect", state.currentVideo,
    { path: state.currentVideo, params: v.params });
  try {
    const res = await fetch("/api/detect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.currentVideo, params: v.params }),
    });
    const data = await res.json();
    if (!data.ok) { finalizeTask(task, "fail", ["启动检测失败：" + (data.message || "")]); return; }
    task.jobId = data.job_id;
    state.jobs.detect = task.jobId;
    state.jobTasks.set(task.jobId, { kind: "detect", col: "detect", batch: false, tasks: [task] });
    setTaskRunning(task, "启动中…");
    openSSE(task.jobId, "detect");
  } catch (e) {
    finalizeTask(task, "fail", ["网络错误：" + e.message]);
  }
}

// ---------------- 裁剪流程 ----------------
async function runTrim() {
  // 前置校验不通过时静默返回（失败反馈由任务中心 / 状态栏承担，不再弹右下角提示）
  if (state.jobs.trim) return;
  if (!state.currentVideo) return;
  if (!state.outputDir) return;
  const v = validateTrim(state);
  if (v.errors.length) return;
  setStatus("裁剪中…");
  const task = addTask("trim", state.currentVideo,
    { path: state.currentVideo, output_dir: state.outputDir, params: v.params });
  try {
    const res = await fetch("/api/trim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: state.currentVideo,
        output_dir: state.outputDir,
        params: v.params,
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      // 终点参数校验失败（后端返回 400）时，在终点行内同步提示
      if (data.message && data.message.indexOf("终点") !== -1) {
        const endRow = rowOf("end_value");
        if (endRow) showRowError(endRow, data.message);
      }
      finalizeTask(task, "fail", ["启动裁剪失败：" + (data.message || "")]);
      return;
    }
    task.jobId = data.job_id;
    state.jobs.trim = task.jobId;
    state.jobTasks.set(task.jobId, { kind: "trim", col: "trim", batch: false, tasks: [task] });
    setTaskRunning(task, "启动中…");
    openSSE(task.jobId, "trim");
  } catch (e) {
    finalizeTask(task, "fail", ["网络错误：" + e.message]);
  }
}

// ---------------- 任务中心：任务引擎 ----------------
// 任务中心把「单视频检测/裁剪」与「批量检测/裁剪」统一为任务条目，按列展示。
// 任务结构：{ key, col, path, name, status, progress, log, payload, jobId, index, result, error, _el }
const TASK_STATUS = { pending: "等待中", running: "运行中", success: "成功", fail: "失败", cancelled: "已取消" };

function tasksOf(col) { return state.tasks[col]; }

function taskListEl(col) {
  return document.getElementById(col === "detect" ? "detectTaskList" : "trimTaskList");
}

function taskEls(t) { return t._el || (t._el = {}); }

function mkTaskBtn(label, title) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "btn small"; b.textContent = label;
  b.title = title; b.disabled = true;
  return b;
}

function buildTaskRow(t) {
  const list = taskListEl(t.col);
  const row = document.createElement("div");
  row.className = "task-item";

  const head = document.createElement("div");
  head.className = "task-head";
  const name = document.createElement("div");
  name.className = "task-name"; name.textContent = t.name; name.title = t.path;
  const badge = document.createElement("span");
  badge.className = "badge b-pending"; badge.textContent = TASK_STATUS.pending;
  const time = document.createElement("span");
  time.className = "task-time";
  head.appendChild(name); head.appendChild(badge); head.appendChild(time);

  const bar = document.createElement("div");
  bar.className = "task-bar";
  const fill = document.createElement("div");
  fill.className = "progress-fill"; fill.style.width = "0%";
  bar.appendChild(fill);

  const detail = document.createElement("div");
  detail.className = "task-detail";

  const log = document.createElement("div");
  log.className = "task-log collapsed";

  const actions = document.createElement("div");
  actions.className = "task-actions";
  const bInterrupt = mkTaskBtn("中断", "中断该任务");
  const bRetry = mkTaskBtn("重试", "重新执行该任务");
  const bDel = mkTaskBtn("删除", "从列表移除该任务");
  actions.appendChild(bInterrupt);
  actions.appendChild(bRetry);
  actions.appendChild(bDel);

  row.appendChild(head);
  row.appendChild(bar);
  row.appendChild(detail);
  row.appendChild(log);
  row.appendChild(actions);
  list.appendChild(row);

  const els = { row, fill, badge, time, detail, log,
                interrupt: bInterrupt, retry: bRetry, del: bDel };
  // 点击卡片任意处展开 / 收起日志；按钮点击不触发切换（由各自处理器接管）
  row.addEventListener("click", (ev) => {
    if (ev.target.closest("button")) return;
    toggleTaskLog(t);
  });
  bInterrupt.addEventListener("click", () => interruptTask(t));
  bRetry.addEventListener("click", () => retryTask(t));
  bDel.addEventListener("click", () => removeTask(t));
  return els;
}

function toggleTaskLog(t) {
  taskEls(t).log.classList.toggle("collapsed");
}

function addTask(col, path, payload, init) {
  const t = Object.assign({
    key: "t" + (++state._taskSeq),
    col, path, name: basename(path) || "(未知)",
    status: "pending", progress: 0, log: [],
    payload: payload || null, jobId: null, index: null,
    result: null, error: null, finishedAt: null,
    groupKey: null, lastStage: null,
  }, init || {});
  tasksOf(col).push(t);
  t._el = buildTaskRow(t);
  refreshTaskVisibility(t);
  updateColumnTotal(col);
  return t;
}

function removeTask(t) {
  const arr = tasksOf(t.col);
  const i = arr.indexOf(t);
  if (i >= 0) arr.splice(i, 1);
  const els = taskEls(t);
  if (els && els.row && els.row.parentNode) els.row.parentNode.removeChild(els.row);
  // 折叠组内任务删空后移除组头；否则摘要随删行收窄
  if (t.groupKey) {
    const g = state.groups.get(t.groupKey);
    if (g) {
      if (g.tasks.every((tt) => !tasksOf(t.col).includes(tt))) {
        if (g.parent && g.parent.parentNode) g.parent.parentNode.removeChild(g.parent);
        state.groups.delete(t.groupKey);
      } else {
        refreshGroupSummary(g);
      }
    }
  }
  updateColumnTotal(t.col);
  refreshGroupVisibility(t.col);
}

function clearFinishedTasks(col) {
  tasksOf(col).slice().forEach((t) => {
    if (t.status === "success" || t.status === "fail" || t.status === "cancelled") removeTask(t);
  });
}

function updateColumnTotal(col) {
  const arr = tasksOf(col);
  const fill = document.getElementById(col === "detect" ? "detectTotalFill" : "trimTotalFill");
  const label = document.getElementById(col === "detect" ? "detectTotalLabel" : "trimTotalLabel");
  const empty = document.getElementById(col === "detect" ? "detectTaskEmpty" : "trimTaskEmpty");
  const box = fill ? fill.closest(".task-total") : null;
  // 「全部中断」可用性跟随本列是否存在等待中 / 运行中任务
  const stopAll = box ? box.querySelector("[data-interrupt-all]") : null;
  if (stopAll) stopAll.disabled = !arr.some((t) => t.status === "pending" || t.status === "running");
  if (empty) empty.hidden = arr.length > 0;
  if (!arr.length) {
    // 无任务：整体隐藏总进度区域，避免空轨道像"坏了"
    if (box) box.hidden = true;
    if (fill) { fill.style.width = "0%"; fill.classList.remove("full", "warn", "running"); }
    if (label) label.textContent = "";
    return;
  }
  if (box) box.hidden = false;
  // 只统计"当前活动任务集"：有未完成任务时，已完成的历史任务不再拉低总进度；
  // 全部完成时展示整个列表的最终结果（避免新批次/清空导致进度条往回走）。
  const activeJobs = new Set();
  let anyActive = false;
  arr.forEach((t) => {
    if (t.status === "pending" || t.status === "running") {
      anyActive = true;
      if (t.jobId) activeJobs.add(t.jobId);
    }
  });
  const base = anyActive
    ? arr.filter((t) => (t.jobId ? activeJobs.has(t.jobId) : t.status === "pending" || t.status === "running"))
    : arr;
  // 条宽 = 处理完成率（失败/取消也算"跑完"）；颜色 = 结果质量（绿=全成，橙红=有失败，蓝流光=运行中）
  let sum = 0, success = 0, hasFail = false, running = false;
  base.forEach((t) => {
    if (t.status === "success") { sum += 100; success++; }
    else if (t.status === "fail" || t.status === "cancelled") { sum += 100; hasFail = true; }
    else { sum += t.progress; if (t.status === "running") running = true; }
  });
  if (fill) {
    fill.style.width = (sum / base.length).toFixed(1) + "%";
    fill.classList.toggle("full", success === base.length);
    fill.classList.toggle("warn", hasFail);
    fill.classList.toggle("running", running);
  }
  // 标签 = 成功数/总数，失败不再计入分子
  if (label) label.textContent = success + "/" + base.length;
}

// ---------------- 筛选 ----------------
function switchFilter(col, f) {
  state.filters[col] = f;
  const tools = document.querySelector(`.task-tools[data-col="${col}"]`);
  if (tools) tools.querySelectorAll(".chip").forEach((c) => {
    c.classList.toggle("active", c.dataset.filter === f);
  });
  tasksOf(col).forEach(refreshTaskVisibility);
  refreshGroupVisibility(col);
}

function refreshTaskVisibility(t) {
  const f = state.filters[t.col] || "all";
  const keep = (
    f === "all" ? true :
    f === "running" ? (t.status === "running" || t.status === "pending") :
    f === "done" ? t.status === "success" :
    (t.status === "fail" || t.status === "cancelled")
  );
  taskEls(t).row.classList.toggle("hidden", !keep);
}

// 状态迁移后重投影可见性：行按当前筛选条件显隐，批量组头随组内任务收放。
// 由 setTaskRunning / setTaskProgress / finalizeTask / startRetry 在状态变化时调用，
// 保证「失败」里重试的任务立刻移入「运行中」、跑完后再按结果归位。
function refreshTaskFilterView(t) {
  refreshTaskVisibility(t);
  refreshGroupVisibility(t.col);
}

// ---------------- 批量折叠组 ----------------
// 批量任务结束后折叠为一行摘要（可展开查看逐文件明细）。
// 摘要是实时投影：重算自「仍存在于列中的组内任务」，重试 / 删行后自动跟进。
function collapseBatch(ctx) {
  if (!ctx.batch || !ctx.tasks || ctx.tasks.length <= 1) return;
  if (state.groups.has(ctx.jobId)) return;
  const tasks = ctx.tasks;
  const firstEl = taskEls(tasks[0]).row;
  const parent = document.createElement("div");
  parent.className = "task-group";
  const caret = document.createElement("span");
  caret.className = "caret"; caret.textContent = "▶";
  const title = document.createElement("span");
  title.className = "task-group-title";
  const summary = document.createElement("span");
  summary.className = "task-group-summary";
  parent.appendChild(caret);
  parent.appendChild(title);
  parent.appendChild(summary);
  firstEl.parentNode.insertBefore(parent, firstEl);

  const g = {
    parent, tasks, expanded: false,
    kind: ctx.kind === "batch_detect" ? "批量检测" : "批量裁剪",
    title, summary,
  };
  state.groups.set(ctx.jobId, g);
  tasks.forEach((t) => { taskEls(t).row.classList.add("group-hide"); });
  parent.addEventListener("click", () => toggleBatchGroup(g));
  refreshGroupSummary(g);
  refreshGroupVisibility(ctx.col);
}

// 重算折叠组摘要：只统计仍存在于列中的组内任务（已删行不计入）。
// 由 collapseBatch（构建）、finalizeTask / startRetry（重试流）、removeTask（删行）触发。
function refreshGroupSummary(g) {
  const arr = tasksOf(g.tasks[0].col);
  const live = g.tasks.filter((t) => arr.includes(t));
  const ok = live.filter((t) => t.status === "success").length;
  const fail = live.filter((t) => t.status === "fail").length;
  const canc = live.filter((t) => t.status === "cancelled").length;
  g.title.textContent = g.kind + " · " + live.length + " 个文件";
  let sum = ok + "/" + live.length;
  if (fail) sum += " · 失败 " + fail;
  if (canc) sum += " · 取消 " + canc;
  g.summary.textContent = sum;
}

function toggleBatchGroup(g) {
  g.expanded = !g.expanded;
  g.parent.classList.toggle("open", g.expanded);
  g.tasks.forEach((t) => taskEls(t).row.classList.toggle("group-hide", !g.expanded));
}

function refreshGroupVisibility(col) {
  const f = state.filters[col] || "all";
  state.groups.forEach((g) => {
    if (!g.tasks.length || g.tasks[0].col !== col) return;
    const parent = g.parent;
    if (!parent || !parent.parentNode) return;
    if (f === "all") { parent.classList.remove("hidden"); return; }
    const visible = g.tasks.some((t) => (
      f === "running" ? (t.status === "running" || t.status === "pending") :
      f === "done" ? t.status === "success" :
      (t.status === "fail" || t.status === "cancelled")
    ));
    parent.classList.toggle("hidden", !visible);
  });
}

// ---------------- 任务状态更新 ----------------
const BADGE_MAP = {
  pending: ["b-pending", "等待中"],
  running: ["b-running", "运行中"],
  success: ["b-success", "成功"],
  fail: ["b-fail", "失败"],
  cancelled: ["b-cancelled", "已取消"],
};

function setBadge(el, status) {
  const item = BADGE_MAP[status] || BADGE_MAP.pending;
  el.className = "badge " + item[0];
  el.textContent = item[1];
}

function fmtTime(ts) {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function taskLog(t, txt) {
  if (txt == null || txt === "") return;
  const els = taskEls(t);
  const d = document.createElement("div");
  d.textContent = txt;
  els.log.appendChild(d);
  while (els.log.children.length > 30) els.log.removeChild(els.log.firstChild);
}

function setTaskRunning(t, label) {
  t.status = "running";
  const els = taskEls(t);
  els.row.classList.remove("pending", "done", "fail", "cancelled");
  els.row.classList.add("running");
  setBadge(els.badge, "running");
  if (label) els.detail.textContent = label;
  updateTaskActions(t);
  updateColumnTotal(t.col);
  refreshTaskFilterView(t);
}

function setTaskProgress(t, pct, summary) {
  const wasRunning = t.status === "running";
  if (t.status !== "success" && t.status !== "fail" && t.status !== "cancelled") t.status = "running";
  t.progress = Math.max(0, Math.min(100, Math.round(Number(pct) || 0)));
  taskEls(t).fill.style.width = t.progress + "%";
  if (summary) taskEls(t).detail.textContent = summary;
  updateTaskActions(t);
  updateColumnTotal(t.col);
  // 兜底：任务未经 setTaskRunning 直接收到进度时（pending → running）也要落入「运行中」
  if (!wasRunning) refreshTaskFilterView(t);
}

function finalizeTask(t, status, lines, result, error) {
  t.status = status;
  t.progress = 100;
  t.finishedAt = Date.now();
  t.result = result || null;
  t.error = error || null;
  const els = taskEls(t);
  els.row.classList.remove("pending", "running", "done", "fail", "cancelled");
  els.row.classList.add(status === "success" ? "done" : status);
  els.fill.style.width = "100%";
  setBadge(els.badge, status);
  els.time.textContent = fmtTime(t.finishedAt);
  els.detail.textContent = (lines && lines.length) ? lines[0] : TASK_STATUS[status];
  (lines || []).forEach((l) => taskLog(t, l));
  updateTaskActions(t);
  updateColumnTotal(t.col);
  refreshTaskFilterView(t);
  const g = t.groupKey && state.groups.get(t.groupKey);
  if (g) refreshGroupSummary(g);
}

function updateTaskActions(t) {
  const els = taskEls(t);
  const queued = !!(state.retryQueues[t.col] && state.retryQueues[t.col].includes(t));
  const running = ((t.status === "running" || t.status === "pending") && !!t.jobId) || queued;
  const done = t.status === "success" || t.status === "fail" || t.status === "cancelled";
  els.interrupt.disabled = !running;
  els.retry.disabled = !(done && !!t.payload);
  // 删除仅移除条目、不取消后端任务：运行中禁用，避免任务失控后无从中断
  els.del.disabled = !done;
}

// 单个任务结果终态 -> { status, lines }
function finalizeItemResult(r, col) {
  if (!r) return { status: "fail", lines: ["任务异常结束"] };
  if (r.cancelled) return { status: "cancelled", lines: ["任务已取消"] };
  if (r.error) return { status: "fail", lines: ["✗ " + r.error] };
  if (col === "detect") {
    const lines = (r.skipped ? ["已使用缓存结果"] : []);
    if (!r.detected) {
      lines.push("未检测到目标颜色" + (r.message ? "：" + r.message : ""));
      return { status: "fail", lines };
    }
    const ts = (r.timestamp != null) ? r.timestamp.toFixed(3) + "s" : "?";
    const conf = (r.confidence != null) ? "（置信度 " + r.confidence.toFixed(4) + "）" : "";
    lines.push("检测到目标颜色：帧#" + (r.frame + 1) + " @ " + ts + conf);
    return { status: "success", lines };
  }
  if (r.ok === false) return { status: "fail", lines: ["✗ " + (r.error || "裁剪失败")] };
  const out = (r.output_files || []).map((f) => "↳ " + basename(f));
  const range = "起点：" + (r.frame ? "#" + r.frame + " 帧" : (r.timestamp || 0).toFixed(3) + "s");
  return { status: "success", lines: ["裁剪完成：" + range].concat(out) };
}

// ---------------- 中断 / 重试 ----------------
function interruptTask(t) {
  const queue = state.retryQueues[t.col];
  const qi = queue ? queue.indexOf(t) : -1;
  if (qi !== -1) {
    // 排队未启动：出队即取消，无需后端参与
    queue.splice(qi, 1);
    finalizeTask(t, "cancelled", ["已取消（尚未开始）"]);
    setStatus("已取消排队任务「" + t.name + "」");
    return;
  }
  if (!t.jobId) return;
  const ctx = state.jobTasks.get(t.jobId);
  const colName = t.col === "detect" ? "检测" : "裁剪";
  if (ctx && ctx.batch && t.index) {
    // 批量任务：单文件取消，不影响同批其他文件
    fetchJsonTimeout("/api/jobs/" + t.jobId + "/cancel-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: t.index }),
    }).catch(() => {});
    finalizeTask(t, "cancelled", ["已请求中断该文件任务"]);
    setStatus("已请求中断「" + t.name + "」");
  } else {
    fetchJsonTimeout("/api/jobs/" + t.jobId + "/cancel", { method: "POST" }).catch(() => {});
    setStatus(colName + "任务已请求中断");
  }
  updateTaskActions(t);
}

// 「全部中断」：取消本列当前运行中的全部任务（批量、单文件与重试），
// 逐个 job 发整单取消，终态由各自 SSE 的 cancelled 事件统一落到任务行；
// 排队未启动的重试任务直接出队终态化
function interruptColumnAll(col) {
  const ids = [];
  ["detect", "trim", "batch"].forEach((k) => {
    const jid = state.jobs[k];
    if (!jid) return;
    const ctx = state.jobTasks.get(jid);
    if (ctx && ctx.col === col) ids.push(jid);
  });
  state.retryRunning[col].forEach((t) => {
    if (t.jobId && !ids.includes(t.jobId)) ids.push(t.jobId);
  });
  const queue = state.retryQueues[col];
  const queuedCount = queue.length;
  while (queue.length) finalizeTask(queue.shift(), "cancelled", ["已取消（尚未开始）"]);
  if (!ids.length && !queuedCount) return;
  ids.forEach((jid) =>
    fetchJsonTimeout("/api/jobs/" + jid + "/cancel", { method: "POST" }).catch(() => {}));
  setStatus("已请求中断本列全部任务");
}

function retryTask(t) {
  if (!t.payload) return;
  startRetry(t);
  enqueueRetry(t);
}

function startRetry(t) {
  t.status = "pending"; t.progress = 0; t.result = null; t.error = null; t.log = [];
  t.finishedAt = null; t.lastStage = null;
  const els = taskEls(t);
  els.row.classList.remove("done", "fail", "cancelled", "running");
  els.row.classList.add("pending");
  els.fill.style.width = "0%";
  setBadge(els.badge, "pending");
  els.time.textContent = "";
  els.detail.textContent = "";
  els.log.innerHTML = "";
  els.log.classList.add("collapsed");
  updateTaskActions(t);
  updateColumnTotal(t.col);
  refreshTaskFilterView(t);  // 重试使任务离开「失败」视图，等待 / 运行中归位
  const g = t.groupKey && state.groups.get(t.groupKey);
  if (g) refreshGroupSummary(g);  // 重试使任务回到未完成态，摘要立即跟进
}

// ---------------- 重试排队 ----------------
// 重试不再与主流程互斥：每列维护一个队列，调度器按「批处理 → 并发数」
// （0 = 自动）派发独立单文件任务，排队任务显示等待中，可中断出队。
function parallelWorkers() {
  const inp = document.getElementById("param-batch_workers");
  const n = inp ? Math.floor(Number(inp.value) || 0) : 0;
  if (n > 0) return Math.min(16, n);
  const hc = navigator.hardwareConcurrency || 1;
  return Math.max(1, Math.min(hc, 4));  // 与后端 _parallel_workers 默认策略对齐
}

function enqueueRetry(t) {
  state.retryQueues[t.col].push(t);
  updateTaskActions(t);  // 排队任务可中断（出队即取消）
  taskEls(t).detail.textContent = "排队中…";
  pumpRetryQueue(t.col);
  if (state.retryQueues[t.col].includes(t)) {
    setStatus("「" + t.name + "」并发已满，已排队等待自动开始");
  }
}

function pumpRetryQueue(col) {
  const queue = state.retryQueues[col];
  while (queue.length && state.retryRunning[col].size < parallelWorkers()) {
    const t = queue.shift();
    if (!tasksOf(col).includes(t)) continue;  // 行已被移除：跳过
    state.retryRunning[col].add(t);
    launchRetryJob(t);
  }
}

// 重试失败落终态并让队列继续推进。ctx 存在（启动后阶段的失败，如轮询
// 连续出错）时走 finishRetryJob 完整清理，避免 jobTasks 遗留死 ctx；
// 启动前失败（POST 失败/超时）尚无 ctx，仅释放名额。
function finishRetryFailure(t, msg, ctx) {
  finalizeTask(t, "fail", [msg]);
  if (ctx) {
    finishRetryJob(ctx);
    return;
  }
  state.retryRunning[t.col].delete(t);
  pumpRetryQueue(t.col);
}

// 重试任务结束（终态确认或清理）：释放并发名额并推进本列队列。
// 与 openSSE 的 stop() 等价，但重试走轮询、无 SSE 需关闭。
function finishRetryJob(ctx) {
  clearTimeout(ctx._pollTimer);
  const t = ctx.tasks[0];
  state.retryRunning[ctx.col].delete(t);
  state.jobTasks.delete(ctx.jobId);
  updateActions();
  pumpRetryQueue(ctx.col);
}

// 带超时的 JSON 请求：浏览器同主机连接数有限（SSE 长连接会挤占），
// 网络栈偶发把请求搁置且永不唤醒——超时兜底把卡死转成可见失败。
async function fetchJsonTimeout(url, opts, ms) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms || 8000);
  try {
    const res = await fetch(url, Object.assign({ cache: "no-store" }, opts, { signal: ctl.signal }));
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

// 终态分发（重试轮询与 SSE 断线兜底共用）：按任务状态落到对应处理器，
// 返回是否已终态。处理器异常不阻断调用方的清理（名额 / 槽位必须释放）。
function applyJobStatus(ctx, d) {
  try {
    if (d.status === "done") handleJobDone(ctx, d.result || {});
    else if (d.status === "error") handleJobError(ctx, { message: d.error || "任务执行失败" });
    else if (d.status === "cancelled") handleJobCancelled(ctx);
    else return false;
  } catch (e) { /* 终态已落，副作用失败不影响清理 */ }
  return true;
}

// 重试任务进度轮询：不再为每个重试开一条 SSE 长连接（并发重试会把浏览器
// 同主机连接配额占满，后续任何请求都会被搁置），改用 1s 增量轮询——带游标
// 拉取任务事件回放缓冲，逐事件喂入与 SSE 相同的渲染管线，粒度与首跑一致。
function pollRetryJob(ctx) {
  let errors = 0;
  const tick = async () => {
    if (!state.jobTasks.has(ctx.jobId)) return;  // 已被外部清理
    try {
      const since = ctx._pollSeq || 0;
      const d = await fetchJsonTimeout("/api/jobs/" + ctx.jobId + "?since=" + since, {}, 5000);
      if (!state.jobTasks.has(ctx.jobId)) return;
      errors = 0;
      // 逐事件回放：与 SSE 同粒度，进度条与日志去重规则看到完整事件序列，
      // 表现和首跑一致（而不是每秒只采样最新快照）
      (d.events || []).forEach((ev) => {
        if (ev.type === "progress") handleJobProgress(ctx, ev.data);
        ctx._pollSeq = ev.seq;
      });
      if (applyJobStatus(ctx, d)) {
        finishRetryJob(ctx);
        return;
      }
      // 尚无任何进度事件时显示已用时
      const t = ctx.tasks[0];
      if ((d.events || []).length === 0 && !d.progress) {
        t._pollSec = (t._pollSec || 0) + 1;
        taskEls(t).detail.textContent =
          (ctx.kind === "detect" ? "检测中…" : "裁剪中…") + "（" + t._pollSec + "s）";
      }
    } catch (e) {
      errors++;
      if (errors >= 3) {
        finishRetryFailure(ctx.tasks[0], "任务状态查询失败：" + e.message, ctx);
        return;
      }
    }
    ctx._pollTimer = setTimeout(tick, 1000);
  };
  tick();
}

async function launchRetryJob(t) {
  const isDetect = t.col === "detect";
  let body;
  if (isDetect) {
    body = { path: t.path, params: t.payload.params };
  } else {
    const params = Object.assign({}, t.payload.params);
    if (t.payload.frame) { params.start_mode = "frame"; params.start_value = t.payload.frame; }
    body = { path: t.path, output_dir: t.payload.output_dir, params };
  }
  try {
    const data = await fetchJsonTimeout(isDetect ? "/api/detect" : "/api/trim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!data.ok) {
      finishRetryFailure(t, "启动" + (isDetect ? "检测" : "裁剪") + "失败：" + (data.message || ""));
      return;
    }
    t.jobId = data.job_id;
    t._pollSec = 0;
    const ctx = {
      kind: isDetect ? "detect" : "trim", col: t.col, batch: false, retry: true, tasks: [t],
      jobId: data.job_id,
    };
    state.jobTasks.set(data.job_id, ctx);
    setTaskRunning(t, "启动中…");
    pollRetryJob(ctx);
  } catch (e) {
    const timedOut = e && e.name === "AbortError";
    finishRetryFailure(t, timedOut ? "启动请求超时（浏览器连接受限），请重试" : "网络错误：" + e.message);
  }
}

// ---------------- 批量流程 ----------------
async function runBatchDetect() {
  const files = [...state.sel];
  // 前置校验不通过时静默返回（失败反馈由任务中心 / 状态栏承担）
  if (!files.length) return;
  if (state.jobs.batch) return;
  const v = validateDetect(state);
  if (v.errors.length) return;
  setStatus("批量检测中…");
  const tasks = files.map((f, i) => addTask("detect", f, { path: f, params: v.params }, { index: i + 1 }));
  try {
    const res = await fetch("/api/batch/detect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        files,
        params: v.params,
        skip_cached: document.getElementById("skipCached")?.checked || false,
      }),
    });
    const data = await res.json();
    if (!data.ok) { tasks.forEach((t) => finalizeTask(t, "fail", ["启动批量检测失败：" + (data.message || "")])); return; }
    state.jobs.batch = data.job_id;
    tasks.forEach((t) => { t.jobId = data.job_id; t.groupKey = data.job_id; updateTaskActions(t); });
    state.jobTasks.set(data.job_id, { kind: "batch_detect", col: "detect", batch: true, tasks, jobId: data.job_id });
    openSSE(data.job_id, "batch_detect");
  } catch (e) {
    tasks.forEach((t) => finalizeTask(t, "fail", ["网络错误：" + e.message]));
  }
}

async function runBatchTrim() {
  const files = [...state.sel];
  // 前置校验不通过时静默返回（失败反馈由任务中心 / 状态栏承担）
  if (!files.length) return;
  if (state.jobs.batch) return;
  if (!state.outputDir) return;
  // 批量裁剪以每个文件各自的检测结果为起点、保留到片尾，
  // 不使用侧栏的会话级起点 / 终点，因此不做相应校验
  const params = collectParams(TRIM_PARAMS);
  // 批量裁剪始终使用批量检测的输出结果作为每个文件的裁剪起点
  const items = [];
  let usable = 0;
  for (const f of files) {
    const r = state.batchDetectResults[f];
    if (r && r.detected && r.frame !== null && r.frame !== undefined) { items.push({ path: f, frame: r.frame + 1 }); usable++; }
    else items.push({ path: f, skip: true, reason: "无检测结果（未命中或未执行批量检测）" });
  }
  // 全部文件都无检测结果时任务中心不会产生条目，用状态栏说明原因
  if (!usable) {
    setStatus("批量裁剪未执行：选中文件均无有效检测结果，请先执行「检测」或「批量检测」");
    return;
  }
  setStatus("批量裁剪中…");
  const tasks = files.map((f, i) => {
    const r = state.batchDetectResults[f];
    return addTask("trim", f,
      { path: f, output_dir: state.outputDir, params, frame: (r && r.detected) ? r.frame + 1 : null },
      { index: i + 1 });
  });
  try {
    const res = await fetch("/api/batch/trim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files: items, output_dir: state.outputDir, params }),
    });
    const data = await res.json();
    if (!data.ok) { tasks.forEach((t) => finalizeTask(t, "fail", ["启动批量裁剪失败：" + (data.message || "")])); return; }
    state.jobs.batch = data.job_id;
    tasks.forEach((t) => { t.jobId = data.job_id; t.groupKey = data.job_id; updateTaskActions(t); });
    state.jobTasks.set(data.job_id, { kind: "batch_trim", col: "trim", batch: true, tasks, jobId: data.job_id });
    openSSE(data.job_id, "batch_trim");
  } catch (e) {
    tasks.forEach((t) => finalizeTask(t, "fail", ["网络错误：" + e.message]));
  }
}

// ---------------- 任务状态 / SSE ----------------
function openSSE(jobId, kind) {
  const ctx = state.jobTasks.get(jobId);
  if (!ctx) return;
  ctx.jobId = jobId;
  const jobKey = ctx.batch ? "batch" : kind;
  const es = new EventSource("/api/jobs/" + jobId + "/events");
  let settled = false;
  const stop = () => {
    if (settled) return;
    settled = true;
    es.close();
    state.jobs[jobKey] = null;
    state.jobTasks.delete(jobId);
    updateActions();
  };
  // 网络层断开兜底：SSE 连接中断可能使终态事件（done/error/cancelled）丢失，
  // 主动查询一次任务状态，确保任务结束后按钮与任务状态不被卡死。
  const pollSettled = () => {
    fetchJsonTimeout("/api/jobs/" + jobId, {}, 5000)
      .then((d) => {
        if (settled || !d.ok) return;
        if (applyJobStatus(ctx, d)) stop();
      })
      .catch(() => { /* 查询失败不影响 SSE 主流程 */ });
  };
  es.addEventListener("progress", (ev) => {
    let p; try { p = JSON.parse(ev.data); } catch (e) { return; }
    handleJobProgress(ctx, p);
  });
  es.addEventListener("file_done", (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
    handleJobFileDone(ctx, d);
  });
  es.addEventListener("done", (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch (e) { d = {}; }
    try { handleJobDone(ctx, d); } finally { stop(); }  // stop 必须执行，否则名额/槽位泄漏
  });
  es.addEventListener("cancelled", () => {
    try { handleJobCancelled(ctx); } finally { stop(); }
  });
  es.addEventListener("error", (ev) => {
    if (ev.data) {
      let d; try { d = JSON.parse(ev.data); } catch (e) { d = {}; }
      try { handleJobError(ctx, d); } finally { stop(); }
      return;
    }
    pollSettled();
  });
}

function markJobsRunning(ctx) {
  ctx.tasks.forEach((t) => { if (t.status === "pending") setTaskRunning(t); });
}

function stageLabel(stage) {
  return { coarse: "粗扫", fine: "细化", precise: "逐帧", fallback: "兜底细扫",
           locate: "定位", pending: "准备", done: "完成" }[stage] || stage || "";
}

// ---------------- 任务中心：SSE 事件处理 ----------------
// 日志减噪：仅记录阶段切换、关键事件（命中/完成/失败/跳过/错误）与整百分比里程碑，
// 逐帧扫描等高频消息只更新进度与详情行，不再逐条刷入日志。
// 批量任务中单个文件处理完成：立即终态化该任务（含缓存命中/取消/失败），
// 不必等整批 done 事件一次性刷新，总进度条随之单调推进。
function handleJobFileDone(ctx, d) {
  const t = ctx.tasks[(d.index || 1) - 1];
  if (!t) { updateColumnTotal(ctx.col); return; }
  const r = finalizeItemResult(d.result || {}, ctx.kind === "batch_detect" ? "detect" : "trim");
  finalizeTask(t, r.status, r.lines, d.result || null);
}
function handleJobProgress(ctx, p) {
  const kind = ctx.kind;
  if (kind === "detect" || kind === "trim") {
    const t = ctx.tasks[0];
    if (!t) return;
    markJobsRunning(ctx);
    if (kind === "detect") {
      const pct = Math.round((p.progress || 0) * 100);
      const st = stageLabel(p.stage);
      const txt = (st && p.message && p.message !== st) ? st + "：" + p.message : (st || p.message || "");
      setTaskProgress(t, pct, txt || ("检测中 " + pct + "%"));
      const stageChanged = t.lastStage !== (p.stage || "");
      t.lastStage = p.stage || "";
      if (stageChanged || /命中|完成|失败|跳过|错误/.test(txt || "") || pct % 25 === 0) {
        taskLog(t, txt || ("检测中 " + pct + "%"));
      }
    } else {
      const pct = Math.round(p.percent || 0);
      setTaskProgress(t, pct, "裁剪中 " + pct + "%（" + (p.seconds || 0).toFixed(1) + "s）");
      if (pct >= 100 || pct === 0 || pct - (t._lastTrimLog || 0) >= 25) {
        taskLog(t, "裁剪中 " + pct + "%");
        t._lastTrimLog = pct;
      }
    }
    return;
  }
  // 批量：按 index 定位到对应文件任务
  markJobsRunning(ctx);
  const t = ctx.tasks[(p.index || 1) - 1];
  if (!t) { updateColumnTotal(ctx.col); return; }
  if (kind === "batch_detect") {
    // 单文件进度从 0 起（后端 pending 事件已带 progress=0），
    // 不使用批内位置百分比，避免任务开局虚高、总进度条随后回退
    const pct = Math.round((p.progress || 0) * 100);
    const st = stageLabel(p.stage);
    const txt = st ? (st + "：" + (p.message || "")) : (p.message || "");
    setTaskProgress(t, pct, txt || ("检测中 " + pct + "%"));
    const stageChanged = t.lastStage !== (p.stage || "");
    t.lastStage = p.stage || "";
    if (stageChanged || /命中|完成|失败|跳过|错误/.test(txt || "") || pct % 25 === 0) {
      taskLog(t, txt || ("检测中 " + pct + "%"));
    }
  } else {
    const pctF = (p.percent_file != null) ? Math.round(p.percent_file) : null;
    const pct = (pctF != null) ? pctF : 0;
    setTaskProgress(t, pct, "裁剪中 " + pct + "%");
    if (pctF != null && (pctF >= 100 || pctF === 0 || pctF - (t._lastTrimLog || 0) >= 25)) {
      taskLog(t, "裁剪中 " + pctF + "%");
      t._lastTrimLog = pctF;
    }
  }
  updateColumnTotal(ctx.col);
}

function handleJobDone(ctx, data) {
  const kind = ctx.kind;
  if (kind === "detect") {
    const t = ctx.tasks[0];
    if (!t) return;
    const r = finalizeItemResult(data, "detect");
    finalizeTask(t, r.status, r.lines, data);
    applyDetectSideEffects(data);
    return;
  }
  if (kind === "trim") {
    const t = ctx.tasks[0];
    if (!t) return;
    const r = finalizeItemResult(data, "trim");
    finalizeTask(t, r.status, r.lines, data);
    setStatus("裁剪完成");
    return;
  }
  if (kind === "batch_detect") {
    const results = data.results || [];
    const map = {};
    results.forEach((r) => { if (r && r.file) { map[r.file] = r; state.batchRuns.add(r.file); } });
    state.batchDetectResults = map;
    ctx.tasks.forEach((t, i) => {
      // 已由 file_done 终态化的跳过，避免日志重复（断线兜底时仍会补齐）
      if (t.status === "success" || t.status === "fail" || t.status === "cancelled") return;
      const r = finalizeItemResult(results[i] || {}, "detect");
      finalizeTask(t, r.status, r.lines, results[i] || null);
    });
    const s = data.summary || {};
    setStatus("批量检测完成：命中 " + (s.detected || 0) + " / " + (s.total || 0) + (s.failed ? "，失败 " + s.failed : "") + (s.cancelled ? "，取消 " + s.cancelled : ""));
    collapseBatch(ctx);
    return;
  }
  // batch_trim
  const results = data.results || [];
  ctx.tasks.forEach((t, i) => {
    // 已由 file_done 终态化的跳过，避免日志重复（断线兜底时仍会补齐）
    if (t.status === "success" || t.status === "fail" || t.status === "cancelled") return;
    const r = finalizeItemResult(results[i] || {}, "trim");
    finalizeTask(t, r.status, r.lines, results[i] || null);
  });
  const s = data.summary || {};
  setStatus("批量裁剪完成：成功 " + (s.ok || 0) + " / " + (s.total || 0) + (s.failed ? "，失败 " + s.failed : "") + (s.cancelled ? "，取消 " + s.cancelled : ""));
  collapseBatch(ctx);
}

function handleJobError(ctx, data) {
  ctx.tasks.forEach((t) => finalizeTask(t, "fail", ["✗ " + (data.message || "任务执行失败")], null, data.message));
  setStatus("任务失败");
  collapseBatch(ctx);
}

function handleJobCancelled(ctx) {
  ctx.tasks.forEach((t) => finalizeTask(t, "cancelled", ["任务已取消"]));
  setStatus("任务已取消");
  collapseBatch(ctx);
}

// ---------------- 结果展示 ----------------
// 单视频检测完成后的副作用：关联裁剪起点 + 播放器标记
function applyDetectSideEffects(data) {
  const srcPath = (data && data.path) || state.currentVideo;
  if (srcPath) { state.batchDetectResults[srcPath] = data; state.batchRuns.add(srcPath); }
  if (data.detected) {
    setParam("start_mode", "frame");
    setParam("start_value", data.frame + 1);
    Player.setDetected(data, state.currentVideo);
    const row = rowOf("start_value");
    if (row) {
      const tip = document.createElement("div");
      tip.className = "param-tip";
      tip.textContent =
        "已自动关联检测结果：帧 #" + (data.frame + 1) + " @ " +
        (data.timestamp !== null && data.timestamp !== undefined ? data.timestamp.toFixed(3) : "?") + "s（可修改）";
      if (!row.querySelector(".param-tip")) row.appendChild(tip);
    }
    setStatus("检测完成，已自动关联裁剪起点");
  } else {
    Player.clearDetected();
    setStatus("检测完成（未命中）");
  }
}

// 打开视频时，把已匹配的检测结果（缓存 / 本次批量）登记为一条已完成任务
function renderDetectResult(data, sourceTag) {
  const path = (data && data.path) || state.currentVideo || "(未知)";
  // 同一文件已有成功检测记录时不再追加，避免缓存命中与真实任务重复
  if (tasksOf("detect").some((t) => t.path === path && t.status === "success")) return null;
  const r = finalizeItemResult(data, "detect");
  const tag = sourceTag ? "（" + sourceTag + "）" : "";
  const t = addTask("detect", path, null);
  finalizeTask(t, r.status, r.lines.map((l) => l + tag), data);
  // 缓存命中：用独立徽标标记（不可重试）
  if (sourceTag) {
    const els = taskEls(t);
    els.badge.textContent = "缓存";
    els.badge.className = "badge b-cache";
  }
  return t;
}

// ---------------- 动作可用性（状态流转） ----------------
function updateActions() {
  const hasVideo = !!state.currentVideo;
  const hasSel = state.sel && state.sel.size > 0;
  const batchBusy = !!state.jobs.batch;
  const btnDetect = document.getElementById("btnDetect");
  const btnTrim = document.getElementById("btnTrim");
  const btnSetAsStart = document.getElementById("btnSetAsStart");
  const btnSetAsEnd = document.getElementById("btnSetAsEnd");
  const btnBatchDetect = document.getElementById("btnBatchDetect");
  const btnBatchTrim = document.getElementById("btnBatchTrim");
  if (btnDetect) btnDetect.disabled = !hasVideo || !!state.jobs.detect;
  if (btnTrim) btnTrim.disabled = !hasVideo || !!state.jobs.trim || !state.outputDir;
  if (btnSetAsStart) btnSetAsStart.disabled = !hasVideo;
  if (btnSetAsEnd) btnSetAsEnd.disabled = !hasVideo;
  if (btnBatchDetect) btnBatchDetect.disabled = !hasSel || batchBusy;
  if (btnBatchTrim) btnBatchTrim.disabled = !hasSel || batchBusy || !state.outputDir;
}

// ---------------- 终点参数联动（区间裁剪） ----------------
// 终点方式为「无（到片尾）」时禁用终点输入框，并清除终点值与终点标记
// （选择「无」即「清除终点」，退化为原有片头裁剪行为；不触碰起点参数）。
function syncEndControls() {
  const modeEl = document.getElementById("param-end_mode");
  const valEl = document.getElementById("param-end_value");
  if (!modeEl || !valEl) return;
  const none = modeEl.value === "none";
  valEl.disabled = none;
  if (none) {
    setParam("end_value", "");
    Player.clearEndMark();
  }
}

function initTrimEndControls() {
  const modeEl = document.getElementById("param-end_mode");
  if (modeEl) modeEl.addEventListener("change", syncEndControls);
  syncEndControls();
}

// ---------------- 设置自动保存（配置文件持久化） ----------------
let settingsTimer = null;

function scheduleSave() {
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(saveSettings, 400);
}

async function saveSettings() {
  // 裁剪起点 / 终点（start_mode / start_value / end_mode / end_value）
  // 因视频而异，不写入配置文件
  const trimmer = collectParams(TRIM_PARAMS);
  delete trimmer.start_mode;
  delete trimmer.start_value;
  delete trimmer.end_mode;
  delete trimmer.end_value;
  const batchWorkersEl = document.getElementById("param-batch_workers");
  const payload = {
    workspace: state.workspace || "",
    output_dir: state.outputDir || "",
    detector: collectParams(DETECTOR_PARAMS),
    trimmer,
    batch: {
      workers: batchWorkersEl
        ? Math.max(0, Math.min(16, Math.floor(Number(batchWorkersEl.value) || 0)))
        : 0,
    },
  };
  try {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) { /* 保存失败静默处理，不影响正常使用 */ }
}

async function resetSettings() {
  if (!window.confirm("确定恢复所有默认设置吗？\n将重置工作区、输出目录及全部参数。")) return;
  clearTimeout(settingsTimer);
  try {
    const res = await fetch("/api/settings/reset", { method: "POST" });
    const data = await res.json();
    if (!data.ok) return;
    const s = data.settings || {};
    buildParamsForm(document.getElementById("detectParams"), DETECTOR_PARAMS, s.detector);
    buildParamsForm(document.getElementById("trimParams"), TRIM_PARAMS, s.trimmer);
    initTrimEndControls();  // 表单重建后重新绑定终点方式联动（终点默认「无」）
    // 批处理 / 缓存卡片控件不随 buildParamsForm 重建，手动恢复默认
    const skipCached = document.getElementById("skipCached");
    if (skipCached) skipCached.checked = false;
    const batchWorkers = document.getElementById("param-batch_workers");
    if (batchWorkers) batchWorkers.value = "0";
    await refreshWorkspace();
    await refreshOutput();
    updateActions();
    setStatus("已恢复默认设置");
  } catch (e) { /* 忽略：重置失败时页面保持原状 */ }
}

// ---------------- 事件绑定 ----------------
function wireUI() {
  document.getElementById("workspaceLoad").addEventListener("click", () => {
    loadWorkspace(document.getElementById("workspacePath").value.trim());
  });
  document.getElementById("workspacePath").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadWorkspace(document.getElementById("workspacePath").value.trim());
  });
  document.getElementById("workspaceBrowse").addEventListener("click", () => openDirModal("workspace"));
  document.getElementById("outputBrowse").addEventListener("click", () => openDirModal("output"));
  document.getElementById("outputPath").addEventListener("keydown", (e) => {
    if (e.key === "Enter") setOutputDir(document.getElementById("outputPath").value.trim());
  });

  document.getElementById("btnDetect").addEventListener("click", runDetect);
  document.getElementById("btnTrim").addEventListener("click", runTrim);

  // 任务中心：筛选 chip 与「清空」按钮（两列各一套）
  ["detect", "trim"].forEach((col) => {
    const tools = document.querySelector(`.task-tools[data-col="${col}"]`);
    if (tools) {
      tools.querySelectorAll(".chip").forEach((c) => {
        c.addEventListener("click", () => switchFilter(col, c.dataset.filter));
      });
    }
    const panel = document.getElementById(col === "detect" ? "panelTaskDetect" : "panelTaskTrim");
    const clear = panel && panel.querySelector("[data-clear]");
    if (clear) clear.addEventListener("click", () => clearFinishedTasks(col));
    const stopAll = panel && panel.querySelector("[data-interrupt-all]");
    if (stopAll) stopAll.addEventListener("click", () => interruptColumnAll(col));
  });

  // 批处理：选择 + 批量执行
  document.getElementById("btnSelectAll").addEventListener("click", selectAll);
  document.getElementById("btnClearSel").addEventListener("click", clearSelection);
  document.getElementById("btnBatchDetect").addEventListener("click", runBatchDetect);
  document.getElementById("btnBatchTrim").addEventListener("click", runBatchTrim);

  // 目录弹窗
  document.getElementById("dirChoose").addEventListener("click", chooseCurrentDir);
  document.querySelectorAll("#dirModal [data-close]").forEach((b) =>
    b.addEventListener("click", closeDirModal)
  );
  document.getElementById("dirModal").addEventListener("click", (e) => {
    if (e.target.id === "dirModal") closeDirModal();
  });

  // 设置：恢复默认 + 参数变更自动保存
  document.getElementById("resetSettings").addEventListener("click", resetSettings);
  ["detectParams", "trimParams"].forEach((id) => {
    const el = document.getElementById(id);
    el.addEventListener("input", scheduleSave);
    el.addEventListener("change", scheduleSave);
  });

  updateActions();
}

// ---------------- 初始化 ----------------
async function initApp() {
  Player.init();
  // 读取已保存设置：参数面板用保存值初始化；
  // 工作区 / 输出目录由后端在启动时按配置文件恢复（refreshWorkspace/refreshOutput 读取）。
  let saved = null;
  try {
    const res = await fetch("/api/settings");
    const data = await res.json();
    if (data.ok) saved = data.settings;
  } catch (e) { /* 设置读取失败不影响使用 */ }
  buildParamsForm(
    document.getElementById("detectParams"),
    DETECTOR_PARAMS,
    (saved && saved.detector) || null,
  );
  buildParamsForm(
    document.getElementById("trimParams"),
    TRIM_PARAMS,
    (saved && saved.trimmer) || null,
  );
  // 裁剪起点随视频而异，不随配置恢复：启动时一律默认留空
  setParam("start_mode", "frame");
  setParam("start_value", "");
  // 终点同样因视频而异不持久化：启动时默认「无（到片尾）」= 原有行为
  setParam("end_mode", "none");
  setParam("end_value", "");
  initTrimEndControls();
  // 恢复持久化检测缓存：刷新 / 重启后选视频即可自动匹配上次检测结果
  await hydrateDetectCache();
  initCacheSettingsUI(saved);
  initBatchWorkersUI(saved);
  wireUI();
  window.__onTrimParamFilled = (key) => {
    const colEl = document.getElementById("panelTaskTrim");
    if (colEl) colEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
    const row = rowOf(key || "start_value");
    if (row) {
      row.style.animation = "flash 1s ease 2";
      setTimeout(() => { row.style.animation = ""; }, 2200);
    }
  };
  await refreshWorkspace();
  await refreshOutput();
  // 初始化总进度条：无任务时整体隐藏，避免空轨道
  updateColumnTotal("detect");
  updateColumnTotal("trim");
  updateActions();
  setStatus("就绪");
}

document.addEventListener("DOMContentLoaded", initApp);
