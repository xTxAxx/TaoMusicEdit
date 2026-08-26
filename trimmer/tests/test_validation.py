# -*- coding: utf-8 -*-
"""参数校验单元测试。"""

import os

import pytest

from trimmer.core.errors import InputNotFoundError, UnsupportedFormatError, ValidationError
from trimmer.core.probe import AudioStream, MediaInfo, VideoStream
from trimmer.utils.validation import (
    validate_cut_params,
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
