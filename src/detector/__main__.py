# -*- coding: utf-8 -*-
"""支持 ``py -m detector <视频文件>`` 形式的命令行调用（在 src 目录下运行，
或设置 PYTHONPATH=src；也可直接运行 ``py src\\detector\\__main__.py``）。"""
import os
import sys

# 防御引导：直接运行本文件时也能导入 detector 包
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from detector.cli import main

if __name__ == "__main__":
    sys.exit(main())
