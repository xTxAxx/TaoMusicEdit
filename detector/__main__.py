# -*- coding: utf-8 -*-
"""支持 ``py -m detector <视频文件>`` 形式的命令行调用（无需安装，需在项目根目录运行）。"""
import sys

from detector.cli import main

if __name__ == "__main__":
    sys.exit(main())
