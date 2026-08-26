# -*- coding: utf-8 -*-
"""统一的日志工具。"""
import logging
import sys
from typing import Optional

_configured: set = set()
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"


def _ensure_configured(level: str) -> None:
    key = f"detector:{level.upper()}"
    if key in _configured:
        return
    logger = logging.getLogger("detector")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    _configured.add(key)


def get_logger(name: str = "detector", level: Optional[str] = None) -> logging.Logger:
    """获取模块统一格式的 logger。

    :param name: logger 名称（建议使用 ``__name__``）
    :param level: 日志级别，不传则沿用已配置级别
    """
    if level:
        _ensure_configured(level)
    return logging.getLogger(name)
