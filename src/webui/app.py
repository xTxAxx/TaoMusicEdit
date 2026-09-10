# -*- coding: utf-8 -*-
"""TaoMusicEdit Web UI —— 集成 detector 与 trimmer 模块的工作台后端。

提供：
- 工作区 / 输出目录管理与文件浏览
- 视频元信息探测与 Range 流式播放
- 帧精确抽帧接口（供逐帧控制与帧捕获）
- detector / trimmer 异步任务（后台线程 + SSE 进度 + 取消）

运行方式（项目根目录）::

    python run.py          # 统一入口（推荐）
    py src\\webui\\app.py  # 也可直接运行本文件
"""
from __future__ import annotations

import array
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_BASE = os.path.dirname(os.path.abspath(__file__))   # src/webui
_SRC = os.path.dirname(_BASE)                         # src
_PROJECT_ROOT = os.path.dirname(_SRC)                 # 项目根目录
# 保证可导入 detector / trimmer（src 目录）与本地 vendored flask
for _p in (_SRC, os.path.join(_BASE, "_vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cv2  # noqa: E402
from flask import (  # noqa: E402
    Flask,
    Response,
    jsonify,
    make_response,
    render_template,
    request,
)

from detector import VideoColorDetector, hex_to_rgb  # noqa: E402
from detector.core.errors import DetectorError  # noqa: E402
from detector.utils.ffmpeg import probe_video, read_frame  # noqa: E402
from trimmer import OutputMode, Trimmer, TrimmerConfig  # noqa: E402
from trimmer.core.errors import TrimmerError  # noqa: E402
from trimmer.core.ffmpeg import find_ffmpeg  # noqa: E402
from trimmer.core.probe import probe as trimmer_probe  # noqa: E402
from trimmer.utils.validation import validate_frame, validate_timestamp  # noqa: E402

from webui.jobs import FileCancelled, JobCancelled, manager, sse_payload  # noqa: E402

from webui import settings as settings_mod  # noqa: E402  (设置持久化)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# 常量与全局状态
# ---------------------------------------------------------------------------
VIDEO_EXTS = {
    ".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".m4v", ".mpeg", ".mpg",
    ".ts", ".mts", ".m2ts", ".wmv", ".3gp", ".3g2", ".ogv",
}
MIME = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".avi": "video/x-msvideo", ".mkv": "video/x-matroska", ".webm": "video/webm",
    ".flv": "video/x-flv", ".ts": "video/mp2t", ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg", ".wmv": "video/x-ms-wmv", ".3gp": "video/3gpp",
    ".ogv": "video/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".m4a": "audio/mp4", ".aac": "audio/aac",
}
#: 文件树遍历时跳过的目录
SKIP_DIRS = {
    "_vendor", ".git", "__pycache__", ".pytest_cache", "node_modules",
    ".venv", "venv", ".idea", ".vscode", "dist", "build", ".trae",
}
FRAME_CACHE_MAX = 120
#: 音频波峰窗口缓存上限（LRU），按 (path, start, end, buckets) 键控
WAVE_CACHE_MAX = 64

#: 启动时从配置文件恢复上次的工作区 / 输出目录；未保存或路径失效时回退到项目根目录
_SAVED = settings_mod.load()
_DEFAULT_WS = _PROJECT_ROOT
_DEFAULT_OD = _PROJECT_ROOT
STATE = {
    "workspace": _SAVED.get("workspace") if os.path.isdir(_SAVED.get("workspace") or "") else _DEFAULT_WS,
    "output_dir": _SAVED.get("output_dir") if os.path.isdir(_SAVED.get("output_dir") or "") else _DEFAULT_OD,
}
_probe_cache: dict = {}
_frame_cache: dict = {}
_wave_cache: dict = {}
_cache_lock = threading.Lock()

# ---------------------------------------------------------------------------
# 检测结果持久化缓存（SQLite）：detect 与 batch detect 完成后写入，
# 刷新页面 / 重启服务后仍可复用。
#
# 设计要点：
# - 单库文件 src/webui/detect_cache.db，WAL 模式，写入为单条事务（原子、无写放大）；
# - 主键使用 os.path.normcase 归一化路径（Windows 大小写不敏感），另存原始路径；
# - 惰性初始化：首次访问时建表。
# ---------------------------------------------------------------------------
import sqlite3  # noqa: E402

_DETECT_CACHE_DB = os.path.join(_BASE, "detect_cache.db")
_detect_cache_lock = threading.RLock()
_detect_db: sqlite3.Connection | None = None


def _norm_key(path: str) -> str:
    """缓存主键：绝对路径 + 系统大小写归一化。"""
    return os.path.normcase(os.path.abspath(path))


def _cache_cfg() -> dict:
    """读取缓存配置（enabled / limit），来自 settings.json 的 detect_cache 组。"""
    s = settings_mod.load()
    dc = s.get("detect_cache") or {}
    try:
        limit = max(0, int(dc.get("limit") or 0))
    except (TypeError, ValueError):
        limit = 0
    return {"enabled": bool(dc.get("enabled", True)), "limit": limit}


def _count_detect_cache() -> int:
    """当前缓存条目数。"""
    try:
        db = _get_detect_db()
        with _detect_cache_lock:
            return int(db.execute("SELECT COUNT(*) FROM detect_cache").fetchone()[0])
    except sqlite3.Error:
        return 0


def _get_detect_db() -> sqlite3.Connection:
    """获取缓存数据库连接（惰性初始化）。"""
    global _detect_db
    with _detect_cache_lock:
        if _detect_db is not None:
            return _detect_db
        conn = sqlite3.connect(_DETECT_CACHE_DB, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS detect_cache ("
            " key TEXT PRIMARY KEY,"       # normcase 归一化路径
            " orig_path TEXT NOT NULL,"    # 原始绝对路径（用于返回与展示）
            " mtime REAL NOT NULL,"
            " size INTEGER NOT NULL,"
            " ts REAL NOT NULL,"
            " result TEXT NOT NULL)"       # 检测结果 JSON
        )
        conn.commit()
        _detect_db = conn
        return _detect_db


def _detect_cache_entry_valid(path: str, mtime: float, size: int) -> bool:
    """校验缓存条目对应的视频文件未发生变化（mtime / size 一致才视为有效）。"""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return mtime == st.st_mtime and size == st.st_size


def store_detect_result(result: dict, path: str) -> None:
    """把一次检测结果写入持久化缓存（单次检测与批量检测共用）。

    单条 INSERT OR REPLACE 事务：原子写入，无全量重写开销。
    受配置控制：缓存关闭时不写入；设置了条目上限时按 ts 淘汰最旧条目。
    """
    cfg = _cache_cfg()
    if not cfg["enabled"]:
        return  # 缓存已停用
    abs_path = os.path.abspath(path)
    try:
        st = os.stat(abs_path)
    except OSError:
        return  # 文件已消失则无需缓存
    payload = json.dumps(dict(result), ensure_ascii=False)
    try:
        db = _get_detect_db()
        with _detect_cache_lock, db:
            db.execute(
                "INSERT OR REPLACE INTO detect_cache VALUES (?,?,?,?,?,?)",
                (_norm_key(abs_path), abs_path,
                 st.st_mtime, st.st_size, time.time(), payload),
            )
            if cfg["limit"] > 0:
                # 超出上限时淘汰最旧的条目（按写入时间 ts 排序）
                db.execute(
                    "DELETE FROM detect_cache WHERE key NOT IN ("
                    " SELECT key FROM detect_cache ORDER BY ts DESC LIMIT ?)",
                    (cfg["limit"],),
                )
    except sqlite3.Error:
        pass  # 写入失败不影响检测流程本身


def get_cached_detect_result(path: str) -> dict | None:
    """读取单个视频的有效缓存结果；文件变化、缓存停用或无记录返回 None。"""
    if not _cache_cfg()["enabled"]:
        return None
    abs_path = os.path.abspath(path)
    try:
        db = _get_detect_db()
        with _detect_cache_lock:
            row = db.execute(
                "SELECT orig_path, mtime, size, result FROM detect_cache WHERE key=?",
                (_norm_key(abs_path),),
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    orig_path, mtime, size, payload = row
    if not _detect_cache_entry_valid(orig_path, mtime, size):
        return None
    try:
        result = json.loads(payload)
    except ValueError:
        return None
    result["file"] = orig_path  # 统一带上 file 字段，方便批处理流程复用
    return result


def list_valid_detect_cache() -> dict:
    """返回所有有效缓存条目 {原始路径: 结果}；顺带清理源文件已消失的孤儿记录。"""
    if not _cache_cfg()["enabled"]:
        return {}
    try:
        db = _get_detect_db()
        with _detect_cache_lock:
            rows = db.execute(
                "SELECT key, orig_path, mtime, size, result FROM detect_cache"
            ).fetchall()
    except sqlite3.Error:
        return {}
    entries: dict = {}
    stale_keys: list = []
    for key, orig_path, mtime, size, payload in rows:
        # 源文件已删除 / 移动：记录待清理的孤儿条目
        if not os.path.isfile(orig_path):
            stale_keys.append(key)
            continue
        if not _detect_cache_entry_valid(orig_path, mtime, size):
            continue  # 文件仍在但内容变化：保留记录（重新检测后会覆盖），暂不当无效返回
        try:
            result = json.loads(payload)
        except ValueError:
            stale_keys.append(key)
            continue
        result["file"] = orig_path
        entries[orig_path] = result
    if stale_keys:
        try:
            with _detect_cache_lock, db:
                db.executemany(
                    "DELETE FROM detect_cache WHERE key=?",
                    [(k,) for k in stale_keys],
                )
        except sqlite3.Error:
            pass
    return entries


def prune_detect_cache() -> list:
    """清理失效条目（源文件已删除 / 已修改 / 结果损坏），返回被删的原始路径列表。

    与 list_valid_detect_cache 的区别：手动触发、更彻底——连"文件仍在但内容
    已变化"的过期记录也一并删除。
    """
    removed_paths: list = []
    try:
        db = _get_detect_db()
        rows = db.execute(
            "SELECT key, orig_path, mtime, size FROM detect_cache"
        ).fetchall()
        bad = [
            (key, orig_path) for key, orig_path, mtime, size in rows
            if not _detect_cache_entry_valid(orig_path, mtime, size)
        ]
        if bad:
            with _detect_cache_lock, db:
                db.executemany("DELETE FROM detect_cache WHERE key=?", [(k,) for k, _ in bad])
            removed_paths = [p for _, p in bad]
    except sqlite3.Error:
        pass
    return removed_paths


def clear_detect_cache() -> int:
    """清空全部缓存条目，返回清除前的条目数。"""
    try:
        db = _get_detect_db()
        before = _count_detect_cache()
        with _detect_cache_lock, db:
            db.execute("DELETE FROM detect_cache")
        return before
    except sqlite3.Error:
        return 0


def _writable(path: str):
    """检测目录是否可写：创建并删除一个临时文件。返回 (ok, message)。"""
    if not os.path.isdir(path):
        return False, "目录不存在"
    probe = os.path.join(path, f".webui_write_test_{os.getpid()}")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True, ""
    except OSError as exc:
        return False, f"目录不可写：{exc}"


def _video_info(path: str):
    """探测视频元信息并缓存。"""
    info = _probe_cache.get(path)
    if info is None:
        info = probe_video(path)
        _probe_cache[path] = info
    return info


def _get_frame_jpeg(path: str, t: float):
    """提取指定时间戳的精确帧并编码为 JPEG（FIFO 上限缓存）。

    命中时不刷新顺序，淘汰固定取最早插入项，故为 FIFO 而非 LRU；
    如需真 LRU（提升逐帧回看命中率），需在命中时做一次"删后重插"。
    """
    key = (path, round(t, 3))
    with _cache_lock:
        hit = _frame_cache.get(key)
    if hit is not None:
        return hit
    info = _video_info(path)
    frame = read_frame(path, max(0.0, t), info.width, info.height)
    if frame is None:
        return None
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        return None
    data = buf.tobytes()
    with _cache_lock:
        if len(_frame_cache) >= FRAME_CACHE_MAX:
            try:
                _frame_cache.pop(next(iter(_frame_cache)))
            except StopIteration:
                pass
        _frame_cache[key] = data
    return data


def _waveform_peaks(path: str, start: float, end: float, buckets: int):
    """用 ffmpeg 解码 ``[start, end]`` 区间的音频为 f32le 单声道，聚合每桶 min/max。

    采样率按桶数自适应（每桶约 4 个样本），控制解码开销：
    - 概览（整段）采样率低，样本量小；
    - 深缩放的短窗口采样率高，保证波峰分辨率。

    返回: ``[[min, max], ...]``（长度 buckets，取值 [-1, 1]）；
    解码失败或无音轨时返回 ``None``。
    """
    span = max(0.001, end - start)
    sr = max(1000, min(48000, int(4 * buckets / span) + 1))
    try:
        ffmpeg = find_ffmpeg()
    except TrimmerError:
        return None
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.6f}", "-i", path,
        "-t", f"{span:.6f}",
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(sr),
        "-f", "f32le", "-",
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=1 << 16,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    buf = array.array("f")
    try:
        while True:
            chunk = proc.stdout.read(1 << 16)
            if not chunk:
                break
            buf.frombytes(chunk)
        proc.stdout.close()
    except Exception:
        # 读取异常：终止进程并释放管道，避免挂起
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.wait()
        except Exception:
            pass
        return None
    return_code = proc.wait()
    if return_code != 0 or not buf:
        return None

    lo = [1.0] * buckets
    hi = [-1.0] * buckets
    scale = buckets / (sr * span)
    for i, v in enumerate(buf):
        b = int(i * scale)
        if b >= buckets:
            break
        if v < lo[b]:
            lo[b] = v
        if v > hi[b]:
            hi[b] = v
    peaks = []
    for i in range(buckets):
        if hi[i] < lo[i]:
            peaks.append([0.0, 0.0])  # 空桶（时长不足或解码边界）
        else:
            peaks.append([round(lo[i], 4), round(hi[i], 4)])
    return peaks


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
@app.after_request
def _no_cache_api(resp):
    """API 响应一律禁缓存，避免浏览器启发式缓存导致前端读到过期数据。"""
    if resp.headers.get("Cache-Control") is None and request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    resp = make_response(render_template("index.html"))
    # 页面本身不做任何缓存，避免浏览器保留旧版 HTML 导致与新 JS 错配
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# ---------------------------------------------------------------------------
# 工作区
# ---------------------------------------------------------------------------
def build_tree(root: str, max_nodes: int = 2000):
    """递归扫描目录并构建文件树。"""

    def walk(dirpath: str, depth: int):
        if depth > 12:
            return []
        nodes = []
        try:
            entries = sorted(
                os.scandir(dirpath),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except OSError:
            return []
        for e in entries:
            if e.name.startswith((".", "$")) or e.name in SKIP_DIRS:
                continue
            try:
                is_dir = e.is_dir()
            except OSError:
                continue
            if is_dir:
                children = walk(e.path, depth + 1)
                nodes.append({
                    "name": e.name, "path": e.path, "type": "dir",
                    "children": children,
                })
            else:
                ext = os.path.splitext(e.name)[1].lower()
                try:
                    size = e.stat().st_size
                except OSError:
                    size = 0
                nodes.append({
                    "name": e.name, "path": e.path, "type": "file",
                    "ext": ext, "is_video": ext in VIDEO_EXTS, "size": size,
                })
            if len(nodes) >= max_nodes:
                break
        return nodes

    return walk(root, 0)


@app.route("/api/workspace", methods=["GET"])
def get_workspace():
    p = STATE["workspace"]
    return jsonify({
        "path": p,
        "exists": bool(p and os.path.isdir(p)),
        "writable": _writable(p)[0] if p else False,
    })


@app.route("/api/workspace", methods=["POST"])
def set_workspace():
    data = request.get_json(force=True, silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "message": "请提供工作目录路径"}), 400
    if not os.path.isdir(path):
        return jsonify({"ok": False, "message": f"目录不存在：{path}"}), 400
    STATE["workspace"] = os.path.abspath(path)
    tree = build_tree(STATE["workspace"])
    return jsonify({
        "ok": True, "path": STATE["workspace"],
        "writable": _writable(STATE["workspace"])[0], "tree": tree,
    })


# ---------------------------------------------------------------------------
# 输出目录
# ---------------------------------------------------------------------------
@app.route("/api/output-dir", methods=["GET"])
def get_output_dir():
    p = STATE["output_dir"]
    ok, msg = _writable(p) if p else (False, "未设置")
    return jsonify({"path": p, "exists": bool(p and os.path.isdir(p)),
                    "writable": ok, "message": msg})


@app.route("/api/output-dir", methods=["POST"])
def set_output_dir():
    data = request.get_json(force=True, silent=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "message": "请提供输出目录路径"}), 400
    if not os.path.isdir(path):
        return jsonify({"ok": False, "message": f"目录不存在：{path}"}), 400
    ok, msg = _writable(path)
    if not ok:
        return jsonify({"ok": False, "message": msg}), 400
    STATE["output_dir"] = os.path.abspath(path)
    return jsonify({"ok": True, "path": STATE["output_dir"], "writable": True})


# ---------------------------------------------------------------------------
# 设置持久化（配置文件：工作区 / 输出目录 / 模块参数）
# ---------------------------------------------------------------------------
@app.route("/api/settings", methods=["GET"])
def get_settings():
    """读取已保存的设置（未保存时返回默认值）。"""
    return jsonify({
        "ok": True,
        "settings": settings_mod.load(),
        "defaults": settings_mod.defaults(),
        "path": settings_mod.SETTINGS_PATH,
    })


@app.route("/api/settings", methods=["POST"])
def save_settings():
    """保存设置：前端在参数/路径变化后调用，服务端合并后写入配置文件。"""
    data = request.get_json(force=True, silent=True) or {}
    saved = settings_mod.save(data)
    return jsonify({"ok": True, "settings": saved})


@app.route("/api/settings/reset", methods=["POST"])
def reset_settings():
    """恢复默认设置：删除配置文件，并把工作区 / 输出目录重置为项目根目录。"""
    s = settings_mod.reset()
    STATE["workspace"] = s.get("workspace") or _DEFAULT_WS
    STATE["output_dir"] = s.get("output_dir") or _DEFAULT_OD
    _probe_cache.clear()
    return jsonify({"ok": True, "settings": s})


# ---------------------------------------------------------------------------
# 目录浏览（文件选择器）
# ---------------------------------------------------------------------------
@app.route("/api/list-dir")
def list_dir():
    path = request.args.get("path", "")
    if not path:
        path = STATE["workspace"]
    if not os.path.isdir(path):
        return jsonify({"ok": False, "message": "目录不存在"}), 400
    path = os.path.abspath(path)
    parent = os.path.dirname(path) or path
    subdirs = []
    try:
        for e in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if e.name.startswith((".", "$")) or e.name in SKIP_DIRS:
                continue
            if e.is_dir():
                subdirs.append(e.name)
    except OSError:
        pass
    return jsonify({"ok": True, "path": path, "parent": parent,
                    "subdirs": subdirs})


# ---------------------------------------------------------------------------
# 视频探测 / 流式播放 / 帧抽取
# ---------------------------------------------------------------------------
@app.route("/api/probe")
def api_probe():
    path = request.args.get("path", "")
    if not os.path.isfile(path):
        return jsonify({"ok": False, "message": "文件不存在"}), 400
    try:
        info = probe_video(path)
    except DetectorError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    if not info.has_video:
        return jsonify({"ok": False, "message": "文件不是有效视频或缺少视频流"}), 400
    _probe_cache[path] = info
    return jsonify({
        "ok": True, "path": path, "width": info.width, "height": info.height,
        "fps": info.fps, "duration": info.duration, "nb_frames": info.nb_frames,
        "codec": info.codec, "pix_fmt": info.pix_fmt,
    })


@app.route("/media")
def media():
    """以 HTTP Range 流式返回视频文件，供 HTML5 播放器平滑播放。"""
    path = request.args.get("path", "")
    if not path or not os.path.isfile(path):
        return Response("Not Found", status=404)
    size = os.path.getsize(path)
    if size <= 0:
        return Response("Empty", status=404)
    mime = MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream")

    start, end = 0, size - 1
    status = 200
    rng = request.headers.get("Range")
    if rng:
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if m:
            start = int(m.group(1)) if m.group(1) else 0
            end = int(m.group(2)) if m.group(2) else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                resp = Response("", status=416)
                resp.headers["Content-Range"] = f"bytes */{size}"
                return resp
            status = 206

    def gen():
        chunk = 1024 * 1024
        remaining = end - start + 1
        with open(path, "rb") as f:
            f.seek(start)
            while remaining > 0:
                data = f.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    resp = Response(gen(), status=status, mimetype=mime)
    resp.headers["Accept-Ranges"] = "bytes"
    if status == 206:
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    resp.headers["Content-Length"] = str(end - start + 1)
    return resp


@app.route("/api/frame")
def api_frame():
    """提取指定时间戳的精确帧（JPEG），用于逐帧控制与帧捕获。"""
    path = request.args.get("path", "")
    try:
        t = float(request.args.get("time", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "time 参数非法"}), 400
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "message": "文件不存在"}), 404
    try:
        data = _get_frame_jpeg(path, t)
    except DetectorError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    if data is None:
        return jsonify({"ok": False, "message": "无法提取该时间戳的帧"}), 422
    resp = Response(data, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.route("/api/waveform")
def api_waveform():
    """返回音频波峰（每桶 min/max），供前端进度条波形叠加。

    参数:
        path: 视频绝对路径。
        start/end: 时间窗口（秒），默认整段。
        buckets: 波峰桶数（16~8000），约等于画布像素宽 × 2。
    """
    path = request.args.get("path", "")
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "message": "文件不存在"}), 404
    try:
        start = max(0.0, float(request.args.get("start", 0)))
        buckets = int(request.args.get("buckets", 1000))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "参数非法"}), 400
    buckets = max(16, min(buckets, 8000))
    try:
        info = _video_info(path)
    except DetectorError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    end = info.duration
    raw_end = request.args.get("end")
    if raw_end:
        try:
            end = float(raw_end)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "message": "参数非法"}), 400
    end = max(start + 0.001, min(end, info.duration))

    key = (path, round(start, 2), round(end, 2), buckets)
    with _cache_lock:
        hit = _wave_cache.get(key)
        if hit is not None:
            _wave_cache[key] = hit  # 刷新 LRU 位置
    if hit is None:
        hit = _waveform_peaks(path, start, end, buckets)
        if hit is not None:
            with _cache_lock:
                _wave_cache[key] = hit
                if len(_wave_cache) > WAVE_CACHE_MAX:
                    try:
                        _wave_cache.pop(next(iter(_wave_cache)))
                    except StopIteration:
                        pass

    if hit is None:
        # 解码失败通常意味着无音轨；前端据此降级为纯进度条
        resp = jsonify({"ok": True, "has_audio": False, "peaks": [],
                        "duration": info.duration, "start": start, "end": end,
                        "buckets": buckets})
    else:
        resp = jsonify({"ok": True, "has_audio": True, "peaks": hit,
                        "duration": info.duration, "start": start, "end": end,
                        "buckets": buckets})
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


