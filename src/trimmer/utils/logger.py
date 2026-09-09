# -*- coding: utf-8 -*-
"""日志工具：统一的日志器配置与获取入口。"""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "trimmer"
_configured = False


def get_logger(name=None):
    """获取 trimmer 命名空间下的日志器。"""
    return logging.getLogger(name or _LOGGER_NAME)


def configure_logging(verbose=False):
    """配置 trimmer 日志器：默认输出到 stderr，verbose 时显示 DEBUG 级别。"""
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger
    handler = logging.StreamHandler(sys.stderr)
    if verbose:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    else:
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    _configured = True
    return logger
