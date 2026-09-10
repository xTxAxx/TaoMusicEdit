# -*- coding: utf-8 -*-
"""配置模块单元测试。"""
import pytest

from detector.config import DetectorConfig
from detector.core.errors import ConfigError
from detector.core.detector import VideoColorDetector


class TestConfig:
    def test_defaults(self):
        cfg = DetectorConfig()
        assert cfg.target_color == (247, 241, 15)
        assert cfg.confidence_threshold == 0.97
        assert cfg.color_tolerance == 10.0
        assert cfg.points == ((20, 20), (1900, 20), (20, 1060), (1900, 1060))
        assert cfg.search_window == 40.0
        assert cfg.initial_start == 35.0
        assert cfg.coarse_step == 5.0
        assert cfg.fine_step == 1.0

    def test_validate_ok(self):
        DetectorConfig().validate()

    @pytest.mark.parametrize(
        "kw",
        [
            {"confidence_threshold": 1.5},
            {"confidence_threshold": -0.1},
            {"color_tolerance": -5.0},
            {"target_color": (300, 0, 0)},
            {"points": ()},
            {"points": ((1, 2, 3),)},
            {"search_window": 0},
            {"initial_start": -1},
            {"coarse_step": 0},
            {"fine_step": -1},
            {"fine_step": 6.0, "coarse_step": 5.0},
            {"extractor": "unknown"},
        ],
    )
    def test_validate_rejects(self, kw):
        cfg = DetectorConfig().with_overrides(**kw)
        with pytest.raises(ValueError):
            cfg.validate()

    def test_with_overrides(self):
        cfg = DetectorConfig().with_overrides(confidence_threshold=0.9, color_tolerance=15.0)
        assert cfg.confidence_threshold == 0.9
        assert cfg.color_tolerance == 15.0
        # 原对象不被修改
        assert DetectorConfig().confidence_threshold == 0.97

    def test_with_overrides_unknown_key(self):
        with pytest.raises(TypeError):
            DetectorConfig().with_overrides(not_a_key=1)

    def test_detector_rejects_invalid_config(self):
        with pytest.raises(ConfigError):
            VideoColorDetector(confidence_threshold=2.0)

    def test_detector_config_and_kwargs_conflict(self):
        with pytest.raises(TypeError):
            VideoColorDetector(DetectorConfig(), confidence_threshold=0.9)