# ---------------------------------------------------------------------------
# Detector 任务
# ---------------------------------------------------------------------------
def build_detector_overrides(params: dict) -> dict:
    """将前端参数转换为 detector 配置覆盖项，并做基本校验。"""
    overrides = {"log_level": "WARNING"}
    if params.get("target_color"):
        text = str(params["target_color"]).strip()
        if text.startswith("#"):
            overrides["target_color"] = hex_to_rgb(text)
        else:
            parts = [int(p) for p in re.split(r"[,; ]+", text) if p.strip()]
            if len(parts) != 3 or not all(0 <= c <= 255 for c in parts):
                raise ValueError("目标颜色格式应为 #RRGGBB 或 R,G,B（0-255）")
            overrides["target_color"] = tuple(parts)
    for key in ("confidence_threshold", "color_tolerance", "search_window",
                "initial_start", "coarse_step", "fine_step"):
        val = params.get(key)
        if val is not None and val != "":
            overrides[key] = float(val)
    if params.get("scale_points") is not None:
        overrides["scale_points"] = bool(params["scale_points"])
    if params.get("points"):
        text = str(params["points"]).strip()
        if text:
            nums = [int(n) for n in re.split(r"[,;\s]+", text) if n.strip()]
            if len(nums) < 2 or len(nums) % 2 != 0:
                raise ValueError("检测点坐标必须为成对的 x,y 坐标，如: 20,20 300,300")
            overrides["points"] = tuple(
                (nums[i], nums[i + 1]) for i in range(0, len(nums), 2)
            )
    if params.get("extractor"):
        overrides["extractor"] = str(params["extractor"])
    hwaccel = params.get("hwaccel")
    if hwaccel:
        overrides["hwaccel"] = str(hwaccel)
    return overrides


