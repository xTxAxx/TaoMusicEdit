# -*- coding: utf-8 -*-
"""集成测试：生成测试视频并验证各裁剪模式（需要 FFmpeg，缺失时自动跳过）。"""

import os
import shutil
import subprocess

import pytest

from trimmer.core.errors import OutputExistsError, OverwriteInputError, TrimmerError
from trimmer.core.trimmer import OutputMode, Trimmer, TrimmerConfig

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 FFmpeg")


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    """生成一个 30fps、10 秒、带 AAC 音频的测试视频。"""
    out_dir = tmp_path_factory.mktemp("sample")
    path = str(out_dir / "sample.mp4")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-force_key_frames", "expr:gte(t,n_forced*1)",  # 每秒关键帧，保证流复制剪切点附近有关键帧
        "-c:a", "aac", "-shortest", path,
    ]
    subprocess.run(cmd, check=True)
    return path


def _probe_duration(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return float(proc.stdout.strip())


def _stream_types(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return proc.stdout.split()


class TestIntegration:
    def test_timestamp_copy(self, sample):
        result = Trimmer(TrimmerConfig(input_path=sample, timestamp=5.0, force=True)).run()
        assert len(result.output_files) == 1
        out = result.output_files[0]
        assert os.path.exists(out)
        assert _probe_duration(out) == pytest.approx(5.0, abs=0.5)

    def test_frame_reencode(self, sample):
        # 30fps → 第 150 帧 ≈ 5 秒
        result = Trimmer(TrimmerConfig(input_path=sample, frame=150, reencode=True, force=True)).run()
        out = result.output_files[0]
        assert os.path.exists(out)
        assert _probe_duration(out) == pytest.approx(5.0, abs=0.2)

    def test_frame_reencode_matches_resolution(self, sample):
        result = Trimmer(TrimmerConfig(input_path=sample, frame=60, reencode=True, force=True)).run()
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0",
             result.output_files[0]],
            capture_output=True, text=True,
        )
        assert proc.stdout.strip().startswith("320,240")

    def test_audio_only(self, sample):
        result = Trimmer(TrimmerConfig(input_path=sample, timestamp=3.0,
                                       output_mode=OutputMode.AUDIO_ONLY, force=True)).run()
        out = result.output_files[0]
        assert os.path.exists(out)
        assert _stream_types(out) == ["audio"]

    def test_video_only(self, sample):
        result = Trimmer(TrimmerConfig(input_path=sample, timestamp=3.0,
                                       output_mode=OutputMode.VIDEO_ONLY, force=True)).run()
        out = result.output_files[0]
        assert os.path.exists(out)
        types = _stream_types(out)
        assert "video" in types and "audio" not in types

    def test_split(self, sample):
        result = Trimmer(TrimmerConfig(input_path=sample, timestamp=3.0,
                                       output_mode=OutputMode.SPLIT, force=True)).run()
        assert len(result.output_files) == 2
        for f in result.output_files:
            assert os.path.exists(f)
        types = _stream_types(result.output_files[0])
        assert "video" in types and "audio" not in types
        assert _stream_types(result.output_files[1]) == ["audio"]

    def test_reencode_result_flags(self, sample):
        result = Trimmer(TrimmerConfig(input_path=sample, timestamp=3.0,
                                       reencode=True, force=True)).run()
        assert result.reencoded is True
        assert result.video_encoder in ("libx264", "h264_nvenc", "h264_qsv", "h264_amf")
        assert result.audio_encoder in ("aac",)

    def test_error_input_missing(self, sample, tmp_path):
        with pytest.raises(TrimmerError):
            Trimmer(TrimmerConfig(input_path=str(tmp_path / "nope.mp4"),
                                  timestamp=1.0)).run()

    def test_error_timestamp_exceeds(self, sample):
        with pytest.raises(TrimmerError):
            Trimmer(TrimmerConfig(input_path=sample, timestamp=999.0)).run()

    def test_error_no_cut_param(self, sample):
        with pytest.raises(TrimmerError):
            Trimmer(TrimmerConfig(input_path=sample)).run()

    def test_overwrite_confirmation(self, sample):
        # 输出已存在且未 force、无 confirm 回调 → 抛 OutputExistsError
        Trimmer(TrimmerConfig(input_path=sample, timestamp=1.0, force=True)).run()
        with pytest.raises(OutputExistsError):
            Trimmer(TrimmerConfig(input_path=sample, timestamp=1.0)).run()
        # 用户拒绝覆盖 → 抛 OutputExistsError
        with pytest.raises(OutputExistsError):
            Trimmer(TrimmerConfig(input_path=sample, timestamp=1.0)).run(confirm_cb=lambda p: False)
        # 用户同意覆盖 → 成功
        result = Trimmer(TrimmerConfig(input_path=sample, timestamp=1.0)).run(confirm_cb=lambda p: True)
        assert os.path.exists(result.output_files[0])

    def test_never_overwrite_input(self, sample):
        # 输出路径指向输入文件本身 → 拒绝覆盖原文件
        with pytest.raises(OverwriteInputError):
            Trimmer(TrimmerConfig(input_path=sample, timestamp=1.0, output=sample)).run()
