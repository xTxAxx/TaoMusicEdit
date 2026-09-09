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

  // —— 时间轴视口（缩放 / 平移共用）——
  let viewStart = 0;          // 可见窗口起点（秒）
  let viewDur = 0;            // 可见窗口时长（秒）；0 = 尚未初始化
  let zoomDragging = false;   // 时间轴缩略条拖动中
  let zoomDragOffset = 0;     // 拖动时指针相对缩略条左缘的偏移（px）
  let wave = null;            // 概览波峰 { start, end, peaks:[[min,max],...] }
  let waveCache = new Map();  // 窗口波峰缓存：key "start:end:buckets" -> 条目
  let waveToken = 0;          // 概览请求令牌（防止旧视频响应覆盖新视频）
  let waveReqToken = 0;       // 窗口请求令牌（防止过期响应覆盖当前视图）
  let waveTimer = null;       // 窗口请求防抖定时器

  const ZOOM_STEP = 1.15;     // 滚轮每格缩放倍数
  const WAVE_BUCKETS = 2000;  // 概览波峰桶数
  const WAVE_CACHE_MAX = 24;  // 窗口波峰缓存条数（LRU）

  function init() {
    if (inited) return;
    inited = true;
    els.video = document.getElementById("video");
    els.overlay = document.getElementById("overlay");
    els.stage = document.getElementById("playerStage");
    els.btnPlay = document.getElementById("btnPlay");
    els.btnPrev = document.getElementById("btnPrev");
    els.btnNext = document.getElementById("btnNext");
    els.scrubber = document.getElementById("scrubber");
    els.scrubWave = document.getElementById("scrubWave");
    els.scrubFill = document.getElementById("scrubFill");
    els.scrubThumb = document.getElementById("scrubThumb");
    els.scrubMarks = document.getElementById("scrubMarks");
    els.zoomScroll = document.getElementById("zoomScroll");
    els.zoomView = document.getElementById("zoomView");
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
      // 播放中播放头移出可见窗口时自动平移跟随（用户拖动缩略条时除外）
      if (info && viewDur > 0 && viewDur < info.duration - 1e-6 &&
          !scrubbing && !zoomDragging && !els.video.paused) {
        if (currentTime < viewStart || currentTime > viewStart + viewDur) {
          viewStart = Math.max(0, Math.min(currentTime - viewDur / 2, info.duration - viewDur));
          scheduleWaveWindow();
        }
      }
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
    initZoomScroll();
    initScrubZoom();
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
    viewStart = 0;
    viewDur = 0;
    wave = null;
    waveCache.clear();
    waveToken++;
    waveReqToken++;
    if (waveTimer) { clearTimeout(waveTimer); waveTimer = null; }
    els.playerLoading.hidden = false;
    return fetch("/api/probe?path=" + encodeURIComponent(vpath))
      .then((r) => r.json())
      .then((data) => {
        if (!data.ok) {
          throw new Error(data.message || "无法解析视频");
        }
        info = data;
        viewDur = data.duration;
        currentTime = 0;
        els.video.src = "/media?path=" + encodeURIComponent(vpath);
        updateMeta();
        updateHud();
        updateStartBadge();
        updateEndBadge();
        render();
        loadWaveform();
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
      const x = Math.max(0, Math.min(rect.width, ev.clientX - rect.left));
      // 在可见窗口内换算时间：点击/拖动只在当前视口范围内生效
      const t = xToTime(x);
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

  // ---------------- 时间轴视口：缩放 / 平移 / 波形 ----------------
  // 可见窗口 [viewStart, viewStart+viewDur] 映射到进度条全宽。
  function viewDurMin() {
    const fps = (info && info.fps) || 30;
    return Math.max(0.1, 2 / fps);  // 至少缩放到 2 帧
  }

  function xToTime(x) {
    if (!info || viewDur <= 0) return viewStart || 0;
    return viewStart + (x / els.scrubber.clientWidth) * viewDur;
  }

  // 锚点式缩放：保持鼠标下的时间点不动，拉长/缩短可见窗口
  function zoomAt(x, factor) {
    if (!info || viewDur <= 0) return;
    const dur = info.duration;
    const W = els.scrubber.clientWidth || 1;
    const ratio = Math.max(0, Math.min(1, x / W));
    const anchor = viewStart + ratio * viewDur;
    let nd = viewDur * factor;
    nd = Math.max(viewDurMin(), Math.min(nd, dur));
    let ns = anchor - ratio * nd;
    ns = Math.max(0, Math.min(ns, dur - nd));
    viewStart = ns;
    viewDur = nd;
    afterViewChange();
  }

  function panTo(t) {
    if (!info || viewDur <= 0) return;
    const dur = info.duration;
    viewStart = Math.max(0, Math.min(t, dur - viewDur));
    afterViewChange();
  }

  function resetView() {
    if (!info) return;
    viewStart = 0;
    viewDur = info.duration;
    afterViewChange();
  }

  function afterViewChange() {
    updateHud();
    render();
    scheduleWaveWindow();
  }

  // 概览波形：载入视频时拉取整段低分辨率波峰
  function loadWaveform() {
    if (!path || !info) return;
    const token = ++waveToken;
    fetch("/api/waveform?path=" + encodeURIComponent(path) +
      "&start=0&end=" + info.duration + "&buckets=" + WAVE_BUCKETS)
      .then((r) => r.json())
      .then((data) => {
        if (token !== waveToken || !data.ok) return;
        wave = data.has_audio ? { start: 0, end: info.duration, peaks: data.peaks } : null;
        render();
      })
      .catch(() => {});
  }

  // 从缓存中挑选能完整覆盖当前可见窗口、分辨率最高的波峰数据；兜底用概览
  function pickWaveData() {
    const v0 = viewStart, v1 = viewStart + viewDur;
    let best = null;
    waveCache.forEach((e) => {
      if (e.start <= v0 + 1e-6 && e.end >= v1 - 1e-6) {
        if (!best || (e.end - e.start) < (best.end - best.start)) best = e;
      }
    });
    return best || wave;
  }

  // 视图变化后防抖请求当前窗口的高分辨率波峰（只解码可见区间）
  function scheduleWaveWindow() {
    if (!path || !info) return;
    if (waveTimer) clearTimeout(waveTimer);
    waveTimer = setTimeout(requestWaveWindow, 120);
  }

  function requestWaveWindow() {
    waveTimer = null;
    if (!path || !info) return;
    const dur = info.duration;
    const W = els.scrubber.clientWidth || 800;
    const span = viewDur;
    let s = viewStart - span * 0.2;
    let e = viewStart + span * 1.2;
    s = Math.max(0, s);
    e = Math.min(dur, e);
    const buckets = Math.min(WAVE_BUCKETS, Math.max(64, Math.ceil(W * 2)));
    const key = s.toFixed(2) + ":" + e.toFixed(2) + ":" + buckets;
    if (waveCache.has(key)) { render(); return; }
    const token = ++waveReqToken;
    fetch("/api/waveform?path=" + encodeURIComponent(path) +
      "&start=" + s.toFixed(3) + "&end=" + e.toFixed(3) + "&buckets=" + buckets)
      .then((r) => r.json())
      .then((data) => {
        if (token !== waveReqToken || !data.ok || !data.has_audio) return;
        waveCache.set(key, { start: data.start, end: data.end, peaks: data.peaks });
        if (waveCache.size > WAVE_CACHE_MAX) {
          const it = waveCache.keys().next();
          if (!it.done) waveCache.delete(it.value);
        }
        render();
      })
      .catch(() => {});
  }

  // 时间轴水平滚动条：拖动比例缩略条平移；点击轨道将视图中心对齐到点击处
  function initZoomScroll() {
    els.zoomScroll.addEventListener("pointerdown", (ev) => {
      if (!info || viewDur <= 0) return;
      const dur = info.duration;
      const rect = els.zoomScroll.getBoundingClientRect();
      if (ev.target === els.zoomView) {
        zoomDragging = true;
        els.zoomScroll.setPointerCapture(ev.pointerId);
        zoomDragOffset = ev.clientX - rect.left - (viewStart / dur) * rect.width;
      } else {
        const ratio = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width));
        viewStart = ratio * (dur - viewDur) - viewDur / 2;
        viewStart = Math.max(0, Math.min(viewStart, dur - viewDur));
        afterViewChange();
      }
    });
    els.zoomScroll.addEventListener("pointermove", (ev) => {
      if (!zoomDragging) return;
      const dur = info.duration;
      const rect = els.zoomScroll.getBoundingClientRect();
      const ratio = (ev.clientX - rect.left - zoomDragOffset) / rect.width;
      viewStart = ratio * (dur - viewDur);
      viewStart = Math.max(0, Math.min(viewStart, dur - viewDur));
      afterViewChange();
    });
    const endDrag = () => { zoomDragging = false; };
    els.zoomScroll.addEventListener("pointerup", endDrag);
    els.zoomScroll.addEventListener("pointercancel", endDrag);
  }

  // 进度条悬停时：Ctrl + 滚轮缩放时间轴；双击恢复全览
  function initScrubZoom() {
    els.scrubber.addEventListener("wheel", (ev) => {
      if (!info) return;
      if (!ev.ctrlKey && !ev.metaKey) return;  // 未按住 Ctrl 不拦截
      ev.preventDefault();                     // 阻止浏览器页面缩放
      const rect = els.scrubber.getBoundingClientRect();
      const x = Math.max(0, Math.min(rect.width, ev.clientX - rect.left));
      zoomAt(x, ev.deltaY > 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
    }, { passive: false });
    els.scrubber.addEventListener("dblclick", () => resetView());
  }

  function formatTick(sec, step) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    const p = (n) => String(n).padStart(2, "0");
    let text = (h ? h + ":" : "") + p(m) + ":" + p(s);
    if (step < 1) text += "." + Math.floor((sec % 1) * 10);
    return text;
  }

  // 高倍缩放时在波形顶部绘制时间刻度尺，便于精确定位
  function drawRuler(ctx, W, H) {
    const dur = info ? info.duration : 0;
    if (!dur || viewDur > dur / 2) return;
    const target = 90;  // 目标刻度间距（px）
    const raw = viewDur / (W / target);
    let step = 1;
    const mag = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-6))));
    for (const m of [1, 2, 5, 10]) {
      if (m * mag >= raw) { step = m * mag; break; }
    }
    ctx.strokeStyle = "rgba(255,255,255,0.35)";
    ctx.fillStyle = "rgba(255,255,255,0.75)";
    ctx.font = '9px "Segoe UI", "Microsoft YaHei", monospace';
    ctx.beginPath();
    const first = Math.ceil(viewStart / step) * step;
    for (let t = first; t <= viewStart + viewDur + 1e-6; t += step) {
      const x = (t - viewStart) / viewDur * W;
      if (x < 0 || x > W) continue;
      ctx.moveTo(x, 5);
      ctx.lineTo(x, 11);
      ctx.fillText(formatTick(t, step), x + 3, 9);
    }
    ctx.stroke();
  }

  // 将波峰绘制到进度条内的 canvas（时间映射与进度条完全一致）
  function drawWaveform() {
    const cv = els.scrubWave;
    const W = els.scrubber.clientWidth, H = els.scrubber.clientHeight;
    if (!cv || W <= 0 || H <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.max(1, Math.round(W * dpr));
    cv.height = Math.max(1, Math.round(H * dpr));
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    drawRuler(ctx, W, H);
    const data = pickWaveData();
    if (!data || !data.peaks || !data.peaks.length) return;
    const dataStart = data.start, dataEnd = data.end;
    const dataSpan = dataEnd - dataStart;
    const peaks = data.peaks;
    const B = peaks.length;
    const top = 16;  // 波形区从刻度尺下方开始
    const mid = top + (H - top - 4) / 2;
    const amp = (H - top - 8) / 2;
    ctx.fillStyle = "rgba(120,180,255,0.6)";
    for (let x = 0; x < W; x++) {
      const t0 = viewStart + (x / W) * viewDur;
      const t1 = viewStart + ((x + 1) / W) * viewDur;
      let b0 = Math.floor((t0 - dataStart) / dataSpan * B);
      let b1 = Math.ceil((t1 - dataStart) / dataSpan * B);
      if (b1 <= 0 || b0 >= B) continue;  // 该列不在数据范围内
      b0 = Math.max(0, b0);
      b1 = Math.min(B, b1);
      let mn = 1, mx = -1;
      for (let i = b0; i < b1; i++) {
        const p = peaks[i];
        if (p[0] < mn) mn = p[0];
        if (p[1] > mx) mx = p[1];
      }
      if (mx < mn) continue;
      const yTop = mid - mx * amp;
      const yBot = mid - mn * amp;
      ctx.fillRect(x, yTop, 1, Math.max(1, yBot - yTop));
    }
  }

  function drawScrubber() {
    const dur = info ? info.duration : 0;
    // 可见窗口内播放头百分比（越界时钳制到边界）
    const pct = (dur > 0 && viewDur > 0)
      ? Math.max(0, Math.min(100, ((currentTime - viewStart) / viewDur) * 100))
      : 0;
    els.scrubFill.style.width = pct + "%";
    els.scrubThumb.style.left = "calc(" + pct + "% - 7px)";
    els.scrubMarks.innerHTML = "";
    const marks = [];
    if (detectedMark && dur > 0) marks.push({ t: detectedMark.time, cls: "detected", label: "检测帧" });
    if (startMark && dur > 0) marks.push({ t: startMark.time, cls: "captured", label: "起点" });
    if (endMark && dur > 0) marks.push({ t: endMark.time, cls: "end", label: "终点" });
    marks.forEach((mk) => {
      const p = (viewDur > 0) ? ((mk.t - viewStart) / viewDur) * 100 : -1;
      if (p < -3 || p > 103) return;  // 窗口外的标记不显示
      const d = document.createElement("div");
      d.className = "scrub-mark " + mk.cls;
      d.style.left = Math.max(0, Math.min(100, p)) + "%";
      d.title = mk.label + " @ " + formatTime(mk.t);
      els.scrubMarks.appendChild(d);
    });
    // 时间轴缩略条：宽度 = 可见比例，位置 = 窗口起点
    if (els.zoomView && dur > 0) {
      els.zoomView.style.width = (viewDur / dur * 100) + "%";
      els.zoomView.style.left = (viewStart / dur * 100) + "%";
    }
    drawWaveform();
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
