// app.js —— 主应用逻辑：工作区 / 输出区 / 文件树 / Detect·Trim 任务编排 / SSE 进度 / 反馈
"use strict";

const state = {
  workspace: "",
  outputDir: "",
  currentVideo: null,      // 当前选中视频绝对路径
  video: null,             // 当前视频元信息（duration/fps/nb_frames），供参数范围校验
  detectResult: null,
  jobs: { detect: null, trim: null, batch: null },  // 正在运行的任务 job_id
  sel: new Set(),          // 批处理选择的视频路径集合
  selAnchor: null,         // shift 区选的锚点路径
  batchDetectResults: {},  // 批量 Detect 结果缓存 { path: result }
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

function formatBytes(n) {
  if (!isFinite(n)) return "";
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}

// ---------------- Toast 反馈（已停用：按需求不再显示右下角提示） ----------------
function toast(_type, _message) { /* no-op */ }
window.toast = toast;

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
    await loadWorkspace(data.path, false);
  }
}

async function loadWorkspace(path, notify = true) {
  if (!path) return;
  const prevWs = state.workspace;
  const res = await fetch("/api/workspace", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await res.json();
  if (!data.ok) {
    toast("error", "载入工作区失败：" + data.message);
    return;
  }
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
  if (notify) toast("success", "已载入工作区：" + data.path);
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
  if (!node.is_video) return null;
  const li = document.createElement("li");
  li.className = "tree-file" + (node.is_video ? " video" : "");
  const row = document.createElement("div");
  row.className = "tree-row file";
  row.dataset.path = node.path;
  const ext = (node.ext || "").replace(".", "").toUpperCase();
  row.innerHTML =
    '<span class="tree-dot ' + (node.is_video ? "vid" : "file") + '"></span>' +
    '<span class="tree-name">' + esc(node.name) + "</span>" +
    '<span class="tree-meta">' + (node.is_video ? ext : formatBytes(node.size)) + "</span>";
  li.appendChild(row);
  row.addEventListener("click", (e) => {
    if (!node.is_video) { toast("info", "非视频文件：仅支持视频格式（mp4/avi/mov 等）"); return; }
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
  toast("success", "已全选 " + rows.length + " 个视频");
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
  let cached = false;
  if (!r) {
    r = await fetchCachedDetect(vpath);
    if (r === null || state.currentVideo !== vpath) return;  // 无缓存或已切换视频
    cached = true;
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
    toast("success",
      "已匹配" + (cached ? "缓存检测结果" : "批量检测结果") + "：帧 #" +
      (r.frame + 1) + "，已填入裁剪起点");
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
  if (el) el.textContent = "当前缓存：" + count + " 条";
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
    if (d.ok) {
      renderCacheStats(d.count);
      toast("ok", "缓存设置已保存");
    } else {
      toast("error", "保存失败：" + (d.message || "未知错误"));
    }
  } catch (e) {
    toast("error", "保存失败：网络异常");
  }
}

// 绑定缓存设置控件；saved 为 /api/settings 返回值（含 detect_cache 组）
function initCacheSettingsUI(saved) {
  const selEnabled = document.getElementById("param-cache_enabled");
  const inpLimit = document.getElementById("param-cache_limit");
  if (!selEnabled || !inpLimit) return;

  // 用保存的设置初始化控件（无保存值时用默认：启用 / 不限）
  const dc = (saved && saved.detect_cache) || {};
  selEnabled.value = dc.enabled === false ? "0" : "1";
  inpLimit.value = String(dc.limit != null ? dc.limit : 0);
  void refreshCacheConfig();

  let limitTimer = null;
  selEnabled.addEventListener("change", () => {
    saveCacheConfig({ enabled: selEnabled.value === "1" });
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
        toast("ok", d.removed > 0
          ? `已清理 ${d.removed} 条失效缓存`
          : "没有需要清理的失效条目");
        // 同步移除本地批处理表中对应视频的结果
        for (const p of d.paths || []) delete state.batchDetectResults[p];
        renderCacheStats(d.count);
      }
    } catch (e) { toast("error", "清理失败：网络异常"); }
  });

  document.getElementById("btnClearCache")?.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/detect-cache/clear", { method: "POST" });
      const d = await res.json();
      if (d.ok) {
        state.batchDetectResults = {};
        renderCacheStats(0);
        toast("ok", `已清空缓存（原 ${d.removed} 条）`);
      }
    } catch (e) { toast("error", "清空失败：网络异常"); }
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
    toast("error", "输出目录设置失败：" + data.message);
    renderOutputInfo({ path, writable: false, message: data.message });
    return;
  }
  state.outputDir = data.path;
  document.getElementById("outputPath").value = data.path;
  renderOutputInfo(data);
  toast("success", "输出目录已设置：" + data.path);
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
    el.innerHTML = '<span class="ok">✓ 可写</span> · 检测/裁剪结果将保存到该目录';
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
  if (!data.ok) { toast("error", data.message); return; }
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
  if (state.jobs.detect) { toast("warn", "检测任务正在运行中，请稍候"); return; }
  if (!state.currentVideo) { toast("warn", "请先在文件列表中选择视频"); return; }
  const v = validateDetect(state);
  if (v.errors.length) {
    v.errors.forEach((m) => toast("error", m));
    return;
  }
  setBusy("detect", true);
  setStatus("检测中…");
  try {
    const res = await fetch("/api/detect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.currentVideo, params: v.params }),
    });
    const data = await res.json();
    if (!data.ok) { toast("error", data.message || "启动检测失败"); setBusy("detect", false); return; }
    state.jobs.detect = data.job_id;
    openSSE(data.job_id, "detect");
  } catch (e) {
    toast("error", "网络错误：" + e.message);
    setBusy("detect", false);
  }
}

