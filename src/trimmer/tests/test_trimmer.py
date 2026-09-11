# -*- coding: utf-8 -*-
"""Trimmer 命令构建与输出路径单元测试（不调用 ffmpeg）。"""

import os

import pytest

from trimmer.core.errors import FFmpegExecutionError
from trimmer.core.probe import AudioStream, MediaInfo, VideoStream
from trimmer.core.trimmer import OutputMode, Trimmer, TrimmerConfig, build_command


def _media():
    video = VideoStream(codec="h264", fps=30.0,
                        bit_rate=1_000_000, pix_fmt="yuv420p")
    audio = AudioStream(codec="aac", sample_rate=48000, channels=2, bit_rate=128_000)
    return MediaInfo(path="x.mp4", duration=100.0,
                     video=video, audio=audio, nb_frames=3000)


class TestBuildCommand:
    """build_command 纯函数测试。"""

    def _cmd(self, config, **kw):
        defaults = dict(video_encoder="copy", audio_encoder="copy",
                        include_video=True, include_audio=True, hwaccel_args=[])
        defaults.update(kw)
        return build_command(config, _media(), "/abs/in.mp4", 10.0, "/abs/out.mp4", **defaults)

    def test_copy_mode(self):
        cmd = self._cmd(TrimmerConfig(input_path="x"))
        assert cmd[0] == "-y"
        assert "-i" in cmd
        assert cmd[cmd.index("-i") + 1] == "/abs/in.mp4"
        assert cmd[cmd.index("-c:v") + 1] == "copy"
        assert cmd[cmd.index("-c:a") + 1] == "copy"
        assert "-b:v" not in cmd
        assert "-movflags" in cmd  # mp4 输出需要 +faststart
        assert cmd[-1] == "/abs/out.mp4"

    def test_start_time_is_seek_point(self):
        # 起始时间用于输入侧 -ss；未指定终点时不使用 -t/-to 截止：保留到片尾
        cmd = self._cmd(TrimmerConfig(input_path="x"))
        assert cmd[cmd.index("-ss") + 1] == "10.000000000"
        assert cmd.index("-ss") < cmd.index("-i")
        assert "-t" not in cmd
        assert "-to" not in cmd

    def test_reencode_matches_params(self):
        cfg = TrimmerConfig(input_path="x", reencode=True)
        cmd = self._cmd(cfg, video_encoder="libx264", audio_encoder="aac")
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-b:v") + 1] == "1000000"
        assert cmd[cmd.index("-r") + 1].startswith("30.")
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
        assert cmd[cmd.index("-c:a") + 1] == "aac"
        assert cmd[cmd.index("-b:a") + 1] == "128000"
        assert cmd[cmd.index("-ar") + 1] == "48000"
        assert cmd[cmd.index("-ac") + 1] == "2"

    def test_video_only(self):
        cmd = self._cmd(TrimmerConfig(input_path="x"), include_audio=False)
        assert "-an" in cmd
        assert cmd[cmd.index("-map") + 1] == "0:v:0?"

    def test_audio_only(self):
        cmd = self._cmd(TrimmerConfig(input_path="x"), include_video=False)
        assert "-vn" in cmd
        assert cmd[cmd.index("-map") + 1] == "0:a:0?"

    def test_hwaccel_args_prepended(self):
        cmd = self._cmd(TrimmerConfig(input_path="x"), hwaccel_args=["-hwaccel", "auto"])
        hw_idx = cmd.index("-hwaccel")
        i_idx = cmd.index("-i")
        assert hw_idx < i_idx  # -hwaccel 需在 -i 之前

    def test_mkv_no_faststart(self):
        cfg = TrimmerConfig(input_path="x")
        cmd = build_command(cfg, _media(), "/abs/in.mp4", 10.0, "/abs/out.mkv",
                            video_encoder="copy", audio_encoder="copy",
                            include_video=True, include_audio=True, hwaccel_args=[])
        assert "-movflags" not in cmd


