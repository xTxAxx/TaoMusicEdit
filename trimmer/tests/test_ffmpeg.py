# -*- coding: utf-8 -*-
"""FFmpeg 封装模块单元测试（不依赖真实 ffmpeg 命令）。"""

from trimmer.core.ffmpeg import detect_gpu_encoder, detect_gpu_vendor

_AVAILABLE = {
    "libx264", "aac",
    "h264_nvenc", "hevc_nvenc",
    "h264_qsv", "hevc_qsv",
    "h264_amf", "hevc_amf",
}


class TestDetectGpuEncoder:
    def test_no_vendor_uses_priority(self):
        assert detect_gpu_encoder("h264", _AVAILABLE) == "h264_nvenc"

    def test_amd_prefers_amf(self):
        assert detect_gpu_encoder("h264", _AVAILABLE, vendor="amd") == "h264_amf"

    def test_amd_hevc_prefers_amf(self):
        assert detect_gpu_encoder("hevc", _AVAILABLE, vendor="amd") == "hevc_amf"

    def test_nvidia_prefers_nvenc(self):
        assert detect_gpu_encoder("h264", _AVAILABLE, vendor="nvidia") == "h264_nvenc"

    def test_intel_prefers_qsv(self):
        assert detect_gpu_encoder("h264", _AVAILABLE, vendor="intel") == "h264_qsv"

    def test_vendor_encoder_absent_falls_back(self):
        # AMD 的 amf 编码器不存在时，回退到通用优先级
        available = {n for n in _AVAILABLE if "amf" not in n}
        assert detect_gpu_encoder("h264", available, vendor="amd") == "h264_nvenc"

    def test_exclude_skips_failed(self):
        # AMD 用户首选 amf 失败被排除后，尝试 amf 优先序中的下一个候选 nvenc
        assert detect_gpu_encoder("h264", _AVAILABLE, vendor="amd",
                                  exclude={"h264_amf"}) == "h264_nvenc"
        # 排除全部硬件编码器后返回 None
        assert detect_gpu_encoder(
            "h264", _AVAILABLE,
            exclude={"h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox", "h264_mf"},
        ) is None

    def test_no_available_returns_none(self):
        assert detect_gpu_encoder("h264", set()) is None

    def test_unknown_family_returns_none(self):
        assert detect_gpu_encoder("av1", _AVAILABLE) is None


class TestDetectGpuVendor:
    def test_returns_valid_value(self):
        # 返回值必须是合法厂商或 None（未知），不应抛异常
        assert detect_gpu_vendor() in ("nvidia", "amd", "intel", None)
