# -*- coding: utf-8 -*-
"""B 站解析、WBI 签名、取流选择测试。"""

from __future__ import annotations

import json
import types

import media_crawler.bilibili as bilibili
from media_crawler.bilibili import (
    BiliClient,
    _parse_arc_response,
    enc_wbi,
    extract_video_refs,
    parse_space_input,
    parse_space_item,
)

VIEW_MULTI_P = {
    "bvid": "BV1xx411c7mD",
    "aid": 123,
    "cid": 111,
    "title": "测试视频",
    "pubdate": 1700000000,
    "duration": 125,
    "pic": "//i0.hdslb.com/x.jpg",
    "owner": {"name": "UP主"},
    "stat": {"view": 10000},
    "pages": [
        {"cid": 111, "page": 1, "part": "P1", "duration": 60},
        {"cid": 222, "page": 2, "part": "P2", "duration": 65},
    ],
}


def test_extract_video_refs():
    text = (
        "看看 https://www.bilibili.com/video/BV1GJ411x7h7/?p=1 和 "
        "https://b23.tv/abc 还有 av170001，重复 BV1GJ411x7h7"
    )
    assert extract_video_refs(text) == ["BV1GJ411x7h7", "av170001"]
    assert extract_video_refs("没有链接") == []
    assert extract_video_refs("看av170001") == ["av170001"]


def test_parse_space_input():
    assert parse_space_input("https://space.bilibili.com/2/video") == 2
    assert parse_space_input("546195") == 546195
    assert parse_space_input("https://space.bilibili.com/2?mid=3") == 2
    assert parse_space_input("https://www.bilibili.com/video/BV1xx411c7mD") is None
    assert parse_space_input("abc") is None


def test_view_to_item_multi_p():
    item = BiliClient._view_to_item(VIEW_MULTI_P)
    assert item is not None
    assert item.uid == "bilibili:BV1xx411c7mD"
    assert item.title == "测试视频（2P）"
    assert item.author == "UP主"
    assert item.duration_ms == 125_000
    assert item.views == 10000
    assert item.cover_url == "https://i0.hdslb.com/x.jpg"
    assert item.page_url == "https://www.bilibili.com/video/BV1xx411c7mD?p=1"
    assert len(item.payload["pages"]) == 2
    assert item.payload["pages"][1]["cid"] == 222


def test_view_to_item_single_p():
    single = dict(VIEW_MULTI_P)
    single["pages"] = [{"cid": 111, "page": 1, "part": "", "duration": 60}]
    item = BiliClient._view_to_item(single)
    assert item is not None
    assert item.title == "测试视频"
    assert "?p=" not in item.page_url


def test_view_to_item_union_staff():
    view = dict(VIEW_MULTI_P)
    view.update(
        {
            "bvid": "BV1Sh411N7pH",
            "title": "联合投稿视频",
            "owner": {"mid": 10001, "name": "主UP"},
            "staff": [
                {"mid": 10001, "name": "主UP", "title": "UP主"},
                {"mid": 2, "name": "合作UP", "title": "参演"},
            ],
            "pages": [{"cid": 1, "page": 1, "part": "P1", "duration": 60}],
        }
    )
    item = BiliClient._view_to_item(view)
    assert item is not None
    assert item.author == "主UP"
    assert item.author_id == "10001"
    assert item.collaborators == ["合作UP（参演）"]
    assert item.collab_flag is True
    assert item.is_collab is True


def test_view_to_items_expands_multi_p():
    items = BiliClient._view_to_items(VIEW_MULTI_P)
    assert [item.uid for item in items] == [
        "bilibili:BV1xx411c7mD:111",
        "bilibili:BV1xx411c7mD:222",
    ]
    first, second = items
    assert first.title == "P1 P1 - 测试视频"
    assert first.page_index == 1
    assert first.page_count == 2
    assert first.page_label == "P1/2"
    assert first.duration_ms == 60_000
    assert first.page_url == "https://www.bilibili.com/video/BV1xx411c7mD?p=1"
    assert first.payload["pages"] == [{"cid": 111, "page": 1, "part": "P1", "duration": 60}]
    assert second.page_label == "P2/2"
    assert second.display_kind == "视频·P2/2"
    assert second.page_url.endswith("?p=2")


