# -*- coding: utf-8 -*-
"""detector 命令行入口。

从其他项目直接调用，只需传入视频文件路径即可得到 frame 和 time::

    py detector/cli.py video.mp4

常用选项::

    py detector/cli.py video.mp4 --target '#F7F10F' --tolerance 15
    py detector/cli.py video.mp4 --points 20,20 300,300 --target 255,0,0   # 自定义检测点
    py detector/cli.py video.mp4 --detail        # 附带详细诊断（输出到 stderr）

默认只向 stdout 输出一行机器可读结果（便于脚本解析）::

    frame=734 time=24.466666666666665

未命中目标颜色时向 stderr 输出提示并以退出码 1 结束；
所有诊断日志统一走 stderr，不污染 stdout 输出。
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

# 保证可直接运行 `py detector/cli.py`：将项目根目录（detector 包所在处）加入模块搜索路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector import VideoColorDetector, hex_to_rgb
from detector.core.errors import DetectorError


def _parse_color(text: str):
    """解析目标颜色：#RRGGBB 或 R,G,B（或空格分隔）。"""
    text = text.strip()
    if text.startswith("#"):
        rgb = hex_to_rgb(text)
    elif "," in text:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        rgb = tuple(int(p) for p in parts)
    else:
        rgb = tuple(int(p) for p in text.split())
    if len(rgb) != 3 or not all(0 <= c <= 255 for c in rgb):
        raise argparse.ArgumentTypeError(
            f"无法解析颜色: {text!r}，请使用 #RRGGBB 或 R,G,B"
        )
    return rgb


def _parse_point(text: str):
    """解析单个检测点坐标：'x,y' 或空格分隔的 'x y'。"""
    text = text.strip()
    sep = "," if "," in text else " "
    parts = [p.strip() for p in text.split(sep) if p.strip()]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"无法解析检测点 {text!r}，请使用格式 'x,y'，例如 20,20"
        )
    try:
        x, y = int(parts[0]), int(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"检测点坐标必须是整数，收到 {text!r}"
        )
    if x < 0 or y < 0:
        raise argparse.ArgumentTypeError(f"检测点坐标不能为负，收到 {text!r}")
    return (x, y)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-color-detector",
        description=(
            "视频颜色检测：定位视频前 40s 内目标颜色的结尾帧（最后一个命中帧），"
            "输出 frame 和 time。"
        ),
    )
    parser.add_argument("video", help="视频文件路径")
    parser.add_argument(
        "-c", "--target", type=_parse_color, default=None, metavar="COLOR",
        help="目标颜色，如 '#F7F10F' 或 '247,241,15'（默认 #F7F10F）",
    )
    parser.add_argument(
        "--threshold", type=float, default=None,
        help="置信度阈值（默认 0.97）",
    )
    parser.add_argument(
        "--tolerance", type=float, default=None,
        help="颜色容差（默认 10.0）",
    )
    parser.add_argument(
        "--extractor", choices=("ffmpeg", "opencv"), default=None,
        help="帧提取器（默认 ffmpeg）",
    )
    parser.add_argument(
        "--points", type=_parse_point, nargs="+", default=None, metavar="X,Y",
        help=(
            "自定义检测点坐标，可传多个，数量由坐标个数决定；"
            "例: --points 20,20 300,300 1900,20（默认四点针对 1920x1080）"
        ),
    )
    parser.add_argument(
        "--no-scale-points", dest="scale_points", action="store_false",
        help="关闭检测点坐标按视频分辨率等比缩放（默认开启）",
    )
    parser.add_argument(
        "--detail", action="store_true",
        help="输出详细诊断信息（置信度、定位区间、阶段统计等，写入 stderr）",
    )
    parser.add_argument(
        "--log-level", default="WARNING",
        help="日志级别（默认 WARNING，日志写入 stderr）",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    overrides: dict = {"log_level": args.log_level}
    if args.target is not None:
        overrides["target_color"] = args.target
    if args.threshold is not None:
        overrides["confidence_threshold"] = args.threshold
    if args.tolerance is not None:
        overrides["color_tolerance"] = args.tolerance
    if args.extractor is not None:
        overrides["extractor"] = args.extractor
    if args.points is not None:
        overrides["points"] = tuple(args.points)
    if args.scale_points is False:
        overrides["scale_points"] = False

    try:
        detector = VideoColorDetector(**overrides)
        result = detector.detect(args.video, detail=args.detail)
    except DetectorError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    if not result.detected or result.frame is None:
        print("未检测到目标颜色", file=sys.stderr)
        return 1

    # 极简输出：一行 frame + time
    print(f"frame={result.frame} time={result.timestamp}")

    if args.detail and result.details is not None:
        d = result.details
        print(f"confidence={d.confidence:.4f}", file=sys.stderr)
        print(f"coarse_bracket={d.coarse_bracket} fine_bracket={d.fine_bracket}", file=sys.stderr)
        print(f"stage_stats={d.stage_stats}", file=sys.stderr)
        if d.point_matches:
            for p in d.point_matches:
                print(
                    f"point({p.x},{p.y}) RGB={p.rgb} "
                    f"conf={p.confidence:.4f} matched={p.matched}",
                    file=sys.stderr,
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
