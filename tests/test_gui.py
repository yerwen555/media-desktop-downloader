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


def test_results_table_expand_item(tk_root):
    from media_crawler.ui.table import ResultsTable

    table = ResultsTable(tk_root)
    table.pack()
    try:
        aggregate = MediaItem(
            "bilibili",
            "bilibili:BV1xx411c7mD",
            "video",
            "测试视频（2P）",
            "UP主",
            1700000000,
            125000,
            payload={"bvid": "BV1xx411c7mD", "pages": [{"cid": 111}, {"cid": 222}]},
        )
        other = _items()[0]
        table.set_items([aggregate, other], {})
        tk_root.update_idletasks()

        pages = [
            MediaItem(
                "bilibili",
                "bilibili:BV1xx411c7mD:111",
                "video",
                "测试视频 - P1 P1",
                "UP主",
                1700000000,
                60000,
                payload={"bvid": "BV1xx411c7mD", "cid": 111, "page": 1, "page_count": 2},
            ),
            MediaItem(
                "bilibili",
                "bilibili:BV1xx411c7mD:222",
                "video",
                "测试视频 - P2 P2",
                "UP主",
                1700000000,
                65000,
                payload={"bvid": "BV1xx411c7mD", "cid": 222, "page": 2, "page_count": 2},
            ),
        ]
        new_uids = table.expand_item(
            aggregate.uid, pages, {"bilibili:BV1xx411c7mD:222": "C:/x/v_P2.mp4"}
        )
        tk_root.update_idletasks()

        assert new_uids == [item.uid for item in pages]
        assert aggregate.uid not in table.items
        assert table.order == [
            "bilibili:BV1xx411c7mD:111",
            "bilibili:BV1xx411c7mD:222",
            other.uid,
        ]
        # 原来整段条目是选中状态，展开后的分P默认继承选中
        assert set(new_uids) <= table.selected
        assert table.selected_count() == 3
        row = table.tree.item("bilibili:BV1xx411c7mD:222", "values")
        assert row[1] == "视频·P2/2"
        assert row[8] == "已下载"
        assert table.tree.exists(aggregate.uid) is False
    finally:
        table.destroy()


def test_page_downloaded_map_matches_parent_files():
    """整段下载留下的 xxx_P3.mp4 应能标记出分P 3 已下载。"""
    import types

    from media_crawler.ui.app import MainWindow

    records = {
        "bilibili:BV1": {
            "files": [
                "D:/dl/up/2026_测试_BV1_P1.mp4",
                "D:/dl/up/2026_测试_BV1_P3.mp4",
            ]
        },
        "bilibili:BV1:222": {"files": ["D:/dl/up/单独下的_P2.mp4"]},
    }
    fake = types.SimpleNamespace(
        store=types.SimpleNamespace(get=lambda uid: records.get(uid))
    )
    pages = [
        MediaItem("bilibili", "bilibili:BV1:111", "video", "P1", payload={"page": 1, "bvid": "BV1"}),
        MediaItem("bilibili", "bilibili:BV1:222", "video", "P2", payload={"page": 2, "bvid": "BV1"}),
        MediaItem("bilibili", "bilibili:BV1:333", "video", "P3", payload={"page": 3, "bvid": "BV1"}),
        MediaItem("bilibili", "bilibili:BV1:444", "video", "P4", payload={"page": 4, "bvid": "BV1"}),
    ]
    found = MainWindow._page_downloaded_map(fake, "bilibili:BV1", pages)
    assert found == {
        "bilibili:BV1:111": "D:/dl/up/2026_测试_BV1_P1.mp4",
        "bilibili:BV1:222": "D:/dl/up/单独下的_P2.mp4",
        "bilibili:BV1:333": "D:/dl/up/2026_测试_BV1_P3.mp4",
    }


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