def _detect_one(path: str, overrides: dict, progress_cb=None) -> dict:
    """对单个视频执行颜色检测，返回结果 dict（单任务与批处理共用）。"""
    detector = VideoColorDetector(**overrides)
    try:
        result = detector.detect(path, progress_callback=progress_cb, detail=True)
    finally:
        detector.close()

    if result.detected and result.timestamp is not None:
        # 预热检测帧缓存：检测完成后前端跳转到检测帧会再次请求 /api/frame，
        # 提前把该帧写入后端缓存，使跳转立即命中，避免重复 ffmpeg 抽取导致卡顿。
        try:
            _get_frame_jpeg(path, result.timestamp)
        except Exception:
            pass

    d = result.details
    points = []
    if d and d.point_matches:
        points = [{
            "x": p.x, "y": p.y, "confidence": p.confidence,
            "rgb": list(p.rgb), "matched": p.matched,
        } for p in d.point_matches]
    return {
        "detected": result.detected,
        "frame": result.frame,
        "timestamp": result.timestamp,
        "confidence": d.confidence if d else None,
        "resolution": list(d.resolution) if d and d.resolution else None,
        "duration": d.duration_seconds if d else 0.0,
        "message": d.message if d else "",
        "points": points,
    }


def run_detect(job, payload: dict) -> dict:
    """执行颜色检测（在后台线程中运行）。"""
    path = payload.get("path")
    if not path or not os.path.isfile(path):
        raise ValueError("请选择有效的视频文件")
    overrides = build_detector_overrides(payload.get("params") or {})

    def cb(stage, progress, message):
        if job.cancel_event.is_set():
            raise JobCancelled()
        job.publish("progress", {
            "stage": stage, "progress": progress, "message": message,
        })

    result = _detect_one(path, overrides, progress_cb=cb)
    result["path"] = path
    store_detect_result(result, path)  # 写入持久化缓存，刷新 / 重启后可复用
    return result


