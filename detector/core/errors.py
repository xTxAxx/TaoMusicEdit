# -*- coding: utf-8 -*-
"""检测器异常层级定义。

所有模块抛出的异常均为 :class:`DetectorError` 的子类，
调用方只需捕获 :class:`DetectorError` 即可统一处理。
"""


class DetectorError(Exception):
    """检测器基础异常，所有业务异常的统一父类。"""


class VideoNotFoundError(DetectorError):
    """视频文件不存在或无法访问。"""


class UnsupportedFormatError(DetectorError):
    """视频格式不支持：无法解析、无视频流或不是有效视频文件。"""


class FFmpegError(DetectorError):
    """FFmpeg / FFprobe 调用失败。"""


class VideoDecodeError(DetectorError):
    """视频帧解码失败。"""


class ConfigError(ValueError, DetectorError):
    """配置参数非法。"""
