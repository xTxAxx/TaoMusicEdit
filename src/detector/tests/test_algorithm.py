# -*- coding: utf-8 -*-
"""核心算法「反向跳帧 + 局部细化」单元测试（纯逻辑，无需真实视频）。"""
from typing import List

import pytest

from detector.core.algorithm import FrameInfo, OnsetResult, detect_offset, detect_onset

FPS = 30
WINDOW = 40.0


def make_probe(onset_sec: float, duration: float = WINDOW):
    """构造模拟「onset_sec 起命中」的探针与逐帧扫描器。"""

    def probe_time(t: float) -> bool:
        return 0.0 <= t <= duration and t >= onset_sec

    def scan_frames(t0: float, t1: float) -> List[FrameInfo]:
        start = int(round(t0 * FPS))
        end = int(round(t1 * FPS))
        frames = []
        for i in range(start, end + 1):
            ts = i / FPS
            frames.append(FrameInfo(i, ts, probe_time(ts)))
        return frames

    return probe_time, scan_frames


def make_region_probe(onset_sec: float, offset_sec: float, duration: float = WINDOW):
    """构造模拟「[onset_sec, offset_sec] 区间命中」的探针与逐帧扫描器。"""

    def probe_time(t: float) -> bool:
        return 0.0 <= t <= duration and onset_sec <= t <= offset_sec

    def scan_frames(t0: float, t1: float) -> List[FrameInfo]:
        start = int(round(t0 * FPS))
        end = int(round(t1 * FPS))
        frames = []
        for i in range(start, end + 1):
            ts = i / FPS
            frames.append(FrameInfo(i, ts, probe_time(ts)))
        return frames

    return probe_time, scan_frames


def run(onset_sec: float, duration: float = WINDOW, **kw) -> OnsetResult:
    probe_time, scan_frames = make_probe(onset_sec, duration)
    return detect_onset(
        probe_time,
        scan_frames,
        search_window=WINDOW,
        initial_start=35.0,
        coarse_step=5.0,
        fine_step=1.0,
        **kw,
    )


def run_offset(onset_sec: float, offset_sec: float, duration: float = WINDOW, **kw) -> OnsetResult:
    probe_time, scan_frames = make_region_probe(onset_sec, offset_sec, duration)
    return detect_offset(
        probe_time,
        scan_frames,
        search_window=WINDOW,
        initial_start=35.0,
        coarse_step=5.0,
        fine_step=1.0,
        **kw,
    )


class TestNoMatch:
    def test_never_matches(self):
        res = run(onset_sec=WINDOW + 100)  # onset 在窗口外
        assert res.detected is False
        assert res.target_frame is None
        assert res.coarse_bracket is None

    def test_video_shorter_than_window(self):
        res = run(onset_sec=60.0, duration=10.0)  # onset 超出视频时长
        assert res.detected is False


class TestBasicOnset:
    def test_onset_at_exact_second(self):
        res = run(onset_sec=10.0)
        assert res.detected is True
        assert res.target_frame is not None
        assert res.target_frame.frame_index == 300  # 10.0 * 30
        assert res.target_frame.timestamp == pytest.approx(10.0)
        # 定位区间 (5, 10] -> 细化 (9, 10]
        assert res.coarse_bracket == (5.0, 10.0)
        assert res.fine_bracket == (9.0, 10.0)

    def test_onset_between_seconds(self):
        res = run(onset_sec=8.3)
        assert res.detected is True
        assert res.target_frame is not None
        assert res.target_frame.frame_index == int(round(8.3 * FPS))  # 249
        assert 8.0 <= res.target_frame.timestamp <= 9.0
        assert res.coarse_bracket == (5.0, 10.0)

    def test_target_is_first_matching_frame(self):
        # 逐帧扫描应返回区间内第一个匹配帧
        probe_time, scan_frames = make_probe(9.5)
        res = detect_onset(
            probe_time, scan_frames,
            search_window=WINDOW, initial_start=35.0, coarse_step=5.0, fine_step=1.0,
        )
        assert res.detected is True
        assert res.target_frame.frame_index == int(round(9.5 * FPS))


class TestEdgeOnset:
    def test_onset_at_window_start(self):
        res = run(onset_sec=0.5)
        assert res.detected is True
        assert res.target_frame is not None
        assert res.target_frame.frame_index == int(round(0.5 * FPS))

    def test_onset_above_initial_start(self):
        # onset 位于 35s 之后、窗口内：(35, 40] 缝隙由兜底细扫覆盖
        res = run(onset_sec=37.5)
        assert res.detected is True
        assert res.target_frame is not None
        assert res.target_frame.frame_index == int(round(37.5 * FPS))

    def test_onset_in_coarse_gap(self):
        # onset 位于粗网格缝隙 (12.5, 14)，窄区域由兜底细扫捕获
        res = run(onset_sec=13.2)
        assert res.detected is True
        assert res.target_frame is not None
        assert res.target_frame.frame_index == int(round(13.2 * FPS))


