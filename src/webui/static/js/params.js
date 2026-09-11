// params.js —— 数据驱动的参数面板：名称 / 类型 / 默认值 / 说明 / 必填 / 校验
"use strict";

// 文案三层模型：label ≤6 字名词；desc ≤25 字只写语义（行内常显）；tip ≤80 字承载
// 取值范围 / 默认值 / 联动关系 / 操作提示（悬停气泡，渲染层见 buildParamsForm）
// Detector 参数定义（对应 detector/config.py DetectorConfig 可配置项）
const DETECTOR_PARAMS = [
  { key: "target_color", label: "目标颜色", type: "text", default: "#F7F10F",
    group: "target", placeholder: "#F7F10F", desc: "待匹配的目标颜色",
    tip: "支持 #RRGGBB 或 R,G,B 两种写法；与「颜色容差」共同决定匹配范围，检测不到时可适当调大容差" },
  { key: "points", label: "检测点坐标", type: "text",
    default: "20,20 1900,20 20,1060 1900,1060",
    group: "target",
    placeholder: "20,20 300,300 1900,20",
    desc: "画面上参与匹配的采样点",
    tip: "x,y 坐标，空格分隔可写多个；默认四点针对 1920x1080，坐标个数即检测点个数。例：20,20 300,300 1900,20" },
  { key: "confidence_threshold", label: "置信度阈值", type: "number",
    group: "target",
    default: 0.97, min: 0, max: 1, step: 0.01, desc: "匹配的严格程度",
    tip: "取值 0~1，越高越严格；漏检时调低、误检时调高。默认 0.97" },
  { key: "color_tolerance", label: "颜色容差", type: "number", default: 10.0,
    group: "target",
    min: 0, step: 0.5, desc: "允许的颜色偏差幅度",
    tip: "按 RGB 欧氏距离计算，用于兼容有损编码的颜色偏差；检测不到时可适当调大。默认 10" },
  { key: "scale_points", label: "检测点自动缩放", type: "bool", default: true,
    group: "target",
    desc: "按视频分辨率缩放检测点",
    tip: "勾选后按视频实际分辨率对「检测点坐标」等比缩放，关闭则使用原始坐标。默认开启" },
  { key: "search_window", label: "检测窗口(秒)", type: "number", default: 40,
    group: "algo",
    min: 1, step: 1, desc: "只在视频前 N 秒内检测",
    tip: "限制检测的搜索范围（秒），窗口之外不参与匹配；片头不在视频开头的场景可调大。默认 40" },
  { key: "initial_start", label: "反向跳帧起始(秒)", type: "number", default: 35,
    group: "algo",
    min: 0, step: 1, desc: "反向扫描的起始时间点",
    tip: "从该时间点向片头回扫以定位目标帧；应小于「检测窗口」。通常无需修改。默认 35" },
  { key: "coarse_step", label: "粗扫步长(秒)", type: "number", default: 5,
    group: "algo",
    min: 0.1, step: 0.5, desc: "粗扫阶段的抽帧间隔",
    tip: "第一轮大步长扫描的间隔（秒）；须不小于「细扫步长」。通常无需修改。默认 5" },
  { key: "fine_step", label: "细扫步长(秒)", type: "number", default: 1,
    group: "algo",
    min: 0.1, step: 0.1, desc: "细扫阶段的抽帧间隔",
    tip: "第二轮小步长扫描的间隔（秒），越小定位越精确、耗时越长；须不大于「粗扫步长」。默认 1" },
  { key: "extractor", label: "帧提取器", type: "select", default: "ffmpeg",
    group: "backend",
    options: [{ v: "ffmpeg", l: "FFmpeg（精确）" }, { v: "opencv", l: "OpenCV（回退）" }],
    desc: "抽帧使用的后端",
    tip: "FFmpeg 时间戳精确，但要求 ffmpeg 已加入 PATH；OpenCV 为回退方案。默认 FFmpeg" },
  { key: "hwaccel", label: "GPU 硬解", type: "select", default: "auto",
    group: "backend",
    options: [{ v: "auto", l: "自动（推荐）" }, { v: "none", l: "关闭（CPU 软解）" },
              { v: "d3d11va", l: "D3D11VA（AMD/通用）" }, { v: "dxva2", l: "DXVA2（AMD）" },
              { v: "cuda", l: "CUDA（N 卡）" }, { v: "qsv", l: "QSV（Intel 核显）" }],
    desc: "检测阶段的解码加速",
    tip: "自动按显卡选择解码后端，不可用时回退 CPU 软解，不影响检测结果；与裁剪侧「GPU 加速」（编码）相互独立" },
];

