# -*- coding: utf-8 -*-
"""媒体信息探测模块。

通过 ffprobe 读取输入文件的封装容器、流编码参数（编码器、分辨率、帧率、
码率、采样率、声道数等），并提供重编码时自动匹配参数的编码器映射。
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from trimmer.core.errors import FFmpegExecutionError, UnsupportedFormatError
from trimmer.core.ffmpeg import find_ffprobe

# ---------------------------------------------------------------------------
# 编码器 / 容器映射（用于重编码时自动匹配原视频参数）
# ---------------------------------------------------------------------------

#: 原始视频编码 → 推荐的软件编码器
SOFTWARE_VIDEO_ENCODERS = {
    "h264": "libx264",
    "hevc": "libx265",
    "mpeg2video": "mpeg2video",
    "mpeg4": "mpeg4",
    "vp8": "libvpx",
    "vp9": "libvpx-vp9",
    "av1": "libaom-av1",
    "prores": "prores_ks",
    "mjpeg": "mjpeg",
}
DEFAULT_SOFTWARE_VIDEO_ENCODER = "libx264"

#: 原始音频编码 → 推荐的软件编码器
SOFTWARE_AUDIO_ENCODERS = {
    "aac": "aac",
    "mp3": "libmp3lame",
    "ac3": "ac3",
    "eac3": "eac3",
    "opus": "libopus",
    "vorbis": "libvorbis",
    "flac": "flac",
    "pcm_s16le": "pcm_s16le",
    "pcm_s24le": "pcm_s24le",
    "pcm_s32le": "pcm_s32le",
}
DEFAULT_SOFTWARE_AUDIO_ENCODER = "aac"

#: 音频编码 → 推荐的输出文件扩展名
AUDIO_EXTENSIONS = {
    "aac": ".m4a",
    "mp3": ".mp3",
    "libmp3lame": ".mp3",
    "ac3": ".ac3",
    "eac3": ".eac3",
    "opus": ".opus",
    "libopus": ".opus",
    "vorbis": ".ogg",
    "libvorbis": ".ogg",
    "flac": ".flac",
    "pcm_s16le": ".wav",
    "pcm_s24le": ".wav",
    "pcm_s32le": ".wav",
}
DEFAULT_AUDIO_EXTENSION = ".mka"


@dataclass
class VideoStream:
    """视频流信息。"""

    codec: str
    width: int
    height: int
    fps: float
    bit_rate: Optional[int]
    pix_fmt: str


@dataclass
class AudioStream:
    """音频流信息。"""

    codec: str
    sample_rate: int
    channels: int
    bit_rate: Optional[int]


@dataclass
class MediaInfo:
    """输入媒体的综合信息。"""

    path: str
    duration: float
    container: str
    video: Optional[VideoStream]
    audio: Optional[AudioStream]
    nb_frames: Optional[int] = None

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    @property
    def fps(self) -> float:
        return self.video.fps if self.video else 0.0


def _parse_rate(rate_str: str) -> float:
    """解析形如 '30000/1001' 的帧率字符串为浮点数；失败返回 0.0。"""
    if not rate_str:
        return 0.0
    try:
        num, _, den = rate_str.partition("/")
        num, den = int(num), int(den)
        if num > 0 and den > 0:
            return float(Fraction(num, den))
    except (ValueError, ZeroDivisionError):
        pass
    return 0.0


def _to_int(value):
    """安全地将可能为字符串的数值转换为 int，失败返回 None。"""
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def probe(path: str) -> MediaInfo:
    """使用 ffprobe 探测输入媒体文件信息。"""
    ffprobe = find_ffprobe()
    cmd = [ffprobe, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise FFmpegExecutionError(f"ffprobe 无法解析文件: {path}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise UnsupportedFormatError(f"无法解析媒体信息（可能不是有效的媒体文件）: {path}") from exc

    fmt = data.get("format", {}) or {}
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    container = os.path.splitext(path)[1].lower() or (fmt.get("format_name") or "").split(",")[0]

    video = None
    audio = None
    nb_frames = None
    for stream in data.get("streams", []) or []:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and video is None:
            fps = _parse_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
            if not fps:
                fps = _parse_rate(stream.get("r_frame_rate") or "25/1")
            video = VideoStream(
                codec=stream.get("codec_name") or "h264",
                width=_to_int(stream.get("width")) or 0,
                height=_to_int(stream.get("height")) or 0,
                fps=fps,
                bit_rate=_to_int(stream.get("bit_rate")),
                pix_fmt=stream.get("pix_fmt") or "yuv420p",
            )
            nb_frames = _to_int(stream.get("nb_frames"))
        elif codec_type == "audio" and audio is None:
            audio = AudioStream(
                codec=stream.get("codec_name") or "aac",
                sample_rate=_to_int(stream.get("sample_rate")) or 44100,
                channels=_to_int(stream.get("channels")) or 2,
                bit_rate=_to_int(stream.get("bit_rate")),
            )

    # 容器未提供时长时用 帧数 / 帧率 估算
    if not duration and video and nb_frames and video.fps:
        duration = nb_frames / video.fps

    return MediaInfo(
        path=path,
        duration=duration,
        container=container,
        video=video,
        audio=audio,
        nb_frames=nb_frames,
    )


def infer_audio_extension(media: MediaInfo) -> str:
    """根据输入音频编码推断音频文件的扩展名。"""
    if media.audio and media.audio.codec in AUDIO_EXTENSIONS:
        return AUDIO_EXTENSIONS[media.audio.codec]
    return DEFAULT_AUDIO_EXTENSION


def recommend_software_video_encoder(media: MediaInfo) -> str:
    """根据原视频编码推荐最接近的软件编码器。"""
    if media.video and media.video.codec in SOFTWARE_VIDEO_ENCODERS:
        return SOFTWARE_VIDEO_ENCODERS[media.video.codec]
    return DEFAULT_SOFTWARE_VIDEO_ENCODER


def recommend_software_audio_encoder(media: MediaInfo) -> str:
    """根据原音频编码推荐最接近的软件编码器。"""
    if media.audio and media.audio.codec in SOFTWARE_AUDIO_ENCODERS:
        return SOFTWARE_AUDIO_ENCODERS[media.audio.codec]
    return DEFAULT_SOFTWARE_AUDIO_ENCODER
