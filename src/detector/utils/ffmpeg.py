# -*- coding: utf-8 -*-
"""FFmpeg / OpenCV 帧提取工具。

设计要点
--------
- 通过 ``ffprobe`` 获取视频元数据（分辨率 / 帧率 / 时长 / 帧数 / 编码）。
- 通过 ``ffmpeg`` 以「输入定位 + 单帧解码」的方式抽取指定时间戳的帧，
  避免整段解码，显著提升大视频的抽帧效率：
  ``ffmpeg -ss <t> -i <path> -frames:v 1 -f rawvideo -pix_fmt bgr24 -``
- 支持 GPU 硬件解码（NVIDIA CUDA / AMD D3D11VA·DXVA2 / Intel QSV 等），
  不可用时自动降级 CPU 软解，保证任何环境下都能工作。
- 提供基于 OpenCV 的替代提取器，作为 FFmpeg 不可用时的回退方案。
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from .logger import get_logger
from ..core.errors import FFmpegError, VideoDecodeError

logger = get_logger(__name__)

#: 各平台「自动」模式下的硬解 API 尝试顺序：
#: Windows 上 AMD 显卡走 d3d11va / dxva2，N 卡为 cuda，Intel 核显为 qsv；
#: 非 Windows 平台按 macOS / Linux 惯例排列。任何一个成功即采用。
_HWACCEL_CANDIDATES_WINDOWS = ("d3d11va", "dxva2", "cuda", "qsv")
_HWACCEL_CANDIDATES_UNIX = ("vaapi", "cuda", "qsv")
_HWACCEL_CANDIDATES_DARWIN = ("videotoolbox",)

#: 允许用户显式指定的全部取值（含关闭项）
HWACCEL_CHOICES = (
    "auto", "none",
    "d3d11va", "dxva2",   # Windows：系统级解码，AMD/N/A 卡通用
    "cuda",               # NVIDIA NVDEC
    "qsv",                # Intel Quick Sync
    "vaapi",              # Linux
    "videotoolbox",       # macOS
)


def _hwaccel_candidates() -> tuple:
    """按当前操作系统返回自动探测候选列表。"""
    system = platform.system()
    if system == "Windows":
        return _HWACCEL_CANDIDATES_WINDOWS
    if system == "Darwin":
        return _HWACCEL_CANDIDATES_DARWIN
    return _HWACCEL_CANDIDATES_UNIX


@dataclass
class VideoInfo:
    """视频元信息。"""

    path: str
    width: int
    height: int
    fps: float
    duration: float
    nb_frames: int
    has_video: bool = True
    codec: str = ""
    pix_fmt: str = ""


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    """执行外部命令并捕获输出，失败时抛 FFmpegError。"""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FFmpegError(f"找不到可执行文件: {cmd[0]!r}，请检查 ffmpeg 安装或配置路径") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()[-2000:]
        raise FFmpegError(f"命令执行失败: {' '.join(cmd)}\n{err}")
    return proc


def _test_hwaccel(ffmpeg_path: str, api: str, info: VideoInfo) -> bool:
    """用指定硬解 API 对目标视频实际解 1 帧，验证该环境是否真正可用。

    只看返回码与产出字节数——驱动缺失 / FFmpeg 未编入该后端 / 该编码不受
    支持都会在这里失败，从而安全地尝试下一个候选或软解。
    """
    cmd = [
        ffmpeg_path, "-loglevel", "error",
        "-hwaccel", api,
        "-i", info.path,
        "-frames:v", "1", "-an", "-sn", "-dn",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, check=False,
        )
    except FileNotFoundError:
        return False
    want = info.width * info.height * 3
    return proc.returncode == 0 and len(proc.stdout) >= want


def resolve_hwaccel(requested: str, info: VideoInfo, ffmpeg_path: str = "ffmpeg") -> str:
    """把用户请求的硬解策略解析为实际可用的 API 名称（失败自动降级）。

    :param requested: "auto" / "none" / 具体后端名（cuda、d3d11va 等）
    :param info: 目标视频元信息（探测用真实视频更可靠）
    :return: 实际生效的后端名；软解返回 "none"
    """
    req = (requested or "none").lower()
    if req in ("", "none", "cpu", "sw"):
        return "none"
    candidates = _hwaccel_candidates() if req == "auto" else (req,)
    for api in candidates:
        if _test_hwaccel(ffmpeg_path, api, info):
            logger.info("GPU 硬解启用: %s (%s)", api, os.path.basename(info.path))
            return api
        logger.warning(
            "硬解后端 %s 对该视频不可用，尝试下一个候选…", api
        )
    logger.warning("GPU 硬解均不可用，已回退 CPU 软解: %s", info.path)
    return "none"


def _decode_args(hwaccel: str) -> List[str]:
    """按生效的硬解后端生成输入侧参数（须位于 -i 之前）。"""
    if hwaccel and hwaccel != "none":
        # 不指定 -hwaccel_output_format：解码帧自动回传系统内存后再转 bgr24，
        # 对下游保持与软解完全一致的输出格式。
        return ["-hwaccel", hwaccel]
    return []


def probe_video(video_path: str, ffprobe_path: str = "ffprobe") -> VideoInfo:
    """使用 ffprobe 读取视频元信息。

    :raises FFmpegError: ffprobe 调用失败或无法解析
    """
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,duration,nb_frames,codec_name,pix_fmt",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path,
    ]
    proc = _run(cmd)
    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise FFmpegError(f"ffprobe 输出解析失败: {video_path}") from exc

    streams = data.get("streams") or []
    info = VideoInfo(
        path=video_path,
        width=0,
        height=0,
        fps=0.0,
        duration=0.0,
        nb_frames=0,
        has_video=False,
    )
    if streams:
        s = streams[0]
        info.has_video = True
        info.width = int(s.get("width") or 0)
        info.height = int(s.get("height") or 0)
        info.codec = s.get("codec_name") or ""
        info.pix_fmt = s.get("pix_fmt") or ""
        r_fps = _parse_fps(s.get("r_frame_rate")) or _parse_fps(s.get("avg_frame_rate")) or 0.0
        info.fps = round(r_fps, 6)
        try:
            info.nb_frames = int(float(s.get("nb_frames") or 0))
        except (TypeError, ValueError):
            info.nb_frames = 0
        dur = s.get("duration")
        if not dur:
            dur = (data.get("format") or {}).get("duration")
        info.duration = float(dur or 0.0)
    return info


def _parse_fps(rate: Optional[str]) -> Optional[float]:
    if not rate:
        return None
    try:
        if "/" in rate:
            num, den = rate.split("/")
            den = float(den)
            return float(num) / den if den else None
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return None


def read_frame(
    video_path: str,
    timestamp: float,
    width: int,
    height: int,
    ffmpeg_path: str = "ffmpeg",
    pix_fmt: str = "bgr24",
    hwaccel: str = "none",
) -> Optional[np.ndarray]:
    """抽取指定时间戳的一帧，返回 BGR 格式 (H, W, 3) 数组；越界返回 None。

    使用 ``-ss`` 输入定位（精确到目标帧），仅解码目标附近帧，效率高。
    :param hwaccel: 硬解后端（cuda / d3d11va / dxva2 / qsv…），"none" 为 CPU 软解
    """
    ts = max(0.0, float(timestamp))
    cmd = [
        ffmpeg_path,
        "-loglevel", "error",
        *_decode_args(hwaccel),
        "-ss", f"{ts:.6f}",
        "-i", video_path,
        "-frames:v", "1",
        "-an", "-sn", "-dn",
        "-f", "rawvideo",
        "-pix_fmt", pix_fmt,
        "-",
    ]
    proc = _run(cmd)
    raw = proc.stdout
    frame_bytes = width * height * 3
    if len(raw) < frame_bytes:
        return None
    frame = np.frombuffer(raw[:frame_bytes], dtype=np.uint8).reshape((height, width, 3))
    return frame.copy()


def read_frame_range(
    video_path: str,
    start_ts: float,
    end_ts: float,
    width: int,
    height: int,
    ffmpeg_path: str = "ffmpeg",
    pix_fmt: str = "bgr24",
    hwaccel: str = "none",
) -> List[np.ndarray]:
    """抽取 [start_ts, end_ts] 区间内的全部帧，返回 BGR 帧列表。

    一次 FFmpeg 调用顺序解码区间内所有帧，适合逐帧扫描。
    :param hwaccel: 硬解后端（cuda / d3d11va / dxva2 / qsv…），"none" 为 CPU 软解
    """
    if end_ts <= start_ts:
        return []
    cmd = [
        ffmpeg_path,
        "-loglevel", "error",
        *_decode_args(hwaccel),
        "-ss", f"{max(0.0, float(start_ts)):.6f}",
        "-to", f"{max(0.0, float(end_ts)):.6f}",
        "-i", video_path,
        "-an", "-sn", "-dn",
        "-f", "rawvideo",
        "-pix_fmt", pix_fmt,
        "-",
    ]
    proc = _run(cmd)
    raw = proc.stdout
    frame_bytes = width * height * 3
    if not raw:
        return []
    frames = []
    for i in range(0, len(raw) - frame_bytes + 1, frame_bytes):
        frame = np.frombuffer(raw[i : i + frame_bytes], dtype=np.uint8).reshape(
            (height, width, 3)
        )
        frames.append(frame.copy())
    return frames


# ---------------------------------------------------------------------------
# 帧提取器（统一接口，供检测器选用）
# ---------------------------------------------------------------------------


class FrameExtractor:
    """帧提取器统一接口。"""

    def __init__(self, video_info: VideoInfo, ffmpeg_path: str = "ffmpeg"):
        self.info = video_info
        self.ffmpeg_path = ffmpeg_path

    def read(self, timestamp: float) -> Optional[np.ndarray]:
        """读取指定时间戳（秒）的一帧 BGR 数组，越界返回 None。"""
        raise NotImplementedError

    def read_range(self, start_ts: float, end_ts: float) -> List[np.ndarray]:
        """读取 [start_ts, end_ts] 区间内的所有帧。"""
        raise NotImplementedError

    def close(self) -> None:
        """释放资源。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FFmpegExtractor(FrameExtractor):
    """基于 FFmpeg 子进程调用的提取器（默认，精确且高效）。

    支持可选 GPU 硬解：构造时解析 ``hwaccel`` 策略（auto 时逐个探测平台候选），
    实际可用后端记录在 :attr:`hwaccel_active`；不可用时自动降级软解。
    """

    def __init__(self, video_info: VideoInfo, ffmpeg_path: str = "ffmpeg",
                 hwaccel: str = "none"):
        super().__init__(video_info, ffmpeg_path)
        # 解析一次即可：对同一视频，硬解可用性在整个会话内不会变化
        self.hwaccel_active: str = resolve_hwaccel(hwaccel, video_info, ffmpeg_path)

    def read(self, timestamp: float) -> Optional[np.ndarray]:
        try:
            return read_frame(
                self.info.path,
                timestamp,
                self.info.width,
                self.info.height,
                ffmpeg_path=self.ffmpeg_path,
                hwaccel=self.hwaccel_active,
            )
        except FFmpegError as exc:
            logger.warning("读取帧失败 t=%.3fs: %s", timestamp, exc)
            return None

    def read_range(self, start_ts: float, end_ts: float) -> List[np.ndarray]:
        try:
            return read_frame_range(
                self.info.path,
                start_ts,
                end_ts,
                self.info.width,
                self.info.height,
                ffmpeg_path=self.ffmpeg_path,
                hwaccel=self.hwaccel_active,
            )
        except FFmpegError as exc:
            logger.warning("读取帧区间失败 [%.3f, %.3f]: %s", start_ts, end_ts, exc)
            return []


