# -*- coding: utf-8 -*-
"""重命名：把用户输入的新标题应用到作品，并在需要时同步重命名已下载文件。"""

from __future__ import annotations

import re
from pathlib import Path

from .models import MediaItem
from .storage import DownloadStore
from .utils import sanitize_name, strip_html, unique_path


def _safe_title(title: str, limit: int = 60) -> str:
    return sanitize_name(strip_html((title or "").strip()), limit, default="作品")


def _date_prefix(item: MediaItem) -> str:
    return item.date_text if item.date_text != "-" else ""


def rename_downloaded_files(
    item: MediaItem,
    new_title: str,
    store: DownloadStore,
) -> tuple[bool, list[str], str]:
    """把 ITEM 对应的本地文件按新标题重命名，并更新 SQLite 记录。

    返回 (是否成功, 新文件路径列表, 错误信息)。
    """
    record = store.get(item.uid)
    if record is None:
        return False, [], "没有找到下载记录"
    old_files = [Path(p) for p in record.get("files") or []]
    if not old_files:
        return False, [], "下载记录中没有文件路径"
    missing = [p for p in old_files if not p.exists()]
    if missing:
        return False, [], f"文件已不存在：{missing[0]}"

    prefix = _date_prefix(item)
    moves: list[tuple[Path, Path]] = []

    if item.platform == "douyin":
        aweme_id = str(item.payload.get("aweme_id") or item.uid.split(":")[-1])
        short_id = aweme_id[-8:]
        if item.kind == "video":
            title = _safe_title(new_title, 60)
            filename = sanitize_name(f"{prefix}_{title}_{short_id}.mp4".lstrip("_"), 150)
            moves.append((old_files[0], old_files[0].parent / filename))
        else:
            title = _safe_title(new_title, 60)
            album_name = sanitize_name(f"{prefix}_{title}_{short_id}".lstrip("_"), 80)
            album_dir = old_files[0].parent
            new_dir = album_dir.parent / album_name
            if new_dir.exists() and new_dir != album_dir:
                new_dir = unique_path(new_dir)
            moves.extend((old, new_dir / old.name) for old in old_files)
    else:  # bilibili
        bvid = str(item.payload.get("bvid") or item.uid.split(":")[-1])
        title = _safe_title(new_title, 70)
        for old in old_files:
            page_match = re.search(r"_P(\d+)(?=\.mp4$)", old.name)
            page_suffix = f"_P{page_match.group(1)}" if page_match else ""
            filename = sanitize_name(f"{prefix}_{title}_{bvid}{page_suffix}.mp4".lstrip("_"), 150)
            moves.append((old, old.parent / filename))

    # 先处理“新名字就是原文件”的情况，再逐个移动
    resolved: list[tuple[Path, Path]] = []
    try:
        for old, target in moves:
            if old == target:
                resolved.append((old, old))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target = unique_path(target)
            old.rename(target)
            resolved.append((old, target))
    except OSError as exc:
        for old, target in reversed(resolved):
            try:
                if target != old:
                    target.rename(old)
            except OSError:
                pass
        return False, [], f"重命名文件失败：{exc}"

    # 相册换目录后，清掉空的旧目录
    if item.platform == "douyin" and item.kind == "image":
        old_dir = old_files[0].parent
        new_dir = resolved[0][1].parent
        try:
            if old_dir != new_dir and old_dir.exists() and not any(old_dir.iterdir()):
                old_dir.rmdir()
        except OSError:
            pass

    new_paths = [str(target) for _, target in resolved]
    store.update_record(item.uid, title=new_title, files=new_paths)
    store.set_custom_title(item.uid, new_title)
    return True, new_paths, ""