// Trimmer 参数定义（对应 trimmer/core/trimmer.py TrimmerConfig）
const TRIM_PARAMS = [
  { key: "start_mode", label: "起点方式", type: "select", default: "frame",
    group: "range",
    options: [{ v: "frame", l: "帧序号 (-f)" }, { v: "timestamp", l: "时间戳 (-t)" }],
    desc: "裁剪起点的表示方式",
    tip: "帧序号（-f）或时间戳（-t）二选一；随视频变化，不随配置保存" },
  { key: "start_value", label: "裁剪起点", type: "number", default: "",
    group: "range",
    required: true, step: 0.001, placeholder: "输入帧序号或时间戳…",
    desc: "保留片段的起点",
    tip: "随视频变化，不随配置保存。提示：先执行「检测」或点播放器「设为起点」可自动填入" },
  { key: "end_mode", label: "终点方式", type: "select", default: "none",
    group: "range",
    options: [{ v: "none", l: "无（到片尾）" }, { v: "frame", l: "帧序号 (-F)" },
              { v: "timestamp", l: "时间戳 (-T)" }],
    desc: "裁剪终点的表示方式",
    tip: "「无」表示保留到片尾；切换为「无」会同时清除已填写的终点值" },
  { key: "end_value", label: "裁剪终点", type: "number", default: "",
    group: "range",
    step: 0.001, placeholder: "输入帧序号或时间戳…",
    desc: "保留片段的终点",
    tip: "须大于起点，留空表示裁剪到片尾。提示：点播放器「设为终点」可自动填入当前帧" },
  { key: "suffix", label: "输出文件名后缀", type: "text", default: "_trim",
    group: "output",
    desc: "追加在输出文件名后的后缀",
    tip: "示例：input.mp4 裁剪输出为 input_trim.mp4。默认「_trim」" },
  { key: "output_mode", label: "输出模式", type: "select", default: "full",
    group: "output",
    options: [{ v: "full", l: "完整视频" }, { v: "video", l: "无声视频" },
              { v: "audio", l: "纯音频" }, { v: "split", l: "分离音视频" }],
    desc: "输出内容的组合方式",
    tip: "共四种：完整视频、无声视频、纯音频、分离音视频。默认完整视频" },
  { key: "force", label: "覆盖已存在文件", type: "bool", default: false,
    group: "output",
    desc: "输出文件已存在时直接覆盖",
    tip: "勾选后不再因同名输出文件报错，直接覆盖写入。默认关闭" },
  { key: "reencode", label: "重编码", type: "bool", default: false,
    group: "output",
    desc: "重新编码以精确到帧",
    tip: "默认流复制：快速无损，但起止点会吸附到关键帧；重编码可精确到帧并自动匹配原视频参数，耗时更长" },
  { key: "hw_accel", label: "GPU 加速", type: "select", default: "auto",
    group: "codec",
    options: [{ v: "auto", l: "自动" }, { v: "none", l: "关闭" }, { v: "force", l: "强制" }],
    desc: "裁剪阶段的编码加速",
    tip: "作用于重编码阶段：自动、关闭、强制；与检测侧「GPU 硬解」（解码）相互独立。默认自动" },
  { key: "video_codec", label: "视频编码器", type: "text", default: "",
    group: "codec",
    placeholder: "libx264（可选）", desc: "重编码使用的视频编码器",
    tip: "如 libx264；仅勾选「重编码」时生效，留空自动匹配原视频参数" },
  { key: "audio_codec", label: "音频编码器", type: "text", default: "",
    group: "codec",
    placeholder: "aac（可选）", desc: "重编码使用的音频编码器",
    tip: "如 aac；仅勾选「重编码」时生效，留空自动匹配原视频参数" },
];

