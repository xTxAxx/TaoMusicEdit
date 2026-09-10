# -*- coding: utf-8 -*-
"""视频颜色检测器集成测试。

覆盖：
- 合成视频的端到端精确检测（已知起始帧）
- 真实视频（input1/2/3.mp4）的目标检测
- 坐标适配（分辨率缩放）
- 异常处理（文件不存在 / 非视频）
- 进度回调、OpenCV 提取器、公共 API 完整性
"""
import os
import pytest

import detector
from detector import (
    ConfigError,
    Detection,
    DetectorError,
    UnsupportedFormatError,
    VideoColorDetector,
    VideoNotFoundError,
)
from detector.config import DetectorConfig
from detector.tests.conftest import (
    REAL_OFFSET_RANGE,
    REQUIRE_REAL,
    make_synthetic_video,
)

TARGET = (247, 241, 15)
SYN_POINTS = ((5, 5), (300, 5), (5, 220), (300, 220))  # 320x240 四点
# 合成视频颜色区域 [1.5, 2.5]s，30fps，末帧索引为 round(2.5*30)-1 = 74
SYN_OFFSET_FRAME = int(round(2.5 * 30)) - 1  # 74


@pytest.fixture
def synth_320(tmp_path):
    """320x240 合成视频：颜色区域 [1.5, 2.5]s，四点为目标色。"""
    return make_synthetic_video(
        str(tmp_path / "s.mkv"),
        width=320, height=240, fps=30, duration_sec=3.0,
        onset_sec=1.5, offset_sec=2.5, target_color=TARGET, points=SYN_POINTS,
    )


def detector_for_synth():
    """适配 320x240 合成视频的检测器配置。"""
    return VideoColorDetector(
        target_color=TARGET,
        points=SYN_POINTS,
        scale_points=False,
        color_tolerance=0.0,  # 无损编码下要求精确匹配
        confidence_threshold=0.97,
    )


class TestSyntheticEndToEnd:
    def test_detects_exact_offset_frame(self, synth_320):
        result = detector_for_synth().detect(synth_320)
        assert result.detected is True
        assert result.frame == SYN_OFFSET_FRAME
        assert result.timestamp == pytest.approx(SYN_OFFSET_FRAME / 30)

    def test_minimal_result_and_unpacking(self, synth_320):
        """默认结果极简：核心字段 + 解构赋值，无多余诊断数据。"""
        result = detector_for_synth().detect(synth_320)
        assert isinstance(result, Detection)
        assert result.detected is True
        assert isinstance(result.frame, int)
        assert isinstance(result.timestamp, float)
        assert result.details is None  # 默认不计算详细诊断

        frame, time = result  # 解构赋值
        assert frame == result.frame
        assert time == result.timestamp
        assert bool(result) is True

    def test_detail_mode(self, synth_320):
        """detail=True 时才提供详细诊断信息。"""
        result = detector_for_synth().detect(synth_320, detail=True)
        assert result.detected is True
        assert result.frame == SYN_OFFSET_FRAME
        d = result.details
        assert d is not None
        assert d.confidence is not None and d.confidence >= 0.97
        assert len(d.point_matches) == 4
        assert all(p.matched for p in d.point_matches)
        assert all(p.confidence >= 0.97 for p in d.point_matches)
        assert d.coarse_bracket is not None and d.fine_bracket is not None
        assert isinstance(d.stage_stats, dict)
        assert d.resolution == (320, 240)
        assert isinstance(d.message, str)

    def test_wrong_target_not_detected(self, synth_320):
        # 目标改为红色：合成视频为黄色，应不命中
        det = VideoColorDetector(
            target_color=(255, 0, 0),
            points=SYN_POINTS,
            scale_points=False,
            color_tolerance=10.0,
        )
        result = det.detect(synth_320)
        assert result.detected is False
        assert result.frame is None
        assert result.timestamp is None
        frame, time = result
        assert frame is None and time is None

    def test_progress_callback(self, synth_320):
        events = []
        result = detector_for_synth().detect(synth_320, progress_callback=lambda s, p, m: events.append(s))
        assert result.detected is True
        assert "done" in events

    def test_opencv_extractor(self, synth_320):
        det = VideoColorDetector(
            target_color=TARGET, points=SYN_POINTS, scale_points=False,
            color_tolerance=0.0, extractor="opencv",
        )
        result = det.detect(synth_320)
        assert result.detected is True
        assert result.frame == SYN_OFFSET_FRAME


