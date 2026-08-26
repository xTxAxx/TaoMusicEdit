# -*- coding: utf-8 -*-
"""反向跳帧 + 局部细化 检测算法（纯逻辑，便于单元测试）。

算法流程（在指定时间窗口内定位目标颜色起始帧 onset）：

1. 初始阶段（反向跳帧）
   从 ``initial_start``（默认 35s）开始，按 ``coarse_step``（默认 5s）步长
   向 0 反向抽帧检测，得到粗粒度采样序列；同时补扫窗口上端，
   保证完整覆盖 ``[0, search_window]``。

2. 定位阶段
   在粗粒度序列中找到「不匹配 → 匹配」的过渡区间 ``(t_prev, t_hit]``，
   其中 ``t_prev`` 不满足条件、``t_hit`` 满足条件。

3. 细化阶段
   在 ``(t_prev, t_hit]`` 内按 ``fine_step``（默认 1s）步长再次检测，
   找到第一个匹配点，得到更窄的区间 ``(t_prev_f, t_hit_f]``。

4. 精确阶段
   在 ``(t_prev_f, t_hit_f]`` 内逐帧扫描（经由 ``scan_frames`` 回调），
   精确定位第一个满足条件的目标帧。

本模块不依赖具体视频/帧提取实现，仅通过两个回调与外部交互：
- ``probe_time(t) -> bool``：给定时间（秒），返回该时刻帧是否命中；
- ``scan_frames(t0, t1) -> List[FrameInfo]``：返回区间内逐帧信息（含匹配标记）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

#: 时间比较容差
_EPS = 1e-6


@dataclass
class FrameInfo:
    """单帧的匹配信息。"""

    frame_index: int
    timestamp: float
    matched: bool
    confidence: float = 0.0

    def __post_init__(self) -> None:
        self.frame_index = int(self.frame_index)
        self.timestamp = float(self.timestamp)


@dataclass
class OnsetResult:
    """算法输出：目标起始帧定位结果。"""

    detected: bool
    coarse_bracket: Optional[Tuple[float, float]]  # (t_prev, t_hit)
    fine_bracket: Optional[Tuple[float, float]]  # (t_prev_f, t_hit_f)
    target_frame: Optional[FrameInfo]
    stage_stats: Dict[str, int]
    message: str


def _coarse_times(search_window: float, initial_start: float, coarse_step: float) -> List[float]:
    """生成覆盖 [0, search_window] 的粗粒度采样时刻（升序，含两端）。"""
    times: List[float] = []
    t = float(initial_start)
    while t >= 0.0:
        times.append(round(t, 6))
        t -= coarse_step
    t = float(initial_start) + coarse_step
    while t <= search_window + _EPS:
        times.append(round(t, 6))
        t += coarse_step
    times = sorted(set(t for t in times if t <= search_window + _EPS))
    if times and times[0] != 0.0:
        times = [0.0] + times
    if times and times[-1] != search_window:
        times = times + [round(search_window, 6)]
    return times


def detect_onset(
    probe_time: Callable[[float], bool],
    scan_frames: Callable[[float, float], List[FrameInfo]],
    *,
    search_window: float = 40.0,
    initial_start: float = 35.0,
    coarse_step: float = 5.0,
    fine_step: float = 1.0,
    progress_cb: Optional[Callable[[str, float, str], None]] = None,
) -> OnsetResult:
    """执行反向跳帧 + 局部细化检测。

    :param probe_time: 时间(秒) -> 该时刻帧是否命中
    :param scan_frames: 区间(t0, t1) -> 区间内逐帧信息列表（须含 frame_index / timestamp / matched）
    :param search_window: 检测窗口（秒）
    :param initial_start: 反向跳帧起始时间（秒）
    :param coarse_step: 初始大步长（秒）
    :param fine_step: 细化步长（秒）
    :param progress_cb: 进度回调 (stage, progress[0,1], message)
    """
    if coarse_step <= 0 or fine_step <= 0:
        raise ValueError("coarse_step / fine_step 必须大于 0")
    if fine_step > coarse_step:
        raise ValueError("fine_step 必须不大于 coarse_step")

    stats: Dict[str, int] = {"coarse": 0, "fine": 0, "precise": 0}
    coarse_times = _coarse_times(search_window, initial_start, coarse_step)

    # ---- 进度折算：按阶段权重（coarse/fine/precise），保证单调递增且最终为 1.0 ----
    W_COARSE, W_FINE, W_PRECISE = 0.25, 0.25, 0.5

    def _report(stage: str, progress: float, message: str) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(stage, max(0.0, min(1.0, progress)), message)
        except Exception:  # 回调异常不应中断检测
            pass

    coarse_total = max(1, len(coarse_times))

    # ---------------- 阶段 1：反向跳帧 ----------------
    # 按“从 initial_start 向 0”的反向顺序实际抽帧检测，体现“反向跳帧”。
    coarse_map: Dict[float, bool] = {}
    for i, t in enumerate(reversed(coarse_times), start=1):
        matched = bool(probe_time(t))
        coarse_map[t] = matched
        stats["coarse"] += 1
        _report("coarse", W_COARSE * (i / coarse_total),
                f"反向抽帧 time={t:.2f}s -> matched={matched}")

    coarse: List[FrameInfo] = [
        FrameInfo(-1, t, coarse_map[t]) for t in coarse_times
    ]

    # ---------------- 阶段 2：定位 ----------------
    fine_total = int(math.ceil(coarse_step / fine_step))
    t_prev: Optional[float] = None
    t_hit: Optional[float] = None
    for i in range(1, len(coarse)):
        if coarse[i].matched and not coarse[i - 1].matched:
            t_prev, t_hit = coarse[i - 1].timestamp, coarse[i].timestamp
            break
    if t_prev is None:
        if any(c.matched for c in coarse):
            # 窗口起点即匹配：onset 落在 [0, coarse_step]
            t_prev, t_hit = 0.0, float(coarse_step)
        else:
            # 兜底：粗网格未命中，整窗以 fine_step 细扫，
            # 避免漏检位于粗网格缝隙或 (initial_start, search_window] 的窄区域
            fine_total = int(math.ceil(search_window / fine_step))
            fallback_hit: Optional[float] = None
            ft = 0.0
            while ft <= search_window + _EPS:
                if bool(probe_time(ft)):
                    fallback_hit = ft
                    break
                stats["fine"] += 1
                _report("fallback", W_COARSE + W_FINE * (stats["fine"] / fine_total),
                        f"整窗细扫 time={ft:.2f}s -> matched=False")
                ft += fine_step
            if fallback_hit is None:
                _report("locate", W_COARSE, "未检测到目标颜色")
                return OnsetResult(False, None, None, None, stats, "未检测到目标颜色")
            t_prev = max(0.0, fallback_hit - fine_step)
            t_hit = fallback_hit
            _report("locate", W_COARSE, f"兜底定位粗区间 ({t_prev:.2f}, {t_hit:.2f}]")

    assert t_prev is not None and t_hit is not None
    coarse_bracket = (float(t_prev), float(t_hit))
    _report("locate", W_COARSE, f"定位粗区间 ({t_prev:.2f}, {t_hit:.2f}]")

    # ---------------- 阶段 3：细化 ----------------
    t_prev_f: float = float(t_prev)
    t_hit_f: float = float(t_hit)
    t = float(t_prev) + fine_step
    while t <= float(t_hit) + _EPS:
        matched = bool(probe_time(t))
        stats["fine"] += 1
        _report("fine", W_COARSE + W_FINE * (stats["fine"] / fine_total),
                f"细化 time={t:.2f}s -> matched={matched}")
        if matched:
            t_hit_f = t
            t_prev_f = max(float(t_prev), t - fine_step)
            break
        t += fine_step

    fine_bracket = (t_prev_f, t_hit_f)
    _report("fine", W_COARSE + W_FINE, f"细化区间 ({t_prev_f:.2f}, {t_hit_f:.2f}]")

    # ---------------- 阶段 4：精确逐帧扫描 ----------------
    target_frame: Optional[FrameInfo] = None
    frames = scan_frames(t_prev_f, t_hit_f)
    precise_total = max(1, len(frames))
    for i, f in enumerate(frames, start=1):
        stats["precise"] += 1
        _report("precise", W_COARSE + W_FINE + W_PRECISE * (i / precise_total),
                f"逐帧扫描 frame={f.frame_index} time={f.timestamp:.6f}s matched={f.matched}")
        if f.matched:
            target_frame = f
            break

    if target_frame is None:
        _report("done", 1.0, f"细化区间 ({t_prev_f:.2f}, {t_hit_f:.2f}] 内未找到匹配帧")
        return OnsetResult(
            False,
            coarse_bracket,
            fine_bracket,
            None,
            stats,
            f"细化区间 ({t_prev_f:.2f}, {t_hit_f:.2f}] 内未找到匹配帧",
        )

    _report("done", 1.0, f"命中目标帧 frame={target_frame.frame_index} time={target_frame.timestamp:.6f}s")
    return OnsetResult(
        True,
        coarse_bracket,
        fine_bracket,
        target_frame,
        stats,
        f"命中目标帧 frame={target_frame.frame_index} time={target_frame.timestamp:.6f}s",
    )


def detect_offset(
    probe_time: Callable[[float], bool],
    scan_frames: Callable[[float, float], List[FrameInfo]],
    *,
    search_window: float = 40.0,
    initial_start: float = 35.0,
    coarse_step: float = 5.0,
    fine_step: float = 1.0,
    progress_cb: Optional[Callable[[str, float, str], None]] = None,
) -> OnsetResult:
    """执行「反向跳帧 + 局部细化」的结尾定位（最后一个命中帧）。

    与 :func:`detect_onset` 对称：在指定时间窗口内定位目标颜色区域的**结尾**：

    1. 初始阶段（反向跳帧）
       构建 ``[0, search_window]`` 的粗粒度采样命中图。

    2. 定位阶段
       找最后一个「匹配 → 不匹配」的过渡区间 ``(t_hit, t_next]``，
       其中 ``t_hit`` 满足条件、``t_next`` 不满足条件。

    3. 细化阶段
       在 ``(t_hit, t_next]`` 内按 ``fine_step`` 细扫，得到 ``(t_hit_f, t_next_f]``。

    4. 精确阶段
       在细化区间内逐帧扫描（经由 ``scan_frames`` 回调），
       定位**最后一个**满足条件的目标帧（颜色区域结尾）。
    """
    if coarse_step <= 0 or fine_step <= 0:
        raise ValueError("coarse_step / fine_step 必须大于 0")
    if fine_step > coarse_step:
        raise ValueError("fine_step 必须不大于 coarse_step")

    stats: Dict[str, int] = {"coarse": 0, "fine": 0, "precise": 0}
    coarse_times = _coarse_times(search_window, initial_start, coarse_step)

    # ---- 进度折算：按阶段权重（coarse/fine/precise），保证单调递增且最终为 1.0 ----
    W_COARSE, W_FINE, W_PRECISE = 0.25, 0.25, 0.5

    def _report(stage: str, progress: float, message: str) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(stage, max(0.0, min(1.0, progress)), message)
        except Exception:  # 回调异常不应中断检测
            pass

    coarse_total = max(1, len(coarse_times))

    # ---------------- 阶段 1：反向跳帧 ----------------
    coarse_map: Dict[float, bool] = {}
    for i, t in enumerate(reversed(coarse_times), start=1):
        matched = bool(probe_time(t))
        coarse_map[t] = matched
        stats["coarse"] += 1
        _report("coarse", W_COARSE * (i / coarse_total),
                f"反向抽帧 time={t:.2f}s -> matched={matched}")

    coarse: List[FrameInfo] = [FrameInfo(-1, t, coarse_map[t]) for t in coarse_times]

    # ---------------- 阶段 2：定位（最后一个 匹配→不匹配 过渡） ----------------
    matched_idx = [i for i, c in enumerate(coarse) if c.matched]
    if matched_idx:
        last = matched_idx[-1]
        if last == len(coarse) - 1:
            # 窗口末端仍命中：右边界取窗口末端，左边界取前一个粗点
            t_hit = coarse[last - 1].timestamp if last > 0 else 0.0
            t_next = search_window
        else:
            t_hit = coarse[last].timestamp
            t_next = coarse[last + 1].timestamp
    else:
        # 兜底：粗网格未命中，整窗以 fine_step 细扫，记录最后一个命中点
        fine_total = max(1, int(math.ceil(search_window / fine_step)))
        fallback_last: Optional[float] = None
        fallback_next: float = search_window
        ft = 0.0
        while ft <= search_window + _EPS:
            matched = bool(probe_time(ft))
            if matched:
                fallback_last = ft
            elif fallback_last is not None:
                fallback_next = ft
                break
            stats["fine"] += 1
            _report("fallback", W_COARSE + W_FINE * (stats["fine"] / fine_total),
                    f"整窗细扫 time={ft:.2f}s -> matched={matched}")
            ft += fine_step
        if fallback_last is None:
            _report("locate", W_COARSE, "未检测到目标颜色")
            return OnsetResult(False, None, None, None, stats, "未检测到目标颜色")
        t_hit = max(0.0, fallback_last - fine_step)
        t_next = fallback_next
        _report("locate", W_COARSE + W_FINE * (stats["fine"] / fine_total),
                f"兜底定位粗区间 ({t_hit:.2f}, {t_next:.2f}]")

    assert t_hit is not None and t_next is not None
    coarse_bracket = (float(t_hit), float(t_next))
    _report("locate", W_COARSE, f"定位粗区间 ({t_hit:.2f}, {t_next:.2f}]")

    # ---------------- 阶段 3：细化（找最后一个命中点） ----------------
    fine_total = max(1, int(math.ceil((t_next - t_hit) / fine_step)))
    t_hit_f: float = float(t_hit)
    t_next_f: float = float(t_next)
    # 粗扫已确认 t_hit 命中则直接记录，避免重复探测（同时保证进度单调递增）
    last_f: Optional[float] = float(t_hit) if coarse_map.get(t_hit, False) else None
    t = float(t_hit) + fine_step
    while t <= float(t_next) + _EPS:
        matched = bool(probe_time(t))
        stats["fine"] += 1
        _report("fine", W_COARSE + W_FINE * (stats["fine"] / fine_total),
                f"细化 time={t:.2f}s -> matched={matched}")
        if matched:
            last_f = t
        elif last_f is not None:
            t_next_f = t
            break
        t += fine_step
    if last_f is None:
        # 防御：细化区间内未命中，退回用粗区间左端继续
        last_f = t_hit
    t_hit_f = last_f
    if t_next_f <= t_hit_f + _EPS:
        t_next_f = float(t_next)
    fine_bracket = (t_hit_f, t_next_f)
    _report("fine", W_COARSE + W_FINE, f"细化区间 ({t_hit_f:.2f}, {t_next_f:.2f}]")

    # ---------------- 阶段 4：精确逐帧扫描（取最后一个命中帧） ----------------
    # 若细化区间退化（结尾恰在窗口末端），向前扩展一个 fine_step 保证可扫描
    scan_t0 = t_hit_f - fine_step if t_next_f - t_hit_f <= _EPS else t_hit_f
    scan_t0 = max(0.0, min(scan_t0, t_next_f))
    target_frame: Optional[FrameInfo] = None
    frames = scan_frames(scan_t0, t_next_f)
    precise_total = max(1, len(frames))
    for i, f in enumerate(frames, start=1):
        stats["precise"] += 1
        _report("precise", W_COARSE + W_FINE + W_PRECISE * (i / precise_total),
                f"逐帧扫描 frame={f.frame_index} time={f.timestamp:.6f}s matched={f.matched}")
        if f.matched:
            target_frame = f

    if target_frame is None:
        _report("done", 1.0, f"细化区间 ({t_hit_f:.2f}, {t_next_f:.2f}] 内未找到匹配帧")
        return OnsetResult(
            False,
            coarse_bracket,
            fine_bracket,
            None,
            stats,
            f"细化区间 ({t_hit_f:.2f}, {t_next_f:.2f}] 内未找到匹配帧",
        )

    _report("done", 1.0, f"命中目标帧 frame={target_frame.frame_index} time={target_frame.timestamp:.6f}s")
    return OnsetResult(
        True,
        coarse_bracket,
        fine_bracket,
        target_frame,
        stats,
        f"命中目标帧 frame={target_frame.frame_index} time={target_frame.timestamp:.6f}s",
    )
