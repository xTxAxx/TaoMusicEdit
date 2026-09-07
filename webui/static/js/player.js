// player.js —— 视频播放器组件
// 原生 <video> 负责平滑播放（HTTP Range 流式），overlay 画布负责：
//   帧精确逐帧显示（←/→ 或“设为起点”时经后端 /api/frame 抽取精确帧）、
//   检测标记叠加、捕获帧指示。
"use strict";

const Player = (function () {
  const els = {};
  let path = null;            // 当前视频绝对路径
  let info = null;            // /api/probe 结果
  let currentTime = 0;        // 当前显示时间（秒）
  let markers = [];           // 检测标记（视频原生坐标系）
  let markerVisible = true;
  let exactImg = null;        // 正在显示的精确帧图像（null = 显示 <video>）
  let stepBusy = false;
  let stepPending = 0;
  let stepToken = 0;
  let startMark = null;       // 已设为起点的帧位置 { time, frameOneBased }
  let detectedMark = null;    // { time, frameOneBased }
  let endMark = null;         // 已设为终点的帧位置 { time, frameOneBased }（区间裁剪）
  let scrubbing = false;
  let inited = false;

  function init() {
    if (inited) return;
    inited = true;
    els.video = document.getElementById("video");
    els.overlay = document.getElementById("overlay");
    els.stage = document.getElementById("playerStage");
    els.btnPlay = document.getElementById("btnPlay");
    els.btnPrev = document.getElementById("btnPrev");
    els.btnNext = document.getElementById("btnNext");
    els.volume = document.getElementById("volume");
    els.scrubber = document.getElementById("scrubber");
    els.scrubFill = document.getElementById("scrubFill");
    els.scrubThumb = document.getElementById("scrubThumb");
    els.scrubMarks = document.getElementById("scrubMarks");
    els.timecode = document.getElementById("timecode");
    els.frameInfo = document.getElementById("frameInfo");
    els.videoMeta = document.getElementById("videoMeta");
    els.markerToggle = document.getElementById("markerToggle");
    els.btnSetAsStart = document.getElementById("btnSetAsStart");
    els.btnSetAsEnd = document.getElementById("btnSetAsEnd");
    els.playerLoading = document.getElementById("playerLoading");
    els.playerHud = document.getElementById("playerHud");
    els.startBadge = document.getElementById("startBadge");
    els.endBadge = document.getElementById("endBadge");

    els.btnPlay.addEventListener("click", togglePlay);
    els.btnPrev.addEventListener("click", () => stepFrames(-1));
    els.btnNext.addEventListener("click", () => stepFrames(1));
    els.volume.addEventListener("input", () => {
      els.video.volume = parseFloat(els.volume.value) || 0;
      els.video.muted = els.video.volume === 0;
    });
    els.markerToggle.addEventListener("change", () => {
      markerVisible = els.markerToggle.checked;
      render();
    });
    els.btnSetAsStart.addEventListener("click", setAsStart);
    if (els.btnSetAsEnd) els.btnSetAsEnd.addEventListener("click", setAsEnd);
    // 点击终点徽标 = 清除终点（恢复「保留到片尾」的原有行为）
    if (els.endBadge) els.endBadge.addEventListener("click", clearEnd);
    // 点击起点徽标 = 清除当前设点，回退到算法检测到的起点（无检测起点则留空）
    if (els.startBadge) els.startBadge.addEventListener("click", clearStart);

    // 视频事件
    els.video.addEventListener("loadedmetadata", () => {
      updateHud();
      render();
    });
    els.video.addEventListener("timeupdate", () => {
      // 精确帧模式下 currentTime 由逐帧/检测跳转控制，避免 <video> 位置漂移覆盖
      if (!scrubbing && !exactImg) currentTime = els.video.currentTime;
      updateHud();
      render();
    });
    els.video.addEventListener("play", () => {
      exactImg = null; // 恢复 <video> 显示
      els.btnPlay.textContent = "暂停";
      render();
    });
    els.video.addEventListener("pause", () => {
      els.btnPlay.textContent = "播放";
    });
    els.video.addEventListener("ended", () => { els.btnPlay.textContent = "播放"; });
    els.video.addEventListener("error", () => {
      if (els.video.src && !els.video.currentSrc) {
        window.toast && toast("error", "浏览器无法播放该视频（可能编码不受支持）");
      }
    });

    initScrubber();
    window.addEventListener("keydown", onKeydown);
    window.addEventListener("resize", () => render());
    if (window.ResizeObserver) {
      new ResizeObserver(() => render()).observe(els.stage);
    }
  }

  // ---------------- 加载 ----------------
  function loadVideo(vpath) {
    path = vpath;
    info = null;
    markers = [];
    startMark = null;
    detectedMark = null;
    endMark = null;
    exactImg = null;
    els.playerLoading.hidden = false;
    return fetch("/api/probe?path=" + encodeURIComponent(vpath))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) {
          throw new Error(data.message || "无法解析视频");
        }
        info = data;
        currentTime = 0;
        els.video.src = "/media?path=" + encodeURIComponent(vpath);
        updateMeta();
        updateHud();
        updateStartBadge();
        updateEndBadge();
        render();
        return data;
      })
      .catch((err) => {
        window.toast && toast("error", "加载视频失败：" + err.message);
        throw err;
      })
      .finally(() => { els.playerLoading.hidden = true; });
  }

  function totalFrames() {
    if (!info) return 0;
    return info.nb_frames || Math.floor(info.duration * (info.fps || 30));
  }

  // ---------------- 播放控制 ----------------
  function togglePlay() {
    if (!info || !path) { window.toast && toast("warn", "请先在文件列表中选择视频"); return; }
    if (exactImg) {
      exactImg = null;
      setVideoTime(currentTime);
    }
    if (els.video.paused) {
      els.video.play().catch(() => {});
    } else {
      els.video.pause();
    }
  }

  function pause() {
    if (els.video && !els.video.paused) els.video.pause();
  }

  function setVideoTime(t) {
    const dur = info ? info.duration : 0;
    els.video.currentTime = Math.max(0, Math.min(t, dur));
  }

  function seekTo(t) {
    if (!info) return;
    t = Math.max(0, Math.min(t, info.duration));
    exactImg = null;
    currentTime = t;
    setVideoTime(t);
    updateHud();
    render();
  }

  // ---------------- 帧精确控制 ----------------
  function stepFrames(delta) {
    if (!info || !path) { window.toast && toast("warn", "请先选择视频"); return; }
    if (stepBusy) { stepPending += delta; return; }
    const fps = info.fps || 30;
    const total = totalFrames();
    let idx = Math.round(currentTime * fps) + delta;
    idx = Math.max(0, Math.min(idx, total - 1));
    pause();
    stepBusy = true;
    const t = idx / fps;
    showHud("正在定位第 " + (idx + 1) + " 帧…");
    const token = ++stepToken;
    fetchFrame(t)
      .then((img) => {
        if (token !== stepToken) return;
        exactImg = img;
        currentTime = t;
        updateHud();
        render();
      })
      .catch((err) => { window.toast && toast("error", "帧加载失败：" + err.message); })
      .finally(() => {
        stepBusy = false;
        hideHud();
        if (stepPending !== 0) {
          const d = stepPending;
          stepPending = 0;
          stepFrames(d);
        }
      });
  }

  function fetchFrame(t) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      let settled = false;
      const settle = (fn, arg) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        fn(arg);
      };
      img.onload = () => settle(resolve, img);
      img.onerror = () => settle(reject, new Error("无法获取帧图像"));
      const timer = setTimeout(() => {
        img.src = ""; // 取消浏览器侧挂起的请求，释放连接
        settle(reject, new Error("帧请求超时"));
      }, 8000);
      img.src = "/api/frame?path=" + encodeURIComponent(path) + "&time=" + t;
    });
  }

  // ---------------- 设为起点 ----------------
  // 读取当前播放帧，根据「起点方式」自动填入帧序号或时间戳到裁剪参数。
  function setAsStart() {
    if (!info || !path) { window.toast && toast("warn", "请先选择视频"); return; }
    const fps = info.fps || 30;
    const t = Math.max(0, currentTime);
    const frameOneBased = Math.round(t * fps) + 1;
    const modeEl = document.getElementById("param-start_mode");
    const mode = (modeEl && modeEl.value) || "frame";
    startMark = { time: t, frameOneBased };
    const token = ++stepToken;
    fetchFrame(t).then((img) => { if (token === stepToken) { exactImg = img; render(); } }).catch(() => {});
    if (mode === "timestamp") {
      setParam("start_value", t.toFixed(6));
      window.toast && toast("success", "已以当前帧时间戳 " + formatTime(t) + " 设为裁剪起点（-t）");
    } else {
      setParam("start_value", frameOneBased);
      window.toast && toast("success", "已以当前帧 第 " + frameOneBased + " 帧 @ " + formatTime(t) + " 设为裁剪起点（-f）");
    }
    updateStartBadge();
    updateHud();
    render();
    window.__onTrimParamFilled && window.__onTrimParamFilled("start_value");
  }

  // ---------------- 设为终点（区间裁剪） ----------------
  // 读取当前播放帧，根据「终点方式」自动填入帧序号或时间戳到裁剪参数；
  // 终点方式为「无（到片尾）」时自动沿用起点方式（两者保持相互独立，可事后修改）。
  function setAsEnd() {
    if (!info || !path) { window.toast && toast("warn", "请先选择视频"); return; }
    const fps = info.fps || 30;
    const t = Math.max(0, currentTime);
    const frameOneBased = Math.round(t * fps) + 1;
    let modeEl = document.getElementById("param-end_mode");
    let mode = (modeEl && modeEl.value) || "none";
    if (mode === "none") {
      const startModeEl = document.getElementById("param-start_mode");
      mode = (startModeEl && startModeEl.value) || "frame";
      setParam("end_mode", mode);  // 触发 app.js 联动启用终点输入框
    }
    endMark = { time: t, frameOneBased };
    const token = ++stepToken;
    fetchFrame(t).then((img) => { if (token === stepToken) { exactImg = img; render(); } }).catch(() => {});
    if (mode === "timestamp") {
      setParam("end_value", t.toFixed(6));
      window.toast && toast("success", "已以当前帧时间戳 " + formatTime(t) + " 设为裁剪终点（-T）");
    } else {
      setParam("end_value", frameOneBased);
      window.toast && toast("success", "已以当前帧 第 " + frameOneBased + " 帧 @ " + formatTime(t) + " 设为裁剪终点（-F）");
    }
    updateEndBadge();
    updateHud();
    render();
    window.__onTrimParamFilled && window.__onTrimParamFilled("end_value");
  }

  // ---------------- 清除终点 ----------------
  // 「清除终点」入口：终点方式切回「无（到片尾）」并清除终点标记，
  // 退化为原有片头裁剪行为；不触碰起点参数（两者相互独立）。
  function clearEnd() {
    const modeEl = document.getElementById("param-end_mode");
    if (modeEl && modeEl.value !== "none") {
      setParam("end_mode", "none");  // 触发 app.js 联动：清空终点值并清除标记
    } else {
      clearEndMark();
    }
  }

  function clearEndMark() {
    endMark = null;
    updateEndBadge();
    render();
  }

  // ---------------- 清除起点（回退到检测起点） ----------------
  // 点击起点徽标：若存在算法检测到的起点（detectedMark），则裁剪起点恢复为检测帧；
  // 否则清空裁剪起点参数（无检测起点的场景）。
  function clearStart() {
    if (detectedMark) {
      // 回退到检测起点后，起点与「自动关联的检测默认」一致，不再保留自定义起点标记
      startMark = null;
      const modeEl = document.getElementById("param-start_mode");
      const mode = (modeEl && modeEl.value) || "frame";
      if (mode === "timestamp") {
        setParam("start_value", detectedMark.time.toFixed(6));
      } else {
        setParam("start_value", detectedMark.frameOneBased);
      }
      window.toast && toast("success", "已回退到检测起点：第 " + detectedMark.frameOneBased + " 帧 @ " + formatTime(detectedMark.time));
    } else {
      startMark = null;
      setParam("start_mode", "frame");
      setParam("start_value", "");
      window.toast && toast("info", "无检测起点，已清空裁剪起点");
    }
    updateStartBadge();
    updateHud();
    render();
  }

  // ---------------- 检测标记 ----------------
  function setDetected(data, videoPath) {
    if (!data || !data.detected) {
      markers = [];
      detectedMark = null;
      exactImg = null;
      render();
      return;
    }
    if (videoPath && path !== videoPath) {
      // 检测结果属于其他视频，不叠加到当前播放器
      markers = [];
      detectedMark = null;
      return;
    }
    markers = (data.points || []).map((p) => ({
      x: p.x, y: p.y, confidence: p.confidence, rgb: p.rgb, matched: p.matched,
    }));
    detectedMark = { time: data.timestamp, frameOneBased: data.frame + 1 };
    // 跳转到检测帧并显示标记（以精确帧图像展示，不 seek <video>，避免连接占用）
    if (info && path) {
      pause();
      const token = ++stepToken;
      const t = data.timestamp;
      fetchFrame(t)
        .then((img) => {
          if (token !== stepToken) return;
          exactImg = img;
          currentTime = t;
          render();
        })
        .catch(() => {
          // 精确帧获取失败时退化为直接跳转 <video> 位置
          if (token === stepToken) {
            exactImg = null;
            currentTime = t;
            setVideoTime(t);
            render();
          }
        });
    }
    render();
  }

  function clearDetected() {
    markers = [];
    detectedMark = null;
    render();
  }

  function setMarkersVisible(v) {
    markerVisible = v;
    els.markerToggle.checked = v;
    render();
  }

  // ---------------- 渲染 ----------------
  function contentRect() {
    const sw = els.stage.clientWidth, sh = els.stage.clientHeight;
    const vw = info ? info.width : 16, vh = info ? info.height : 9;
    if (!vw || !vh) return { x: 0, y: 0, w: sw, h: sh, scale: 1, sw, sh };
    const scale = Math.min(sw / vw, sh / vh);
    const w = vw * scale, h = vh * scale;
    return { x: (sw - w) / 2, y: (sh - h) / 2, w, h, scale, sw, sh };
  }

  function render() {
    const canvas = els.overlay;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const rect = contentRect();
    canvas.width = Math.max(1, Math.round(rect.sw * dpr));
    canvas.height = Math.max(1, Math.round(rect.sh * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.sw, rect.sh);
    if (exactImg && exactImg.complete && exactImg.naturalWidth) {
      ctx.drawImage(exactImg, rect.x, rect.y, rect.w, rect.h);
    }
    drawMarkers(ctx, rect);
    drawScrubber();
  }

  function drawMarkers(ctx, rect) {
    if (!markerVisible || !markers.length) return;
    const pts = markers.map((m) => ({
      x: rect.x + m.x * rect.scale, y: rect.y + m.y * rect.scale, m,
    }));
    // 区域高亮矩形
    if (pts.length >= 2) {
      const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
      const minX = Math.min(...xs) - 8, minY = Math.min(...ys) - 8;
      const w = Math.max(...xs) - minX + 16, h = Math.max(...ys) - minY + 16;
      ctx.save();
      ctx.strokeStyle = "rgba(46,204,113,0.85)";
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(minX, minY, w, h);
      ctx.restore();
    }
    pts.forEach(({ x, y, m }) => {
      const color = m.matched ? "#2ecc71" : "#e74c3c";
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2); ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x - 14, y); ctx.lineTo(x + 14, y);
      ctx.moveTo(x, y - 14); ctx.lineTo(x, y + 14);
      ctx.stroke();
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.fill();
      ctx.font = '11px "Segoe UI", "Microsoft YaHei", sans-serif';
      ctx.fillText("conf=" + m.confidence.toFixed(3), x + 18, y - 14);
      ctx.restore();
    });
  }

  // ---------------- 进度条（自定义 + 标记指示器） ----------------
  function initScrubber() {
    const setFromEvent = (ev) => {
      const rect = els.scrubber.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
      const t = ratio * (info ? info.duration : 0);
      currentTime = t;
      setVideoTime(t);
      updateHud();
      render();
    };
    els.scrubber.addEventListener("pointerdown", (ev) => {
      scrubbing = true;
      els.scrubber.setPointerCapture(ev.pointerId);
      setFromEvent(ev);
    });
    els.scrubber.addEventListener("pointermove", (ev) => {
      if (scrubbing) setFromEvent(ev);
    });
    els.scrubber.addEventListener("pointerup", (ev) => {
      scrubbing = false;
      setFromEvent(ev);
    });
    els.scrubber.addEventListener("pointercancel", () => { scrubbing = false; });
  }

  function drawScrubber() {
    const dur = info ? info.duration : 0;
    const pct = dur > 0 ? (currentTime / dur) * 100 : 0;
    els.scrubFill.style.width = pct + "%";
    els.scrubThumb.style.left = "calc(" + pct + "% - 7px)";
    els.scrubMarks.innerHTML = "";
    const marks = [];
    if (detectedMark && dur > 0) marks.push({ t: detectedMark.time, cls: "detected", label: "检测帧" });
    if (startMark && dur > 0) marks.push({ t: startMark.time, cls: "captured", label: "起点" });
    if (endMark && dur > 0) marks.push({ t: endMark.time, cls: "end", label: "终点" });
    marks.forEach((mk) => {
      const d = document.createElement("div");
      d.className = "scrub-mark " + mk.cls;
      d.style.left = (mk.t / dur * 100) + "%";
      d.title = mk.label + " @ " + formatTime(mk.t);
      els.scrubMarks.appendChild(d);
    });
  }

  // ---------------- 信息显示 ----------------
  function updateHud() {
    const dur = info ? info.duration : 0;
    els.timecode.textContent = formatTime(currentTime) + " / " + formatTime(dur);
    const total = totalFrames();
    const idx = Math.round(currentTime * (info ? info.fps : 30));
    els.frameInfo.textContent =
      "帧 " + Math.min(idx + 1, Math.max(total, 1)) + " / " + total +
      (exactImg ? " · 精确帧" : "");
  }

  function updateMeta() {
    if (!info) return;
    els.videoMeta.textContent =
      info.width + "×" + info.height + " @ " + info.fps.toFixed(2) + "fps · " +
      info.codec + " · " + info.nb_frames + " 帧 · 时长 " + formatTime(info.duration);
  }

  function updateStartBadge() {
    if (els.startBadge) {
      els.startBadge.textContent = startMark
        ? "✕ 起点：第 " + startMark.frameOneBased + " 帧 @ " + formatTime(startMark.time)
        : "";
      els.startBadge.classList.toggle("on", !!startMark);
      els.startBadge.title = startMark
        ? (detectedMark ? "点击清除起点（回退到检测起点：第 " + detectedMark.frameOneBased + " 帧 @ " + formatTime(detectedMark.time) + "）" : "点击清除起点")
        : "";
    }
  }

  function updateEndBadge() {
    if (els.endBadge) {
      els.endBadge.textContent = endMark
        ? "✕ 终点：第 " + endMark.frameOneBased + " 帧 @ " + formatTime(endMark.time)
        : "";
      els.endBadge.classList.toggle("on", !!endMark);
      els.endBadge.title = endMark ? "点击清除终点（恢复保留到片尾）" : "";
    }
  }

  function showHud(text) {
    els.playerHud.textContent = text;
    els.playerHud.classList.add("show");
  }
  function hideHud() { els.playerHud.classList.remove("show"); }

  // ---------------- 快捷键 ----------------
  function onKeydown(e) {
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea") return;
    if (e.target.isContentEditable) return;
    if (e.code === "ArrowLeft") {
      e.preventDefault();
      stepFrames(-1);
    } else if (e.code === "ArrowRight") {
      e.preventDefault();
      stepFrames(1);
    } else if (e.code === "Space") {
      e.preventDefault();
      togglePlay();
    }
  }

  function formatTime(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    const ms = Math.floor((sec % 1) * 1000);
    const p = (n) => String(n).padStart(2, "0");
    return p(h) + ":" + p(m) + ":" + p(s) + "." + String(ms).padStart(3, "0");
  }

  return {
    init,
    loadVideo,
    togglePlay,
    pause,
    seekTo,
    stepFrames,
    setAsStart,
    setAsEnd,
    clearEndMark,
    setDetected,
    clearDetected,
    setMarkersVisible,
    getPath: () => path,
    getInfo: () => info,
    getTime: () => currentTime,
    formatTime,
  };
})();

document.addEventListener("DOMContentLoaded", () => Player.init());
