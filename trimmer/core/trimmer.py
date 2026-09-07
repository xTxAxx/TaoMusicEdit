# -*- coding: utf-8 -*-
"""裁剪引擎。

负责裁剪流程的编排：参数校验 → 媒体探测 → 输出路径解析 → 覆盖确认 →
编码策略（流复制 / 重编码 / GPU 加速）→ FFmpeg 命令构建与执行 → 结果返回。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from trimmer.core.errors import (
    FFmpegExecutionError,
    NoStreamError,
    OutputExistsError,
    OverwriteInputError,
    TrimmerError,
    ValidationError,
)
from trimmer.core.ffmpeg import (
    FFmpegRunner,
    detect_available_encoders,
    detect_gpu_encoder,
    detect_gpu_vendor,
    supports_input_side_to,
)
from trimmer.core.probe import (
    MediaInfo,
    infer_audio_extension,
    probe,
    recommend_software_audio_encoder,
    recommend_software_video_encoder,
)
from trimmer.utils.logger import get_logger
from trimmer.utils.validation import (
    validate_cut_params,
    validate_end_after_start,
    validate_end_params,
    validate_frame,
    validate_input_path,
    validate_timestamp,
)

logger = get_logger(__name__)

#: 需要 +faststart 优化标志的输出扩展名（mp4 家族）
_FASTSTART_EXTS = {".mp4", ".m4v", ".mov", ".m4a", ".3gp", ".3g2"}


class OutputMode(str, Enum):
    """输出模式枚举。"""

    FULL = "full"           #: 完整视频（含音频）
    VIDEO_ONLY = "video"    #: 仅视频流（无声）
    AUDIO_ONLY = "audio"    #: 仅音频流
    SPLIT = "split"         #: 分离输出视频与音频两个文件


@dataclass
class TrimmerConfig:
    """裁剪配置参数。

    起点（``timestamp`` / ``frame`` 二选一）必填；终点（``end_timestamp`` /
    ``end_frame`` 二选一）可选，缺省时保留到片尾（与原有行为一致）。
    起点与终点的方式可不同（如起点用帧序号、终点用时间戳），内部统一
    换算为时间戳执行。
    """

    input_path: str
    timestamp: Optional[float] = None      #: 裁剪起始时间戳（秒），与 frame 二选一
    frame: Optional[int] = None            #: 裁剪起始帧序号（>=1），与 timestamp 二选一
    end_timestamp: Optional[float] = None  #: 裁剪终点时间戳（秒），与 end_frame 二选一；缺省 = 保留到片尾
    end_frame: Optional[int] = None        #: 裁剪终点帧序号（>=1），与 end_timestamp 二选一；缺省 = 保留到片尾
    output: Optional[str] = None           #: 自定义输出路径（可选）
    output_dir: Optional[str] = None       #: 自定义输出目录（可选）
    suffix: str = "_trim"                  #: 默认输出文件名后缀
    force: bool = False                    #: 输出文件已存在时强制覆盖，不询问
    reencode: bool = False                 #: 是否重编码（默认流复制快速裁剪）
    video_codec: Optional[str] = None      #: 显式指定视频编码器（可选）
    audio_codec: Optional[str] = None      #: 显式指定音频编码器（可选）
    hw_accel: str = "auto"                 #: GPU 加速策略：auto / none / force
    output_mode: OutputMode = OutputMode.FULL
    verbose: bool = False


@dataclass
class TrimmerResult:
    """裁剪结果。"""

    input_path: str
    output_files: List[str]
    output_mode: OutputMode
    cut_duration: float          #: 输出（保留）时长（秒）
    timestamp: float             #: 裁剪起始时间戳（秒）
    frame: Optional[int]         #: 裁剪起始帧序号（帧模式）
    reencoded: bool
    hw_accelerated: bool
    video_encoder: Optional[str] = None
    audio_encoder: Optional[str] = None
    message: str = ""
    end_timestamp: Optional[float] = None  #: 裁剪终点时间戳（秒）；None = 保留到片尾
    end_frame: Optional[int] = None        #: 裁剪终点帧序号（帧模式）


def _codec_family(encoder: str) -> Optional[str]:
    """根据编码器名称推断编码家族（h264 / hevc），用于挑选对应 GPU 编码器。"""
    name = encoder.lower()
    if "hevc" in name or name in ("libx265", "h265"):
        return "hevc"
    if "h264" in name or name in ("libx264", "x264", "avc"):
        return "h264"
    return None


def build_command(
    config: TrimmerConfig,
    media: MediaInfo,
    input_path: str,
    start_time: float,
    output_path: str,
    *,
    video_encoder: str,
    audio_encoder: str,
    include_video: bool,
    include_audio: bool,
    hwaccel_args: Optional[List[str]] = None,
    end_time: Optional[float] = None,
) -> List[str]:
    """构建单条 ffmpeg 命令的参数列表（不含可执行文件）。

    该函数为纯函数，便于单元测试。``end_time`` 为 None 时保留从
    ``start_time`` 到视频末尾的内容（裁剪掉开头部分，原有行为）；
    提供时保留 ``[start_time, end_time)`` 区间（区间裁剪）：

    - FFmpeg >= 4.4：``-to`` 与 ``-ss`` 同为输入侧选项（置于 ``-i`` 之前），
      两者均在输入时间轴上取绝对位置，起点吸附不影响终点位置；
    - 旧版 FFmpeg 回退：输出侧 ``-t <end - start>``（时长随起点吸附整体前移）。

    重编码时会自动匹配原视频的编码器、码率、帧率、像素格式，
    以及音频编码器、码率、采样率、声道数。
    """
    args: List[str] = ["-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
    args += list(hwaccel_args or [])
    args += ["-ss", f"{start_time:.9f}"]
    use_input_to = end_time is not None and supports_input_side_to()
    if end_time is not None and use_input_to:
        args += ["-to", f"{end_time:.9f}"]
    args += ["-i", input_path]
    if end_time is not None and not use_input_to:
        args += ["-t", f"{end_time - start_time:.9f}"]

    if include_video:
        args += ["-map", "0:v:0?"]
        if config.reencode:
            args += ["-c:v", video_encoder]
            if media.video:
                if media.video.bit_rate:
                    args += ["-b:v", str(media.video.bit_rate)]
                args += ["-r", f"{media.video.fps:.6f}"]
                if media.video.pix_fmt:
                    args += ["-pix_fmt", media.video.pix_fmt]
        else:
            args += ["-c:v", "copy"]
    else:
        args += ["-vn"]

    if include_audio:
        args += ["-map", "0:a:0?"]
        if config.reencode:
            args += ["-c:a", audio_encoder]
            if media.audio:
                if media.audio.bit_rate:
                    args += ["-b:a", str(media.audio.bit_rate)]
                if media.audio.sample_rate:
                    args += ["-ar", str(media.audio.sample_rate)]
                if media.audio.channels:
                    args += ["-ac", str(media.audio.channels)]
        else:
            args += ["-c:a", "copy"]
    else:
        args += ["-an"]

    args += ["-avoid_negative_ts", "make_zero"]
    ext = os.path.splitext(output_path)[1].lower()
    if ext in _FASTSTART_EXTS:
        args += ["-movflags", "+faststart"]
    args.append(output_path)
    return args


class Trimmer:
    """视频裁剪器。

    典型用法::

        config = TrimmerConfig(input_path="input.mp4", timestamp=24.9)
        result = Trimmer(config).run()

        # 区间裁剪：保留 [24.9s, 3600s)
        config = TrimmerConfig(input_path="input.mp4", timestamp=24.9, end_timestamp=3600.0)
        result = Trimmer(config).run()
    """

    def __init__(self, config: TrimmerConfig):
        self.config = config
        self.media: Optional[MediaInfo] = None
        self.input_path: Optional[str] = None
        self.start_time: float = 0.0
        self.end_time: Optional[float] = None  #: 裁剪终点时间戳（秒）；None = 保留到片尾
        self.out_duration: Optional[float] = None  #: 输出（保留）时长，未知时为 None
        self.timestamp: float = 0.0
        self.frame: Optional[int] = None
        self.end_frame: Optional[int] = None
        self.output_paths: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # 准备阶段：校验、探测、路径解析、覆盖确认
    # ------------------------------------------------------------------

    def _prepare(self, confirm_cb: Optional[Callable[[str], bool]] = None) -> None:
        cfg = self.config
        self.input_path = validate_input_path(cfg.input_path)
        mode, value = validate_cut_params(cfg.timestamp, cfg.frame)
        self.media = probe(self.input_path)

        if mode == "timestamp":
            self.start_time = validate_timestamp(value, self.media)
            self.timestamp = value
            self.frame = None
        else:
            self.start_time = validate_frame(value, self.media)
            self.frame = value
            self.timestamp = self.start_time

        # 终点（可选）：时间戳 / 帧序号二选一，换算后须大于起点
        end_mode, end_value = validate_end_params(cfg.end_timestamp, cfg.end_frame)
        if end_mode == "timestamp":
            self.end_time = validate_timestamp(end_value, self.media, label="终点时间戳")
            self.end_frame = None
        elif end_mode == "frame":
            self.end_time = validate_frame(end_value, self.media, label="终点帧序号")
            self.end_frame = end_value
        else:
            self.end_time = None
            self.end_frame = None
        if self.end_time is not None:
            validate_end_after_start(self.end_time, self.start_time)

        if self.end_time is not None:
            self.out_duration = self.end_time - self.start_time
        elif self.media.duration:
            self.out_duration = max(0.0, self.media.duration - self.start_time)

        self._check_streams()
        self.output_paths = self._resolve_output_paths()
        self._check_overwrite(confirm_cb)

    def _check_streams(self) -> None:
        """根据输出模式校验输入是否包含所需音 / 视频流。"""
        mode = self.config.output_mode
        if mode in (OutputMode.FULL, OutputMode.VIDEO_ONLY, OutputMode.SPLIT) and not self.media.has_video:
            raise NoStreamError("输入文件中不包含视频流，无法输出视频。")
        if mode == OutputMode.AUDIO_ONLY and not self.media.has_audio:
            raise NoStreamError("输入文件中不包含音频流，无法输出纯音频。")
        if mode == OutputMode.FULL and not self.media.has_audio:
            logger.warning("输入文件中不包含音频流，输出将仅包含视频。")

    def _resolve_output_paths(self) -> Dict[str, str]:
        """解析输出文件路径。

        默认输出到原视频所在目录，文件名在原名后追加 ``suffix``；
        SPLIT 模式下追加 ``_video`` / ``_audio`` 后缀。
        """
        cfg = self.config
        input_dir = os.path.dirname(self.input_path) or "."
        stem = os.path.splitext(os.path.basename(self.input_path))[0]
        input_ext = os.path.splitext(os.path.basename(self.input_path))[1].lower()
        video_ext = input_ext or ".mp4"
        audio_ext = infer_audio_extension(self.media)

        out_dir = os.path.abspath(cfg.output_dir or input_dir or ".")
        os.makedirs(out_dir, exist_ok=True)

        mode = cfg.output_mode
        paths: Dict[str, str] = {}

        if mode in (OutputMode.FULL, OutputMode.VIDEO_ONLY):
            if cfg.output:
                out = cfg.output
                if not os.path.splitext(out)[1]:
                    out += video_ext
            else:
                out = os.path.join(out_dir, stem + cfg.suffix + video_ext)
            paths["main"] = os.path.abspath(out)

        elif mode == OutputMode.AUDIO_ONLY:
            if cfg.output:
                out = cfg.output
                if not os.path.splitext(out)[1]:
                    out += audio_ext
            else:
                out = os.path.join(out_dir, stem + cfg.suffix + audio_ext)
            paths["main"] = os.path.abspath(out)

        else:  # SPLIT
            if cfg.output:
                vout = cfg.output
                if not os.path.splitext(vout)[1]:
                    vout += video_ext
                base, _ = os.path.splitext(vout)
                aout = base + "_audio" + audio_ext
            else:
                vout = os.path.join(out_dir, stem + cfg.suffix + "_video" + video_ext)
                aout = os.path.join(out_dir, stem + cfg.suffix + "_audio" + audio_ext)
            paths["video"] = os.path.abspath(vout)
            paths["audio"] = os.path.abspath(aout)

        return paths

    def _check_overwrite(self, confirm_cb: Optional[Callable[[str], bool]]) -> None:
        """覆盖确认：拒绝覆盖原文件；已存在的输出文件需用户确认。"""
        input_abs = os.path.normcase(os.path.abspath(self.input_path))
        for out in self.output_paths.values():
            if os.path.normcase(os.path.abspath(out)) == input_abs:
                raise OverwriteInputError(
                    f"输出路径与输入文件相同（{out}），禁止覆盖原始文件，请指定其他输出路径。"
                )
        for out in self.output_paths.values():
            if os.path.exists(out) and not self.config.force:
                if confirm_cb is None:
                    raise OutputExistsError(f"输出文件已存在: {out}（可使用 force=True 或 --force 覆盖）")
                if not confirm_cb(out):
                    raise OutputExistsError(f"已取消，输出文件存在: {out}")

    # ------------------------------------------------------------------
    # 编码策略
    # ------------------------------------------------------------------

    def _plan_encoders(self, exclude: Optional[set] = None) -> Tuple[str, str, List[str], Optional[str]]:
        """决定重编码使用的视频 / 音频编码器与 GPU 加速参数。

        优先匹配系统显卡厂商对应的硬件编码器（NVIDIA→nvenc、AMD→amf、
        Intel→qsv），再按通用优先级挑选；exclude 用于跳过已失败的编码器。

        返回 ``(video_encoder, audio_encoder, hwaccel_args, gpu_encoder)``。
        """
        exclude = set(exclude or ())
        video_encoder = self.config.video_codec or recommend_software_video_encoder(self.media)
        audio_encoder = self.config.audio_codec or recommend_software_audio_encoder(self.media)
        hwaccel_args: List[str] = []
        gpu: Optional[str] = None

        if self.config.hw_accel in ("auto", "force"):
            family = _codec_family(video_encoder)
            if family:
                gpu = detect_gpu_encoder(family, detect_available_encoders(),
                                         vendor=detect_gpu_vendor(), exclude=exclude)
                if gpu:
                    video_encoder = gpu
                    hwaccel_args = ["-hwaccel", "auto"]
                    logger.info("检测到 GPU 编码器 %s，已启用硬件加速。", gpu)
                elif self.config.hw_accel == "force":
                    raise ValidationError(f"未检测到可用于 {family} 的 GPU 编码器（--hw-accel force）。")
                else:
                    logger.info("未检测到可用 GPU 编码器，使用软件编码 %s。", video_encoder)
        return video_encoder, audio_encoder, hwaccel_args, gpu

    # ------------------------------------------------------------------
    # 命令构建与执行
    # ------------------------------------------------------------------

    def _build_plans(
        self, video_encoder: str, audio_encoder: str, hwaccel_args: List[str]
    ) -> List[Tuple[str, List[str], str]]:
        """根据输出模式生成一条或多条 (输出路径, ffmpeg 参数, 描述) 计划。"""
        mode = self.config.output_mode
        plans: List[Tuple[str, List[str], str]] = []

        if mode in (OutputMode.FULL, OutputMode.VIDEO_ONLY):
            include_video = True
            include_audio = mode == OutputMode.FULL
            out = self.output_paths["main"]
            label = "完整视频" if include_audio else "无声视频"
            plans.append((out, build_command(
                self.config, self.media, self.input_path, self.start_time, out,
                video_encoder=video_encoder, audio_encoder=audio_encoder,
                include_video=include_video, include_audio=include_audio,
                hwaccel_args=hwaccel_args, end_time=self.end_time), label))

        elif mode == OutputMode.AUDIO_ONLY:
            out = self.output_paths["main"]
            plans.append((out, build_command(
                self.config, self.media, self.input_path, self.start_time, out,
                video_encoder=video_encoder, audio_encoder=audio_encoder,
                include_video=False, include_audio=True,
                hwaccel_args=[], end_time=self.end_time), "纯音频"))

        else:  # SPLIT
            vout = self.output_paths["video"]
            aout = self.output_paths["audio"]
            plans.append((vout, build_command(
                self.config, self.media, self.input_path, self.start_time, vout,
                video_encoder=video_encoder, audio_encoder=audio_encoder,
                include_video=True, include_audio=False,
                hwaccel_args=hwaccel_args, end_time=self.end_time), "分离视频"))
            plans.append((aout, build_command(
                self.config, self.media, self.input_path, self.start_time, aout,
                video_encoder=video_encoder, audio_encoder=audio_encoder,
                include_video=False, include_audio=True,
                hwaccel_args=[], end_time=self.end_time), "分离音频"))

        return plans

    def _run_plans(self, video_encoder: str, audio_encoder: str,
                   hwaccel_args: List[str], progress_cb,
                   cancel_event: Optional[threading.Event] = None) -> None:
        plans = self._build_plans(video_encoder, audio_encoder, hwaccel_args)
        for out_path, args, label in plans:
            self._execute(out_path, args, label, progress_cb, cancel_event)

    def _execute(self, out_path: str, args: List[str], label: str, progress_cb,
                 cancel_event: Optional[threading.Event] = None) -> None:
        logger.info("正在生成%s: %s", label, out_path)
        runner = FFmpegRunner()
        runner.run(args, total_duration=self.out_duration, progress_cb=progress_cb,
                   cancel_event=cancel_event)
        if not os.path.exists(out_path):
            # 流复制区间模式下双端吸附关键帧可能导不出任何内容 → 明确报错
            if self.end_time is not None and not self.config.reencode:
                raise FFmpegExecutionError(self._empty_interval_message())
            logger.warning("未检测到输出文件: %s", out_path)
            return
        if self.end_time is not None and not self.config.reencode:
            self._check_interval_output(out_path)

    def _empty_interval_message(self) -> str:
        return (f"流复制模式下区间 [{self.start_time:.3f}s, {self.end_time:.3f}s) "
                "双端吸附关键帧后无可输出内容，请扩大区间或使用重编码获得精确边界。")

    def _check_interval_output(self, out_path: str) -> None:
        """流复制区间模式：校验输出包含实际内容。

        流复制时起点与终点均受关键帧 / 包边界吸附影响，极端情况下可能
        产出空文件；此时明确报错而非静默输出空文件（见设计文档 §5.2）。
        """
        try:
            out_duration = probe(out_path).duration or 0.0
        except TrimmerError:
            out_duration = 0.0
        if out_duration <= 0.0:
            raise FFmpegExecutionError(self._empty_interval_message())

    def _build_message(self, gpu: Optional[str]) -> str:
        if self.config.reencode:
            return f"已重编码（GPU 加速: {gpu}）" if gpu else "已重编码"
        return "流复制（未重编码）"

    # ------------------------------------------------------------------
    # 对外主流程
    # ------------------------------------------------------------------

    def run(self, progress_cb: Optional[Callable[[float, float], None]] = None,
            confirm_cb: Optional[Callable[[str], bool]] = None,
            cancel_event: Optional[threading.Event] = None) -> TrimmerResult:
        """执行裁剪并返回结果。

        参数:
            progress_cb: 进度回调 ``(percent, seconds)``。
            confirm_cb: 覆盖确认回调，返回 True 表示同意覆盖；为 None 时
                若输出已存在将直接抛出 OutputExistsError。
            cancel_event: 可选取消事件；置位时终止 ffmpeg 并抛出
                ``KeyboardInterrupt``（用于 Web UI 等场景的中断控制）。
        """
        self._prepare(confirm_cb)

        if self.config.reencode:
            video_encoder, audio_encoder, hwaccel_args, gpu = self._plan_encoders()
        else:
            video_encoder, audio_encoder, hwaccel_args, gpu = "copy", "copy", [], None

        # 循环执行：GPU 编码失败时自动尝试下一个可用 GPU 编码器，最终回退软件编码
        failed_gpu = set()
        while True:
            try:
                self._run_plans(video_encoder, audio_encoder, hwaccel_args,
                                progress_cb, cancel_event)
                break
            except FFmpegExecutionError:
                # 非 GPU 编码失败，或 --hw-accel force 强制模式：抛出原始错误
                if not (gpu and hwaccel_args) or self.config.hw_accel == "force":
                    raise
                logger.warning("GPU 编码器 %s 执行失败，尝试其他可用编码器。", gpu)
                failed_gpu.add(gpu)
                video_encoder, audio_encoder, hwaccel_args, gpu = self._plan_encoders(exclude=failed_gpu)
                if not gpu:
                    logger.warning("可用 GPU 编码器均已失败，回退到软件编码 %s。", video_encoder)

        return TrimmerResult(
            input_path=self.input_path,
            output_files=list(self.output_paths.values()),
            output_mode=self.config.output_mode,
            cut_duration=self.out_duration or 0.0,
            timestamp=self.timestamp,
            frame=self.frame,
            reencoded=self.config.reencode,
            hw_accelerated=gpu is not None,
            video_encoder=video_encoder if self.config.reencode else None,
            audio_encoder=audio_encoder if self.config.reencode else None,
            message=self._build_message(gpu),
            end_timestamp=self.end_time,
            end_frame=self.end_frame,
        )