@app.route("/api/detect", methods=["POST"])
def api_detect():
    data = request.get_json(force=True, silent=True) or {}
    jid = manager.create("detect", "颜色检测", lambda job: run_detect(job, data))
    return jsonify({"ok": True, "job_id": jid})


def _parallel_workers() -> int:
    """批处理文件级并行度。

    优先级：设置中的 batch.workers（界面「批处理 → 并发数」，0 = 自动）
    → 环境变量 TAOMUSIC_BATCH_WORKERS → 默认 min(CPU 核数, 4)。
    """
    try:
        saved = settings_mod.load()
        workers = int(saved.get("batch", {}).get("workers") or 0)
        if workers > 0:
            return min(16, workers)
    except (TypeError, ValueError):
        pass
    try:
        env = int(os.environ.get("TAOMUSIC_BATCH_WORKERS", "0"))
        if env > 0:
            return env
    except (TypeError, ValueError):
        pass
    return max(1, min(os.cpu_count() or 1, 4))


def run_batch_detect(job, payload: dict) -> dict:
    """批量执行颜色检测：文件级并行（线程池），逐个汇报进度。

    payload.skip_cached 为真时，已有有效缓存的视频直接采用缓存结果，
    跳过重新检测（用于"全选后增量检测新文件"的场景）。

    并行度见 :func:`_parallel_workers`；结果按入参顺序返回。
    取消（cancel_event）置位后，工作线程在进度回调中抛出 JobCancelled，
    主线程随即停止等待并向上传播（不再派发新任务）。
    """
    files = payload.get("files") or []
    overrides = build_detector_overrides(payload.get("params") or {})
    skip_cached = bool(payload.get("skip_cached"))
    total = len(files)
    results: list = [None] * total

    def worker(i: int, path: str):
        """处理单个文件，返回 (index, result)；异常在内部收敛为错误结果。"""

        def done(result: dict):
            # 单文件完成即发布终态事件，前端立即标记该任务完成，无需等整批结束
            job.publish("file_done", {"index": i + 1, "file": path, "result": result})
            return i, result

        if job.cancel_event.is_set():
            raise JobCancelled()
        # 单文件取消：该索引已在取消集合中（含排队未开始的文件）直接返回取消结果
        if (i + 1) in job.file_cancel:
            return done({"file": path, "detected": False, "error": "已取消", "cancelled": True})
        job.publish("progress", {
            "index": i + 1, "total": total, "file": path,
            "percent": round(i / total * 100, 1),
            "progress": 0.0,  # 单文件进度从 0 起，避免以"批内位置"虚占进度
            "stage": "pending", "message": "准备检测",
        })
        if not path or not os.path.isfile(path):
            return done({"file": path, "detected": False, "error": "文件不存在"})

        # 跳过已缓存：命中有效缓存则直接出结果，不做实际检测
        if skip_cached:
            cached = get_cached_detect_result(path)
            if cached is not None:
                cached["skipped"] = True
                return done(cached)

        def cb(stage, progress, message):
            if job.cancel_event.is_set():
                raise JobCancelled()
            if (i + 1) in job.file_cancel:
                raise FileCancelled()
            job.publish("progress", {
                "index": i + 1, "total": total, "file": path,
                "percent": round((i + progress) / total * 100, 1),
                "stage": stage, "progress": progress, "message": message,
            })

        try:
            r = _detect_one(path, overrides, progress_cb=cb)
            r["file"] = path
            store_detect_result(r, path)  # 每个文件完成即写缓存，中断不丢已完成部分
            return done(r)
        except FileCancelled:
            return done({"file": path, "detected": False, "error": "已取消", "cancelled": True})
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不中断整体
            return done({"file": path, "detected": False, "error": str(exc)})

    workers = _parallel_workers()
    if total <= 1 or workers <= 1:
        # 单文件 / 禁用并行：退化为同步执行
        for i, path in enumerate(files):
            results[i] = worker(i, path)[1]
    else:
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="batch-detect")
        try:
            futures = [pool.submit(worker, i, path) for i, path in enumerate(files)]
            for fut in futures:
                idx, r = fut.result()  # JobCancelled / KeyboardInterrupt 由此向上传播
                results[idx] = r
        except (JobCancelled, KeyboardInterrupt):
            pool.shutdown(wait=False, cancel_futures=True)
            raise JobCancelled() from None
        finally:
            pool.shutdown(wait=False)

    skipped = sum(1 for r in results if r and r.get("skipped"))
    return {
        "results": results,
        "summary": {
            "total": total,
            "detected": sum(1 for r in results if r and r.get("detected")),
            "failed": sum(1 for r in results if r and r.get("error") and not r.get("cancelled")),
            "cancelled": sum(1 for r in results if r and r.get("cancelled")),
            "skipped": skipped,
        },
    }


