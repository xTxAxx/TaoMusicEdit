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


class TestIntervalIntegration:
    """区间裁剪集成测试（A2 / A3 / A4 验收）。"""

    def test_reencode_duration_exact(self, sample):
        # A2：重编码区间 [2s, 6s)，输出时长 = end - start（误差 ≤ 1 帧级别）
        result = Trimmer(TrimmerConfig(input_path=sample, timestamp=2.0,
                                       end_timestamp=6.0, reencode=True, force=True)).run()
        out = result.output_files[0]
        assert os.path.exists(out)
        assert _probe_duration(out) == pytest.approx(4.0, abs=1 / 30.0 + 0.02)
        assert result.cut_duration == pytest.approx(4.0)
        assert result.end_timestamp == pytest.approx(6.0)
        assert result.end_frame is None

    def test_stream_copy_interval(self, sample):
        # A3：流复制区间 [2s, 8s)——起点吸附关键帧（每秒一个），终点包级截断
        result = Trimmer(TrimmerConfig(input_path=sample, timestamp=2.0,
                                       end_timestamp=8.0, force=True)).run()
        out = result.output_files[0]
        assert os.path.exists(out)
        dur = _probe_duration(out)
        assert dur > 0
        assert dur == pytest.approx(6.0, abs=0.5)

    def test_mixed_start_frame_end_timestamp(self, sample):
        # 起点帧序号 × 终点时间戳自由组合：第 91 帧（3s）→ 7s
        result = Trimmer(TrimmerConfig(input_path=sample, frame=91,
                                       end_timestamp=7.0, reencode=True, force=True)).run()
        assert _probe_duration(result.output_files[0]) == pytest.approx(4.0, abs=1 / 30.0 + 0.02)
        assert result.frame == 91
        assert result.end_timestamp == pytest.approx(7.0)

    def test_end_frame_mode(self, sample):
        # 终点帧序号：[第 61 帧, 第 181 帧) = [2s, 6s)
        result = Trimmer(TrimmerConfig(input_path=sample, frame=61, end_frame=181,
                                       reencode=True, force=True)).run()
        assert _probe_duration(result.output_files[0]) == pytest.approx(4.0, abs=1 / 30.0 + 0.02)
        assert result.end_frame == 181

    def test_error_end_not_after_start(self, sample):
        # A4：终点 ≤ 起点 → VALIDATION_ERROR
        with pytest.raises(TrimmerError):
            Trimmer(TrimmerConfig(input_path=sample, timestamp=5.0,
                                  end_timestamp=5.0, force=True)).run()
        with pytest.raises(TrimmerError):
            Trimmer(TrimmerConfig(input_path=sample, timestamp=5.0,
                                  end_timestamp=4.0, force=True)).run()

    def test_error_end_exceeds_duration(self, sample):
        # A4：终点超范围 → VALIDATION_ERROR
        with pytest.raises(TrimmerError):
            Trimmer(TrimmerConfig(input_path=sample, timestamp=1.0,
                                  end_timestamp=999.0, force=True)).run()

    def test_error_end_exceeds_total_frames(self, sample):
        with pytest.raises(TrimmerError):
            Trimmer(TrimmerConfig(input_path=sample, frame=30,
                                  end_frame=100000, force=True)).run()

    def test_error_end_both_modes(self, sample):
        # A4：end_timestamp 与 end_frame 冲突 → VALIDATION_ERROR
        with pytest.raises(TrimmerError):
            Trimmer(TrimmerConfig(input_path=sample, timestamp=1.0,
                                  end_timestamp=5.0, end_frame=150, force=True)).run()

    def test_end_at_duration_boundary(self, sample):
        # 终点 = 视频时长（10s）：等价于保留 [2s, 片尾]
        result = Trimmer(TrimmerConfig(input_path=sample, timestamp=2.0,
                                       end_timestamp=10.0, force=True)).run()
        out = result.output_files[0]
        assert _probe_duration(out) == pytest.approx(8.0, abs=0.5)
