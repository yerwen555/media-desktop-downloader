# -*- coding: utf-8 -*-
"""命令行选择表达式测试。"""

from __future__ import annotations

from media_crawler.cli import parse_selection


def test_parse_selection_empty_means_all():
    assert parse_selection("", 4) == [0, 1, 2, 3]
    assert parse_selection("a", 4) == [0, 1, 2, 3]
    assert parse_selection("*", 3) == [0, 1, 2]


def test_parse_selection_none():
    assert parse_selection("n", 4) == []
    assert parse_selection("none", 4) == []
    assert parse_selection("0", 4) == []


def test_parse_selection_ranges():
    assert parse_selection("1,3,5-8", 10) == [0, 2, 4, 5, 6, 7]
    assert parse_selection("5-3", 10) == [2, 3, 4]
    assert parse_selection("1，3", 5) == [0, 2]


def test_parse_selection_exclude():
    assert parse_selection("!3,5", 6) == [0, 1, 3, 5]
    assert parse_selection("!3,5-6", 8) == [0, 1, 3, 6, 7]
    assert parse_selection("!1", 3) == [1, 2]


def test_parse_selection_invalid_tokens():
    # 2 → 保留；abc/99/空区间忽略
    assert parse_selection("abc, 2, 99, -3", 4) == [1]
    assert parse_selection("!abc", 4) == [0, 1, 2, 3]