@app.route("/api/batch/detect", methods=["POST"])
def api_batch_detect():
    data = request.get_json(force=True, silent=True) or {}
    files = data.get("files") or []
    if not files:
        return jsonify({"ok": False, "message": "请至少选择一个视频文件"}), 400
    jid = manager.create("batch_detect", "批量颜色检测", lambda job: run_batch_detect(job, data))
    return jsonify({"ok": True, "job_id": jid})


# ---------------------------------------------------------------------------
# 检测结果缓存查询：前端刷新后自动匹配上次检测结果
# ---------------------------------------------------------------------------
@app.route("/api/detect-cache")
def api_detect_cache():
    """查询检测结果缓存。

    - 带 ``path`` 参数：返回该单个视频的有效缓存（文件变化视为无效）。
    - 不带参数：返回所有有效缓存条目（``{路径: 结果}``），用于启动时整体恢复。
    """
    path = request.args.get("path", "").strip()
    if path:
        if not os.path.isfile(path):
            return jsonify({"ok": False, "message": "文件不存在"}), 404
        result = get_cached_detect_result(path)
        return jsonify({"ok": True, "hit": result is not None,
                        "result": result})
    # 全量查询：仅返回源文件仍存在且未变化的条目，并顺带清理孤儿记录
    return jsonify({"ok": True, "entries": list_valid_detect_cache()})


