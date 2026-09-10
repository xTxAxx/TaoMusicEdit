# -*- coding: utf-8 -*-
"""视频颜色检测器：对外主入口。

组合了 FFmpeg 帧提取、多检测点颜色匹配与「反向跳帧 + 局部细化」算法，
提供简洁的初始化与检测接口，返回结构化结果。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..config import DetectorConfig
from ..utils.color import rgb_to_hex
from ..utils.ffmpeg import (
    FrameExtractor,
    VideoInfo,
    create_extractor,
    probe_video,
)
from ..utils.logger import get_logger
from .algorithm import FrameInfo, detect_offset
from .errors import (
    ConfigError,
    FFmpegError,
    UnsupportedFormatError,
    VideoNotFoundError,
)
from .matcher import FrameMatcher, FrameMatch, PointMatch


@dataclass
class Detection:
    """极简检测结果，只暴露调用方最关心的核心字段。

    ::

        detector = VideoColorDetector()
        result = detector.detect("video.mp4")
        if result.detected:
            print(result.frame, result.timestamp)

    支持解构赋值，直接得到帧序号与时间戳::

        frame, time = detector.detect("video.mp4")

    详细诊断信息（各点置信度、定位区间、阶段统计等）属于内部调试数据，
    仅在 ``detect(..., detail=True)`` 时通过 :attr:`details` 提供。
    """

    detected: bool
    frame: Optional[int] = None
    timestamp: Optional[float] = None
    details: Optional["DetectionDetails"] = None

    def __bool__(self) -> bool:
        return self.detected

    def __iter__(self):
        yield self.frame
        yield self.timestamp

    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试输出
        return f"Detection(detected={self.detected}, frame={self.frame}, timestamp={self.timestamp})"


@dataclass
class DetectionDetails:
    """详细诊断信息（仅在 ``detect(..., detail=True)`` 时提供）。

    对应旧版 ``DetectionResult`` 的扩展字段，供调试 / 可视化等进阶场景使用。
    """

    confidence: Optional[float] = None
    point_matches: Optional[List[PointMatch]] = None
    coarse_bracket: Optional[tuple] = None
    fine_bracket: Optional[tuple] = None
    stage_stats: dict = field(default_factory=dict)
    duration_seconds: float = 0.0
    resolution: Optional[tuple] = None
    message: str = ""


class VideoColorDetector:
    """视频颜色检测器。

    ::

        detector = VideoColorDetector()
        frame, time = detector.detect("video.mp4")
        if frame is not None:
            print(f"目标帧: {frame}, 时间戳: {time:.6f}s")

    可通过 ``config`` 对象整体传入配置，或用关键字参数覆盖单个配置项：
    ::

        detector = VideoColorDetector(confidence_threshold=0.95, color_tolerance=15.0)
    """

    def __init__(self, config: Optional[DetectorConfig] = None, **overrides):
        if config is not None and overrides:
            raise TypeError("config 与关键字覆盖参数不能同时使用")
        base = config if config is not None else DetectorConfig()
        if overrides:
            base = base.with_overrides(**overrides)
        try:
            base.validate()
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        self.config: DetectorConfig = base
        self.logger = get_logger(__name__, base.log_level)
        self._extractor: Optional[FrameExtractor] = None
        self._matcher: Optional[FrameMatcher] = None

    # ------------------------------------------------------------------
    # 帧/匹配准备
    # ------------------------------------------------------------------
    def _resolve_media(self, video_path: str) -> VideoInfo:
        if not os.path.isfile(video_path):
            raise VideoNotFoundError(f"视频文件不存在: {video_path}")
        try:
            info = probe_video(video_path, self.config.ffprobe_path)
        except FFmpegError as exc:
            raise UnsupportedFormatError(
                f"无法解析视频文件（可能不是有效视频）: {video_path} -> {exc}"
            ) from exc
        if not info.has_video or info.width <= 0 or info.height <= 0:
            raise UnsupportedFormatError(
                f"文件不是有效的视频或缺少视频流: {video_path}（codec={info.codec or 'N/A'}）"
            )
        return info

    def _get_matcher(self, info: VideoInfo) -> FrameMatcher:
        if self._matcher is None:
            self._matcher = FrameMatcher(
                target_color=self.config.target_color,
                points=self.config.points,
                confidence_threshold=self.config.confidence_threshold,
                color_tolerance=self.config.color_tolerance,
                scale_points=self.config.scale_points,
                reference_resolution=self.config.reference_resolution,
            )
        self._matcher.set_resolution(info.width, info.height)
        return self._matcher

    def _get_extractor(self, info: VideoInfo) -> FrameExtractor:
        if self._extractor is None or self._extractor.info.path != info.path:
            self.close()
            self._extractor = create_extractor(
                info, self.config.extractor, self.config.ffmpeg_path,
                hwaccel=getattr(self.config, "hwaccel", "none"),
            )
            # 实际生效的解码方式（auto / 指定后端不可用时可能已降级软解）
            active = getattr(self._extractor, "hwaccel_active", "none")
            if active != "none":
                self.logger.info("帧提取解码后端: GPU 硬解 %s", active)
            else:
                self.logger.info("帧提取解码后端: CPU 软解")
        return self._extractor

    def close(self) -> None:
        """释放帧提取器资源。"""
        if self._extractor is not None:
            self._extractor.close()
            self._extractor = None

    # ------------------------------------------------------------------
    # 主检测流程
    # ------------------------------------------------------------------
    def detect(
        self,
        video_path: str,
        progress_callback: Optional[Callable[[str, float, str], None]] = None,
        detail: bool = False,
    ) -> Detection:
        """对指定视频执行检测。

        :param video_path: 视频文件路径
        :param progress_callback: 本次调用的进度回调，覆盖配置中的全局回调；
            签名 (stage: str, progress: float, message: str)
        :param detail: 是否附带详细诊断信息（各点置信度 / 定位区间 / 阶段统计），
            默认 ``False``，只返回核心的 frame 与 time
        :return: :class:`Detection` 极简结果；未命中时 ``detected=False``，frame/timestamp 为 None
        :raises VideoNotFoundError: 文件不存在
        :raises UnsupportedFormatError: 非视频 / 无视频流
        :raises FFmpegError: FFmpeg 调用失败
        """
        callback = progress_callback or self.config.progress_callback

        self.logger.info("开始检测: %s", video_path)
        info = self._resolve_media(video_path)
        matcher = self._get_matcher(info)
        extractor = self._get_extractor(info)

        self.logger.info(
            "视频信息: %dx%d fps=%.3f 时长=%.2fs 帧数=%d codec=%s",
            info.width,
            info.height,
            info.fps,
            info.duration,
            info.nb_frames,
            info.codec,
        )
        self.logger.info(
            "有效检测点: %s（目标色 %s 阈值=%.2f 容差=%.1f）",
            matcher.effective_points,
            rgb_to_hex(self.config.target_color),
            self.config.confidence_threshold,
            self.config.color_tolerance,
        )

        # 窗口参数按视频时长钳制
        search_window = min(self.config.search_window, info.duration)
        initial_start = min(self.config.initial_start, search_window)
        if search_window <= 0:
            return Detection(
                detected=False,
                details=DetectionDetails(
                    message="视频时长为 0，无法检测", duration_seconds=info.duration
                ),
            )

        def probe_time(t: float) -> bool:
            if t < 0 or t > info.duration:
                return False
            frame = extractor.read(t)
            if frame is None:
                self.logger.debug("time=%.3fs 帧读取失败，视为不匹配", t)
                return False
            return bool(matcher.match(frame))

        def scan_frames(t0: float, t1: float) -> List[FrameInfo]:
            frames = extractor.read_range(t0, t1)
            first_index = int(round(t0 * info.fps)) if info.fps else 0
            result: List[FrameInfo] = []
            for i, frame in enumerate(frames):
                fm = matcher.match(frame)
                idx = first_index + i
                ts = idx / info.fps if info.fps else t0 + i
                result.append(FrameInfo(idx, ts, fm.matched, fm.confidence))
            return result

        self.logger.info(
            "启动结尾定位检测（最后一个命中帧）: window=[0,%.2fs] start=%.2fs coarse=%.1fs fine=%.1fs",
            search_window,
            initial_start,
            self.config.coarse_step,
            self.config.fine_step,
        )
        onset = detect_offset(
            probe_time,
            scan_frames,
            search_window=search_window,
            initial_start=initial_start,
            coarse_step=self.config.coarse_step,
            fine_step=self.config.fine_step,
            progress_cb=callback,
        )

        self.logger.info(
            "检测结束: detected=%s stage_stats=%s message=%s",
            onset.detected,
            onset.stage_stats,
            onset.message,
        )

        if not onset.detected or onset.target_frame is None:
            return Detection(
                detected=False,
                details=DetectionDetails(
                    coarse_bracket=onset.coarse_bracket,
                    fine_bracket=onset.fine_bracket,
                    stage_stats=onset.stage_stats,
                    duration_seconds=info.duration,
                    resolution=(info.width, info.height),
                    message=onset.message,
                ),
            )

        target = onset.target_frame
        result = Detection(
            detected=True,
            frame=target.frame_index,
            timestamp=target.timestamp,
        )
        if detail:
            # 重新读取目标帧以取得各点明细（保证结果一致性）
            fm: FrameMatch = FrameMatch(False, 0.0, [])
            frame = extractor.read(target.timestamp)
            if frame is not None:
                fm = matcher.match(frame)
            result.details = DetectionDetails(
                confidence=fm.confidence if fm.points else target.confidence,
                point_matches=fm.points or None,
                coarse_bracket=onset.coarse_bracket,
                fine_bracket=onset.fine_bracket,
                stage_stats=onset.stage_stats,
                duration_seconds=info.duration,
                resolution=(info.width, info.height),
                message=onset.message,
            )
        return result
