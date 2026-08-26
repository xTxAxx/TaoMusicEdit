# -*- coding: utf-8 -*-
"""pytest 共享夹具：真实视频路径、合成视频生成等。"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

import numpy as np
import pytest

# 项目根目录（d:\Git\TaoMusicEdit）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL_VIDEOS: Dict[str, str] = {
    "input1": os.path.join(PROJECT_ROOT, "input1.mp4"),
    "input2": os.path.join(PROJECT_ROOT, "input2.mp4"),
    "input3": os.path.join(PROJECT_ROOT, "input3.mp4"),
}

# 真实视频中目标颜色 #F7F10F 出现的已知结尾区间（最后一个命中帧，由调研确定）
REAL_OFFSET_RANGE = (24.0, 26.0)  # 秒


def _have_real_videos() -> bool:
    return any(os.path.isfile(p) for p in REAL_VIDEOS.values())


REQUIRE_REAL = pytest.mark.skipif(
    not _have_real_videos(), reason="缺少真实视频样本 input1/2/3.mp4"
)


@pytest.fixture(scope="session")
def real_videos() -> Dict[str, str]:
    return dict(REAL_VIDEOS)


def make_synthetic_video(
    path: str,
    *,
    width: int = 320,
    height: int = 240,
    fps: int = 30,
    duration_sec: float = 3.0,
    onset_sec: float = 1.5,
    offset_sec: Optional[float] = None,
    target_color: Tuple[int, int, int] = (247, 241, 15),
    points: Tuple[Tuple[int, int], ...] = ((5, 5), (300, 5), (5, 220), (300, 220)),
    background: Tuple[int, int, int] = (18, 18, 18),
    ffmpeg_path: str = "ffmpeg",
) -> str:
    """用 FFmpeg 无损编码生成一个合成测试视频。

    前 ``onset_sec`` 秒为纯背景色，``[onset_sec, offset_sec]`` 区间内
    四个检测点填充目标颜色（``offset_sec`` 默认到视频结尾），
    之后恢复为背景色，以便用已知的精确起始帧/结尾帧验证检测器。
    """
    n = int(round(duration_sec * fps))
    onset_frame = int(round(onset_sec * fps))
    offset_frame = int(round((offset_sec or duration_sec) * fps))
    target_bgr = (target_color[2], target_color[1], target_color[0])
    frames: List[np.ndarray] = []
    for i in range(n):
        frame = np.full((height, width, 3), background, dtype=np.uint8)
        if onset_frame <= i < offset_frame:
            for (x, y) in points:
                frame[y, x] = target_bgr
        frames.append(frame)

    raw = b"".join(f.tobytes() for f in frames)
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as f:
        raw_path = f.name
        f.write(raw)
    try:
        cmd = [
            ffmpeg_path,
            "-loglevel", "error",
            "-y",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}",
            "-r", str(fps),
            "-i", raw_path,
            "-c:v", "ffv1",
            "-pix_fmt", "bgr24",  # RGB 无损，避免 YUV 转换引入颜色偏差
            path,
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"合成视频编码失败: {proc.stderr.decode('utf-8', errors='replace')[-1000:]}"
            )
    finally:
        if os.path.exists(raw_path):
            os.remove(raw_path)
    return path
