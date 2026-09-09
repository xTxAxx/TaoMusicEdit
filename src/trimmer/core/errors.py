# -*- coding: utf-8 -*-
"""trimmer 自定义异常体系。

所有异常继承自 :class:`TrimmerError`，携带稳定的错误码（code）
与建议的进程退出码（exit_code），便于上层统一处理与展示。
"""


class TrimmerError(Exception):
    """所有 trimmer 异常的基类。"""

    exit_code = 1

    def __init__(self, message, *, code="TRIM_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


class FFmpegNotFoundError(TrimmerError):
    """找不到 ffmpeg / ffprobe 可执行文件。"""

    def __init__(self, message):
        super().__init__(message, code="FFMPEG_NOT_FOUND")


class InputNotFoundError(TrimmerError):
    """输入视频文件不存在。"""

    def __init__(self, message):
        super().__init__(message, code="INPUT_NOT_FOUND")


class UnsupportedFormatError(TrimmerError):
    """不支持的输入 / 输出格式。"""

    def __init__(self, message):
        super().__init__(message, code="UNSUPPORTED_FORMAT")


class NoStreamError(TrimmerError):
    """输入文件缺少当前输出模式所要求的音 / 视频流。"""

    def __init__(self, message):
        super().__init__(message, code="NO_STREAM")


class ValidationError(TrimmerError):
    """参数校验失败。"""

    def __init__(self, message):
        super().__init__(message, code="VALIDATION_ERROR")


class OutputExistsError(TrimmerError):
    """输出文件已存在且用户未确认覆盖。"""

    def __init__(self, message):
        super().__init__(message, code="OUTPUT_EXISTS")


class OverwriteInputError(TrimmerError):
    """输出路径与输入文件相同，拒绝覆盖原始文件。"""

    def __init__(self, message):
        super().__init__(message, code="OVERWRITE_INPUT")


class FFmpegExecutionError(TrimmerError):
    """ffmpeg 进程执行失败。"""

    def __init__(self, message):
        super().__init__(message, code="FFMPEG_EXECUTION_ERROR")
