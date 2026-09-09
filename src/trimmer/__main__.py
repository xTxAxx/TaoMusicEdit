# -*- coding: utf-8 -*-
"""``python -m trimmer`` 程序入口（在 src 目录下运行，或设置 PYTHONPATH=src）。"""
import os
import sys

# 防御引导：直接运行本文件时也能导入 trimmer 包
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from trimmer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
