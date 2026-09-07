# -*- coding: utf-8 -*-
"""trimmer 命令行入口：参数解析与主流程。"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

# 允许直接以脚本方式运行（如 `py .\trimmer\cli.py`）时也能导入本包：
# 以模块方式运行时 __package__ 非空，无需此引导
if __package__ in (None, ""):
    _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

from trimmer import __version__
from trimmer.core.errors import TrimmerError
from trimmer.core.trimmer import OutputMode, Trimmer, TrimmerConfig
from trimmer.utils.logger import configure_logging, get_logger

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="trimmer",
        description="基于 FFmpeg 的高精度视频裁剪工具：按帧序号或时间戳指定起点（可选终点）"
                    "裁剪视频——终点缺省时保留到片尾，提供终点时保留 [起点, 终点) 区间；"
                    "默认流复制快速裁剪，可选重编码并自动匹配原视频参数、GPU 硬件加速。",
        epilog="示例:\n"
               "  trimmer input.mp4 -t 24.9                # 按时间戳裁剪（流复制，保留到片尾）\n"
               "  trimmer input.mp4 -f 900                 # 按帧序号裁剪\n"
               "  trimmer input.mp4 -t 24.9 -T 3600        # 区间裁剪：保留 [24.9s, 3600s)\n"
               "  trimmer input.mp4 -f 735 -F 108000       # 帧序号区间：保留 [第735帧, 第108000帧)\n"
               "  trimmer input.mp4 -t 24.9 -r --hw-accel  # 重编码并启用 GPU 加速\n"
               "  trimmer input.mp4 -t 24.9 --no-audio     # 输出无声视频\n"
               "  trimmer input.mp4 -t 24.9 --audio-only   # 仅输出音频\n"
               "  trimmer input.mp4 -t 24.9 --split -d out # 分离音视频到 out 目录",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("-h", "-help", "--help", action="help", help="显示帮助信息并退出")
    parser.add_argument("input", metavar="INPUT", help="输入视频文件路径（支持绝对/相对路径）")

    # 裁剪参数：时间戳与帧序号二选一（在解析后进一步校验“恰好一个”）
    cut = parser.add_mutually_exclusive_group()
    cut.add_argument("-t", "--timestamp", type=float, dest="timestamp", metavar="SEC",
                     help="裁剪起始时间戳（秒，浮点数，如 24.9），保留此后内容，与 --frame 二选一")
    cut.add_argument("-f", "--frame", type=int, dest="frame", metavar="N",
                     help="裁剪起始帧序号（从 1 开始），保留第 N 帧及之后内容，与 --timestamp 二选一")

    # 区间终点（可选）：与起点方式可不同，内部统一换算为时间戳执行
    end = parser.add_mutually_exclusive_group()
    end.add_argument("-T", "--end-timestamp", type=float, dest="end_timestamp", metavar="SEC",
                     help="裁剪终点时间戳（秒，浮点数），保留 [起点, 终点) 区间；"
                          "与 --end-frame 二选一，缺省 = 保留到片尾")
    end.add_argument("-F", "--end-frame", type=int, dest="end_frame", metavar="N",
                     help="裁剪终点帧序号（从 1 开始），保留 [起点, 终点) 区间；"
                          "与 --end-timestamp 二选一，缺省 = 保留到片尾")

    # 输入 / 输出
    parser.add_argument("-o", "--output", dest="output", metavar="FILE",
                        help="输出文件路径（默认在原视频目录生成 '<原名>_trim<扩展名>'）")
    parser.add_argument("-d", "--output-dir", dest="output_dir", metavar="DIR",
                        help="输出目录（默认与原视频同目录，不存在则自动创建）")
    parser.add_argument("-s", "--suffix", dest="suffix", default="_trim", metavar="SUFFIX",
                        help="默认输出文件名后缀（默认 _trim）")
    parser.add_argument("-y", "--force", dest="force", action="store_true",
                        help="输出文件已存在时直接覆盖，不再询问")

    # 编码选项
    parser.add_argument("-r", "--reencode", dest="reencode", action="store_true",
                        help="重编码输出（默认流复制快速裁剪）")
    parser.add_argument("-c", "--video-codec", dest="video_codec", metavar="CODEC",
                        help="指定视频编码器（如 libx264 / hevc_nvenc），默认自动匹配原视频")
    parser.add_argument("--audio-codec", dest="audio_codec", metavar="CODEC",
                        help="指定音频编码器（如 aac / libmp3lame），默认自动匹配原视频")
    parser.add_argument("--hw-accel", dest="hw_accel", choices=("auto", "none", "force"),
                        nargs="?", const="auto", default="auto", metavar="MODE",
                        help="GPU 加速策略：单独使用 --hw-accel 等价于 --hw-accel auto"
                             "（自动检测优先使用，默认）；none 关闭；force 强制")

    # 输出模式（互斥）
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-audio", "--video-only", dest="video_only", action="store_true",
                      help="仅输出视频流（无声视频）")
    mode.add_argument("--audio-only", dest="audio_only", action="store_true",
                      help="仅输出音频流")
    mode.add_argument("--split", dest="split", action="store_true",
                      help="分离输出视频文件与音频文件")

    parser.add_argument("--verbose", dest="verbose", action="store_true", help="输出详细调试日志")
    parser.add_argument("-V", "--version", action="version", version=f"trimmer {__version__}")
    return parser


def build_config(args: argparse.Namespace) -> TrimmerConfig:
    """将解析后的命令行参数转换为 TrimmerConfig。"""
    if args.audio_only:
        output_mode = OutputMode.AUDIO_ONLY
    elif args.video_only:
        output_mode = OutputMode.VIDEO_ONLY
    elif args.split:
        output_mode = OutputMode.SPLIT
    else:
        output_mode = OutputMode.FULL
    return TrimmerConfig(
        input_path=args.input,
        timestamp=args.timestamp,
        frame=args.frame,
        end_timestamp=args.end_timestamp,
        end_frame=args.end_frame,
        output=args.output,
        output_dir=args.output_dir,
        suffix=args.suffix,
        force=args.force,
        reencode=args.reencode,
        video_codec=args.video_codec,
        audio_codec=args.audio_codec,
        hw_accel=args.hw_accel,
        output_mode=output_mode,
        verbose=args.verbose,
    )


def default_progress(pct: float, seconds: float) -> None:
    """默认进度回调：输出单行动态进度条。"""
    width = 30
    filled = int(width * pct // 100)
    bar = "#" * filled + "-" * (width - filled)
    sys.stdout.write(f"\r进度: [{bar}] {pct:5.1f}%（{seconds:.2f}s）")
    sys.stdout.flush()


def confirm_overwrite(path: str) -> bool:
    """交互式覆盖确认。"""
    try:
        answer = input(f"输出文件已存在: {path}\n是否覆盖？[y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def print_result(result) -> None:
    """打印裁剪结果。"""
    print()
    print("裁剪完成。")
    print(f"  输入文件: {result.input_path}")
    if result.frame:
        print(f"  从第 {result.frame} 帧开始（{result.timestamp:.6f}s）")
    else:
        print(f"  从 {result.timestamp:.6f}s 开始")
    if result.end_timestamp is not None:
        if result.end_frame:
            print(f"  到第 {result.end_frame} 帧结束（{result.end_timestamp:.6f}s，"
                  "保留 [起点, 终点) 区间）")
        else:
            print(f"  到 {result.end_timestamp:.6f}s 结束（保留 [起点, 终点) 区间）")
    print(f"  处理方式: {result.message}")
    print("  输出文件:")
    for out in result.output_files:
        print(f"    - {out}")


def main(argv: Optional[List[str]] = None) -> int:
    """命令行主入口，返回进程退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    try:
        config = build_config(args)
        result = Trimmer(config).run(progress_cb=default_progress, confirm_cb=confirm_overwrite)
        print_result(result)
        return 0
    except TrimmerError as exc:
        print(f"错误[{exc.code}]: {exc.message}", file=sys.stderr)
        return getattr(exc, "exit_code", 1) or 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