def test_view_to_items_without_expand_keeps_aggregate():
    items = BiliClient._view_to_items(VIEW_MULTI_P, expand_pages=False)
    assert len(items) == 1
    assert items[0].uid == "bilibili:BV1xx411c7mD"
    assert items[0].title == "测试视频（2P）"
    assert items[0].page_count == 2
    assert items[0].page_label == ""


def test_expand_item_uses_view_info(monkeypatch):
    aggregate = BiliClient._view_to_items(VIEW_MULTI_P, expand_pages=False)[0]
    monkeypatch.setattr(
        BiliClient, "view_info", lambda self, bvid=None, aid=None: VIEW_MULTI_P
    )
    client = BiliClient.__new__(BiliClient)
    pages = client.expand_item(aggregate)
    assert len(pages) == 2
    assert all(item.page_index for item in pages)


def test_build_page_filename_keeps_suffix():
    page_item = BiliClient._view_to_items(VIEW_MULTI_P)[1]
    name = bilibili.build_page_filename(page_item, {"page": 2, "cid": 222}, 94)
    assert "BV1xx411c7mD" in name
    assert "测试视频" in name
    assert "P1 P1 -" not in name
    assert "（94P）" in name  # 与旧版整段下载的文件名一致，升级后能识别已下载
    assert name.endswith("_P2.mp4")
    single = bilibili.build_page_filename(page_item, {"page": 1, "cid": 111}, 1)
    assert "_P1" not in single and single.endswith(".mp4")


def test_parse_space_item():
    raw = {
        "bvid": "BV1yy411c7mD",
        "aid": 456,
        "title": '<em class="keyword">测试</em>视频',
        "author": "空间UP",
        "created": 1700000000,
        "length": "12:34",
        "play": 5000,
        "pic": "//i0.hdslb.com/y.jpg",
    }
    item = parse_space_item(raw)
    assert item is not None
    assert item.uid == "bilibili:BV1yy411c7mD"
    assert item.title == "测试视频"
    assert item.duration_ms == 754_000
    assert item.views == 5000
    assert item.author == "空间UP"
    assert item.cover_url == "https://i0.hdslb.com/y.jpg"
    union_raw = dict(raw)
    union_raw.update({"mid": 10001, "is_union_video": 1})
    union_item = parse_space_item(union_raw)
    assert union_item is not None
    assert union_item.author_id == "10001"
    assert union_item.collab_flag is True
    assert parse_space_item({}) is None


def test_parse_arc_response():
    body = {
        "code": 0,
        "data": {"list": {"vlist": [{"bvid": "BV1"}]}},
    }
    vlist, code = _parse_arc_response(body)
    assert vlist == [{"bvid": "BV1"}] and code == 0
    vlist, code = _parse_arc_response(json.dumps(body).encode())
    assert vlist == [{"bvid": "BV1"}]
    assert _parse_arc_response({"code": -352, "message": "风控"}) == ([], -352)
    assert _parse_arc_response(None) == ([], None)
    assert _parse_arc_response("bad json") == ([], None)


def test_enc_wbi_known_vector(monkeypatch):
    fake_time = types.SimpleNamespace(time=lambda: 1700000000)
    monkeypatch.setattr(bilibili, "time", fake_time)
    params = enc_wbi(
        {"bvid": "BV1GJ411x7h7", "cid": 137649199, "qn": 80},
        "7cd084941338484aae1ad9425b84077c",
        "4932caff0ff746eab6f01bf08b70ac45",
    )
    assert params["wts"] == "1700000000"
    assert params["bvid"] == "BV1GJ411x7h7"
    assert params["cid"] == "137649199"
    assert params["w_rid"] == "fe48a3eb4a831ee2ca6b50bfeb484fd2"


def test_enc_wbi_filters_special_chars(monkeypatch):
    fake_time = types.SimpleNamespace(time=lambda: 1700000000)
    monkeypatch.setattr(bilibili, "time", fake_time)
    params = enc_wbi({"keyword": "a!'()*b"}, "k" * 32, "s" * 32)
    assert params["keyword"] == "ab"
    assert len(params["w_rid"]) == 32


