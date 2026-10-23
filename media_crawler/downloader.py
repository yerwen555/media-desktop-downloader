# -*- coding: utf-8 -*-
"""下载调度：并发、去重、进度回调、取消。"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from .bilibili import BiliClient
from .config import AppConfig
from .douyin import DouyinCrawler
from .models import DownloadSummary, ItemOutcome, MediaItem
from .net import build_session
from .storage import DownloadStore

StartCb = Callable[[MediaItem], None]
ProgressCb = Callable[[str, float], None]
DoneCb = Callable[[ItemOutcome], None]


class DownloadManager:
    def __init__(self, cfg: AppConfig, store: DownloadStore, logger: logging.Logger | None = None):
        self.cfg = cfg
        self.store = store
        self.log = logger or logging.getLogger("media_crawler")

    def run(
        self,
        items: list[MediaItem],
        *,
        cookies: str = "",
        cancel: threading.Event | None = None,
        on_start: StartCb | None = None,
        on_progress: ProgressCb | None = None,
        on_done: DoneCb | None = None,
    ) -> DownloadSummary:
        summary = DownloadSummary(total=len(items))
        if not items:
            return summary
        cancel = cancel or threading.Event()

        douyin_items = [item for item in items if item.platform == "douyin"]
        bili_items = [item for item in items if item.platform == "bilibili"]
        unknown = [item for item in items if item.platform not in ("douyin", "bilibili")]
        for item in unknown:
            outcome = ItemOutcome(item.uid, item.title, False, error="未知平台")
            self._finish(summary, outcome, on_done)

        douyin_session = None
        douyin_crawler = None
        if douyin_items:
            douyin_session = build_session(self.cfg.user_agent, cookies)
            douyin_crawler = DouyinCrawler(self.cfg, self.log)
        bili_client = BiliClient(self.cfg, cookies, self.log) if bili_items else None

        def work(item: MediaItem) -> ItemOutcome:
            if cancel.is_set():
                return ItemOutcome(item.uid, item.title, False, error="已取消")
            if on_start is not None:
                self._safe(on_start, item)

            if self.cfg.skip_downloaded:
                record = self.store.get(item.uid)
                if record is not None:
                    files = [p for p in record.get("files", []) if Path(p).exists()]
                    if files:
                        self.log.info("已下载，跳过: %s", item.title)
                        return ItemOutcome(item.uid, item.title, True, files, skipped=True)
                    self.store.remove(item.uid)

            def progress_callback(fraction: float) -> None:
                if on_progress is not None:
                    try:
                        on_progress(item.uid, max(0.0, min(1.0, fraction)))
                    except Exception:
                        pass

            try:
                if item.platform == "douyin":
                    assert douyin_session is not None and douyin_crawler is not None
                    return douyin_crawler.download_item(
                        item, douyin_session, self.cfg, progress_callback, cancel
                    )
                assert bili_client is not None
                return bili_client.download_item(item, self.cfg, progress_callback, cancel)
            except Exception as exc:  # 单个任务失败不影响其他任务
                self.log.exception("下载 %s 失败: %s", item.title, exc)
                return ItemOutcome(item.uid, item.title, False, error=str(exc))

        workers = max(1, min(int(self.cfg.max_workers), len(items)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dl") as executor:
            futures = {executor.submit(work, item): item for item in items}
            for future in as_completed(futures):
                outcome = future.result()
                if outcome.ok and not outcome.skipped:
                    size = self._total_size(outcome.files)
                    outcome.bytes_written = size
                    summary.bytes_written += size
                    if outcome.error:
                        # 部分分 P 失败：不写入数据库，下次可自动补下缺失部分
                        self.log.warning(
                            "部分完成: %s（%s）", outcome.title, outcome.error
                        )
                    else:
                        item = futures[future]
                        self.store.mark(item, outcome.files, size)
                self._finish(summary, outcome, on_done)

        summary.cancelled = cancel.is_set()
        self.log.info("下载结束：%s", summary.text)
        return summary

    # ---------- 辅助 ----------
    def _finish(
        self,
        summary: DownloadSummary,
        outcome: ItemOutcome,
        on_done: DoneCb | None,
    ) -> None:
        if outcome.ok:
            if outcome.skipped:
                summary.skipped += 1
            else:
                summary.succeeded += 1
        else:
            summary.failed += 1
        summary.outcomes.append(outcome)
        if on_done is not None:
            self._safe(on_done, outcome)

    @staticmethod
    def _total_size(files: list[str]) -> int:
        total = 0
        for file in files:
            try:
                total += os.path.getsize(file)
            except OSError:
                continue
        return total

    @staticmethod
    def _safe(callback, arg) -> None:
        try:
            callback(arg)
        except Exception:
            pass