// ---------------- 裁剪流程 ----------------
async function runTrim() {
  if (state.jobs.trim) { toast("warn", "裁剪任务正在运行中，请稍候"); return; }
  if (!state.currentVideo) { toast("warn", "请先在文件列表中选择视频"); return; }
  if (!state.outputDir) { toast("warn", "请先设置输出目录"); return; }
  const v = validateTrim(state);
  if (v.errors.length) {
    v.errors.forEach((m) => toast("error", m));
    return;
  }
  setBusy("trim", true);
  setStatus("裁剪中…");
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
      toast("error", data.message || "启动裁剪失败");
      // 终点参数校验失败（后端返回 400）时，在终点行内同步提示
      if (data.message && data.message.indexOf("终点") !== -1) {
        const endRow = rowOf("end_value");
        if (endRow) showRowError(endRow, data.message);
      }
      setBusy("trim", false);
      return;
    }
    state.jobs.trim = data.job_id;
    openSSE(data.job_id, "trim");
  } catch (e) {
    toast("error", "网络错误：" + e.message);
    setBusy("trim", false);
  }
}

// ---------------- 批处理流程 ----------------
// 批处理统一使用一个「批处理」卡片，不再区分批量检测 / 批量裁剪
function openBatchPanel(title, count) {
  const t = document.getElementById("batchTitle");
  if (t) t.textContent = title + " · " + count + " 个文件";
  const box = document.getElementById("batchResult");
  if (box) box.innerHTML = "";
  const wrap = document.getElementById("batchProgress");
  if (wrap) wrap.hidden = true;
  const fill = document.getElementById("batchProgressFill");
  if (fill) fill.style.width = "0%";
  const label = document.getElementById("batchProgressLabel");
  if (label) label.textContent = "";
}

function setBatchBusy(busy) {
  const wrap = document.getElementById("batchProgress");
  const cancel = document.getElementById("batchCancel");
  if (wrap) wrap.hidden = !busy;
  if (cancel) cancel.disabled = !busy;
  if (busy) {
    const fill = document.getElementById("batchProgressFill");
    const label = document.getElementById("batchProgressLabel");
    if (fill) fill.style.width = "0%";
    if (label) label.textContent = "启动中…";
  }
  updateActions();
}

async function runBatchDetect() {
  const files = [...state.sel];
  if (!files.length) { toast("warn", "请先在工作区选择视频文件（Ctrl 单选 / Shift 区选 / 全选）"); return; }
  if (state.jobs.batch) { toast("warn", "批处理任务正在运行中，请稍候"); return; }
  const v = validateDetect(state);
  if (v.errors.length) {
    v.errors.forEach((m) => toast("error", m));
    return;
  }
  openBatchPanel("批量检测", files.length);
  setBatchBusy(true);
  setStatus("批量检测中…");
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
    if (!data.ok) { toast("error", data.message || "启动批量检测失败"); setBatchBusy(false); return; }
    state.jobs.batch = data.job_id;
    openSSE(data.job_id, "batch_detect");
  } catch (e) {
    toast("error", "网络错误：" + e.message);
    setBatchBusy(false);
  }
}