// 子组定义：顺序即渲染顺序；folded 为默认折叠态，实际以 localStorage 记忆优先
// （键 tao.ui.fold.<容器id>.<组id>，纯 UI 偏好，不写入 settings.json）
const DETECTOR_GROUPS = [
  { id: "target",  title: "检测目标",   folded: false },
  { id: "algo",    title: "搜索算法",   folded: true },
  { id: "backend", title: "性能与后端", folded: true },
];
const TRIM_GROUPS = [
  { id: "range",  title: "裁剪区间", folded: false,
    tag: "随视频 · 不保存", tagTip: "该组参数随视频变化，不随配置保存；重启后起点默认留空" },
  { id: "output", title: "输出选项", folded: false },
  { id: "codec",  title: "编码与加速", folded: true },
];
// 挂载到参数数组：buildParamsForm(spec) 按 spec.groups 分桶渲染；
// 未定义 groups 的参数数组退化为整块平铺（向后兼容）
DETECTOR_PARAMS.groups = DETECTOR_GROUPS;
TRIM_PARAMS.groups = TRIM_GROUPS;

// 构建单个参数行（label / 控件 / 短说明）；id 与 data-key 契约见 README「配置持久化」
function buildParamRow(p, values) {
  const row = document.createElement("div");
  row.className = "param-row" + (p.required ? " required" : "");
  row.dataset.key = p.key;

  const label = document.createElement("label");
  label.className = "param-label";
  label.htmlFor = "param-" + p.key;
  label.innerHTML = p.label + (p.required ? ' <span class="req">*</span>' : "");

  const ctl = document.createElement("div");
  ctl.className = "param-ctl";
  let input;
  if (p.type === "select") {
    input = document.createElement("select");
    (p.options || []).forEach((o) => {
      const op = document.createElement("option");
      op.value = o.v;
      op.textContent = o.l;
      input.appendChild(op);
    });
  } else if (p.type === "bool") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.className = "bool-input";
  } else {
    input = document.createElement("input");
    input.type = p.type;
    if (p.min !== undefined) input.min = p.min;
    if (p.max !== undefined) input.max = p.max;
    if (p.step !== undefined) input.step = p.step;
    if (p.placeholder) input.placeholder = p.placeholder;
  }
  input.id = "param-" + p.key;
  input.dataset.key = p.key;
  if (values && values[p.key] !== undefined) {
    if (p.type === "bool") input.checked = !!values[p.key];
    else input.value = values[p.key];
  } else if (p.type === "bool") {
    input.checked = !!p.default;
  } else {
    input.value = p.default !== undefined ? p.default : "";
  }
  // 即时校验提示
  input.addEventListener("input", () => clearRowError(row));
  ctl.appendChild(input);
  row.appendChild(label);
  row.appendChild(ctl);
  const desc = document.createElement("div");
  desc.className = "param-desc";
  desc.textContent = p.desc || "";
  row.appendChild(desc);
  // 悬停详情气泡（纯 CSS，见 style.css [data-tip]）；行内短 desc 始终保留作触屏降级
  if (p.tip) row.dataset.tip = p.tip;
  return row;
}

// 子组折叠状态的本地记忆（纯 UI 偏好，不进 settings.json；读写失败静默降级）
function groupFoldKey(containerId, groupId) {
  return "tao.ui.fold." + containerId + "." + groupId;
}
function readFolded(containerId, g) {
  try {
    const saved = localStorage.getItem(groupFoldKey(containerId, g.id));
    return saved == null ? !!g.folded : saved === "1";
  } catch (e) { return !!g.folded; }
}
function writeFolded(containerId, groupId, folded) {
  try { localStorage.setItem(groupFoldKey(containerId, groupId), folded ? "1" : "0"); }
  catch (e) { /* 隐私模式等场景下忽略 */ }
}

