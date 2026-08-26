# -*- coding: utf-8 -*-
"""
trimmer —— 基于 FFmpeg 的高精度视频裁剪工具包。

特性:
- 按帧序号或时间戳从指定位置开始保留视频（裁剪掉开头部分），两者严格二选一校验
- 默认流复制（不重编码）快速裁剪，可选重编码并自动匹配原视频参数
- 自动检测 GPU 并优先硬件加速，GPU 失败自动回退软件编码
- 支持完整视频 / 无声视频 / 纯音频 / 分离音视频四种输出模式
- 覆盖确认机制、防误覆盖原文件、进度反馈与完善错误处理

Python API 用法:
    from trimmer import OutputMode, Trimmer, TrimmerConfig

    config = TrimmerConfig(input_path="input.mp4", timestamp=24.9)
    result = Trimmer(config).run()
    print(result.output_files)
"""

from trimmer.core.errors import (
    FFmpegExecutionError,
    FFmpegNotFoundError,
    InputNotFoundError,
    NoStreamError,
    OutputExistsError,
    OverwriteInputError,
    TrimmerError,
    UnsupportedFormatError,
    ValidationError,
)
from trimmer.core.trimmer import OutputMode, Trimmer, TrimmerConfig, TrimmerResult

__version__ = "1.0.0"

__all__ = [
    "OutputMode",
    "Trimmer",
    "TrimmerConfig",
    "TrimmerResult",
    "TrimmerError",
    "FFmpegNotFoundError",
    "FFmpegExecutionError",
    "InputNotFoundError",
    "UnsupportedFormatError",
    "NoStreamError",
    "ValidationError",
    "OutputExistsError",
    "OverwriteInputError",
]