def test_pick_dash_streams():
    dash = {
        "video": [
            {"id": 16, "bandwidth": 100, "baseUrl": "u16"},
            {"id": 32, "bandwidth": 200, "baseUrl": "u32"},
            {"id": 64, "bandwidth": 300, "baseUrl": "u64"},
        ],
        "audio": [
            {"id": 30216, "bandwidth": 10, "baseUrl": "a1"},
            {"id": 30280, "bandwidth": 20, "base_url": "a2"},
        ],
    }
    stream = BiliClient._pick_dash_streams(dash, qn=32)
    assert stream is not None
    assert stream["video"] == ["u32"]
    assert stream["audio"] == ["a2"]
    assert stream["quality"].startswith("480P")

    high = BiliClient._pick_dash_streams(dash, qn=80)
    assert high is not None and high["video"] == ["u64"]

    assert BiliClient._pick_dash_streams({"video": []}, 80) is None


DASH_MULTI = {
    "video": [
        {"id": 120, "width": 3840, "height": 2160, "frameRate": "60", "codecid": 13,
         "bandwidth": 9_000_000, "baseUrl": "u4k_av1"},
        {"id": 120, "width": 3840, "height": 2160, "frameRate": "60", "codecid": 7,
         "bandwidth": 20_000_000, "baseUrl": "u4k_avc"},
        {"id": 116, "width": 1920, "height": 1080, "frameRate": "60", "codecid": 7,
         "bandwidth": 8_000_000, "baseUrl": "u1080p60"},
        {"id": 80, "width": 1920, "height": 1080, "frameRate": "30", "codecid": 7,
         "bandwidth": 3_000_000, "baseUrl": "u1080"},
        {"id": 32, "width": 854, "height": 480, "frameRate": "30", "codecid": 7,
         "bandwidth": 400_000, "baseUrl": "u480"},
    ],
    "audio": [{"id": 30280, "bandwidth": 320_000, "baseUrl": "a320"}],
}


def test_pick_dash_streams_respects_quality_cap():
    # 127 = 最高可用：4K，同清晰度优先 AVC（不黑屏）
    top = BiliClient._pick_dash_streams(DASH_MULTI, 127)
    assert top is not None
    assert top["video"] == ["u4k_avc"]
    assert top["quality"] == "4K（3840×2160@60fps）"
    assert top["audio"] == ["a320"]

    # 上限 116：只能到 1080P60
    p60 = BiliClient._pick_dash_streams(DASH_MULTI, 116)
    assert p60 is not None and p60["video"] == ["u1080p60"]

    # 上限 80：1080P30
    p30 = BiliClient._pick_dash_streams(DASH_MULTI, 80)
    assert p30 is not None and p30["video"] == ["u1080"]

    # 上限 32：480P（未登录的真实情况）
    low = BiliClient._pick_dash_streams(DASH_MULTI, 32)
    assert low is not None and low["video"] == ["u480"]


def test_playurl_asks_for_full_dash_features(monkeypatch):
    client = BiliClient.__new__(BiliClient)
    seen = {}

    def fake_get_json(self, url, params=None, **kwargs):
        seen["params"] = dict(params or {})
        seen["wbi"] = bool(kwargs.get("wbi"))
        return {"code": 0, "data": {"dash": DASH_MULTI, "quality": 120}}

    monkeypatch.setattr(BiliClient, "_get_json", fake_get_json)
    stream = client.playurl("BV1xx411c7mD", 111, 127)
    # fnval=4048 才会返回 4K / 60fps / HDR / AV1 等全部特性
    assert seen["params"]["fnval"] == 4048
    assert seen["params"]["qn"] == 127
    assert seen["params"]["fourk"] == 1
    assert seen["wbi"] is True
    assert stream["video"] == ["u4k_avc"]
    assert stream["id"] == 120


def test_has_login_cookie():
    assert bilibili.has_login_cookie("SESSDATA=abc123; bili_jct=x") is True
    assert bilibili.has_login_cookie("SESSDATA=; bili_jct=x") is False
    assert bilibili.has_login_cookie("") is False
    assert bilibili.has_login_cookie("buvid3=abc") is False


def test_stream_urls_backup():
    stream = {
        "baseUrl": "a",
        "backupUrl": ["b", "c", ""],
    }
    assert bilibili._stream_urls(stream) == ["a", "b", "c"]