class TestCoordinateScaling:
    def test_scale_points_unit(self):
        from detector.core.matcher import FrameMatcher

        m = FrameMatcher(
            target_color=TARGET,
            points=((20, 20), (1900, 20), (20, 1060), (1900, 1060)),
            scale_points=True,
            reference_resolution=(1920, 1080),
        )
        pts = m.set_resolution(320, 240)
        assert pts == [(3, 4), (317, 4), (3, 236), (317, 236)]

    def test_scaled_detection_end_to_end(self, tmp_path):
        # 用 320x240 视频 + 默认（1920x1080）检测点坐标，验证坐标缩放后仍可检测
        scaled = [(3, 4), (317, 4), (3, 236), (317, 236)]
        path = make_synthetic_video(
            str(tmp_path / "scaled.mkv"),
            width=320, height=240, fps=30, duration_sec=3.0,
            onset_sec=1.5, offset_sec=2.5, target_color=TARGET, points=tuple(scaled),
        )
        result = VideoColorDetector(  # 使用默认检测点与坐标缩放
            color_tolerance=0.0, confidence_threshold=0.97,
        ).detect(path)
        assert result.detected is True
        assert result.frame == SYN_OFFSET_FRAME

    def test_points_clamped_out_of_bounds(self):
        from detector.core.matcher import FrameMatcher

        m = FrameMatcher(
            target_color=TARGET,
            points=((5000, 5000),),  # 明显越界
            scale_points=False,
        )
        pts = m.set_resolution(320, 240)
        assert pts[0] == (319, 239)


@REQUIRE_REAL
class TestRealVideos:
    @pytest.mark.parametrize("name", ["input1", "input2", "input3"])
    def test_detect_real(self, real_videos, name):
        result = VideoColorDetector().detect(real_videos[name], detail=True)
        assert result.detected is True, name
        assert result.frame is not None
        # 结尾帧应落在调研得到的 [24, 26]s 区间内
        assert REAL_OFFSET_RANGE[0] <= result.timestamp <= REAL_OFFSET_RANGE[1], name
        d = result.details
        assert d.confidence >= 0.97
        assert len(d.point_matches) == 4
        assert all(p.matched for p in d.point_matches)
        # 精确阶段应是逐帧扫描的（探测帧数很小，远小于整片解码）
        total_probes = sum(d.stage_stats.values())
        assert total_probes < 80

    def test_tolerance_effect(self, real_videos):
        # 容差 0 + 阈值 0.999（严格精确匹配）：有损编码下的区域帧无法达到该置信度 → 不命中
        strict = VideoColorDetector(color_tolerance=0.0, confidence_threshold=0.999)
        result = strict.detect(real_videos["input1"])
        assert result.detected is False

    def test_threshold_below_region_conf_detects(self, real_videos):
        # 调低阈值仍能命中，且结果与默认一致
        default = VideoColorDetector()
        loose = VideoColorDetector(confidence_threshold=0.9)
        r1 = default.detect(real_videos["input1"])
        r2 = loose.detect(real_videos["input1"])
        assert r2.detected is True
        assert r2.frame == r1.frame


class TestErrors:
    def test_file_not_found(self):
        with pytest.raises(VideoNotFoundError):
            VideoColorDetector().detect("no_such_video_12345.mp4")

    def test_not_a_video(self, tmp_path):
        bad = os.path.join(str(tmp_path), "bad.txt")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("this is not a video")
        with pytest.raises(UnsupportedFormatError):
            VideoColorDetector().detect(bad)

    def test_all_errors_share_base(self):
        assert issubclass(VideoNotFoundError, DetectorError)
        assert issubclass(UnsupportedFormatError, DetectorError)
        assert issubclass(ConfigError, DetectorError)


class TestPublicAPI:
    def test_public_symbols(self):
        for name in [
            "VideoColorDetector", "Detection", "DetectionDetails", "DetectorConfig",
            "FrameMatcher", "FrameMatch", "PointMatch",
            "DetectorError", "VideoNotFoundError", "UnsupportedFormatError",
            "FFmpegError", "VideoDecodeError", "ConfigError",
            "hex_to_rgb", "rgb_to_hex", "color_confidence", "color_match",
            "DEFAULT_TARGET_COLOR", "DEFAULT_POINTS",
        ]:
            assert hasattr(detector, name), name

    def test_version(self):
        assert isinstance(detector.__version__, str)

    def test_config_dict_api(self):
        cfg = DetectorConfig().with_overrides(
            target_color=detector.hex_to_rgb("#F7F10F"),
            confidence_threshold=0.97,
            color_tolerance=10.0,
        )
        d = VideoColorDetector(cfg)
        assert d.config.target_color == TARGET