@app.route("/api/detect-cache/config", methods=["GET"])
def api_detect_cache_config_get():
    """读取缓存配置与当前条目统计。"""
    cfg = _cache_cfg()
    # 统计始终基于全表（即使停用也显示已存数据量，便于用户决策）
    return jsonify({"ok": True, "config": cfg, "count": _count_detect_cache()})


@app.route("/api/detect-cache/config", methods=["POST"])
def api_detect_cache_config_set():
    """更新缓存配置（enabled / limit），写入 settings.json 持久化。"""
    data = request.get_json(force=True, silent=True) or {}
    s = settings_mod.load()
    dc = s.setdefault("detect_cache", {})
    if "enabled" in data:
        dc["enabled"] = bool(data["enabled"])
    if "skip_cached" in data:
        dc["skip_cached"] = bool(data["skip_cached"])
    if "limit" in data:
        try:
            dc["limit"] = max(0, int(data["limit"]))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "message": "条目上限必须是不小于 0 的整数"}), 400
    saved = settings_mod.save(s)
    # 立即按新上限收缩（调小时淘汰最旧条目）
    limit = saved["detect_cache"]["limit"]
    if limit > 0:
        try:
            db = _get_detect_db()
            with _detect_cache_lock, db:
                db.execute(
                    "DELETE FROM detect_cache WHERE key NOT IN ("
                    " SELECT key FROM detect_cache ORDER BY ts DESC LIMIT ?)",
                    (limit,),
                )
        except sqlite3.Error:
            pass
    return jsonify({"ok": True, "config": saved["detect_cache"],
                    "count": _count_detect_cache()})


@app.route("/api/detect-cache/prune", methods=["POST"])
def api_detect_cache_prune():
    """清理失效条目：源文件已删除 / 已修改 / 结果损坏。"""
    removed = prune_detect_cache()
    return jsonify({"ok": True, "removed": len(removed), "paths": removed,
                    "count": _count_detect_cache()})


@app.route("/api/detect-cache/clear", methods=["POST"])
def api_detect_cache_clear():
    """清空全部缓存条目。"""
    removed = clear_detect_cache()
    return jsonify({"ok": True, "removed": removed, "count": 0})


