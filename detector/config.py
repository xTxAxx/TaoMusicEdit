# -*- coding: utf-8 -*-
"""检测器配置参数模块。

所有可调参数集中于此，`DetectorConfig` 以 dataclass 形式提供，
可在初始化 `VideoColorDetector` 时整体传入或通过关键字逐项覆盖。
"""
from dataclasses import dataclass, field, fields, replace
from typing import Callable, Optional, Tuple, Any

#: 目标颜色 #F7F10F，以 RGB 表示
DEFAULT_TARGET_COLOR = (247, 241, 15)

#: 四个检测点的默认坐标 (x, y)，针对 1920x1080 分辨率设计
DEFAULT_POINTS = ((20, 20), (1900, 20), (20, 1060), (1900, 1060))

#: 默认参考分辨率，用于坐标等比缩放
DEFAULT_REFERENCE_RESOLUTION = (1920, 1080)


@dataclass
class DetectorConfig:
    """视频颜色检测器的全部可配置参数。"""

    # ---------- 颜色与匹配 ----------
    #: 目标颜色 (R, G, B)，默认 #F7F10F
    target_color: Tuple[int, int, int] = DEFAULT_TARGET_COLOR

    #: 颜色匹配置信度阈值（严格匹配，默认 0.97）
    confidence_threshold: float = 0.97

    #: 颜色容差（RGB 欧氏距离容差）。当像素距目标颜色的距离 <= 该值时置信度为 1.0，
    #: 之后按线性递减。默认 10.0，可配置以控制匹配严格程度。
    color_tolerance: float = 10.0

    # ---------- 检测点 ----------
    #: 四个检测点坐标 (x, y)
    points: Tuple[Tuple[int, int], ...] = DEFAULT_POINTS

    #: 是否根据视频实际分辨率对检测点坐标等比缩放（默认开启）
    scale_points: bool = True

    #: 检测点坐标对应的参考分辨率
    reference_resolution: Tuple[int, int] = DEFAULT_REFERENCE_RESOLUTION

    # ---------- 时间窗口与跳帧 ----------
    #: 检测窗口：只检测视频前 N 秒（默认前 40 秒）
    search_window: float = 40.0

    #: 反向跳帧的起始时间（秒），从该位置向 0 反向抽帧
    initial_start: float = 35.0

    #: 初始阶段大步长（秒/次）
    coarse_step: float = 5.0

    #: 细化阶段小步长（秒/次）
    fine_step: float = 1.0

    # ---------- 帧提取与运行 ----------
    #: 帧提取器类型："ffmpeg"（默认，精确快速）或 "opencv"
    extractor: str = "ffmpeg"

    #: GPU 硬件解码策略：
    #: - "auto"  按平台自动探测（Windows: d3d11va → dxva2 → cuda → qsv）
    #: - "none"  CPU 软解（默认，兼容性最好）
    #: - 指定后端名：cuda（N 卡）/ d3d11va、dxva2（AMD 等 Windows 显卡）/
    #:   qsv（Intel 核显）/ vaapi（Linux）/ videotoolbox（macOS）
    #: 所选后端不可用时自动降级软解，不会导致检测失败。
    hwaccel: str = "none"

    #: ffmpeg 可执行文件路径
    ffmpeg_path: str = "ffmpeg"

    #: ffprobe 可执行文件路径
    ffprobe_path: str = "ffprobe"

    #: 日志级别：DEBUG / INFO / WARNING / ERROR
    log_level: str = "INFO"

    #: 全局进度回调，签名 callback(stage: str, progress: float, message: str)
    progress_callback: Optional[Callable[[str, float, str], None]] = field(
        default=None, repr=False, compare=False
    )

    def validate(self) -> None:
        """校验参数合法性，非法时抛出 :class:`ValueError`。"""
        if not (0.0 <= self.confidence_threshold <= 1.0):
            raise ValueError("confidence_threshold 必须在 [0, 1] 范围内")
        if self.color_tolerance < 0.0:
            raise ValueError("color_tolerance 不能为负")
        if len(self.target_color) != 3 or not all(
            0 <= c <= 255 for c in self.target_color
        ):
            raise ValueError("target_color 必须是三个 0-255 的 RGB 分量")
        if len(self.points) < 1:
            raise ValueError("points 至少需要 1 个检测点")
        if any(len(p) != 2 for p in self.points):
            raise ValueError("每个检测点必须是 (x, y) 二元组")
        if self.search_window <= 0:
            raise ValueError("search_window 必须大于 0")
        if self.initial_start < 0:
            raise ValueError("initial_start 不能为负")
        if self.coarse_step <= 0 or self.fine_step <= 0:
            raise ValueError("coarse_step / fine_step 必须大于 0")
        if self.fine_step > self.coarse_step:
            raise ValueError("fine_step 必须不大于 coarse_step")
        if self.extractor not in ("ffmpeg", "opencv"):
            raise ValueError("extractor 仅支持 'ffmpeg' 或 'opencv'")
        # 延迟导入：避免 config → utils.ffmpeg → core → config 的模块级循环导入
        from .utils.ffmpeg import HWACCEL_CHOICES
        if self.hwaccel not in HWACCEL_CHOICES:
            raise ValueError(
                f"hwaccel 仅支持 {HWACCEL_CHOICES} 之一，收到: {self.hwaccel!r}"
            )

    def with_overrides(self, **kwargs: Any) -> "DetectorConfig":
        """返回应用了关键字覆盖的新配置实例（不修改原对象）。"""
        valid = {f.name for f in fields(self)}
        unknown = set(kwargs) - valid
        if unknown:
            raise TypeError(f"未知配置项: {sorted(unknown)}")
        return replace(self, **kwargs)

    def to_dict(self) -> dict:
        """导出为字典（跳过回调等不可序列化字段）。"""
        d = {}
        for f in fields(self):
            if f.repr is False:
                continue
            d[f.name] = getattr(self, f.name)
        return d
