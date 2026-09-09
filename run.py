# -*- coding: utf-8 -*-
"""TaoMusicEdit —— 统一启动入口。

在项目根目录运行::

    python run.py        # 或 py run.py

环境变量（可选）::

    WEBUI_HOST  监听地址，默认 127.0.0.1
    WEBUI_PORT  监听端口，默认 8765

启动后浏览器打开 http://127.0.0.1:8765/ 即可使用 Web UI。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_ROOT, "src")
# 让 src 目录可被导入；webui/app.py 会自行把 src 与本地 vendored flask 加入 sys.path
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from webui.app import STATE, app  # noqa: E402  (src/webui/app.py)


def main() -> None:
    host = os.environ.get("WEBUI_HOST", "127.0.0.1")
    port = int(os.environ.get("WEBUI_PORT", "8765"))
    print(f"TaoMusicEdit Web UI: http://{host}:{port}")
    print(f"默认工作区: {STATE['workspace']}")
    app.run(host=host, port=port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