class OpenCVExtractor(FrameExtractor):
    """基于 OpenCV VideoCapture 的提取器（回退方案）。"""

    def __init__(self, video_info: VideoInfo, ffmpeg_path: str = "ffmpeg"):
        super().__init__(video_info, ffmpeg_path)
        import cv2  # 延迟导入，降低非必要依赖

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(video_info.path)
        if not self._cap.isOpened():
            raise VideoDecodeError(f"OpenCV 无法打开视频: {video_info.path}")

    def read(self, timestamp: float) -> Optional[np.ndarray]:
        ts = max(0.0, float(timestamp))
        if ts > self.info.duration:
            return None
        if not self._cap.set(self._cv2.CAP_PROP_POS_MSEC, ts * 1000.0):
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        return frame

    def read_range(self, start_ts: float, end_ts: float) -> List[np.ndarray]:
        frames = []
        ts = max(0.0, float(start_ts))
        if ts > self.info.duration:
            return frames
        self._cap.set(self._cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
        while ts < end_ts:
            ok, frame = self._cap.read()
            if not ok or frame is None:
                break
            frames.append(frame)
            ts += 1.0 / self.info.fps if self.info.fps else 1.0 / 30.0
        return frames

    def close(self) -> None:
        if getattr(self, "_cap", None) is not None:
            self._cap.release()
            self._cap = None


def create_extractor(
    video_info: VideoInfo, kind: str = "ffmpeg", ffmpeg_path: str = "ffmpeg",
    hwaccel: str = "none",
) -> FrameExtractor:
    """按类型创建帧提取器。"""
    if kind == "opencv":
        # OpenCV 后端内部自带解码加速逻辑，hwaccel 选项不适用
        return OpenCVExtractor(video_info, ffmpeg_path)
    return FFmpegExtractor(video_info, ffmpeg_path, hwaccel=hwaccel)
