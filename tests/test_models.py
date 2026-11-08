# -*- coding: utf-8 -*-
"""数据模型展示属性测试。"""

from __future__ import annotations

from media_crawler.models import MediaItem, format_size


def test_video_item_display():
    item = MediaItem(
        platform="bilibili",
        uid="bilibili:BV1xx411c7mD",
        kind="video",
        title="测试",
        author="UP",
        create_time=1700000000,
        duration_ms=125_000,
        views=12345,
    )
    assert item.platform_text == "B站"
    assert item.type_text == "视频"
    assert item.length_text == "2:05"
    assert item.stat_text == "1.2万"
    assert item.date_text.startswith("2023-11-")


def test_image_item_display():
    item = MediaItem(
        platform="douyin",
        uid="douyin:1",
        kind="image",
        title="图集",
        image_count=6,
        likes=888,
    )
    assert item.platform_text == "抖音"
    assert item.type_text == "图集"
    assert item.length_text == "6 张"
    assert item.stat_text == "888"
    assert item.date_text == "-"


def test_author_fallback():
    item = MediaItem(platform="douyin", uid="douyin:2", kind="video", title="x")
    assert item.author_safe == "未知作者"


def test_collab_display_and_ids():
    item = MediaItem(
        platform="douyin",
        uid="douyin:7699475016803913871",
        kind="video",
        title="合作视频",
        author="主作者",
        payload={"aweme_id": "7699475016803913871"},
        collaborators=["合作作者A", "合作作者B"],
        collab_flag=True,
    )
    assert item.is_collab is True
    assert item.author_display == "主作者 + 合作作者A + 合作作者B"
    assert item.item_id == "7699475016803913871"
    assert item.id_short == "03913871"

    bili = MediaItem(
        platform="bilibili",
        uid="bilibili:BV1xx411c7mD",
        kind="video",
        title="x",
        payload={"bvid": "BV1xx411c7mD"},
    )
    assert bili.item_id == "BV1xx411c7mD"
    assert bili.id_short == "BV1xx411c7mD"
    assert bili.is_collab is False


def test_page_fields_display():
    page = MediaItem(
        platform="bilibili",
        uid="bilibili:BV1xx411c7mD:222",
        kind="video",
        title="测试视频 - P3 词汇",
        payload={
            "bvid": "BV1xx411c7mD",
            "cid": 222,
            "page": 3,
            "page_count": 94,
            "part": "词汇",
            "pages": [{"cid": 222}],
        },
    )
    assert page.page_index == 3
    assert page.page_count == 94
    assert page.is_multi_page is True
    assert page.page_label == "P3/94"
    assert page.display_kind == "视频·P3/94"
    assert page.item_id == "BV1xx411c7mD"

    aggregate = MediaItem(
        platform="bilibili",
        uid="bilibili:BV1xx411c7mD",
        kind="video",
        title="测试视频（2P）",
        payload={"bvid": "BV1xx411c7mD", "pages": [{"cid": 1}, {"cid": 2}]},
    )
    assert aggregate.page_label == ""
    assert aggregate.page_count == 2
    assert aggregate.display_kind == "视频"

    single = MediaItem(platform="bilibili", uid="bilibili:BV2", kind="video", title="x")
    assert single.page_count == 1
    assert single.is_multi_page is False
    assert single.display_kind == "视频"


def test_format_size():
    assert format_size(1023) == "1023B"
    assert format_size(1024) == "1.0KB"
    assert format_size(1024 * 1024) == "1.0MB"
