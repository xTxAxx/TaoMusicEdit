# -*- coding: utf-8 -*-
"""颜色转换与颜色匹配单元测试。"""
import numpy as np
import pytest

from detector.utils.color import (
    MAX_RGB_DISTANCE,
    color_confidence,
    color_match,
    hex_to_rgb,
    rgb_to_hex,
)

TARGET = (247, 241, 15)  # #F7F10F


class TestHexConversion:
    def test_hex_to_rgb(self):
        assert hex_to_rgb("#F7F10F") == (247, 241, 15)
        assert hex_to_rgb("f7f10f") == (247, 241, 15)
        assert hex_to_rgb("#000000") == (0, 0, 0)
        assert hex_to_rgb("#FFFFFF") == (255, 255, 255)

    def test_hex_to_rgb_invalid(self):
        for bad in ["#12345", "#GGGGGG", "12345", ""]:
            with pytest.raises(ValueError):
                hex_to_rgb(bad)

    def test_rgb_to_hex(self):
        assert rgb_to_hex((247, 241, 15)) == "#F7F10F"
        assert rgb_to_hex((0, 0, 0)) == "#000000"
        assert rgb_to_hex((300, -5, 20)) == "#FF0014"  # 越界钳制

    def test_roundtrip(self):
        assert hex_to_rgb(rgb_to_hex(TARGET)) == TARGET


class TestColorConfidence:
    def test_exact_match_is_one(self):
        assert color_confidence(TARGET, TARGET, tolerance=10.0) == pytest.approx(1.0)
        assert color_confidence(TARGET, TARGET, tolerance=0.0) == pytest.approx(1.0)

    def test_within_tolerance_is_one(self):
        # 目标色向绿色偏移 8（小于容差 10）
        near = (247, 249, 15)
        assert color_confidence(near, TARGET, tolerance=10.0) == pytest.approx(1.0)

    def test_beyond_tolerance_decreases(self):
        # 容差 0 时退化为归一化欧氏距离
        pixel = (247, 251, 15)  # 距离 10
        c = color_confidence(pixel, TARGET, tolerance=0.0)
        assert c == pytest.approx(1.0 - 10.0 / MAX_RGB_DISTANCE)

    def test_far_color_low_confidence(self):
        # 黑色与目标色距离约 345（非对角最大距离），置信度应很低
        c = color_confidence((0, 0, 0), TARGET, tolerance=0.0)
        assert 0.0 < c < 0.3
        c2 = color_confidence((18, 18, 18), TARGET, tolerance=10.0)
        assert c2 < 0.5

    def test_monotonic_decreasing(self):
        confs = [
            color_confidence(p, TARGET, tolerance=0.0)
            for p in [(247, 240, 15), (240, 230, 20), (200, 200, 50), (100, 100, 100)]
        ]
        assert all(confs[i] > confs[i + 1] for i in range(len(confs) - 1))

    def test_negative_tolerance_rejected(self):
        with pytest.raises(ValueError):
            color_confidence(TARGET, TARGET, tolerance=-1.0)

    def test_accepts_numpy_input(self):
        assert color_confidence(
            np.array([247, 241, 15]), np.array(TARGET), tolerance=10.0
        ) == pytest.approx(1.0)


class TestMatch:
    def test_threshold_boundary(self):
        assert color_match(0.97, 0.97) is True
        assert color_match(0.9699, 0.97) is False
        assert color_match(0.971, 0.97) is True

    def test_real_region_color_matches_at_097(self):
        # 真实视频中命中区域的像素约 (245, 254, 19)，应能通过 0.97 阈值
        conf = color_confidence((245, 254, 19), TARGET, tolerance=10.0)
        assert color_match(conf, 0.97) is True
