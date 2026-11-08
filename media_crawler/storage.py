# -*- coding: utf-8 -*-
"""SQLite 下载记录：去重、断点续传、已下载标记。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .models import MediaItem

def path_root_available(path: str | Path) -> bool:
    """文件所在的盘符/网络盘根当前是否可访问。

    移动硬盘或网络盘没连接时，文件路径看起来“不存在”，但这不等于文件被删除；
    这时不能清理下载记录，否则重新插回磁盘后会丢失去重信息。
    """
    try:
        anchor = Path(path).anchor
    except (TypeError, ValueError, OSError):
        return True
    if not anchor:
        return True
    try:
        return Path(anchor).exists()
    except OSError:
        return False


_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    uid         TEXT PRIMARY KEY,
    platform    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    title       TEXT,
    author      TEXT,
    files       TEXT,   -- JSON 数组
    size        INTEGER DEFAULT 0,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_downloads_platform ON downloads(platform);
CREATE TABLE IF NOT EXISTS custom_titles (
    uid         TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    updated_at  TEXT
);
"""


class DownloadStore:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    # ---------- 基础 ----------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ---------- 写入 ----------
    def mark(self, item: MediaItem, files: list[str], size: int = 0) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO downloads
                (uid, platform, kind, title, author, files, size, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.uid,
                    item.platform,
                    item.kind,
                    item.title,
                    item.author,
                    json.dumps(files, ensure_ascii=False),
                    int(size),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            conn.commit()

    def remove(self, uid: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM downloads WHERE uid = ?", (uid,))
            conn.execute("DELETE FROM custom_titles WHERE uid = ?", (uid,))
            conn.commit()

    def set_custom_title(self, uid: str, title: str) -> None:
        """保存用户手动改的标题（即使没下载也保留，下次抓取继续显示）。"""
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO custom_titles (uid, title, updated_at) VALUES (?, ?, ?)",
                (uid, title, datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()

    def custom_titles(self) -> dict[str, str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT uid, title FROM custom_titles").fetchall()
        return {uid: title for uid, title in rows if title}

    def update_record(
        self,
        uid: str,
        *,
        title: str | None = None,
        files: list[str] | None = None,
    ) -> bool:
        """重命名等场景下更新记录（标题/文件路径/大小）。返回是否找到记录。"""
        current = self.get(uid)
        if current is None:
            return False
        new_title = current["title"] if title is None else title
        new_files = current["files"] if files is None else files
        new_size = current["size"]
        if files is not None:
            new_size = 0
            for file_path in new_files:
                try:
                    new_size += os.path.getsize(file_path)
                except OSError:
                    continue
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE downloads SET title = ?, files = ?, size = ? WHERE uid = ?",
                (new_title, json.dumps(new_files, ensure_ascii=False), int(new_size), uid),
            )
            conn.commit()
        return True

    def clear(self) -> int:
        with self._lock, self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
            conn.execute("DELETE FROM downloads")
            conn.execute("DELETE FROM custom_titles")
            conn.commit()
            return count

    # ---------- 读取 ----------
    def get(self, uid: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT uid, platform, kind, title, author, files, size, created_at "
                "FROM downloads WHERE uid = ?",
                (uid,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def is_downloaded(self, uid: str) -> bool:
        return self.get(uid) is not None

    def downloaded_map(self, check_files: bool = True) -> dict[str, str]:
        """返回 uid -> 本地首个文件路径。CHECK_FILES=True 时清理文件已丢失的记录。"""
        result: dict[str, str] = {}
        stale: list[str] = []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT uid, files FROM downloads"
            ).fetchall()
        for uid, files_json in rows:
            try:
                files = json.loads(files_json or "[]")
            except ValueError:
                files = []
            path = files[0] if files else ""
            if check_files and path and not Path(path).exists():
                if not path_root_available(path):
                    # 磁盘/网络盘暂时不可用：保留记录，等插回后继续使用
                    result[uid] = path
                    continue
                stale.append(uid)
                continue
            result[uid] = path
        if stale:
            with self._lock, self._connect() as conn:
                conn.executemany("DELETE FROM downloads WHERE uid = ?", [(u,) for u in stale])
                conn.commit()
        return result

    def stats(self) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
            videos = conn.execute(
                "SELECT COUNT(*) FROM downloads WHERE kind = 'video'"
            ).fetchone()[0]
            images = conn.execute(
                "SELECT COUNT(*) FROM downloads WHERE kind = 'image'"
            ).fetchone()[0]
            size = conn.execute("SELECT COALESCE(SUM(size), 0) FROM downloads").fetchone()[0]
        return {"total": total, "video": videos, "image": images, "size": int(size)}

    @staticmethod
    def _row_to_dict(row) -> dict:
        uid, platform, kind, title, author, files_json, size, created_at = row
        try:
            files = json.loads(files_json or "[]")
        except ValueError:
            files = []
        return {
            "uid": uid,
            "platform": platform,
            "kind": kind,
            "title": title,
            "author": author,
            "files": files,
            "size": size,
            "created_at": created_at,
        }
