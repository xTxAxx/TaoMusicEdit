# -*- coding: utf-8 -*-
"""Trimmer 命令构建与输出路径单元测试（不调用 ffmpeg）。"""

import os

from trimmer.core.probe import AudioStream, MediaInfo, VideoStream
from trimmer.core.trimmer import OutputMode, Trimmer, TrimmerConfig, build_command


def _media():
    video = VideoStream(codec="h264", width=1920, height=1080, fps=30.0,
                        bit_rate=1_000_000, pix_fmt="yuv420p")
    audio = AudioStream(codec="aac", sample_rate=48000, channels=2, bit_rate=128_000)
    return MediaInfo(path="x.mp4", duration=100.0, container=".mp4",
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
        # 起始时间用于 -ss，且不再使用 -t 截止：保留从 start_time 到末尾的内容
        cmd = self._cmd(TrimmerConfig(input_path="x"))
        assert cmd[cmd.index("-ss") + 1] == "10.000000000"
        assert cmd.index("-ss") < cmd.index("-i")
        assert "-t" not in cmd

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
