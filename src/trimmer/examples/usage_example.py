# -*- coding: utf-8 -*-
"""trimmer Python API 使用示例。

运行方式（任意工作目录均可，脚本会自动定位仓库根与测试素材）:
    python src/trimmer/examples/usage_example.py
"""

import os
import sys

# 将 src 目录加入 sys.path，便于直接运行本示例（无需先安装）
_SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from trimmer import OutputMode, Trimmer, TrimmerConfig
from trimmer.core.errors import TrimmerError

# 自动定位仓库根目录下的测试视频：优先约定名 input*.mp4，
# 其次回退到根目录任意 .mp4（本仓库素材为「编号_标题_*.mp4」命名）。
# 以脚本自身位置推导仓库根，避免受当前工作目录影响而误命中仓库外的同名文件。
_REPO_ROOT = os.path.dirname(_SRC)


def _find_video():
    """在仓库根目录查找测试视频：约定名优先，其次任意 mp4；找不到返回 None。"""
    for name in ("input1.mp4", "input2.mp4", "input3.mp4"):
        path = os.path.join(_REPO_ROOT, name)
        if os.path.isfile(path):
            return path
    try:
        hits = sorted(f for f in os.listdir(_REPO_ROOT) if f.lower().endswith(".mp4"))
    except OSError:
        hits = []
    return os.path.join(_REPO_ROOT, hits[0]) if hits else None


_VIDEO = _find_video()
# 示例输出统一放到本目录的 output 文件夹，避免污染仓库根目录
_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def _on_progress(pct, seconds):
    """进度回调示例。"""
    print(f"\r进度: {pct:5.1f}%", end="", flush=True)


def _pick_video():
    if _VIDEO is None:
        print("未找到测试视频：请在仓库根目录放入 input1.mp4（或任意 .mp4 素材）后重试。")
        sys.exit(1)
    return _VIDEO


def main():
    video = _pick_video()
    print(f"使用测试视频: {os.path.abspath(video)}")
    print(f"示例输出目录: {_OUT_DIR}")

    # 1. 按时间戳裁剪（默认流复制，快速）：从 5.0s 开始保留
    print("\n== 1. 按时间戳从 5.0s 开始保留（流复制）==")
    result = Trimmer(TrimmerConfig(input_path=video, timestamp=5.0, output_dir=_OUT_DIR,
                                   force=True)).run(progress_cb=_on_progress)
    print("\n输出:", result.output_files, "| 处理方式:", result.message)

    # 2. 按帧序号裁剪（重编码，自动匹配原视频参数）：从第 150 帧开始保留
    print("\n== 2. 按帧序号从第 150 帧开始保留（重编码）==")
    result = Trimmer(TrimmerConfig(input_path=video, frame=150, reencode=True,
                                   output_dir=_OUT_DIR, force=True)).run(progress_cb=_on_progress)
    print("\n输出:", result.output_files, "| 处理方式:", result.message)

    # 3. 仅输出纯音频（流复制）
    print("\n== 3. 仅输出音频（流复制）==")
    result = Trimmer(TrimmerConfig(input_path=video, timestamp=3.0,
                                   output_mode=OutputMode.AUDIO_ONLY,
                                   output_dir=_OUT_DIR, force=True)).run()
    print("输出:", result.output_files)

    # 4. 分离输出视频与音频文件
    print("\n== 4. 分离输出视频与音频 ==")
    result = Trimmer(TrimmerConfig(input_path=video, timestamp=3.0,
                                   output_mode=OutputMode.SPLIT,
                                   output_dir=_OUT_DIR, force=True)).run()
    print("输出:", result.output_files)

    # 5. 错误处理示例：未提供裁剪参数
    print("\n== 5. 错误处理示例 ==")
    try:
        Trimmer(TrimmerConfig(input_path=video, output_dir=_OUT_DIR)).run()
    except TrimmerError as exc:
        print(f"捕获异常 [{exc.code}]: {exc.message}")


if __name__ == "__main__":
    main()
