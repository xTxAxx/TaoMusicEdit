# -*- coding: utf-8 -*-
"""核心检测算法子包。"""
from .detector import VideoColorDetector, Detection, DetectionDetails
from .matcher import FrameMatcher, FrameMatch, PointMatch
from .errors import (
    DetectorError,
    VideoNotFoundError,
    UnsupportedFormatError,
    FFmpegError,
    VideoDecodeError,
    ConfigError,
)

__all__ = [
    "VideoColorDetector",
    "Detection",
    "DetectionDetails",
    "FrameMatcher",
    "FrameMatch",
    "PointMatch",
    "DetectorError",
    "VideoNotFoundError",
    "UnsupportedFormatError",
    "FFmpegError",
    "VideoDecodeError",
    "ConfigError",
]
