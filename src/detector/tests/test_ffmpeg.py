# -*- coding: utf-8 -*-
"""FFmpeg 帧提取工具单元测试。"""
import numpy as np
import pytest

from detector.utils.ffmpeg import (
    FFmpegExtractor,
    OpenCVExtractor,
    read_frame,
    read_frame_range,
    probe_video,
)
from detector.utils.color import color_confidence
from detector.tests.conftest import (
    REQUIRE_REAL,
    make_synthetic_video,
)

TARGET = (247, 241, 15)


@REQUIRE_REAL
class TestProbe:
    def test_probe_metadata(self, real_videos):
        info = probe_video(real_videos["input1"])
        assert info.has_video is True
        assert info.width == 1920
        assert info.height == 1080
        assert info.fps == pytest.approx(30.0, abs=0.5)
        assert info.duration > 200
        assert info.codec.lower() in ("h264", "hevc", "avc1")

    def test_probe_all_videos(self, real_videos):
        for name, path in real_videos.items():
            info = probe_video(path)
            assert info.width == 1920 and info.height == 1080, name


@REQUIRE_REAL
class TestReadFrame:
    def test_read_frame_shape(self, real_videos):
        info = probe_video(real_videos["input1"])
        frame = read_frame(info.path, 15.0, info.width, info.height)
        assert frame is not None
        assert frame.shape == (info.height, info.width, 3)
        assert frame.dtype == np.uint8

    def test_read_frame_in_region_is_target(self, real_videos):
        info = probe_video(real_videos["input1"])
        # 15s 处于目标颜色区域，四点应接近目标色
        frame = read_frame(info.path, 15.0, info.width, info.height)
        for (x, y) in [(20, 20), (1900, 20), (20, 1060), (1900, 1060)]:
            b, g, r = frame[y, x]
            assert color_confidence((r, g, b), TARGET, tolerance=10.0) >= 0.97

    def test_read_frame_out_of_range_returns_none(self, real_videos):
        info = probe_video(real_videos["input1"])
        assert read_frame(info.path, info.duration + 100, info.width, info.height) is None

    def test_read_frame_range(self, real_videos):
        info = probe_video(real_videos["input1"])
        frames = read_frame_range(info.path, 8.0, 9.0, info.width, info.height)
        # 1s 时长，30fps → 约 30 帧
        assert 20 <= len(frames) <= 31
        assert all(f.shape == (info.height, info.width, 3) for f in frames)


class TestSyntheticExtraction:
    def test_synthetic_read_frame_exact(self, tmp_path):
        path = make_synthetic_video(
            str(tmp_path / "s.mkv"), onset_sec=1.5, duration_sec=3.0
        )
        info = probe_video(path)
        assert info.width == 320 and info.height == 240
        assert info.fps == pytest.approx(30.0, abs=0.1)

        # 1.0s（onset 前）不匹配
        f0 = read_frame(path, 1.0, info.width, info.height)
        b, g, r = f0[5, 5]
        assert color_confidence((r, g, b), TARGET, tolerance=0.0) < 0.5

        # 2.0s（onset 后）精确命中
        f1 = read_frame(path, 2.0, info.width, info.height)
        b, g, r = f1[5, 5]
        assert (r, g, b) == TARGET

    def test_synthetic_read_frame_range_exact(self, tmp_path):
        path = make_synthetic_video(
            str(tmp_path / "s.mkv"), onset_sec=1.5, duration_sec=3.0, fps=30
        )
        info = probe_video(path)
        frames = read_frame_range(path, 1.4, 1.7, info.width, info.height)
        # 0.3s * 30fps ≈ 9 帧
        assert 5 <= len(frames) <= 11


@REQUIRE_REAL
class TestExtractors:
    def test_ffmpeg_extractor(self, real_videos):
        info = probe_video(real_videos["input1"])
        with FFmpegExtractor(info) as ex:
            frame = ex.read(10.0)
            assert frame is not None and frame.shape[0] == 1080
            frames = ex.read_range(8.0, 9.0)
            assert len(frames) > 0

    def test_opencv_extractor(self, real_videos):
        info = probe_video(real_videos["input1"])
        with OpenCVExtractor(info) as ex:
            frame = ex.read(10.0)
            assert frame is not None and frame.shape[0] == 1080
            frames = ex.read_range(8.0, 9.0)
            assert len(frames) > 0
