# -*- coding: utf-8 -*-
"""颜色转换与颜色匹配工具函数。

置信度度量说明
--------------
本模块使用「欧氏距离 + 可配置容差」的置信度定义：

- dist = 像素与目标颜色的 RGB 欧氏距离；
- max_dist = sqrt(3) * 255（RGB 空间最大欧氏距离）；
- 若 dist <= tolerance，则置信度 = 1.0；
- 否则置信度 = 1 - (dist - tolerance) / (max_dist - tolerance)。

即：当像素与目标颜色距离不超过 ``tolerance`` 时置信度为 1.0，
超过容差后随距离线性递减至 0；``tolerance`` 即为「颜色容差范围」可配置接口。
"""
from typing import Tuple, Union

import numpy as np

#: RGB 空间最大可能欧氏距离
MAX_RGB_DISTANCE = float(np.sqrt(3.0) * 255.0)

RGB = Tuple[int, int, int]


def hex_to_rgb(hex_str: str) -> RGB:
    """将十六进制颜色字符串转换为 RGB 元组。

    :param hex_str: 形如 ``'#F7F10F'`` 或 ``'F7F10F'``（大小写均可）
    :return: (r, g, b) 元组
    :raises ValueError: 输入格式非法
    """
    s = hex_str.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6:
        raise ValueError(f"非法十六进制颜色: {hex_str!r}")
    try:
        return tuple(int(s[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise ValueError(f"非法十六进制颜色: {hex_str!r}") from exc


def rgb_to_hex(rgb: RGB) -> str:
    """将 RGB 元组转换为大写十六进制颜色字符串，如 ``#F7F10F``。

    :param rgb: (r, g, b) 元组
    """
    if len(rgb) != 3:
        raise ValueError(f"非法 RGB 元组: {rgb!r}")
    return "#" + "".join(f"{max(0, min(255, int(c))):02X}" for c in rgb)


def color_confidence(
    pixel: Union[RGB, np.ndarray],
    target: Union[RGB, np.ndarray],
    tolerance: float = 0.0,
) -> float:
    """计算单个像素与目标颜色的匹配置信度，取值 [0, 1]。

    :param pixel: 像素 RGB 值
    :param target: 目标颜色 RGB 值
    :param tolerance: 颜色容差（RGB 欧氏距离）
    """
    if tolerance < 0:
        raise ValueError("tolerance 不能为负")
    p = np.asarray(pixel, dtype=np.float64).ravel()[:3]
    t = np.asarray(target, dtype=np.float64).ravel()[:3]
    if p.shape != t.shape or p.shape != (3,):
        raise ValueError("pixel / target 必须是长度为 3 的 RGB 值")

    dist = float(np.linalg.norm(p - t))
    if tolerance >= MAX_RGB_DISTANCE:
        return 1.0
    if dist <= tolerance:
        return 1.0
    return max(0.0, min(1.0, 1.0 - (dist - tolerance) / (MAX_RGB_DISTANCE - tolerance)))


def color_match(confidence: float, threshold: float) -> bool:
    """判断置信度是否达到阈值（严格匹配）。"""
    return float(confidence) >= float(threshold)
