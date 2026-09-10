# -*- coding: utf-8 -*-
"""detector 模块使用示例。

运行方式（任意工作目录均可，脚本会自动定位仓库根与测试素材）::

    py src\\detector\\examples\\usage_example.py            # 自动查找测试视频
    py src\\detector\\examples\\usage_example.py <视频路径>  # 显式指定
"""
import os
import sys

# 保证可独立运行：将 src 目录加入模块搜索路径
_SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
# 仓库根目录（本文件位于 <repo>/src/detector/examples/）
_REPO_ROOT = os.path.dirname(_SRC)

from detector import DetectorConfig, VideoColorDetector, hex_to_rgb


def basic_usage(video_path):
    """最简用法：全部使用默认配置，直接拿到 frame 和 time。"""
    print("=== 基本用法（默认配置）===")
    detector = VideoColorDetector()
    frame, time = detector.detect(video_path)  # 解构赋值
    if frame is None:
        print("  未检测到目标颜色")
    else:
        print(f"  目标帧序号: {frame}")
        print(f"  目标时间戳: {time:.6f} 秒")
    print(f"  解构后的值: frame={frame!r}, time={time!r}")


def object_access(video_path):
    """通过结果对象访问核心字段。"""
    print("=== 结果对象访问 ===")
    result = VideoColorDetector().detect(video_path)
    print(f"  result = {result}")
    if result.detected:
        print(f"  result.frame = {result.frame}")
        print(f"  result.timestamp = {result.timestamp:.6f}")
        print(f"  未命中时 result.details 为 None（默认不计算）: {result.details is None}")


def detail_mode(video_path):
    """需要详细诊断信息时使用 detail=True。"""
    print("=== 详细诊断（detail=True）===")
    result = VideoColorDetector().detect(video_path, detail=True)
    if result.detected:
        d = result.details
        print(f"  整体置信度: {d.confidence:.4f}")
        print(f"  粗定位区间: {d.coarse_bracket}")
        print(f"  细化区间:   {d.fine_bracket}")
        print(f"  各阶段探测帧数: {d.stage_stats}")
        for p in d.point_matches:
            print(f"    检测点 ({p.x:4d},{p.y:4d})  RGB={p.rgb}  conf={p.confidence:.4f}  matched={p.matched}")


def custom_config(video_path):
    """自定义配置：调整阈值、容差、检测点与时间窗口。"""
    print("=== 自定义配置 ===")
    config = DetectorConfig(
        target_color=hex_to_rgb("#F7F10F"),
        confidence_threshold=0.97,
        color_tolerance=10.0,
        points=((20, 20), (1900, 20), (20, 1060), (1900, 1060)),
        search_window=40.0,
        initial_start=35.0,
        coarse_step=5.0,
        fine_step=1.0,
        extractor="ffmpeg",
        log_level="WARNING",
    )
    frame, time = VideoColorDetector(config).detect(video_path)
    print(f"  frame={frame} time={time}")


def _pick_video():
    """定位可用测试视频：命令行参数 > 仓库根目录约定名 input*.mp4 > 根目录任意 .mp4。"""
    if len(sys.argv) > 1:
        return sys.argv[1]
    for name in ("input1.mp4", "input2.mp4", "input3.mp4"):
        path = os.path.join(_REPO_ROOT, name)
        if os.path.isfile(path):
            return path
    # 回退：仓库实际素材为「编号_标题_*.mp4」命名，取第一个 mp4
    try:
        hits = sorted(f for f in os.listdir(_REPO_ROOT) if f.lower().endswith(".mp4"))
    except OSError:
        hits = []
    return os.path.join(_REPO_ROOT, hits[0]) if hits else None


def main():
    video = _pick_video()
    if video is None or not os.path.isfile(video):
        print("未找到测试视频：请在仓库根目录放入 input1.mp4（或任意 .mp4 素材），")
        print("或显式传入路径：py src\\detector\\examples\\usage_example.py <视频路径>")
        return
    basic_usage(video)
    object_access(video)
    detail_mode(video)
    custom_config(video)


if __name__ == "__main__":
    main()
