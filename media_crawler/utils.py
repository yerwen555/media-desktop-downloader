# -*- coding: utf-8 -*-
"""通用小工具：文件名清洗、时长解析、唯一路径。"""

from __future__ import annotations

import re
from pathlib import Path

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_HTML_TAG = re.compile(r"<[^>]+>")
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_name(name: str, max_len: int = 80, default: str = "未命名") -> str:
    """把标题变成 Windows 合法文件名。"""
    text = _INVALID_CHARS.sub("_", (name or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    if len(text) > max_len:
        text = text[:max_len].rstrip(" .")
    if not text or text.upper() in _RESERVED_NAMES:
        text = default
    return text


def strip_html(text: str) -> str:
    return _HTML_TAG.sub("", text or "")


def parse_length_text(text: str) -> int:
    """B 站 length 字段（"12:34" / "1:02:03"）→ 毫秒。"""
    if not text:
        return 0
    parts = str(text).split(":")
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        return 0
    seconds = 0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds * 1000


def unique_path(path: Path) -> Path:
    """如果同名文件已存在，自动追加 _1、_2 ..."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}_{path.name}{suffix}")


def format_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}GB"