class TestBuildCommandInterval:
    """build_command 区间模式测试（-to / -t 回退分支）。"""

    def _cmd(self, config, **kw):
        defaults = dict(video_encoder="copy", audio_encoder="copy",
                        include_video=True, include_audio=True, hwaccel_args=[])
        defaults.update(kw)
        return build_command(config, _media(), "/abs/in.mp4", 10.0, "/abs/out.mp4", **defaults)

    def test_input_side_to(self, monkeypatch):
        # FFmpeg >= 4.4：-to 为输入侧选项（-i 之前），与 -ss 同取输入时间轴绝对位置
        monkeypatch.setattr("trimmer.core.trimmer.supports_input_side_to", lambda: True)
        cmd = self._cmd(TrimmerConfig(input_path="x"), end_time=3600.0)
        assert cmd[cmd.index("-to") + 1] == "3600.000000000"
        assert cmd.index("-ss") < cmd.index("-to") < cmd.index("-i")
        assert "-t" not in cmd

    def test_fallback_output_side_t(self, monkeypatch):
        # 旧版 FFmpeg：回退输出侧 -t <end - start>（时长随起点吸附整体前移）
        monkeypatch.setattr("trimmer.core.trimmer.supports_input_side_to", lambda: False)
        cmd = self._cmd(TrimmerConfig(input_path="x"), end_time=3600.0)
        assert "-to" not in cmd
        assert cmd[cmd.index("-t") + 1] == "3590.000000000"
        assert cmd.index("-i") < cmd.index("-t")

    def test_end_applies_to_all_output_modes(self, monkeypatch):
        # 终点与输出模式正交：纯音频输出同样带 -to
        monkeypatch.setattr("trimmer.core.trimmer.supports_input_side_to", lambda: True)
        cmd = self._cmd(TrimmerConfig(input_path="x"), include_video=False,
                        include_audio=True, end_time=60.0)
        assert "-vn" in cmd
        assert cmd[cmd.index("-to") + 1] == "60.000000000"


class TestIntervalOutputCheck:
    """流复制区间模式的空输出保护测试（不调用 ffmpeg）。"""

    def _trimmer(self, tmp_path, **cfg_kw):
        video = tmp_path / "in.mp4"
        video.write_bytes(b"x")
        cfg_kw.setdefault("input_path", str(video))
        t = Trimmer(TrimmerConfig(**cfg_kw))
        t.input_path = os.path.abspath(str(video))
        t.media = _media()
        t.start_time = 1.0
        t.end_time = 2.0
        return t

    def test_empty_output_raises(self, tmp_path, monkeypatch):
        # 输出探测时长为 0 → 明确报错而非产出空文件
        t = self._trimmer(tmp_path, timestamp=1.0, end_timestamp=2.0)
        out = tmp_path / "out.mp4"
        out.write_bytes(b"")
        monkeypatch.setattr("trimmer.core.trimmer.probe", lambda p: MediaInfo(
            path=p, duration=0.0, video=None, audio=None))
        with pytest.raises(FFmpegExecutionError):
            t._check_interval_output(str(out))

    def test_nonempty_output_passes(self, tmp_path, monkeypatch):
        t = self._trimmer(tmp_path, timestamp=1.0, end_timestamp=2.0)
        out = tmp_path / "out.mp4"
        out.write_bytes(b"data")
        monkeypatch.setattr("trimmer.core.trimmer.probe", lambda p: MediaInfo(
            path=p, duration=1.0, video=None, audio=None))
        t._check_interval_output(str(out))  # 不抛异常

    def test_probe_failure_treated_as_empty(self, tmp_path, monkeypatch):
        # 输出探测失败（可能为损坏的空文件）同样按空区间报错
        t = self._trimmer(tmp_path, timestamp=1.0, end_timestamp=2.0)
        out = tmp_path / "out.mp4"
        out.write_bytes(b"")

        def _raise(path):
            raise FFmpegExecutionError("probe failed")

        monkeypatch.setattr("trimmer.core.trimmer.probe", _raise)
        with pytest.raises(FFmpegExecutionError):
            t._check_interval_output(str(out))


class TestOutputPaths:
    """输出路径解析测试。"""

    def _trimmer(self, tmp_path, **cfg_kw):
        video = tmp_path / "in.mp4"
        video.write_bytes(b"x")
        cfg_kw.setdefault("input_path", str(video))
        t = Trimmer(TrimmerConfig(**cfg_kw))
        t.input_path = os.path.abspath(str(video))
        t.media = _media()
        return t

    def test_default_in_input_dir(self, tmp_path):
        paths = self._trimmer(tmp_path, timestamp=1.0)._resolve_output_paths()
        assert paths["main"] == os.path.join(os.path.abspath(str(tmp_path)), "in_trim.mp4")

    def test_custom_dir_and_suffix(self, tmp_path):
        outdir = tmp_path / "out"
        paths = self._trimmer(tmp_path, timestamp=1.0, output_dir=str(outdir),
                              suffix="_cut")._resolve_output_paths()
        assert paths["main"] == os.path.join(os.path.abspath(str(outdir)), "in_cut.mp4")

    def test_split_naming(self, tmp_path):
        paths = self._trimmer(tmp_path, timestamp=1.0,
                              output_mode=OutputMode.SPLIT)._resolve_output_paths()
        assert paths["video"].endswith("in_trim_video.mp4")
        assert paths["audio"].endswith("in_trim_audio.m4a")  # aac → m4a

    def test_custom_output_without_ext(self, tmp_path):
        paths = self._trimmer(tmp_path, timestamp=1.0,
                              output=str(tmp_path / "custom"))._resolve_output_paths()
        assert paths["main"] == os.path.join(os.path.abspath(str(tmp_path)), "custom.mp4")

    def test_custom_output_with_ext(self, tmp_path):
        paths = self._trimmer(tmp_path, timestamp=1.0,
                              output=str(tmp_path / "custom.mkv"))._resolve_output_paths()
        assert paths["main"] == os.path.join(os.path.abspath(str(tmp_path)), "custom.mkv")


