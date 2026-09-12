# -*- coding: utf-8 -*-
"""TaoMusicEdit —— 统一启动入口。

在项目根目录运行::

    python run.py        # 或 py run.py

命令行参数（可选，python run.py --help 查看全部）::

    --host     监听地址，默认 127.0.0.1
    --port     监听端口，默认 29619
    --workers  批处理并发数兜底值，0 = 自动

启动后浏览器打开 http://127.0.0.1:29619/ 即可使用 Web UI。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_ROOT, "src")
# 让 src 目录可被导入；webui/app.py 会自行把 src 与本地 vendored flask 加入 sys.path
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from webui.app import main  # noqa: E402  (src/webui/app.py)


if __name__ == "__main__":
    main()