async function runBatchTrim() {
  const files = [...state.sel];
  if (!files.length) { toast("warn", "请先在工作区选择视频文件（Ctrl 单选 / Shift 区选 / 全选）"); return; }
  if (state.jobs.batch) { toast("warn", "批处理任务正在运行中，请稍候"); return; }
  if (!state.outputDir) { toast("warn", "请先设置输出目录"); return; }
  // 批量裁剪不使用终点（以检测结果为起点、保留到片尾），跳过终点校验
  const v = validateTrim(state, false);
  if (v.errors.length) {
    v.errors.forEach((m) => toast("error", m));
    return;
  }
  // 批量裁剪始终使用批量检测的输出结果作为每个文件的裁剪起点
  const items = [];
  let usable = 0;
  for (const f of files) {
    const r = state.batchDetectResults[f];
    if (r && r.detected && r.frame !== null && r.frame !== undefined) {
      items.push({ path: f, frame: r.frame + 1 });
      usable++;
    } else {
      items.push({ path: f, skip: true, reason: "无检测结果（未命中或未执行批量检测）" });
    }
  }
  if (!usable) { toast("warn", "没有可用于批量裁剪的检测结果，请先执行批量检测"); return; }
  openBatchPanel("批量裁剪", items.length);
  setBatchBusy(true);
  setStatus("批量裁剪中…");
  try {
    const res = await fetch("/api/batch/trim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        files: items,
        output_dir: state.outputDir,
        params: v.params,
      }),
    });
    const data = await res.json();
    if (!data.ok) { toast("error", data.message || "启动批量裁剪失败"); setBatchBusy(false); return; }
    state.jobs.batch = data.job_id;
    openSSE(data.job_id, "batch_trim");
  } catch (e) {
    toast("error", "网络错误：" + e.message);
    setBatchBusy(false);
  }
}

// ---------------- 任务状态 / SSE ----------------
function setBusy(kind, busy) {
  const isDetect = kind === "detect";
  const btn = document.getElementById(isDetect ? "btnDetect" : "btnTrim");
  const wrap = document.getElementById(isDetect ? "detectProgress" : "trimProgress");
  if (btn) btn.disabled = busy;
  if (wrap) wrap.hidden = !busy;
  if (busy) {
    const fill = document.getElementById(isDetect ? "detectProgressFill" : "trimProgressFill");
    const label = document.getElementById(isDetect ? "detectProgressLabel" : "trimProgressLabel");
    if (fill) fill.style.width = "0%";
    if (label) label.textContent = "启动中…";
  }
  updateActions();
}