class TestProgressCallback:
    def test_progress_called(self):
        stages = []
        run(10.0, progress_cb=lambda stage, prog, msg: stages.append((stage, prog)))
        assert len(stages) > 0
        # 包含关键阶段
        stage_names = {s for s, _ in stages}
        assert {"coarse", "locate", "fine", "precise", "done"} <= stage_names
        # 进度单调递增（允许相等）
        progresses = [p for _, p in stages]
        assert all(b >= a for a, b in zip(progresses, progresses[1:]))
        assert progresses[-1] == pytest.approx(1.0)


class TestValidation:
    def test_invalid_steps(self):
        probe_time, scan_frames = make_probe(10.0)
        with pytest.raises(ValueError):
            detect_onset(probe_time, scan_frames, search_window=40.0,
                         initial_start=35.0, coarse_step=-1.0, fine_step=1.0)
        with pytest.raises(ValueError):
            detect_onset(probe_time, scan_frames, search_window=40.0,
                         initial_start=35.0, coarse_step=5.0, fine_step=10.0)


class TestBasicOffset:
    def test_offset_at_exact_second(self):
        # 区域 [10, 24]s：最后一个命中帧应落在 24s
        res = run_offset(onset_sec=10.0, offset_sec=24.0)
        assert res.detected is True
        assert res.target_frame is not None
        assert res.target_frame.frame_index == 720  # 24.0 * 30
        assert res.target_frame.timestamp == pytest.approx(24.0)
        # 定位区间 (20, 25] -> 细化 (24, 25]
        assert res.coarse_bracket == (20.0, 25.0)
        assert res.fine_bracket == (24.0, 25.0)

    def test_offset_between_seconds(self):
        res = run_offset(onset_sec=8.0, offset_sec=24.4667)
        assert res.detected is True
        assert res.target_frame is not None
        assert res.target_frame.frame_index == int(round(24.4667 * FPS))  # 734
        assert 24.0 <= res.target_frame.timestamp <= 25.0
        assert res.coarse_bracket == (20.0, 25.0)

    def test_target_is_last_matching_frame(self):
        # 逐帧扫描应返回区间内最后一个匹配帧（而非第一个）
        probe_time, scan_frames = make_region_probe(9.0, 23.5)
        res = detect_offset(
            probe_time, scan_frames,
            search_window=WINDOW, initial_start=35.0, coarse_step=5.0, fine_step=1.0,
        )
        assert res.detected is True
        assert res.target_frame.frame_index == int(round(23.5 * FPS))  # 705


class TestEdgeOffset:
    def test_offset_at_window_end(self):
        # 区域持续到窗口末端：仍能定位最后一个命中帧
        res = run_offset(onset_sec=10.0, offset_sec=WINDOW)
        assert res.detected is True
        assert res.target_frame is not None
        assert res.target_frame.frame_index == int(round(WINDOW * FPS))  # 1200

    def test_offset_above_initial_start(self):
        # 区域结尾位于 35s 之后、窗口内
        res = run_offset(onset_sec=10.0, offset_sec=38.5)
        assert res.detected is True
        assert res.target_frame is not None
        assert res.target_frame.frame_index == int(round(38.5 * FPS))  # 1155

    def test_offset_in_coarse_gap(self):
        # 区域极窄且位于粗网格缝隙内：由整窗细扫兜底捕获
        res = run_offset(onset_sec=12.5, offset_sec=13.5)
        assert res.detected is True
        assert res.target_frame is not None
        assert res.target_frame.frame_index == int(round(13.5 * FPS))  # 405

    def test_no_region(self):
        res = run_offset(onset_sec=WINDOW + 100, offset_sec=WINDOW + 101)
        assert res.detected is False
        assert res.target_frame is None

    def test_region_shorter_than_video(self):
        res = run_offset(onset_sec=5.0, offset_sec=6.0, duration=10.0)
        assert res.detected is True
        assert res.target_frame is not None
        assert res.target_frame.frame_index == int(round(6.0 * FPS))


class TestOffsetProgressCallback:
    def test_progress_called(self):
        stages = []
        run_offset(10.0, 24.0, progress_cb=lambda stage, prog, msg: stages.append((stage, prog)))
        assert len(stages) > 0
        stage_names = {s for s, _ in stages}
        assert {"coarse", "locate", "fine", "precise", "done"} <= stage_names
        progresses = [p for _, p in stages]
        assert all(b >= a for a, b in zip(progresses, progresses[1:]))
        assert progresses[-1] == pytest.approx(1.0)


class TestOffsetValidation:
    def test_invalid_steps(self):
        probe_time, scan_frames = make_region_probe(10.0, 20.0)
        with pytest.raises(ValueError):
            detect_offset(probe_time, scan_frames, search_window=40.0,
                          initial_start=35.0, coarse_step=-1.0, fine_step=1.0)
        with pytest.raises(ValueError):
            detect_offset(probe_time, scan_frames, search_window=40.0,
                          initial_start=35.0, coarse_step=5.0, fine_step=10.0)
