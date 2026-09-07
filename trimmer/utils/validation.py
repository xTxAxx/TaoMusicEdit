# -*- coding: utf-8 -*-
"""参数校验工具。

负责输入路径、裁剪参数（起点时间戳 / 帧序号二选一，终点可选且同样
二选一）、时间范围、帧范围以及输出模式组合的严格校验。
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

from trimmer.core.errors import (
    InputNotFoundError,
    UnsupportedFormatError,
    ValidationError,
)
from trimmer.core.probe import MediaInfo

#: 支持的输入视频扩展名
SUPPORTED_EXTENSIONS = {
    ".mp4", ".avi", ".mkv", ".mov", ".webm", ".flv", ".m4v",
    ".mpeg", ".mpg", ".ts", ".mts", ".m2ts", ".wmv", ".3gp", ".3g2", ".ogv",
}


def validate_input_path(path: str) -> str:
    """校验输入视频路径，返回其绝对路径。

    异常:
        ValidationError: 路径为空或不是文件。
        InputNotFoundError: 文件不存在。
        UnsupportedFormatError: 扩展名不受支持。
    """
    if not path:
        raise ValidationError("必须提供输入视频文件路径。")
    if not os.path.exists(path):
        raise InputNotFoundError(f"输入文件不存在: {path}")
    if not os.path.isfile(path):
        raise ValidationError(f"输入路径不是文件: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext and ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"暂不支持的文件格式 '{ext}'，支持: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    return os.path.abspath(path)


def validate_cut_params(timestamp: Optional[float], frame: Optional[int]) -> Tuple[str, object]:
    """校验裁剪参数：时间戳与帧序号必须恰好二选一。

    返回 ``(mode, value)``，其中 mode 为 ``"timestamp"`` 或 ``"frame"``。
    """
    has_ts = timestamp is not None
    has_frame = frame is not None
    if has_ts and has_frame:
        raise ValidationError("--timestamp 与 --frame 不能同时使用，必须二选一。")
    if not has_ts and not has_frame:
        raise ValidationError("必须提供裁剪参数：--timestamp 或 --frame（二选一）。")
    return ("timestamp", timestamp) if has_ts else ("frame", frame)


def validate_end_params(end_timestamp: Optional[float],
                        end_frame: Optional[int]) -> Tuple[Optional[str], Optional[object]]:
    """校验终点参数：时间戳与帧序号不可同时提供（均可缺省）。

    返回 ``(mode, value)``；两者均缺省时返回 ``(None, None)``，表示保留到片尾
    （与原有仅起点裁剪的行为完全一致）。
    """
    has_ts = end_timestamp is not None
    has_frame = end_frame is not None
    if has_ts and has_frame:
        raise ValidationError("--end-timestamp 与 --end-frame 不能同时使用，必须二选一。")
    if has_ts:
        return ("timestamp", end_timestamp)
    if has_frame:
        return ("frame", end_frame)
    return (None, None)


def validate_end_after_start(end_time: float, start_time: float) -> float:
    """校验终点（换算后的时间戳）必须大于起点（换算后的时间戳）。"""
    if end_time <= start_time:
        raise ValidationError(
            f"终点必须大于起点：终点 {end_time:.6f}s ≤ 起点 {start_time:.6f}s。"
        )
    return end_time


def validate_timestamp(value: float, media: MediaInfo, *, label: str = "时间戳") -> float:
    """校验时间戳（秒），要求大于 0 且不超过视频总时长。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label}必须是数字（秒）。")
    if value <= 0:
        raise ValidationError(f"{label}必须大于 0。")
    if media.duration and value > media.duration:
        raise ValidationError(f"{label} {value:.6f}s 超出视频总时长 {media.duration:.3f}s。")
    return float(value)


def validate_frame(value: int, media: MediaInfo, *, label: str = "帧序号") -> float:
    """校验帧序号（从 1 计数）并返回其对应的起始时间（秒）。

    第 N 帧对应的起始时间为 ``(N - 1) / fps``，即保留第 N 帧及之后的帧，
    裁剪掉第 1 ~ N-1 帧。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{label}必须是整数。")
    if value < 1:
        raise ValidationError(f"{label}必须 >= 1。")
    if not media.fps:
        raise ValidationError("无法解析输入帧率，不能使用帧序号裁剪。")
    total = media.nb_frames
    if total is None and media.duration:
        total = int(round(media.duration * media.fps))
    if total is not None and value > total:
        raise ValidationError(f"{label} {value} 超出视频总帧数 {total}。")
    return (value - 1) / media.fps
