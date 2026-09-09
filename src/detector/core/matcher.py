# -*- coding: utf-8 -*-
"""帧级颜色匹配器：负责检测点坐标适配与单帧四点匹配判定。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

import numpy as np

from ..utils.color import color_confidence, color_match, RGB


@dataclass
class PointMatch:
    """单个检测点的匹配结果。"""

    x: int
    y: int
    confidence: float
    rgb: Tuple[int, int, int]
    matched: bool

    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试输出
        return (
            f"PointMatch(x={self.x}, y={self.y}, rgb={self.rgb}, "
            f"conf={self.confidence:.4f}, matched={self.matched})"
        )


@dataclass
class FrameMatch:
    """单帧的整体匹配结果（四点全部命中才算匹配）。"""

    matched: bool
    confidence: float
    points: List[PointMatch] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.matched

    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试输出
        return f"FrameMatch(matched={self.matched}, conf={self.confidence:.4f})"


class FrameMatcher:
    """对单帧执行四点颜色匹配。

    负责：
    - 根据视频实际分辨率对检测点坐标进行等比缩放（坐标系统适配）；
    - 计算每个检测点的置信度与是否匹配；
    - 汇总整体匹配结果（所有点同时满足才算命中）。
    """

    def __init__(
        self,
        target_color: RGB = (247, 241, 15),
        points: Sequence[Tuple[int, int]] = ((20, 20), (1900, 20), (20, 1060), (1900, 1060)),
        confidence_threshold: float = 0.97,
        color_tolerance: float = 10.0,
        scale_points: bool = True,
        reference_resolution: Tuple[int, int] = (1920, 1080),
    ):
        self.target_color = tuple(int(c) for c in target_color)
        self.raw_points = [tuple(int(v) for v in p) for p in points]
        self.threshold = float(confidence_threshold)
        self.tolerance = float(color_tolerance)
        self.scale_points = bool(scale_points)
        self.reference_resolution = tuple(int(v) for v in reference_resolution)
        self.width: int = 0
        self.height: int = 0
        self.effective_points: List[Tuple[int, int]] = []

    # ------------------------------------------------------------------
    def set_resolution(self, width: int, height: int) -> List[Tuple[int, int]]:
        """设置视频分辨率并计算实际生效的检测点坐标。

        当开启坐标缩放且视频分辨率与参考分辨率不一致时，
        检测点按宽高比例缩放并取整，同时做越界钳制。
        """
        self.width = int(width)
        self.height = int(height)
        pts: List[Tuple[int, int]] = []
        ref_w, ref_h = self.reference_resolution
        scale_x = 1.0
        scale_y = 1.0
        if self.scale_points and (ref_w, ref_h) and (ref_w != self.width or ref_h != self.height):
            scale_x = self.width / ref_w if ref_w else 1.0
            scale_y = self.height / ref_h if ref_h else 1.0
        for (x, y) in self.raw_points:
            nx = int(round(x * scale_x))
            ny = int(round(y * scale_y))
            nx = max(0, min(nx, self.width - 1))
            ny = max(0, min(ny, self.height - 1))
            pts.append((nx, ny))
        self.effective_points = pts
        return pts

    # ------------------------------------------------------------------
    def match(self, frame_bgr: np.ndarray) -> FrameMatch:
        """对一帧 BGR 图像执行四点颜色匹配。

        :param frame_bgr: OpenCV BGR 顺序的 (H, W, 3) 图像
        :return: 整体匹配结果（含各点明细）
        """
        h, w = frame_bgr.shape[:2]
        if self.width != w or self.height != h:
            self.set_resolution(w, h)

        point_matches: List[PointMatch] = []
        all_matched = True
        for (x, y) in self.effective_points:
            b, g, r = frame_bgr[y, x]
            rgb = (int(r), int(g), int(b))
            conf = color_confidence(rgb, self.target_color, self.tolerance)
            matched = color_match(conf, self.threshold)
            if not matched:
                all_matched = False
            point_matches.append(PointMatch(x, y, conf, rgb, matched))

        overall_conf = min(p.confidence for p in point_matches) if point_matches else 0.0
        return FrameMatch(matched=all_matched, confidence=overall_conf, points=point_matches)