class TestAtomicOutput:
    """输出经临时文件落盘的测试（monkeypatch FFmpegRunner，不调用 ffmpeg）。"""

    class _FakeRunner:
        """模拟 ffmpeg：向参数末位的输出路径写入内容，可注入失败行为。"""

        def __init__(self, behavior=None):
            self._behavior = behavior

        def run(self, args, total_duration=None, progress_cb=None, cancel_event=None):
            out = args[-1]
            if callable(self._behavior):
                self._behavior(out)
            else:
                with open(out, "wb") as f:
                    f.write(b"out-content")

    def _trimmer(self, tmp_path, **cfg_kw):
        video = tmp_path / "in.mp4"
        video.write_bytes(b"x")
        cfg_kw.setdefault("input_path", str(video))
        t = Trimmer(TrimmerConfig(timestamp=1.0, **cfg_kw))
        t.input_path = os.path.abspath(str(video))
        t.media = _media()
        t.out_duration = 10.0
        return t

    def _run_one_plan(self, tmp_path, monkeypatch, behavior=None, **cfg_kw):
        """跑通 _build_plans → _execute 链路（单计划模式），返回最终输出路径。"""
        t = self._trimmer(tmp_path, **cfg_kw)
        t.output_paths = t._resolve_output_paths()
        monkeypatch.setattr("trimmer.core.trimmer.FFmpegRunner",
                            lambda: self._FakeRunner(behavior))
        (video_enc, audio_enc, hwaccel, _gpu) = ("copy", "copy", [], None)
        plans = t._build_plans(video_enc, audio_enc, hwaccel)
        assert len(plans) == 1
        out_path, tmp_path_, args, _label = plans[0]
        t._execute(out_path, tmp_path_, args, "测试", None)
        return out_path

    def test_tmp_path_keeps_extension(self):
        # 临时路径在扩展名前插入 .part：ffmpeg 靠扩展名推断封装格式，不能破坏
        assert Trimmer._tmp_path_for("/x/video_trim.mp4").endswith("video_trim.part.mp4")
        assert Trimmer._tmp_path_for("/x/audio.m4a").endswith("audio.part.m4a")

    def test_success_renames_tmp_to_final(self, tmp_path, monkeypatch):
        out = self._run_one_plan(tmp_path, monkeypatch)
        assert os.path.isfile(out)
        with open(out, "rb") as f:
            assert f.read() == b"out-content"
        assert not os.path.exists(Trimmer._tmp_path_for(out))

    def test_cancel_cleans_tmp_keeps_final(self, tmp_path, monkeypatch):
        # 中断（KeyboardInterrupt）：临时文件被清理，最终路径保持原状
        out_file = tmp_path / "in_trim.mp4"
        out_file.write_bytes(b"old-final")

        def _cancel_and_write(out):
            with open(out, "wb") as f:
                f.write(b"partial")
            raise KeyboardInterrupt("用户取消")

        with pytest.raises(KeyboardInterrupt):
            self._run_one_plan(tmp_path, monkeypatch, behavior=_cancel_and_write)
        assert out_file.read_bytes() == b"old-final"
        assert not os.path.exists(Trimmer._tmp_path_for(str(out_file)))

    def test_ffmpeg_failure_cleans_tmp(self, tmp_path, monkeypatch):
        out_file = tmp_path / "in_trim.mp4"

        def _fail(out):
            with open(out, "wb") as f:
                f.write(b"garbage")
            raise FFmpegExecutionError("ffmpeg 执行失败（退出码 1）。")

        with pytest.raises(FFmpegExecutionError):
            self._run_one_plan(tmp_path, monkeypatch, behavior=_fail)
        assert not os.path.exists(out_file)  # 最终路径从未被触碰
        assert not os.path.exists(Trimmer._tmp_path_for(str(out_file)))

    def test_overwrite_replaces_existing_final(self, tmp_path, monkeypatch):
        out_file = tmp_path / "in_trim.mp4"
        out_file.write_bytes(b"old")
        out = self._run_one_plan(tmp_path, monkeypatch)
        with open(out, "rb") as f:
            assert f.read() == b"out-content"
        assert not os.path.exists(Trimmer._tmp_path_for(str(out_file)))
