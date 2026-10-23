# -*- coding: utf-8 -*-
"""抖音 JSON 解析测试。"""

from __future__ import annotations

import json

from media_crawler.douyin import (
    classify_content,
    extract_aweme_list,
    extract_title,
    parse_aweme,
)

VIDEO_AWEME = {
    "aweme_id": "7300000000000000001",
    "desc": "测试标题/带:非法*字符",
    "create_time": 1700000000,
    "author": {"nickname": "测试作者"},
    "statistics": {"digg_count": 12345},
    "video": {
        "duration": 152000,
        "play_addr": {"url_list": ["https://v1.example/a.mp4"]},
        "cover": {"url_list": ["https://c.example/cover.jpg"]},
        "bit_rate": [
            {"bit_rate": 1000, "play_addr": {"url_list": ["https://v1.example/a.mp4"]}},
            {"bit_rate": 2000, "play_addr": {"url_list": ["https://v2.example/hd.mp4"]}},
        ],
    },
}

IMAGE_AWEME = {
    "aweme_id": "7300000000000000002",
    "desc": "测试图集",
    "create_time": 1700000001,
    "author": {"nickname": "图集作者"},
    "statistics": {"digg_count": 88},
    "images": [
        {"url_list": ["http://i1.example/1.jpg", "http://i1.example/1b.jpg"]},
        {"url_list": ["http://i2.example/2.jpg"]},
    ],
}


def test_classify_content():
    assert classify_content(VIDEO_AWEME) == "video"
    assert classify_content(IMAGE_AWEME) == "image"
    assert classify_content({"aweme_id": "1"}) is None


def test_parse_video_aweme():
    item = parse_aweme(VIDEO_AWEME)
    assert item is not None
    assert item.platform == "douyin"
    assert item.uid == "douyin:7300000000000000001"
    assert item.kind == "video"
    assert item.author == "测试作者"
    assert item.likes == 12345
    assert item.duration_ms == 152000
    assert item.cover_url == "https://c.example/cover.jpg"
    assert item.page_url.endswith("/video/7300000000000000001")
    urls = item.payload["urls"]
    assert urls[0] == "https://v1.example/a.mp4"
    assert "https://v2.example/hd.mp4" in urls
    assert len(urls) == 2  # 去重后


def test_parse_image_aweme():
    item = parse_aweme(IMAGE_AWEME)
    assert item is not None
    assert item.kind == "image"
    assert item.image_count == 2
    assert item.length_text == "2 张"
    assert item.payload["image_urls"][0][0] == "http://i1.example/1.jpg"
    assert item.page_url.endswith("/note/7300000000000000002")


def test_parse_aweme_invalid():
    assert parse_aweme({}) is None
    assert parse_aweme({"aweme_id": "1"}) is None
    assert parse_aweme({"aweme_id": "1", "video": {"play_addr": {"url_list": []}}}) is None


def test_parse_video_has_no_urls():
    broken = {"aweme_id": "2", "desc": "x", "video": {"play_addr": {"url_list": []}}}
    assert parse_aweme(broken) is None


def test_extract_aweme_list_variants():
    assert extract_aweme_list({"aweme_list": [VIDEO_AWEME]}) == [VIDEO_AWEME]
    assert extract_aweme_list(json.dumps({"aweme_list": [VIDEO_AWEME]}).encode()) == [VIDEO_AWEME]
    assert extract_aweme_list({"aweme_detail": VIDEO_AWEME}) == [VIDEO_AWEME]
    assert extract_aweme_list({"aweme_details": [VIDEO_AWEME, IMAGE_AWEME]}) == [
        VIDEO_AWEME,
        IMAGE_AWEME,
    ]
    assert extract_aweme_list(None) == []
    assert extract_aweme_list("not json") == []
    assert extract_aweme_list({"other": 1}) == []

def test_parse_aweme_collaborators():
    item = {
        "aweme_id": "7300000000000000009",
        "desc": "",
        "preview_title": "合作作品",
        "author": {"nickname": "主作者", "uid": "1001"},
        "co_creator_list": [
            {"nickname": "合作作者A", "uid": "1002"},
            {"nickname": "合作作者B", "uid": "1003"},
        ],
        "video": {"play_addr": {"url_list": ["https://v.example/a.mp4"]}},
    }
    parsed = parse_aweme(item)
    assert parsed is not None
    assert parsed.author == "主作者"
    assert parsed.author_id == "1001"
    assert parsed.collaborators == ["合作作者A", "合作作者B"]
    assert parsed.collab_flag is True
    assert parsed.is_collab is True


def test_extract_title_fallbacks():
    assert extract_title({"desc": ""}, "123") == "无标题作品_123"
    assert extract_title({"preview_title": "预览标题"}, "123") == "预览标题"
    assert extract_title({"seo_info": {"ocr_content": "图片里的文字"}}, "123") == "图片里的文字"
    assert extract_title(
        {"text_extra": [{"hashtag_name": "话题"}, {"hashtag_name": "测试"}]}, "123"
    ) == "#话题 #测试"
    assert extract_title({"desc": "  正常标题  "}, "123") == "正常标题"


def test_parse_aweme_uses_fallback_title():
    item = {
        "aweme_id": "7300000000000000003",
        "desc": "",
        "preview_title": "备用标题",
        "author": {"nickname": "作者"},
        "video": {"play_addr": {"url_list": ["https://v.example/a.mp4"]}},
    }
    parsed = parse_aweme(item)
    assert parsed is not None
    assert parsed.title == "备用标题"

