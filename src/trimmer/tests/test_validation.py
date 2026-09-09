# -*- coding: utf-8 -*-
"""参数校验单元测试。"""

import os

import pytest

from trimmer.core.errors import InputNotFoundError, UnsupportedFormatError, ValidationError
from trimmer.core.probe import AudioStream, MediaInfo, VideoStream
from trimmer.utils.validation import (
    validate_cut_params,
    validate_end_after_start,
    validate_end_params,
    validate_frame,
    validate_input_path,
    validate_timestamp,
)


def _media(duration=100.0, fps=30.0, nb_frames=None):
    video = VideoStream(codec="h264", width=1920, height=1080, fps=fps,
                        bit_rate=1000, pix_fmt="yuv420p")
    audio = AudioStream(codec="aac", sample_rate=48000, channels=2, bit_rate=128)
    return MediaInfo(path="x", duration=duration, container=".mp4",
                     video=video, audio=audio, nb_frames=nb_frames)


class TestCutParams:
    """时间戳 / 帧序号二选一校验。"""

    def test_neither(self):
        with pytest.raises(ValidationError):
            validate_cut_params(None, None)

    def test_both(self):
        with pytest.raises(ValidationError):
            validate_cut_params(1.0, 30)

    def test_timestamp(self):
        assert validate_cut_params(5.0, None) == ("timestamp", 5.0)

    def test_frame(self):
        assert validate_cut_params(None, 30) == ("frame", 30)


class TestTimestamp:
    """时间戳校验。"""

    def test_valid(self):
        assert validate_timestamp(24.9, _media()) == 24.9

    def test_int_accept(self):
        assert validate_timestamp(5, _media()) == 5.0

    def test_non_positive(self):
        with pytest.raises(ValidationError):
            validate_timestamp(0, _media())

    def test_negative(self):
        with pytest.raises(ValidationError):
            validate_timestamp(-1.0, _media())

    def test_exceeds_duration(self):
        with pytest.raises(ValidationError):
            validate_timestamp(200.0, _media(duration=100.0))

    def test_bool_rejected(self):
        with pytest.raises(ValidationError):
            validate_timestamp(True, _media())

    def test_string_rejected(self):
        with pytest.raises(ValidationError):
            validate_timestamp("abc", _media())


class TestFrame:
    """帧序号校验。"""

    def test_valid(self):
        # 第 90 帧起始时间 = (90-1)/30 ≈ 2.9667s
        assert validate_frame(90, _media(fps=30.0)) == pytest.approx(89 / 30.0)

    def test_zero(self):
        with pytest.raises(ValidationError):
            validate_frame(0, _media())

    def test_negative(self):
        with pytest.raises(ValidationError):
            validate_frame(-5, _media())

    def test_exceeds_total(self):
        with pytest.raises(ValidationError):
            validate_frame(10000, _media(fps=30.0, duration=100.0, nb_frames=3000))

    def test_bool_rejected(self):
        with pytest.raises(ValidationError):
            validate_frame(True, _media())

    def test_float_rejected(self):
        with pytest.raises(ValidationError):
            validate_frame(1.5, _media())

    def test_no_fps(self):
        media = _media()
        media.video = VideoStream(codec="h264", width=1, height=1, fps=0.0,
                                  bit_rate=None, pix_fmt="")
        with pytest.raises(ValidationError):
            validate_frame(10, media)


class TestEndParams:
    """终点参数二选一校验（均可缺省 = 保留到片尾）。"""

    def test_neither(self):
        assert validate_end_params(None, None) == (None, None)

    def test_both(self):
        with pytest.raises(ValidationError):
            validate_end_params(60.0, 1800)

    def test_timestamp(self):
        assert validate_end_params(60.0, None) == ("timestamp", 60.0)

    def test_frame(self):
        assert validate_end_params(None, 1800) == ("frame", 1800)


class TestEndTimestamp:
    """终点时间戳校验（范围规则与起点对称，标签区分错误消息）。"""

    def test_valid(self):
        assert validate_timestamp(60.0, _media(), label="终点时间戳") == 60.0

    def test_non_positive(self):
        with pytest.raises(ValidationError):
            validate_timestamp(0, _media(), label="终点时间戳")

    def test_exceeds_duration(self):
        with pytest.raises(ValidationError):
            validate_timestamp(200.0, _media(duration=100.0), label="终点时间戳")

    def test_error_message_labeled(self):
        with pytest.raises(ValidationError, match="终点时间戳"):
            validate_timestamp(0, _media(), label="终点时间戳")


class TestEndFrame:
    """终点帧序号校验（范围规则与起点对称）。"""

    def test_valid(self):
        # 第 1801 帧终点时间 = 1800/30 = 60s
        assert validate_frame(1801, _media(fps=30.0), label="终点帧序号") == pytest.approx(60.0)

    def test_exceeds_total(self):
        with pytest.raises(ValidationError):
            validate_frame(10000, _media(fps=30.0, nb_frames=3000), label="终点帧序号")

    def test_zero(self):
        with pytest.raises(ValidationError):
            validate_frame(0, _media(), label="终点帧序号")


class TestEndAfterStart:
    """终点 > 起点顺序校验（换算为时间戳后比较）。"""

    def test_valid(self):
        assert validate_end_after_start(60.0, 24.9) == 60.0

    def test_equal_rejected(self):
        with pytest.raises(ValidationError):
            validate_end_after_start(24.9, 24.9)

    def test_less_rejected(self):
        with pytest.raises(ValidationError):
            validate_end_after_start(10.0, 24.9)

    def test_mixed_modes_via_conversion(self):
        # 起点=帧序号 735（24.467s）、终点=时间戳 24.5s → 换算后终点 > 起点
        start_time = (735 - 1) / 30.0
        assert validate_end_after_start(24.5, start_time) == 24.5


class TestInputPath:
    """输入路径校验。"""

    def test_missing(self, tmp_path):
        with pytest.raises(InputNotFoundError):
            validate_input_path(str(tmp_path / "nope.mp4"))

    def test_empty(self):
        with pytest.raises(ValidationError):
            validate_input_path("")

    def test_unsupported(self, tmp_path):
        p = tmp_path / "x.txt"
        p.write_text("hello")
        with pytest.raises(UnsupportedFormatError):
            validate_input_path(str(p))

    def test_directory(self, tmp_path):
        with pytest.raises(ValidationError):
            validate_input_path(str(tmp_path))

    def test_supported_returns_abspath(self, tmp_path):
        p = tmp_path / "x.mp4"
        p.write_bytes(b"f")
        assert validate_input_path(str(p)) == os.path.abspath(str(p))
