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


def test_stream_urls_backup():
    stream = {
        "baseUrl": "a",
        "backupUrl": ["b", "c", ""],
    }
    assert bilibili._stream_urls(stream) == ["a", "b", "c"]