// 根据参数定义构建表单（container: DOM 元素, spec: 参数数组, values: 初始值）
// spec.groups 存在时按子组渲染（高级组默认折叠），否则整块平铺（向后兼容）。
// 折叠只切换组容器 class、不销毁 DOM：折叠态下 collectParams/setParam 仍可
// 通过 param-<key> 定位到行（检测命中回填等程序化赋值依赖这一点）。
function buildParamsForm(container, spec, values) {
  container.innerHTML = "";
  const groups = spec.groups;
  if (!groups) {
    spec.forEach((p) => container.appendChild(buildParamRow(p, values)));
    return;
  }
  const byGroup = new Map(groups.map((g) => [g.id, []]));
  spec.forEach((p) => {
    const gid = byGroup.has(p.group) ? p.group : groups[0].id;
    byGroup.get(gid).push(p);
  });
  groups.forEach((g) => {
    const rows = byGroup.get(g.id);
    if (!rows.length) return;
    const wrap = document.createElement("section");
    const folded = readFolded(container.id, g);
    wrap.className = "param-group" + (folded ? " folded" : "");

    const head = document.createElement("button");
    head.type = "button";
    head.className = "param-group-head";
    head.setAttribute("aria-expanded", String(!folded));
    const title = document.createElement("span");
    title.className = "param-group-title";
    title.textContent = g.title;
    const count = document.createElement("span");
    count.className = "param-group-count";
    count.textContent = String(rows.length);
    const caret = document.createElement("span");
    caret.className = "param-group-caret";
    caret.textContent = "▸";
    head.appendChild(title);
    if (g.tag) {
      const tag = document.createElement("span");
      tag.className = "param-group-tag";
      tag.textContent = g.tag;
      if (g.tagTip) tag.dataset.tip = g.tagTip;
      head.appendChild(tag);
    }
    head.appendChild(count);
    head.appendChild(caret);
    head.addEventListener("click", () => {
      const nowFolded = wrap.classList.toggle("folded");
      head.setAttribute("aria-expanded", String(!nowFolded));
      writeFolded(container.id, g.id, nowFolded);
    });

    const body = document.createElement("div");
    body.className = "param-group-body";
    rows.forEach((p) => body.appendChild(buildParamRow(p, values)));

    wrap.appendChild(head);
    wrap.appendChild(body);
    container.appendChild(wrap);
  });
}

// 收集表单当前值
function collectParams(spec) {
  const out = {};
  spec.forEach((p) => {
    const el = document.getElementById("param-" + p.key);
    if (!el) return;
    out[p.key] = p.type === "bool" ? el.checked : el.value;
  });
  return out;
}