function openSSE(jobId, kind) {
  const es = new EventSource("/api/jobs/" + jobId + "/events");
  const batch = kind === "batch_detect" || kind === "batch_trim";
  // 批量任务的 job_id 统一存于 state.jobs.batch（而非 batch_detect / batch_trim）
  const jobKey = batch ? "batch" : kind;
  let settled = false;
  const stop = () => {
    if (settled) return;
    settled = true;
    es.close();
    state.jobs[jobKey] = null;
    updateActions();
  };
  // 网络层断开兜底：SSE 连接中断可能使终态事件（done/error/cancelled）丢失，
  // 主动查询一次任务状态，确保任务结束后按钮不会被永久禁用。
  const pollSettled = () => {
    fetch("/api/jobs/" + jobId)
      .then((r) => r.json())
      .then((d) => {
        if (settled || !d.ok) return;
        if (d.status === "done") {
          const res = d.result || {};
          if (kind === "detect") onDetectDone(res);
          else if (kind === "trim") onTrimDone(res);
          else if (kind === "batch_detect") onBatchDetectDone(res);
          else if (kind === "batch_trim") onBatchTrimDone(res);
        } else if (d.status === "error") {
          if (batch) setBatchBusy(false); else setBusy(kind, false);
          toast("error", d.error || "任务执行失败");
          setStatus("任务失败");
        } else if (d.status === "cancelled") {
          if (batch) setBatchBusy(false); else setBusy(kind, false);
          toast("info", "任务已取消");
          setStatus("任务已取消");
        } else {
          return; // 仍在运行：交由 SSE 重连继续接收进度与终态
        }
        stop();
      })
      .catch(() => { /* 查询失败不影响 SSE 主流程 */ });
  };
  es.addEventListener("progress", (ev) => {
    const p = JSON.parse(ev.data);
    if (kind === "detect") onDetectProgress(p);
    else if (kind === "trim") onTrimProgress(p);
    else if (kind === "batch_detect") onBatchDetectProgress(p);
    else if (kind === "batch_trim") onBatchTrimProgress(p);
  });
  es.addEventListener("done", (ev) => {
    const d = JSON.parse(ev.data);
    if (kind === "detect") onDetectDone(d);
    else if (kind === "trim") onTrimDone(d);
    else if (kind === "batch_detect") onBatchDetectDone(d);
    else if (kind === "batch_trim") onBatchTrimDone(d);
    stop();
  });
  es.addEventListener("cancelled", () => {
    if (batch) {
      setBatchBusy(false);
      toast("info", "批量任务已取消");
    } else {
      setBusy(kind, false);
      toast("info", kind === "detect" ? "检测已取消" : "裁剪已取消");
    }
    setStatus("任务已取消");
    stop();
  });
  es.addEventListener("error", (ev) => {
    if (ev.data) {
      const d = JSON.parse(ev.data);
      if (batch) setBatchBusy(kind, false); else setBusy(kind, false);
      toast("error", (d.message || "任务执行失败"));
      setStatus("任务失败");
      stop();
      return;
    }
    // 网络层错误（无 data）：EventSource 会自动重连；
    // 若终态事件恰好在连接中断时丢失，用任务状态接口兜底。
    pollSettled();
  });
}

function onDetectProgress(p) {
  const fill = document.getElementById("detectProgressFill");
  const label = document.getElementById("detectProgressLabel");
  const pct = Math.round((p.progress || 0) * 100);
  if (fill) fill.style.width = pct + "%";
  const stageName = {
    coarse: "粗扫", fine: "细化", precise: "逐帧", fallback: "兜底细扫",
    locate: "定位", done: "完成",
  }[p.stage] || p.stage;
  if (label) label.textContent = "检测中 " + stageName + " " + pct + "% · " + (p.message || "");
}

function onTrimProgress(p) {
  const fill = document.getElementById("trimProgressFill");
  const label = document.getElementById("trimProgressLabel");
  const pct = Math.round(p.percent || 0);
  if (fill) fill.style.width = pct + "%";
  if (label) label.textContent = "裁剪中 " + pct + "%（" + (p.seconds || 0).toFixed(1) + "s）";
}

// ---------------- 批处理进度与结果 ----------------
function onBatchDetectProgress(p) {
  const fill = document.getElementById("batchProgressFill");
  const label = document.getElementById("batchProgressLabel");
  const pct = Math.round(p.percent || 0);
  if (fill) fill.style.width = pct + "%";
  const stageName = {
    coarse: "粗扫", fine: "细化", precise: "逐帧", fallback: "兜底细扫",
    locate: "定位", pending: "准备", done: "完成",
  }[p.stage] || p.stage || "";
  if (label) label.textContent =
    "批量检测 " + (p.index || 0) + "/" + (p.total || 0) + " · " +
    basename(p.file || "") + " · " + stageName + " " + pct + "%";
}

function onBatchTrimProgress(p) {
  const fill = document.getElementById("batchProgressFill");
  const label = document.getElementById("batchProgressLabel");
  const pct = Math.round(p.percent || 0);
  if (fill) fill.style.width = pct + "%";
  if (label) label.textContent =
    "批量裁剪 " + (p.index || 0) + "/" + (p.total || 0) + " · " +
    basename(p.file || "") + " " + pct + "%";
}

function onBatchDetectDone(data) {
  setBatchBusy(false);
  const results = data.results || [];
  const map = {};
  results.forEach((r) => { if (r && r.file) map[r.file] = r; });
  state.batchDetectResults = map;
  renderBatchDetectResults(results, data.summary);
  const s = data.summary || {};
  toast("success", "批量检测完成：命中 " + (s.detected || 0) + " / " + (s.total || 0) +
    (s.skipped ? "，跳过已缓存 " + s.skipped : "") +
    (s.failed ? "，失败 " + s.failed : ""));
  setStatus("批量检测完成");
  updateActions();
}

