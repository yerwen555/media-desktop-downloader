# -*- coding: utf-8 -*-
"""文件名清洗、时长解析等通用工具测试。"""

from __future__ import annotations

from media_crawler.utils import (
    format_bytes,
    parse_length_text,
    sanitize_name,
    strip_html,
    unique_path,
)


def test_sanitize_name_replaces_illegal_chars():
    assert sanitize_name('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_name_trims_and_limits():
    assert sanitize_name("   hello world   ") == "hello world"
    long_name = "长" * 120
    assert len(sanitize_name(long_name, max_len=40)) == 40
    assert sanitize_name("   ", default="未命名") == "未命名"
    assert sanitize_name("CON") == "未命名"


def test_strip_html():
    assert strip_html('<em class="keyword">测试</em>视频') == "测试视频"


def test_parse_length_text():
    assert parse_length_text("12:34") == 754_000
    assert parse_length_text("1:02:03") == 3_723_000
    assert parse_length_text("") == 0
    assert parse_length_text("abc") == 0


def test_unique_path(work_temp):
    first = unique_path(work_temp / "视频.mp4")
    assert first.name == "视频.mp4"
    first.write_bytes(b"x")
    second = unique_path(work_temp / "视频.mp4")
    assert second.name == "视频_1.mp4"
    second.write_bytes(b"x")
    third = unique_path(work_temp / "视频.mp4")
    assert third.name == "视频_2.mp4"


def test_format_bytes():
    assert format_bytes(0) == "0B"
    assert format_bytes(1536) == "1.5KB"
    assert format_bytes(3 * 1024 * 1024) == "3.0MB"
