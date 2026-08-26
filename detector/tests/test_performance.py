# -*- coding: utf-8 -*-
"""性能测试：验证抽帧效率与整体检测耗时。

阈值设置较为宽松（本机平均约 2.8s），以保证在各类机器上稳定通过，
重点是验证“不整片解码”的核心设计。
"""
import time

from detector import VideoColorDetector
from detector.utils.ffmpeg import probe_video, read_frame
from detector.tests.conftest import REQUIRE_REAL


@REQUIRE_REAL
class TestFrameExtractionSpeed:
    def test_single_frame_extraction_fast(self, real_videos):
        """单帧抽取（每次一个 ffmpeg 进程 + 定位解码）应足够快。"""
        info = probe_video(real_videos["input1"])
        times = []
        for t in (5.0, 15.0, 25.0, 35.0):
            start = time.perf_counter()
            read_frame(info.path, t, info.width, info.height)
            times.append(time.perf_counter() - start)
        avg = sum(times) / len(times)
        assert avg < 0.8, f"单帧抽取过慢: {avg:.3f}s"


@REQUIRE_REAL
class TestDetectPerformance:
    def test_detect_fast_and_bounded_probes(self, real_videos):
        """整体检测应快，且探测帧数远小于视频总帧数（避免整片解码）。"""
        info = probe_video(real_videos["input1"])
        total_frames = info.nb_frames  # ~10654

        start = time.perf_counter()
        result = VideoColorDetector().detect(real_videos["input1"], detail=True)
        elapsed = time.perf_counter() - start

        assert result.detected is True
        assert elapsed < 15.0, f"检测耗时过长: {elapsed:.2f}s"

        probes = sum(result.details.stage_stats.values())
        assert probes < 80, f"探测帧数过多: {probes}"
        # 关键：探测帧数应远小于整片帧数（不整片解码）
        assert probes * 100 < total_frames

    def test_probe_count_breakdown(self, real_videos):
        """各阶段探测数量符合算法设计预期。"""
        result = VideoColorDetector().detect(real_videos["input1"], detail=True)
        stats = result.details.stage_stats
        # 粗扫：约 9 次；细化：<= coarse_step/fine_step=5 次
        assert stats["coarse"] <= 11
        assert stats["fine"] <= 5
        # 精确逐帧：<= 1s 区间 * 30fps + 少量余量
        assert stats["precise"] <= 40