function onBatchTrimDone(data) {
  setBatchBusy(false);
  renderBatchTrimResults(data.results || [], data.summary);
  const s = data.summary || {};
  toast("success", "批量裁剪完成：成功 " + (s.ok || 0) + " / " + (s.total || 0) +
    (s.failed ? "，失败 " + s.failed : ""));
  setStatus("批量裁剪完成");
  updateActions();
}

function renderBatchDetectResults(results, summary) {
  const box = document.getElementById("batchResult");
  if (!box) return;
  const s = summary || {};
  const rows = results.map((r) => {
    const name = esc(basename(r.file || ""));
    if (r.error) return "<li><b>" + name + "</b> <span class='bad'>✗ " + esc(r.error) + "</span></li>";
    if (r.detected) {
      const t = (r.timestamp !== null && r.timestamp !== undefined) ? r.timestamp.toFixed(3) + "s" : "?";
      return "<li><b>" + name + "</b> <span class='ok'>✓ 帧#" + (r.frame + 1) + " @ " + t + "</span></li>";
    }
    return "<li><b>" + name + "</b> <span class='bad'>未命中</span></li>";
  }).join("");
  box.innerHTML =
    '<div class="result-msg ' + (s.failed ? "warn" : "ok") + '">批量检测完成：命中 ' +
    (s.detected || 0) + " / " + (s.total || 0) +
    (s.failed ? "，失败 " + s.failed : "") + "</div>" +
    (rows ? "<ul class='points'>" + rows + "</ul>" : "");
}

function renderBatchTrimResults(results, summary) {
  const box = document.getElementById("batchResult");
  if (!box) return;
  const s = summary || {};
  const rows = results.map((r) => {
    const name = esc(basename(r.file || ""));
    if (r.error) return "<li><b>" + name + "</b> <span class='bad'>✗ " + esc(r.error) + "</span></li>";
    const files = (r.output_files || []).map((f) =>
      "<div class='out-sub'>↳ " + esc(basename(f)) + "</div>"
    ).join("");
    return "<li><b>" + name + "</b> <span class='ok'>✓ 裁剪完成</span>" + files + "</li>";
  }).join("");
  box.innerHTML =
    '<div class="result-msg ' + (s.failed ? "warn" : "ok") + '">批量裁剪完成：成功 ' +
    (s.ok || 0) + " / " + (s.total || 0) +
    (s.failed ? "，失败 " + s.failed : "") + "</div>" +
    (rows ? "<ul class='points'>" + rows + "</ul>" : "");
}

// ---------------- 结果展示 ----------------
function onDetectDone(data) {
  setBusy("detect", false);
  state.detectResult = data;
  if (state.currentVideo) state.batchDetectResults[state.currentVideo] = data;  // 供批量裁剪复用
  renderDetectResult(data);
  if (data.detected) {
    // 自动关联 detector 输出到 trimmer 起点参数
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
    toast("success",
      "检测成功：目标帧 #" + (data.frame + 1) + " @ " +
      (data.timestamp ? data.timestamp.toFixed(3) : "?") + "s，已自动填入裁剪起点");
    setStatus("检测完成，已自动关联裁剪起点");
  } else {
    Player.clearDetected();
    toast("warn", "未检测到目标颜色：" + (data.message || ""));
    setStatus("检测完成（未命中）");
  }
  updateActions();
}

function renderDetectResult(data, sourceTag) {
  const box = document.getElementById("detectResult");
  const tag = sourceTag ? "（" + sourceTag + "）" : "";
  if (!data.detected) {
    box.innerHTML = '<div class="result-msg fail">未检测到目标颜色' + tag + "</div>";
    return;
  }
  const pts = (data.points || []).map((p) =>
    "<li>(" + p.x + "," + p.y + ") conf=" + p.confidence.toFixed(3) +
    (p.matched ? ' <span class="ok">命中</span>' : ' <span class="bad">未命中</span>') + "</li>"
  ).join("");
  box.innerHTML =
    '<div class="result-msg ok">检测到目标颜色' + tag + "</div>" +
    "<ul class='kv'><li>目标帧（1 基）：<b>#" + (data.frame + 1) + "</b></li>" +
    "<li>时间戳：<b>" + (data.timestamp ? data.timestamp.toFixed(6) : "?") + " s</b></li>" +
    "<li>整体置信度：<b>" + (data.confidence ? data.confidence.toFixed(4) : "?") + "</b></li>" +
    "<li>检测点数：" + (data.points || []).length + "</li></ul>" +
    "<ul class='points'>" + pts + "</ul>";
}

