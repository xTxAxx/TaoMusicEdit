# -*- coding: utf-8 -*-
"""可复用的视频颜色检测模块。

用法::

    from detector import VideoColorDetector

    frame, time = VideoColorDetector().detect("video.mp4")

模块组成：
- :mod:`detector.config`: 全部可配置参数
- :mod:`detector.core`: 核心检测算法与主检测器
- :mod:`detector.utils`: FFmpeg 帧提取、颜色转换、日志等工具
- :mod:`detector.tests`: 单元 / 集成 / 性能测试
"""
from .config import DetectorConfig, DEFAULT_TARGET_COLOR, DEFAULT_POINTS
from .core import (
    VideoColorDetector,
    Detection,
    DetectionDetails,
    FrameMatcher,
    FrameMatch,
    PointMatch,
    DetectorError,
    VideoNotFoundError,
    UnsupportedFormatError,
    FFmpegError,
    VideoDecodeError,
    ConfigError,
)
from .utils.color import hex_to_rgb, rgb_to_hex, color_confidence, color_match

__version__ = "1.0.0"

__all__ = [
    "VideoColorDetector",
    "Detection",
    "DetectionDetails",
    "DetectorConfig",
    "FrameMatcher",
    "FrameMatch",
    "PointMatch",
    "DetectorError",
    "VideoNotFoundError",
    "UnsupportedFormatError",
    "FFmpegError",
    "VideoDecodeError",
    "ConfigError",
    "DEFAULT_TARGET_COLOR",
    "DEFAULT_POINTS",
    "hex_to_rgb",
    "rgb_to_hex",
    "color_confidence",
    "color_match",
]