// 设置单个参数值
function setParam(key, value) {
  const el = document.getElementById("param-" + key);
  if (!el) return;
  if (el.type === "checkbox") el.checked = !!value;
  else el.value = value;
  clearRowError(el.closest(".param-row"));
  // 程序化赋值后派发 change 事件（冒泡到参数面板），触发设置自动保存
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

function clearRowError(row) {
  if (!row) return;
  row.classList.remove("error");
  const msg = row.querySelector(".param-error");
  if (msg) msg.remove();
}

// 行内即时错误提示
function showRowError(row, message) {
  clearRowError(row);
  row.classList.add("error");
  const msg = document.createElement("div");
  msg.className = "param-error";
  msg.textContent = "⚠ " + message;
  row.appendChild(msg);
}

function rowOf(key) {
  const el = document.getElementById("param-" + key);
  return el ? el.closest(".param-row") : null;
}

// 校验 Trim 参数：起点必填、数值/范围正确、与视频信息比对；
// 终点（可选）方式/数值匹配、范围正确且须大于起点。
// checkEnd = false 时跳过终点校验（批量裁剪不使用终点）。
function validateTrim(state, checkEnd = true) {
  const p = collectParams(TRIM_PARAMS);
  const errors = [];
  const mode = p.start_mode;
  const raw = String(p.start_value).trim();
  const startRow = rowOf("start_value");

  if (!raw) {
    errors.push("请填写裁剪起点（必填）：可先执行「检测」或点播放器「设为起点」自动填入");
    if (startRow) showRowError(startRow, "必填项：裁剪起点");
  } else {
    const num = Number(raw);
    if (!isFinite(num)) {
      errors.push("裁剪起点必须是数字");
      if (startRow) showRowError(startRow, "必须是数字");
    } else if (mode === "frame") {
      if (!Number.isInteger(num) || num < 1) {
        errors.push("帧序号必须是不小于 1 的整数");
        if (startRow) showRowError(startRow, "帧序号须为 ≥1 的整数");
      } else if (state.video && state.video.nb_frames &&
                 num > state.video.nb_frames) {
        errors.push(`帧序号 ${num} 超出视频总帧数 ${state.video.nb_frames}`);
        if (startRow) showRowError(startRow, `超出总帧数 ${state.video.nb_frames}`);
      }
    } else {
      if (num <= 0) {
        errors.push("时间戳必须大于 0");
        if (startRow) showRowError(startRow, "时间戳须大于 0");
      } else if (state.video && num > state.video.duration) {
        errors.push(`时间戳 ${num}s 超出视频时长 ${state.video.duration.toFixed(3)}s`);
        if (startRow) showRowError(startRow, `超出时长 ${state.video.duration.toFixed(3)}s`);
      }
    }
  }

  if (checkEnd) {
    const endRow = rowOf("end_value");
    const endMode = String(p.end_mode || "none");
    const endRaw = String(p.end_value == null ? "" : p.end_value).trim();
    let endOk = false;   // 终点数值本身合法（可参与与起点的比较）
    let endNum = NaN;
    if (endMode === "none") {
      if (endRaw !== "") {
        errors.push("终点方式为「无（到片尾）」时不应填写终点值，请清空裁剪终点");
        if (endRow) showRowError(endRow, "终点方式为「无」时请清空终点值");
      }
    } else if (endRaw === "") {
      errors.push("已选择终点方式，请填写裁剪终点（或改回「无（到片尾）」）");
      if (endRow) showRowError(endRow, "请填写终点或改回「无」");
    } else {
      endNum = Number(endRaw);
      if (!isFinite(endNum)) {
        errors.push("裁剪终点必须是数字");
        if (endRow) showRowError(endRow, "必须是数字");
      } else if (endMode === "frame") {
        if (!Number.isInteger(endNum) || endNum < 1) {
          errors.push("终点帧序号必须是不小于 1 的整数");
          if (endRow) showRowError(endRow, "帧序号须为 ≥1 的整数");
        } else if (state.video && state.video.nb_frames &&
                   endNum > state.video.nb_frames) {
          errors.push(`终点帧序号 ${endNum} 超出视频总帧数 ${state.video.nb_frames}`);
          if (endRow) showRowError(endRow, `超出总帧数 ${state.video.nb_frames}`);
        } else {
          endOk = true;
        }
      } else {
        if (endNum <= 0) {
          errors.push("终点时间戳必须大于 0");
          if (endRow) showRowError(endRow, "时间戳须大于 0");
        } else if (state.video && endNum > state.video.duration) {
          errors.push(`终点时间戳 ${endNum}s 超出视频时长 ${state.video.duration.toFixed(3)}s`);
          if (endRow) showRowError(endRow, `超出时长 ${state.video.duration.toFixed(3)}s`);
        } else {
          endOk = true;
        }
      }
    }
    // 终点须大于起点（换算为秒比较；起点未填 / 非法时由起点校验与后端处理）
    if (endOk && raw !== "") {
      const sNum = Number(raw);
      if (isFinite(sNum)) {
        const fps = (state.video && state.video.fps) || 30;
        const toSec = (m, v) => (m === "timestamp" ? v : (v - 1) / fps);
        if (toSec(endMode, endNum) <= toSec(String(mode || "frame"), sNum)) {
          errors.push("终点必须大于起点");
          if (endRow) showRowError(endRow, "终点须大于起点");
        }
      }
    }
  }

  if (state.video && Number(p.reencode)) {
    // 流复制模式下起终点受关键帧吸附影响，仅在重编码时提示精确性
    const row = rowOf("start_value");
    if (row && !row.classList.contains("error")) {
      const tip = document.createElement("div");
      tip.className = "param-tip";
      tip.textContent = "流复制模式下起点与终点会吸附到关键帧；勾选「重编码」可精确到帧。";
      if (!row.querySelector(".param-tip")) row.appendChild(tip);
    }
  }
  return { params: p, errors };
}

// 校验颜色检测参数（步长约束等）
function validateDetect(state) {
  const p = collectParams(DETECTOR_PARAMS);
  const errors = [];
  const coarse = Number(p.coarse_step);
  const fine = Number(p.fine_step);
  const threshold = Number(p.confidence_threshold);
  if (isFinite(coarse) && isFinite(fine) && fine > coarse) {
    errors.push("细扫步长不能大于粗扫步长");
    showRowError(rowOf("fine_step"), "须 ≤ 粗扫步长");
  }
  if (isFinite(threshold) && (threshold < 0 || threshold > 1)) {
    errors.push("置信度阈值必须在 [0,1] 范围内");
    showRowError(rowOf("confidence_threshold"), "须在 [0,1] 之间");
  }
  const ptsRaw = String(p.points || "").trim();
  if (ptsRaw) {
    const nums = ptsRaw.split(/[,;\s]+/).filter(Boolean);
    if (nums.length < 2 || nums.length % 2 !== 0) {
      errors.push("检测点坐标必须为成对的 x,y，如: 20,20 300,300");
      showRowError(rowOf("points"), "坐标必须成对");
    } else if (nums.some((n) => !/^\d+$/.test(n))) {
      errors.push("检测点坐标必须是整数");
      showRowError(rowOf("points"), "必须是整数");
    }
  }
  return { params: p, errors };
}
