# -*- coding: utf-8 -*-
"""交互式命令行模式（无图形界面时的备用入口）。

用法：
    python app.py --cli
"""

from __future__ import annotations

import logging
import sys
import threading

from .bilibili import BiliClient, extract_video_refs, parse_space_input
from .config import AppConfig, LOGS_DIR, config_summary
from .douyin import DouyinCrawler
from .downloader import DownloadManager
from .logging_setup import setup_logging
from .models import MediaItem
from .storage import DownloadStore


def parse_selection(expression: str, count: int) -> list[int]:
    """解析选择表达式，返回 0 基下标列表。

    - 空串 / `a` / `all`：全选
    - `n` / `none`：都不选
    - `1,3,5-8`：按下标选择（从 1 开始）
    - `!3,5-8`：从全选中排除
    - 正选与排除混合时：先按正选，再排除
    """
    text = (expression or "").strip()
    if text.lower() in ("", "a", "all", "*"):
        return list(range(count))
    if text.lower() in ("n", "none", "0"):
        return []

    tokens = [token.strip() for token in text.replace("，", ",").split(",") if token.strip()]
    # 只要有一个以 ! 开头，整个表达式都按“排除”解释：!3,5-8 = 排除 3 和 5~8
    exclude_mode = any(token.startswith("!") for token in tokens)
    selected: set[int] = set(range(count)) if exclude_mode else set()
    for token in tokens:
        body = token.lstrip("!").strip()
        if not body:
            continue
        if "-" in body:
            start_text, _, end_text = body.partition("-")
            try:
                start, end = int(start_text), int(end_text)
            except ValueError:
                continue
            indexes = range(min(start, end), max(start, end) + 1)
        else:
            try:
                indexes = [int(body)]
            except ValueError:
                continue
        for number in indexes:
            index = number - 1
            if not 0 <= index < count:
                continue
            if exclude_mode:
                selected.discard(index)
            else:
                selected.add(index)
    return sorted(selected)


def _print_items(items: list[MediaItem], downloaded: dict[str, str]) -> None:
    print("\n" + "=" * 88)
    print(f"{'序号':<5}{'类型':<14}{'状态':<8}{'日期':<12}{'时长':<9}标题")
    print("-" * 88)
    for index, item in enumerate(items, start=1):
        status = "已下载" if item.uid in downloaded else "未下载"
        title = item.title if len(item.title) <= 40 else item.title[:38] + "…"
        print(
            f"{index:<6}{item.display_kind:<15}{status:<9}{item.date_text:<13}"
            f"{item.length_text:<10}{title}"
        )
    print("=" * 88)


def _crawl_douyin(cfg: AppConfig, log: logging.Logger):
    url = input("\n粘贴抖音用户主页或作品链接：").strip()
    if not url:
        return None
    crawler = DouyinCrawler(cfg, log)
    cancel = threading.Event()

    def progress(count: int, message: str) -> None:
        print(f"\r[抓取] {message}" + " " * 10, end="", flush=True)

    try:
        result = crawler.collect(url, progress=progress, cancel=cancel)
    except Exception as exc:
        print(f"\n抓取失败：{exc}")
        return None
    print()
    for warning in result.warnings:
        print(f"提示：{warning}")
    return result


def _crawl_bilibili(cfg: AppConfig, log: logging.Logger):
    text = input("\n粘贴 B 站视频链接/BV 号（可多个，空格分隔），或 UP 主空间链接：").strip()
    if not text:
        return None
    client = BiliClient(cfg, logger=log)

    def progress(count: int, message: str) -> None:
        print(f"\r[抓取] {message}" + " " * 10, end="", flush=True)

    try:
        mid = parse_space_input(text)
        if mid:
            result = client.space_items(mid, progress=progress)
        else:
            refs = extract_video_refs(text)
            if not refs:
                print("没有识别到 BV 号，请检查输入。")
                return None
            result = client.video_items(refs, progress=progress)
    except Exception as exc:
        print(f"\n抓取失败：{exc}")
        return None
    print()
    for warning in result.warnings:
        print(f"提示：{warning}")
    return result


def run_cli(cfg: AppConfig | None = None) -> int:
    cfg = cfg or AppConfig.load()
    cfg.ensure_dirs()
    log = setup_logging(LOGS_DIR)
    store = DownloadStore(LOGS_DIR.parent / "media.db")
    warning = getattr(cfg, "dir_warning", "")
    if warning:
        print("警告：" + warning)
    print("\n抖音 / B站 视频下载助手（命令行模式）")
    print(config_summary(cfg))
    stats = store.stats()
    print(f"历史记录：{stats['total']} 个作品已下载到本地\n")

    while True:
        print("\n请选择平台：")
        print("  1. 抖音（用户主页 / 单个作品）")
        print("  2. B站（视频链接 / UP 主空间）")
        print("  0. 退出")
        choice = input("输入序号后回车：").strip()
        if choice == "0":
            print("再见。")
            return 0
        if choice not in ("1", "2"):
            print("无效输入，请重新选择。")
            continue

        result = _crawl_douyin(cfg, log) if choice == "1" else _crawl_bilibili(cfg, log)
        if result is None:
            continue
        items = result.items
        if not items:
            print("没有获取到可下载的作品。")
            for warning in result.warnings:
                print(f"提示：{warning}")
            continue

        downloaded = store.downloaded_map()
        _print_items(items, downloaded)
        print("\n选择要下载的作品：")
        print("  直接回车 = 全部；例：1,3,5-8；!3,5 = 排除 3 和 5；n = 返回")
        expression = input("选择：").strip()
        indexes = parse_selection(expression, len(items))
        if not indexes:
            print("未选择任何作品。")
            continue
        selected = [items[i] for i in indexes]
        print(f"\n即将下载 {len(selected)} 个作品，开始…\n")

        manager = DownloadManager(cfg, store, log)
        counter = {"done": 0}
        total = len(selected)

        def on_start(item: MediaItem) -> None:
            print(f"[{counter['done'] + 1}/{total}] 开始：{item.title}")

        def on_done(outcome) -> None:
            counter["done"] += 1
            if outcome.skipped:
                print(f"    跳过（已下载）：{outcome.title}")
            elif outcome.ok:
                print(f"    完成：{outcome.title}" + (f"（{outcome.error}）" if outcome.error else ""))
            else:
                print(f"    失败：{outcome.title} - {outcome.error}")

        summary = manager.run(
            selected,
            cookies=result.cookies,
            on_start=on_start,
            on_done=on_done,
        )
        print(f"\n下载结果：{summary.text}")
        if summary.failed:
            print("失败项可稍后重新选择下载。")
        input("\n按回车返回主菜单…")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run_cli())
