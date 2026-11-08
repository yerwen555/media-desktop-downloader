# -*- coding: utf-8 -*-
"""跨平台统一的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Platform = Literal["douyin", "bilibili"]
Kind = Literal["video", "image"]


def _fmt_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, sec = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


@dataclass
class MediaItem:
    """一个可勾选下载的作品（抖音视频/图集，或 B 站单个分 P）。"""

    platform: Platform
    uid: str  # 全局唯一键，用于去重/勾选：douyin:<aweme_id> / bilibili:<bvid>:<cid>
    kind: Kind
    title: str
    author: str = ""
    create_time: int = 0  # Unix 秒
    duration_ms: int = 0
    image_count: int = 0
    likes: int = 0
    views: int = 0
    cover_url: str = ""
    page_url: str = ""
    payload: dict[str, Any] = field(default_factory=dict, repr=False)
    # 作者与合作信息（B站联合投稿 / 抖音共创）
    author_id: str = ""
    collaborators: list[str] = field(default_factory=list)
    collab_flag: bool = False

    # ---------- 展示用文本 ----------
    @property
    def platform_text(self) -> str:
        return "抖音" if self.platform == "douyin" else "B站"

    @property
    def type_text(self) -> str:
        return "视频" if self.kind == "video" else "图集"

    # ---------- 分 P 信息（B站多P视频在列表阶段就展开） ----------
    @property
    def page_index(self) -> int:
        """当前条目对应的分 P 序号；非分 P 条目返回 0。"""
        try:
            return int(self.payload.get("page") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def page_count(self) -> int:
        """所属视频的总分 P 数（至少为 1）。"""
        value = self.payload.get("page_count")
        if value:
            try:
                return max(1, int(value))
            except (TypeError, ValueError):
                pass
        pages = self.payload.get("pages")
        if isinstance(pages, list) and pages:
            return max(1, len(pages))
        return 1

    @property
    def is_multi_page(self) -> bool:
        return self.page_count > 1

    @property
    def page_label(self) -> str:
        """列表用分 P 标记：P3/94；整段视频或单 P 为空串。"""
        if not self.is_multi_page or not self.page_index:
            return ""
        return f"P{self.page_index}/{self.page_count}"

    @property
    def display_kind(self) -> str:
        """列表“类型”列：视频·P3/94·合作。"""
        parts = [self.type_text]
        label = self.page_label
        if label:
            parts.append(label)
        if self.is_collab:
            parts.append("合作")
        return "·".join(parts)

    @property
    def is_collab(self) -> bool:
        return bool(self.collaborators or self.collab_flag)

    @property
    def item_id(self) -> str:
        """作品完整 ID：抖音 aweme_id / B站 BV 号。"""
        if self.platform == "bilibili":
            return str(self.payload.get("bvid") or self.uid.split(":")[-1])
        return str(self.payload.get("aweme_id") or self.uid.split(":")[-1])

    @property
    def id_short(self) -> str:
        """列表里显示的短 ID（抖音取后 8 位，与文件名后缀一致）。"""
        full = self.item_id
        if self.platform == "douyin" and len(full) > 8:
            return full[-8:]
        return full

    @property
    def author_display(self) -> str:
        names = [self.author or "未知作者"]
        names.extend(name for name in self.collaborators if name and name not in names)
        return " + ".join(names)

    @property
    def author_table(self) -> str:
        """列表用：合作者只显示名字，角色放到详情里。"""
        if not self.collaborators:
            return self.author_safe
        names: list[str] = []
        for label in self.collaborators:
            name = label.split("（", 1)[0].strip()
            if name and name not in names:
                names.append(name)
        return f"{self.author_safe} + {'、'.join(names)}" if names else self.author_safe

    @property
    def date_text(self) -> str:
        if not self.create_time:
            return "-"
        try:
            return datetime.fromtimestamp(self.create_time).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return "-"

    @property
    def length_text(self) -> str:
        if self.kind == "image":
            return f"{self.image_count} 张" if self.image_count else "图集"
        return _fmt_seconds(self.duration_ms / 1000)

    @property
    def stat_text(self) -> str:
        value = self.likes if self.platform == "douyin" else self.views
        if not value:
            return "-"
        if value >= 10_000:
            return f"{value / 10_000:.1f}万"
        return str(value)

    @property
    def author_safe(self) -> str:
        return self.author or "未知作者"

    def __hash__(self) -> int:
        return hash(self.uid)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MediaItem) and other.uid == self.uid


@dataclass
class CrawlResult:
    """一次抓取的结果。cookies 为浏览器导出的 Cookie，供后续下载复用。"""

    items: list[MediaItem] = field(default_factory=list)
    cookies: str = ""
    author: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ItemOutcome:
    uid: str
    title: str
    ok: bool
    files: list[str] = field(default_factory=list)
    error: str = ""
    skipped: bool = False
    bytes_written: int = 0


@dataclass
class DownloadSummary:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    bytes_written: int = 0
    outcomes: list[ItemOutcome] = field(default_factory=list)
    cancelled: bool = False

    @property
    def text(self) -> str:
        parts = [
            f"共 {self.total} 项",
            f"成功 {self.succeeded}",
            f"失败 {self.failed}",
            f"跳过 {self.skipped}",
        ]
        if self.bytes_written:
            parts.append(f"写入 {format_size(self.bytes_written)}")
        if self.cancelled:
            parts.append("已取消")
        return " | ".join(parts)


def format_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}GB"
