# -*- coding: utf-8 -*-
"""Web UI 设置持久化。

将工作区、输出目录以及 detector / trimmer 的全部可调参数保存为本地
JSON 配置文件（``webui/settings.json``），使 Web UI 重启后自动恢复上次设置，
而不是每次启动都用默认值。

所有读写操作均加锁，避免后台任务线程与请求线程并发写坏文件。
"""
from __future__ import annotations

import json
import os
import threading

_BASE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(_BASE, "settings.json")
_LOCK = threading.Lock()

#: detector 参数默认值（与前端 static/js/params.js 的 default 保持一致）
DEFAULT_DETECTOR = {
    "target_color": "#F7F10F",
    "points": "20,20 1900,20 20,1060 1900,1060",
    "confidence_threshold": "0.97",
    "color_tolerance": "10.0",
    "scale_points": True,
    "search_window": "40",
    "initial_start": "35",
    "coarse_step": "5",
    "fine_step": "1",
    "extractor": "ffmpeg",
}

#: trimmer 参数默认值（与前端 static/js/params.js 的 default 保持一致）。
#: 裁剪起点（start_mode / start_value）因视频而异，不参与持久化，故不在此列出。
DEFAULT_TRIMMER = {
    "suffix": "_trim",
    "output_mode": "full",
    "force": False,
    "reencode": False,
    "hw_accel": "auto",
    "video_codec": "",
    "audio_codec": "",
}

#: 检测结果缓存配置默认值：enabled 关闭读写；limit 条目上限（0 = 不限）；
#: skip_cached 批量检测时跳过已有有效缓存的视频
DEFAULT_DETECT_CACHE = {
    "enabled": True,
    "limit": 0,
    "skip_cached": False,
}


def defaults() -> dict:
    """返回一份全新的默认设置。workspace/output_dir 为空表示使用项目根目录。"""
    return {
        "workspace": "",
        "output_dir": "",
        "detector": dict(DEFAULT_DETECTOR),
        "trimmer": dict(DEFAULT_TRIMMER),
        "detect_cache": dict(DEFAULT_DETECT_CACHE),
    }


def _merge(base: dict, data: dict) -> dict:
    """把 data 中合法的键合并到 base（缺失/类型非法的键回退默认值）。"""
    if not isinstance(data, dict):
        return base
    for key in ("workspace", "output_dir"):
        if isinstance(data.get(key), str):
            base[key] = data[key]
    for group in ("detector", "trimmer"):
        src = data.get(group)
        if isinstance(src, dict):
            for key in base[group]:
                if key in src:
                    base[group][key] = src[key]
    dc = data.get("detect_cache")
    if isinstance(dc, dict):
        if "enabled" in dc:
            base["detect_cache"]["enabled"] = bool(dc["enabled"])
        if "skip_cached" in dc:
            base["detect_cache"]["skip_cached"] = bool(dc["skip_cached"])
        if "limit" in dc:
            try:
                base["detect_cache"]["limit"] = max(0, int(dc["limit"]))
            except (TypeError, ValueError):
                pass
    return base


def load() -> dict:
    """读取设置；配置文件不存在或损坏时返回默认值。"""
    with _LOCK:
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return defaults()
    return _merge(defaults(), data)


def save(data: dict) -> dict:
    """保存设置（先合并再落盘），返回实际写入的内容。"""
    merged = _merge(defaults(), data)
    with _LOCK:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    return merged


def reset() -> dict:
    """恢复默认设置：删除配置文件并返回默认值。"""
    with _LOCK:
        try:
            os.remove(SETTINGS_PATH)
        except OSError:
            pass
    return defaults()