function onTrimDone(data) {
  setBusy("trim", false);
  renderTrimResult(data);
  toast("success", "裁剪完成，共 " + data.output_files.length + " 个输出文件");
  setStatus("裁剪完成");
  updateActions();
}

function renderTrimResult(data) {
  const box = document.getElementById("trimResult");
  const files = (data.output_files || []).map((f) => "<li>" + esc(f) + "</li>").join("");
  let rangeLines = "<li>起点：" + (data.frame ? "#" + data.frame + " 帧" : (data.timestamp || 0).toFixed(3) + "s") + "</li>";
  if (data.end_timestamp != null) {
    rangeLines += "<li>终点：" +
      (data.end_frame ? "#" + data.end_frame + " 帧（" + data.end_timestamp.toFixed(3) + "s）"
                      : data.end_timestamp.toFixed(3) + "s") +
      "</li>";
  }
  box.innerHTML =
    '<div class="result-msg ok">裁剪完成</div>' +
    "<ul class='kv'><li>处理方式：<b>" + esc(data.message || "") + "</b></li>" +
    rangeLines +
    "<li>保留时长：<b>" + data.cut_duration.toFixed(3) + " s</b></li></ul>" +
    "<div class='out-title'>输出文件：</div><ul class='points'>" + files + "</ul>";
}

// ---------------- 取消 ----------------
function cancelJob(kind) {
  const jid = state.jobs[kind];
  if (!jid) return;
  fetch("/api/jobs/" + jid + "/cancel", { method: "POST" });
  const label = { detect: "检测", trim: "裁剪", batch: "批处理" }[kind] || "任务";
  toast("info", "正在取消" + label + "…");
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
  const payload = {
    workspace: state.workspace || "",
    output_dir: state.outputDir || "",
    detector: collectParams(DETECTOR_PARAMS),
    trimmer,
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
    if (!data.ok) { toast("error", data.message || "恢复默认设置失败"); return; }
    const s = data.settings || {};
    buildParamsForm(document.getElementById("detectParams"), DETECTOR_PARAMS, s.detector);
    buildParamsForm(document.getElementById("trimParams"), TRIM_PARAMS, s.trimmer);
    initTrimEndControls();  // 表单重建后重新绑定终点方式联动（终点默认「无」）
    await refreshWorkspace();
    await refreshOutput();
    updateActions();
    toast("success", "已恢复默认设置");
    setStatus("已恢复默认设置");
  } catch (e) {
    toast("error", "网络错误：" + e.message);
  }
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
  document.getElementById("detectCancel").addEventListener("click", () => cancelJob("detect"));
  document.getElementById("trimCancel").addEventListener("click", () => cancelJob("trim"));

  // 批处理：选择 + 批量执行
  document.getElementById("btnSelectAll").addEventListener("click", selectAll);
  document.getElementById("btnClearSel").addEventListener("click", clearSelection);
  document.getElementById("btnBatchDetect").addEventListener("click", runBatchDetect);
  document.getElementById("btnBatchTrim").addEventListener("click", runBatchTrim);
  document.getElementById("batchCancel").addEventListener("click", () => cancelJob("batch"));

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
  wireUI();
  window.__onTrimParamFilled = (key) => {
    document.getElementById("panelTrim").scrollIntoView({ behavior: "smooth", block: "nearest" });
    const row = rowOf(key || "start_value");
    if (row) {
      row.style.animation = "flash 1s ease 2";
      setTimeout(() => { row.style.animation = ""; }, 2200);
    }
  };
  await refreshWorkspace();
  await refreshOutput();
  updateActions();
  setStatus("就绪");
}

document.addEventListener("DOMContentLoaded", initApp);