# ---------------------------------------------------------------------------
# Trimmer 任务
# ---------------------------------------------------------------------------
def _parse_start_value(params: dict) -> tuple[int | None, float | None]:
    """从请求参数解析裁剪起点（帧序号 / 时间戳二选一），非法时抛 ValueError。"""
    mode = params.get("start_mode", "frame")
    raw = params.get("start_value")
    if raw is None or str(raw).strip() == "":
        raise ValueError("请填写裁剪起点（可先执行检测或捕获当前帧后点击「设为起点」）")
    if mode == "frame":
        try:
            return int(float(str(raw).strip())), None
        except (TypeError, ValueError):
            raise ValueError("帧序号必须是整数") from None
    if mode == "timestamp":
        try:
            return None, float(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError("时间戳必须是数字（秒）") from None
    raise ValueError("起点方式非法")


def _parse_end_point(params: dict) -> tuple[int | None, float | None]:
    """从请求参数解析裁剪终点（可选），返回 ``(end_frame, end_timestamp)``。

    终点方式为「无」（``end_mode`` 为 null / 缺省 / ``"none"``）且未填写
    终点值时返回 ``(None, None)``，表示保留到片尾（与原有行为一致）；
    方式与数值不匹配、数值非法时抛 ValueError（消息可直接展示）。
    """
    mode = params.get("end_mode")
    raw = params.get("end_value")
    raw_empty = raw is None or str(raw).strip() == ""
    if mode in (None, "", "none"):
        if not raw_empty:
            raise ValueError("终点方式为「无（到片尾）」时不应填写终点值，请清空裁剪终点")
        return None, None
    if raw_empty:
        raise ValueError("已选择终点方式，请填写裁剪终点（可播放定位后点击「设为终点」捕获当前帧）")
    if mode == "frame":
        try:
            return int(float(str(raw).strip())), None
        except (TypeError, ValueError):
            raise ValueError("终点帧序号必须是整数") from None
    if mode == "timestamp":
        try:
            return None, float(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError("终点时间戳必须是数字（秒）") from None
    raise ValueError("终点方式非法")


def build_trimmer_config(path: str, params: dict, output_dir: str,
                         start_override: dict | None = None) -> TrimmerConfig:
    """构建裁剪配置并校验必要参数。

    ``start_override`` 可指定该文件专属的起点（如批处理中来自检测结果），
    形如 ``{"frame": 735}`` 或 ``{"timestamp": 24.5}``；缺省时使用 params 中的统一起点。
    终点（``end_mode`` / ``end_value``）为可选参数组，与起点相互独立；
    params 中不含终点参数（批处理场景）时等价于无终点（保留到片尾）。
    """
    start_override = start_override or {}
    frame = start_override.get("frame")
    timestamp = start_override.get("timestamp")
    if frame is None and timestamp is None:
        frame, timestamp = _parse_start_value(params)
    elif frame is not None:
        try:
            frame = int(float(frame))
        except (TypeError, ValueError):
            raise ValueError("帧序号必须是整数") from None
        timestamp = None
    else:
        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError):
            raise ValueError("时间戳必须是数字（秒）") from None
        frame = None

    end_frame, end_timestamp = _parse_end_point(params)

    suffix = (params.get("suffix") or "_trim").strip()
    return TrimmerConfig(
        input_path=path,
        frame=frame,
        timestamp=timestamp,
        end_frame=end_frame,
        end_timestamp=end_timestamp,
        output_dir=output_dir or None,
        suffix=suffix,
        force=bool(params.get("force", False)),
        reencode=bool(params.get("reencode", False)),
        video_codec=(params.get("video_codec") or "").strip() or None,
        audio_codec=(params.get("audio_codec") or "").strip() or None,
        hw_accel=params.get("hw_accel") or "auto",
        output_mode=OutputMode(params.get("output_mode") or "full"),
    )


def run_trim(job, payload: dict) -> dict:
    """执行视频裁剪（在后台线程中运行）。"""
    path = payload.get("path")
    if not path or not os.path.isfile(path):
        raise ValueError("请选择有效的视频文件")
    params = payload.get("params") or {}
    output_dir = payload.get("output_dir") or ""
    config = build_trimmer_config(path, params, output_dir)

    def cb(pct, sec):
        if job.cancel_event.is_set():
            raise KeyboardInterrupt()
        job.publish("progress", {"percent": float(pct), "seconds": float(sec)})

    result = Trimmer(config).run(
        progress_cb=cb,
        confirm_cb=lambda p: False,  # 覆盖与否由前端 force 参数决定
        cancel_event=job.cancel_event,
    )
    return {
        "output_files": result.output_files,
        "message": result.message,
        "output_mode": result.output_mode.value,
        "timestamp": result.timestamp,
        "frame": result.frame,
        "end_timestamp": result.end_timestamp,
        "end_frame": result.end_frame,
        "cut_duration": result.cut_duration,
        "reencoded": result.reencoded,
        "hw_accelerated": result.hw_accelerated,
    }


def _validate_trim_request(data: dict) -> str | None:
    """同步校验 /api/trim 请求中的终点参数，返回错误消息（合法时为 None）。

    - 终点方式与数值不匹配 / 数值非法：直接报错（400，含行内错误消息）；
    - 终点超出范围、终点 ≤ 起点：探测输入后换算比较报错（400）；
    - 起点参数 / 路径等其他错误：维持现状，交由后台任务通过 SSE 报错。
    """
    params = data.get("params") or {}
    try:
        end_frame, end_timestamp = _parse_end_point(params)
    except ValueError as exc:
        return str(exc)
    if end_frame is None and end_timestamp is None:
        return None
    path = data.get("path") or ""
    if not path or not os.path.isfile(path):
        return None  # 路径错误交由后台任务报错（现状行为）
    try:
        media = trimmer_probe(path)
    except TrimmerError:
        return None  # 探测失败交由后台任务报错（现状行为）
    try:
        end_time = (validate_timestamp(end_timestamp, media, label="终点时间戳")
                    if end_timestamp is not None
                    else validate_frame(end_frame, media, label="终点帧序号"))
    except TrimmerError as exc:
        return exc.message  # 终点超出视频时长 / 总帧数
    try:
        start_frame, start_timestamp = _parse_start_value(params)
    except ValueError:
        return None  # 起点参数错误交由后台任务报错（现状行为）
    try:
        start_time = (validate_timestamp(start_timestamp, media)
                      if start_timestamp is not None
                      else validate_frame(start_frame, media))
    except TrimmerError:
        return None
    if end_time <= start_time:
        return f"终点必须大于起点：终点 {end_time:.6f}s ≤ 起点 {start_time:.6f}s"
    return None


@app.route("/api/trim", methods=["POST"])
def api_trim():
    data = request.get_json(force=True, silent=True) or {}
    # 终点参数同步校验：方式冲突 / end ≤ start / 超范围 → 400 与行内错误消息
    message = _validate_trim_request(data)
    if message:
        return jsonify({"ok": False, "message": message}), 400
    jid = manager.create("trim", "视频裁剪", lambda job: run_trim(job, data))
    return jsonify({"ok": True, "job_id": jid})


def run_batch_trim(job, payload: dict) -> dict:
    """批量执行视频裁剪：文件级并行（线程池），每个文件使用各自的起点。

    并行度见 :func:`_parallel_workers`；结果按入参顺序返回。
    取消（cancel_event）置位后，工作线程在进度回调中抛出异常，
    主线程随即停止等待并向上传播。
    """
    items = payload.get("files") or []  # [{path, frame?|timestamp?, skip?}]
    params = dict(payload.get("params") or {})
    # 批量 Trim 维持现状：以检测结果为起点、无终点（保留到片尾）。
    # 区间终点为单视频交互式能力（逐视频人工定位），不适用于批处理，
    # 因此显式忽略可能随请求传入的终点参数。
    params.pop("end_mode", None)
    params.pop("end_value", None)
    output_dir = payload.get("output_dir") or ""
    total = len(items)
    results: list = [None] * total

    def worker(i: int, item: dict):
        """裁剪单个文件，返回 (index, result)；异常在内部收敛为错误结果。"""
        path = item.get("path") or ""

        def done(result: dict):
            # 单文件完成即发布终态事件，前端立即标记该任务完成，无需等整批结束
            job.publish("file_done", {"index": i + 1, "file": path, "result": result})
            return i, result

        if job.cancel_event.is_set():
            raise JobCancelled()
        # 单文件取消：直接返回取消结果（含排队未开始的文件）
        if (i + 1) in job.file_cancel:
            return done({"file": path, "ok": False, "error": "已取消", "cancelled": True})
        job.publish("progress", {
            "index": i + 1, "total": total, "file": path,
            "percent": round(i / total * 100, 1),
            "percent_file": 0.0,  # 单文件进度从 0 起，避免以"批内位置"虚占进度
            "stage": "pending", "message": "准备裁剪",
        })
        if item.get("skip"):
            return done({"file": path, "ok": False,
                         "error": item.get("reason") or "已跳过"})
        if not path or not os.path.isfile(path):
            return done({"file": path, "ok": False, "error": "文件不存在"})
        start_override = {}
        if item.get("frame") is not None:
            start_override["frame"] = item["frame"]
        elif item.get("timestamp") is not None:
            start_override["timestamp"] = item["timestamp"]

        def cb(pct, sec):
            if job.cancel_event.is_set():
                raise KeyboardInterrupt()
            if (i + 1) in job.file_cancel:
                raise FileCancelled()
            job.publish("progress", {
                "index": i + 1, "total": total, "file": path,
                "percent": round((i + pct / 100) / total * 100, 1),
                "percent_file": float(pct), "seconds": float(sec),
                "stage": "running", "message": "裁剪中",
            })

        try:
            config = build_trimmer_config(path, params, output_dir, start_override=start_override)
            result = Trimmer(config).run(
                progress_cb=cb,
                confirm_cb=lambda p: False,  # 覆盖与否由前端 force 参数决定
                cancel_event=job.cancel_event,
            )
            return done({
                "file": path, "ok": True,
                "output_files": result.output_files,
                "message": result.message,
            })
        except FileCancelled:
            return done({"file": path, "ok": False, "error": "已取消", "cancelled": True})
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不中断整体
            return done({"file": path, "ok": False, "error": str(exc)})

    workers = _parallel_workers()
    if total <= 1 or workers <= 1:
        # 单文件 / 禁用并行：退化为同步执行
        for i, item in enumerate(items):
            results[i] = worker(i, item)[1]
    else:
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="batch-trim")
        try:
            futures = [pool.submit(worker, i, item) for i, item in enumerate(items)]
            for fut in futures:
                idx, r = fut.result()  # JobCancelled / KeyboardInterrupt 由此向上传播
                results[idx] = r
        except (JobCancelled, KeyboardInterrupt):
            pool.shutdown(wait=False, cancel_futures=True)
            raise JobCancelled() from None
        finally:
            pool.shutdown(wait=False)

    return {
        "results": results,
        "summary": {
            "total": total,
            "ok": sum(1 for r in results if r and r.get("ok")),
            "failed": sum(1 for r in results if r and r.get("error") and not r.get("cancelled")),
            "cancelled": sum(1 for r in results if r and r.get("cancelled")),
        },
    }


