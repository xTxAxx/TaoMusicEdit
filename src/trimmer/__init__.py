# -*- coding: utf-8 -*-
"""
trimmer —— 基于 FFmpeg 的高精度视频裁剪工具包。

特性:
- 按帧序号或时间戳指定起点（严格二选一），可选终点（end_timestamp /
  end_frame 二选一）：终点缺省时保留到片尾（原有行为），提供终点时保留
  [起点, 终点) 区间（区间裁剪）；起点与终点方式可独立选择
- 默认流复制（不重编码）快速裁剪，可选重编码并自动匹配原视频参数
- 自动检测 GPU 并优先硬件加速，GPU 失败自动回退软件编码
- 支持完整视频 / 无声视频 / 纯音频 / 分离音视频四种输出模式
- 覆盖确认机制、防误覆盖原文件、进度反馈与完善错误处理

Python API 用法:
    from trimmer import OutputMode, Trimmer, TrimmerConfig

    # 保留起点到片尾
    config = TrimmerConfig(input_path="input.mp4", timestamp=24.9)
    result = Trimmer(config).run()
    print(result.output_files)

    # 区间裁剪：保留 [24.9s, 3600s)
    config = TrimmerConfig(input_path="input.mp4", timestamp=24.9,
                           end_timestamp=3600.0)
    result = Trimmer(config).run()
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
