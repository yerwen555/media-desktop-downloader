# -*- coding: utf-8 -*-
"""界面组件测试（无显示器环境自动跳过）。"""

from __future__ import annotations

import tkinter as tk

import pytest

from media_crawler.models import MediaItem


def _items() -> list[MediaItem]:
    return [
        MediaItem("douyin", "douyin:a", "video", "视频 A", "作者", 1700000000, 60000),
        MediaItem("douyin", "douyin:b", "image", "图集 B", "作者", 1700000100, image_count=3),
        MediaItem("douyin", "douyin:c", "video", "视频 C", "另一个作者", 1700000200, 90000),
    ]


@pytest.fixture(scope="module")
def tk_root():
    """同一进程内只创建一次 Tk（Windows/Anaconda 下反复创建可能加载 Tcl 失败）。"""
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - 无显示环境
        pytest.skip(f"没有可用的图形环境: {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_results_table_selection(tk_root):
    from media_crawler.ui.table import ResultsTable

    table = ResultsTable(tk_root)
    table.pack()
    try:
        items = _items()
        table.set_items(items, downloaded={"douyin:b": "C:/x/i.jpg"})
        tk_root.update_idletasks()

        assert table.total_count() == 3
        assert table.selected_count() == 3  # 默认全选
        assert table.get_selected_items()[0].title == "视频 A"

        table.select_none()
        assert table.selected_count() == 0

        table.select_all()
        assert table.selected_count() == 3

        table.invert()
        assert table.selected_count() == 0

        table.toggle("douyin:a")
        assert table.selected_count() == 1
        tk_root.update_idletasks()
        row = table.tree.item("douyin:a", "values")
        assert row[0] == "√"
        assert row[8] == "未下载"

        # 已下载状态显示
        row_b = table.tree.item("douyin:b", "values")
        assert row_b[8] == "已下载"

        # 只选未下载：B 已下载，应只选 A、C
        table.select_undownloaded()
        assert table.selected_count() == 2
        assert "douyin:b" not in table.selected
    finally:
        table.destroy()


def test_results_table_shows_id_and_collab(tk_root):
    from media_crawler.ui.table import ResultsTable

    table = ResultsTable(tk_root)
    table.pack()
    try:
        item = MediaItem(
            "bilibili",
            "bilibili:BV1xx411c7mD",
            "video",
            "联合投稿视频",
            "主UP",
            1700000000,
            60000,
            payload={"bvid": "BV1xx411c7mD"},
            collaborators=["合作UP（参演）"],
            collab_flag=True,
        )
        table.set_items([item], {})
        tk_root.update_idletasks()
        row = table.tree.item(item.uid, "values")
        assert row[1] == "视频·合作"
        assert row[3] == "BV1xx411c7mD"
        assert "主UP + 合作UP" == row[7]
    finally:
        table.destroy()


def test_results_table_filter(tk_root):
    from media_crawler.ui.table import ResultsTable

    table = ResultsTable(tk_root)
    table.pack()
    try:
        table.set_items(_items(), downloaded={})
        tk_root.update_idletasks()

        table.kind_var.set("视频")
        table.refresh()
        tk_root.update_idletasks()
        assert len(table._visible_items()) == 2

        table.kind_var.set("全部")
        table.keyword_var.set("另一个")
        table.refresh()
        assert [item.uid for item in table._visible_items()] == ["douyin:c"]

        table.keyword_var.set("不存在的关键词")
        table.refresh()
        assert table._visible_items() == []
    finally:
        table.destroy()


def test_crawl_applies_custom_titles(tk_root):
    import shutil
    import uuid

    from media_crawler.config import PROJECT_ROOT, AppConfig
    from media_crawler.models import CrawlResult, MediaItem
    from media_crawler.storage import DownloadStore
    from media_crawler.ui.app import MainWindow

    work = PROJECT_ROOT / "data" / "tmp" / f"gui_custom_{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    window = MainWindow(tk_root, AppConfig())
    try:
        window.store = DownloadStore(work / "media.db")
        item = MediaItem(
            "douyin", "douyin:9001", "video", "接口里的原标题", "作者", 1700000000, 60000
        )
        window.store.set_custom_title("douyin:9001", "我改的名字")
        window._on_crawl_done(window.douyin_panel, CrawlResult(items=[item]))
        assert window.douyin_panel.table.items["douyin:9001"].title == "我改的名字"
    finally:
        from media_crawler.logging_setup import remove_handler

        remove_handler(window.log, window.log_handler)
        if window._poll_job is not None:
            try:
                tk_root.after_cancel(window._poll_job)
            except Exception:
                pass
        shutil.rmtree(work, ignore_errors=True)


def test_results_table_filter_by_id_and_collab(tk_root):
    from media_crawler.ui.table import ResultsTable

    table = ResultsTable(tk_root)
    table.pack()
    try:
        item = MediaItem(
            "bilibili",
            "bilibili:BV1xx411c7mD",
            "video",
            "相同标题",
            "主UP",
            1700000000,
            60000,
            payload={"bvid": "BV1xx411c7mD"},
            collaborators=["合作UP（参演）"],
            collab_flag=True,
        )
        table.set_items([item], {})
        table.kind_var.set("全部")
        table.keyword_var.set("bv1xx411c7md")
        table.refresh()
        assert len(table._visible_items()) == 1
        table.keyword_var.set("合作UP")
        table.refresh()
        assert len(table._visible_items()) == 1
        table.keyword_var.set("不存在")
        table.refresh()
        assert table._visible_items() == []
    finally:
        table.destroy()


def test_main_window_smoke(tk_root):
    from media_crawler.config import AppConfig
    from media_crawler.ui.app import MainWindow

    window = MainWindow(tk_root, AppConfig())
    tk_root.update_idletasks()
    assert window.douyin_panel.winfo_exists()
    assert window.bili_panel.winfo_exists()
    window.douyin_panel.table.set_items(_items(), {})
    assert window.douyin_panel.table.total_count() == 3
    window._on_close()