@app.route("/api/batch/trim", methods=["POST"])
def api_batch_trim():
    data = request.get_json(force=True, silent=True) or {}
    items = data.get("files") or []
    if not items:
        return jsonify({"ok": False, "message": "请至少选择一个视频文件"}), 400
    jid = manager.create("batch_trim", "批量视频裁剪", lambda job: run_batch_trim(job, data))
    return jsonify({"ok": True, "job_id": jid})


# ---------------------------------------------------------------------------
# 任务状态 / SSE / 取消
# ---------------------------------------------------------------------------
@app.route("/api/jobs/<jid>/events")
def job_events(jid):
    """SSE 事件流：started / progress / done / error / cancelled。"""
    job = manager.get(jid)
    if job is None:
        return jsonify({"ok": False, "message": "任务不存在"}), 404

    def gen():
        yield "retry: 3000\n\n"
        for item in job.drain_queue():
            yield sse_payload(item)
            if item["type"] in ("done", "error", "cancelled"):
                return
        while True:
            # 任务仍在运行时以 15s 为周期发 keepalive；任务已结束则只做 1s 的
            # 短等待——终态事件若还在队列里，毫秒级即可取到，不会被漏掉。
            try:
                item = job._queue.get(timeout=15 if job.status == "running" else 1)
                yield sse_payload(item)
                if item["type"] in ("done", "error", "cancelled"):
                    return
            except queue.Empty:
                # 队列已空：若任务也已结束（如断线重连后终态事件已被上一连接消费），
                # 结束事件流并交由前端 pollSettled 兜底确认终态，
                # 避免 keepalive 空转、任务按钮永久卡在"运行中"。
                if job.status != "running":
                    break
                yield ": keepalive\n\n"

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/jobs/<jid>")
def job_status(jid):
    job = manager.get(jid)
    if job is None:
        return jsonify({"ok": False, "message": "任务不存在"}), 404
    return jsonify({
        "ok": True, "id": job.id, "kind": job.kind, "name": job.name,
        "status": job.status, "result": job.result, "error": job.error,
    })


@app.route("/api/jobs/<jid>/cancel", methods=["POST"])
def job_cancel(jid):
    ok = manager.cancel(jid)
    if not ok:
        return jsonify({"ok": False, "message": "任务不存在"}), 404
    return jsonify({"ok": True})


@app.route("/api/jobs/<jid>/cancel-file", methods=["POST"])
def job_cancel_file(jid):
    """取消批量任务中的单个文件（1 基 index），不影响同批其他文件。"""
    job = manager.get(jid)
    if job is None:
        return jsonify({"ok": False, "message": "任务不存在"}), 404
    data = request.get_json(force=True, silent=True) or {}
    index = data.get("index")
    if not isinstance(index, int) or index < 1:
        return jsonify({"ok": False, "message": "index 必须为正整数"}), 400
    job.file_cancel.add(index)
    return jsonify({"ok": True})


if __name__ == "__main__":
    host = os.environ.get("WEBUI_HOST", "127.0.0.1")
    port = int(os.environ.get("WEBUI_PORT", "8765"))
    print(f"TaoMusicEdit Web UI: http://{host}:{port}")
    print(f"默认工作区: {STATE['workspace']}")
    app.run(host=host, port=port, threaded=True, debug=False)
