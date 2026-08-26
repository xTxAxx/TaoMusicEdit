# -*- coding: utf-8 -*-
"""CLI 参数解析单元测试。"""

import pytest

from trimmer.cli import build_config, build_parser
from trimmer.core.trimmer import OutputMode


def parse(*argv):
    return build_parser().parse_args(list(argv))


class TestParse:
    def test_minimal(self):
        args = parse("a.mp4", "-t", "24.9")
        assert args.input == "a.mp4"
        assert args.timestamp == 24.9
        assert args.frame is None

    def test_short_options(self):
        args = parse("a.mp4", "-f", "100", "-o", "out.mkv", "-d", "dir",
                     "-s", "_cut", "-y", "-r")
        assert args.frame == 100
        assert args.output == "out.mkv"
        assert args.output_dir == "dir"
        assert args.suffix == "_cut"
        assert args.force is True
        assert args.reencode is True

    def test_long_options(self):
        args = parse("a.mp4", "--timestamp", "1.5", "--output", "out.mp4",
                     "--output-dir", "dir", "--suffix", "_x", "--force",
                     "--reencode", "--audio-codec", "libmp3lame")
        assert args.timestamp == 1.5
        assert args.audio_codec == "libmp3lame"

    def test_defaults(self):
        args = parse("a.mp4", "-t", "1.5")
        assert args.suffix == "_trim"
        assert args.hw_accel == "auto"
        assert args.force is False
        assert args.reencode is False
        assert args.output is None
        assert args.output_dir is None

    def test_timestamp_frame_exclusive(self):
        with pytest.raises(SystemExit):
            parse("a.mp4", "-t", "1.0", "-f", "10")

    def test_output_modes_exclusive(self):
        with pytest.raises(SystemExit):
            parse("a.mp4", "-t", "1.0", "--audio-only", "--split")

    def test_hw_accel_choices(self):
        assert parse("a.mp4", "-t", "1.0", "--hw-accel", "none").hw_accel == "none"
        with pytest.raises(SystemExit):
            parse("a.mp4", "-t", "1.0", "--hw-accel", "bogus")

    def test_help(self, capsys):
        with pytest.raises(SystemExit):
            parse("-h")
        out = capsys.readouterr().out
        assert "--timestamp" in out and "--frame" in out and "--reencode" in out
        assert "--hw-accel" in out and "--split" in out


class TestBuildConfig:
    def test_default_mode(self):
        cfg = build_config(parse("a.mp4", "-t", "1.0"))
        assert cfg.output_mode == OutputMode.FULL

    def test_video_only(self):
        cfg = build_config(parse("a.mp4", "-t", "1.0", "--no-audio"))
        assert cfg.output_mode == OutputMode.VIDEO_ONLY

    def test_video_only_alias(self):
        cfg = build_config(parse("a.mp4", "-t", "1.0", "--video-only"))
        assert cfg.output_mode == OutputMode.VIDEO_ONLY

    def test_audio_only(self):
        cfg = build_config(parse("a.mp4", "-t", "1.0", "--audio-only"))
        assert cfg.output_mode == OutputMode.AUDIO_ONLY

    def test_split(self):
        cfg = build_config(parse("a.mp4", "-t", "1.0", "--split"))
        assert cfg.output_mode == OutputMode.SPLIT

    def test_fields_passthrough(self):
        cfg = build_config(parse("a.mp4", "-t", "2.0"))
        assert cfg.timestamp == 2.0
        assert cfg.force is False
        assert cfg.suffix == "_trim"
        cfg = build_config(parse("a.mp4", "-f", "60"))
        assert cfg.frame == 60
