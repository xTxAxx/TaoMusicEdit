# -*- coding: utf-8 -*-
"""工具函数子包：颜色、日志、FFmpeg 帧提取等。"""
from .color import (
    hex_to_rgb,
    rgb_to_hex,
    color_confidence,
    color_match,
    pixel_is_match,
    MAX_RGB_DISTANCE,
)
from .logger import get_logger
from .ffmpeg import (
    probe_video,
    read_frame,
    read_frame_range,
    VideoInfo,
    FrameExtractor,
    FFmpegExtractor,
    OpenCVExtractor,
    create_extractor,
)

__all__ = [
    "hex_to_rgb",
    "rgb_to_hex",
    "color_confidence",
    "color_match",
    "pixel_is_match",
    "MAX_RGB_DISTANCE",
    "get_logger",
    "probe_video",
    "read_frame",
    "read_frame_range",
    "VideoInfo",
    "FrameExtractor",
    "FFmpegExtractor",
    "OpenCVExtractor",
    "create_extractor",
]
