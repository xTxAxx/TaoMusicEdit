# -*- coding: utf-8 -*-
"""FFmpeg 底层封装。

职责:
- 定位 ffmpeg / ffprobe 可执行文件（支持环境变量覆盖）
- 探测 ffmpeg 支持的编码器，用于 GPU 硬件加速自动检测
- 执行 ffmpeg 命令，解析 ``-progress`` 输出并回调进度
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import threading
from typing import Callable, List, Optional

from trimmer.core.errors import FFmpegExecutionError, FFmpegNotFoundError

#: ffmpeg / ffprobe 可执行文件名（可通过环境变量 TRIM_FFMPEG / TRIM_FFPROBE 覆盖）
FFMPEG_EXECUTABLE = "ffmpeg"
FFPROBE_EXECUTABLE = "ffprobe"

#: 各编码家族对应的 GPU 硬件编码器候选（按流行程度排序）
GPU_ENCODER_PRIORITY = {
    "h264": ["h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox", "h264_mf"],
    "hevc": ["hevc_nvenc", "hevc_qsv", "hevc_amf", "hevc_videotoolbox", "hevc_mf"],
}

#: 显卡厂商 → 优先匹配的编码器子串（AMD 用户优先使用 amf 编码器）
VENDOR_ENCODER_PREFIX = {
    "nvidia": "nvenc",
    "amd": "amf",
    "intel": "qsv",
}

_encoders_cache: Optional[set] = None
_vendor_cache: Optional[str] = None


def _run_capture(cmd: List[str]) -> str:
    """执行命令并返回 stdout + stderr 文本；失败返回空串。"""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or "") + (proc.stderr or "")


def _detect_gpu_vendor_impl() -> Optional[str]:
    """按操作系统探测显卡厂商：'nvidia' / 'amd' / 'intel' / None（未知）。"""
    system = platform.system()
    text = ""
    if system == "Windows":
        # 优先 PowerShell CIM，失败则回退 wmic
        for cmd in (
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
            ["wmic", "path", "win32_VideoController", "get", "name"],
        ):
            text = _run_capture(cmd)
            if text.strip():
                break
    elif system == "Linux":
        text = _run_capture(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        if not text.strip():
            text = _run_capture(["lspci", "-nn"])
    elif system == "Darwin":
        text = _run_capture(["system_profiler", "SPDisplaysDataType"])

    lower = text.lower()
    # 注意检查顺序：NVIDIA/AMD 优先，避免 NVIDIA+Intel 双显卡被误判为 Intel
    if any(k in lower for k in ("nvidia", "geforce", "quadro", "tesla", "rtx", "gtx")):
        return "nvidia"
    if any(k in lower for k in ("amd", "ati", "radeon", "advanced micro devices")):
        return "amd"
    if any(k in lower for k in ("intel", "arc", "uhd graphics", "hd graphics")):
        return "intel"
    return None


def detect_gpu_vendor() -> Optional[str]:
    """返回系统显卡厂商（结果缓存）。"""
    global _vendor_cache
    if _vendor_cache is None:
        _vendor_cache = _detect_gpu_vendor_impl()
    return _vendor_cache


def resolve_executable(default_name: str, env_key: str) -> str:
    """定位可执行文件：优先使用环境变量指定的路径，其次搜索 PATH。"""
    candidate = os.environ.get(env_key, default_name)
    path = shutil.which(candidate)
    if path is None and os.path.isfile(candidate):
        path = candidate
    if not path:
        raise FFmpegNotFoundError(
            f"未找到可执行文件 '{default_name}'。请安装 FFmpeg 并加入 PATH，"
            f"或通过环境变量 {env_key} 指定完整路径。"
        )
    return path


def find_ffmpeg() -> str:
    """返回 ffmpeg 可执行文件路径。"""
    return resolve_executable(FFMPEG_EXECUTABLE, "TRIM_FFMPEG")


def find_ffprobe() -> str:
    """返回 ffprobe 可执行文件路径。"""
    return resolve_executable(FFPROBE_EXECUTABLE, "TRIM_FFPROBE")


def detect_available_encoders() -> set:
    """返回当前 ffmpeg 支持的编码器名称集合（结果缓存，避免重复调用）。"""
    global _encoders_cache
    if _encoders_cache is not None:
        return _encoders_cache
    ffmpeg = find_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    encoders = set()
    # 编码器列表形如: " V....D h264_nvenc  NVIDIA NVENC H.264 encoder (codec h264)"
    pattern = re.compile(r"^\s*[VA].....\s+([A-Za-z0-9_]+)")
    for line in proc.stdout.splitlines():
        match = pattern.match(line)
        if match:
            encoders.add(match.group(1))
    _encoders_cache = encoders
    return encoders


def detect_gpu_encoder(codec_family: str, available: Optional[set] = None,
                       vendor: Optional[str] = None, exclude: Optional[set] = None) -> Optional[str]:
    """为指定编码家族挑选一个可用的 GPU 编码器；不可用时返回 None。

    参数:
        codec_family: 编码家族（'h264' / 'hevc'）。
        available: ffmpeg 已编译编码器集合（默认自动探测）。
        vendor: 系统显卡厂商（'nvidia' / 'amd' / 'intel'），用于优先匹配对应编码器。
        exclude: 需跳过的编码器集合（如已经执行失败的编码器）。
    """
    if available is None:
        available = detect_available_encoders()
    exclude = set(exclude or ())
    candidates = GPU_ENCODER_PRIORITY.get(codec_family, [])
    ordered = list(candidates)
    if vendor:
        preferred = VENDOR_ENCODER_PREFIX.get(vendor)
        if preferred:
            # 将本厂商对应的编码器排到最前
            ordered = ([n for n in candidates if preferred in n]
                       + [n for n in candidates if preferred not in n])
    for name in ordered:
        if name not in exclude and name in available:
            return name
    return None


class FFmpegRunner:
    """执行 ffmpeg 命令，解析 ``-progress`` 输出并回调进度。"""

    def __init__(self, ffmpeg: Optional[str] = None):
        self.ffmpeg = ffmpeg or find_ffmpeg()

    @staticmethod
    def _parse_progress_line(line: str) -> Optional[float]:
        """从 progress 输出行解析出已经处理的时间（秒）；无法解析返回 None。"""
        if line.startswith("out_time_us="):
            try:
                return int(line.split("=", 1)[1]) / 1_000_000.0
            except ValueError:
                return None
        return None

    def run(self, args: List[str], total_duration: Optional[float] = None,
            progress_cb: Optional[Callable[[float, float], None]] = None,
            cancel_event: Optional[threading.Event] = None) -> int:
        """运行 ffmpeg。

        参数:
            args: 不含可执行文件的完整参数列表。
            total_duration: 期望输出总时长（秒），用于计算进度百分比。
            progress_cb: 进度回调 ``(percent, seconds)``。
            cancel_event: 可选的取消事件；置位时终止 ffmpeg 进程并抛出
                ``KeyboardInterrupt``（用于 Web UI 等场景的中断控制）。
        返回:
            ffmpeg 进程退出码。
        异常:
            FFmpegExecutionError: ffmpeg 执行失败。
            KeyboardInterrupt: 取消事件被置位，任务被用户中断。
        """
        cmd = [self.ffmpeg] + list(args)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )

        # 单独线程排空 stderr，避免管道写满导致死锁
        def _drain_stderr():
            assert proc.stderr is not None
            for _ in iter(proc.stderr.readline, ""):
                pass
            proc.stderr.close()

        thread = threading.Thread(target=_drain_stderr, daemon=True)
        thread.start()

        assert proc.stdout is not None
        cancelled = False
        try:
            for line in proc.stdout:
                if cancel_event is not None and cancel_event.is_set():
                    proc.kill()
                    cancelled = True
                    break
                seconds = self._parse_progress_line(line.strip())
                if seconds is not None and total_duration and progress_cb:
                    pct = max(0.0, min(100.0, seconds / total_duration * 100.0))
                    progress_cb(pct, seconds)
        finally:
            proc.stdout.close()

        thread.join()
        return_code = proc.wait()
        if cancelled:
            raise KeyboardInterrupt("用户取消")
        if return_code != 0:
            raise FFmpegExecutionError(f"ffmpeg 执行失败（退出码 {return_code}）。")
        return return_code
