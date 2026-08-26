// params.js —— 数据驱动的参数面板：名称 / 类型 / 默认值 / 说明 / 必填 / 校验
"use strict";

// Detector 参数定义（对应 detector/config.py DetectorConfig 可配置项）
const DETECTOR_PARAMS = [
  { key: "target_color", label: "目标颜色", type: "text", default: "#F7F10F",
    placeholder: "#F7F10F", desc: "检测的目标颜色，支持 #RRGGBB 或 R,G,B" },
  { key: "points", label: "检测点坐标", type: "text",
    default: "20,20 1900,20 20,1060 1900,1060",
    placeholder: "20,20 300,300 1900,20",
    desc: "自定义检测点坐标，可传多个，数量由坐标个数决定；例: 20,20 300,300 1900,20（默认四点针对 1920x1080）" },
  { key: "confidence_threshold", label: "置信度阈值", type: "number",
    default: 0.97, min: 0, max: 1, step: 0.01, desc: "匹配阈值 [0,1]，越高越严格" },
  { key: "color_tolerance", label: "颜色容差", type: "number", default: 10.0,
    min: 0, step: 0.5, desc: "RGB 欧氏距离容差，兼容有损编码的颜色偏差" },
  { key: "scale_points", label: "检测点自动缩放", type: "bool", default: true,
    desc: "按视频实际分辨率对检测点坐标等比缩放" },
  { key: "search_window", label: "检测窗口(秒)", type: "number", default: 40,
    min: 1, step: 1, desc: "只检测视频前 N 秒" },
  { key: "initial_start", label: "反向跳帧起始(秒)", type: "number", default: 35,
    min: 0, step: 1, desc: "反向抽帧的起始时间" },
  { key: "coarse_step", label: "粗扫步长(秒)", type: "number", default: 5,
    min: 0.1, step: 0.5, desc: "初始大步长" },
  { key: "fine_step", label: "细扫步长(秒)", type: "number", default: 1,
    min: 0.1, step: 0.1, desc: "细化阶段步长（须 ≤ 粗扫步长）" },
  { key: "extractor", label: "帧提取器", type: "select", default: "ffmpeg",
    options: [{ v: "ffmpeg", l: "FFmpeg（精确）" }, { v: "opencv", l: "OpenCV（回退）" }],
    desc: "帧提取后端" },
  { key: "hwaccel", label: "GPU 硬解", type: "select", default: "auto",
    options: [{ v: "auto", l: "自动（推荐）" }, { v: "none", l: "关闭（CPU 软解）" },
              { v: "d3d11va", l: "D3D11VA（AMD/通用）" }, { v: "dxva2", l: "DXVA2（AMD）" },
              { v: "cuda", l: "CUDA（N 卡）" }, { v: "qsv", l: "QSV（Intel 核显）" }],
    desc: "视频解码加速；后端不可用时自动回退 CPU 软解，不影响检测结果" },
];

// Trimmer 参数定义（对应 trimmer/core/trimmer.py TrimmerConfig）
const TRIM_PARAMS = [
  { key: "start_mode", label: "起点方式", type: "select", default: "frame",
    options: [{ v: "frame", l: "帧序号 (-f)" }, { v: "timestamp", l: "时间戳 (-t)" }],
    desc: "裁剪起始点的表示方式（严格二选一）" },
  { key: "start_value", label: "裁剪起点", type: "number", default: "",
    required: true, step: 0.001, placeholder: "输入帧序号或时间戳…",
    desc: "必填。可先执行 Detect 或捕获当前帧后点击「设为起点」自动填入" },
  { key: "suffix", label: "输出文件名后缀", type: "text", default: "_trim",
    desc: "如 input.mp4 → input_trim.mp4" },
  { key: "output_mode", label: "输出模式", type: "select", default: "full",
    options: [{ v: "full", l: "完整视频" }, { v: "video", l: "无声视频" },
              { v: "audio", l: "纯音频" }, { v: "split", l: "分离音视频" }],
    desc: "输出内容组合" },
  { key: "force", label: "覆盖已存在文件", type: "bool", default: false,
    desc: "输出文件已存在时直接覆盖，不再报错" },
  { key: "reencode", label: "重编码", type: "bool", default: false,
    desc: "默认流复制（快速、无损）；重编码可精确到帧并自动匹配原视频参数" },
  { key: "hw_accel", label: "GPU 加速", type: "select", default: "auto",
    options: [{ v: "auto", l: "自动" }, { v: "none", l: "关闭" }, { v: "force", l: "强制" }],
    desc: "硬件编码加速策略" },
  { key: "video_codec", label: "视频编码器", type: "text", default: "",
    placeholder: "libx264（可选）", desc: "重编码时指定视频编码器，留空自动匹配" },
  { key: "audio_codec", label: "音频编码器", type: "text", default: "",
    placeholder: "aac（可选）", desc: "重编码时指定音频编码器，留空自动匹配" },
];

// 根据参数定义构建表单（container: DOM 元素, spec: 参数数组, values: 初始值）
function buildParamsForm(container, spec, values) {
  container.innerHTML = "";
  spec.forEach((p) => {
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
    container.appendChild(row);
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

// 校验 Trim 参数：起点必填、数值/范围正确、与视频信息比对
function validateTrim(state) {
  const p = collectParams(TRIM_PARAMS);
  const errors = [];
  const mode = p.start_mode;
  const raw = String(p.start_value).trim();
  const startRow = rowOf("start_value");

  if (!raw) {
    errors.push("请填写裁剪起点（必填）：可先执行 Detect 或捕获当前帧后点击「设为起点」");
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
  if (state.video && Number(p.reencode)) {
    // 流复制模式下起点受关键帧吸附影响，仅在重编码时提示精确性
    const row = rowOf("start_value");
    if (row && !row.classList.contains("error")) {
      const tip = document.createElement("div");
      tip.className = "param-tip";
      tip.textContent = "流复制模式下起点会吸附到关键帧；勾选「重编码」可精确到帧。";
      if (!row.querySelector(".param-tip")) row.appendChild(tip);
    }
  }
  return { params: p, errors };
}

// 校验 Detect 参数（步长约束等）
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
