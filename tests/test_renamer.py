# -*- coding: utf-8 -*-
"""重命名已下载文件测试。"""

from __future__ import annotations

from pathlib import Path

from media_crawler.models import MediaItem
from media_crawler.renamer import rename_downloaded_files
from media_crawler.storage import DownloadStore


def _store(work_temp) -> DownloadStore:
    return DownloadStore(work_temp / "media.db")


def test_rename_douyin_video(work_temp):
    store = _store(work_temp)
    folder = work_temp / "downloads" / "抖音" / "作者" / "视频"
    folder.mkdir(parents=True)
    old = folder / "2023-11-15_旧标题_03913871.mp4"
    old.write_bytes(b"video")
    item = MediaItem(
        "douyin",
        "douyin:7699475016803913871",
        "video",
        "旧标题",
        "作者",
        1700000000,
        60000,
        payload={"aweme_id": "7699475016803913871"},
    )
    store.mark(item, [str(old)], 5)

    ok, new_files, error = rename_downloaded_files(item, "新标题/含非法:字符", store)
    assert ok, error
    new_path = Path(new_files[0])
    assert new_path.name == "2023-11-15_新标题_含非法_字符_03913871.mp4"
    assert new_path.exists() and not old.exists()
    record = store.get(item.uid)
    assert record is not None
    assert record["title"] == "新标题/含非法:字符"
    assert record["files"] == [str(new_path)]
    assert record["size"] == 5
    assert store.custom_titles()[item.uid] == "新标题/含非法:字符"


def test_rename_douyin_album(work_temp):
    store = _store(work_temp)
    album = work_temp / "downloads" / "抖音" / "作者" / "图集" / "2023-11-15_旧标题_03913871"
    album.mkdir(parents=True)
    files = [album / "01.jpg", album / "02.jpg"]
    for f in files:
        f.write_bytes(b"img")
    item = MediaItem(
        "douyin",
        "douyin:7699475016803913871",
        "image",
        "旧标题",
        "作者",
        1700000000,
        image_count=2,
        payload={"aweme_id": "7699475016803913871"},
    )
    store.mark(item, [str(f) for f in files], 6)

    ok, new_files, error = rename_downloaded_files(item, "新图集", store)
    assert ok, error
    new_paths = [Path(p) for p in new_files]
    assert {p.name for p in new_paths} == {"01.jpg", "02.jpg"}
    assert all(p.exists() for p in new_paths)
    assert new_paths[0].parent.name == "2023-11-15_新图集_03913871"
    assert not album.exists()
    record = store.get(item.uid)
    assert record is not None
    assert sorted(record["files"]) == sorted(str(p) for p in new_paths)


def test_rename_bilibili_multip(work_temp):
    store = _store(work_temp)
    folder = work_temp / "downloads" / "B站" / "UP主"
    folder.mkdir(parents=True)
    files = [
        folder / "2023-11-15_旧标题_BV1xx411c7mD_P1.mp4",
        folder / "2023-11-15_旧标题_BV1xx411c7mD_P2.mp4",
    ]
    for f in files:
        f.write_bytes(b"v")
    item = MediaItem(
        "bilibili",
        "bilibili:BV1xx411c7mD",
        "video",
        "旧标题",
        "UP主",
        1700000000,
        60000,
        payload={"bvid": "BV1xx411c7mD", "pages": []},
    )
    store.mark(item, [str(f) for f in files], 2)

    ok, new_files, error = rename_downloaded_files(item, "新的合集", store)
    assert ok, error
    names = sorted(Path(p).name for p in new_files)
    assert names == [
        "2023-11-15_新的合集_BV1xx411c7mD_P1.mp4",
        "2023-11-15_新的合集_BV1xx411c7mD_P2.mp4",
    ]
    assert all(Path(p).exists() for p in new_files)
    record = store.get(item.uid)
    assert record is not None
    assert record["title"] == "新的合集"


def test_rename_missing_file(work_temp):
    store = _store(work_temp)
    item = MediaItem(
        "douyin",
        "douyin:1",
        "video",
        "旧",
        "作者",
        1700000000,
        payload={"aweme_id": "1"},
    )
    store.mark(item, [str(work_temp / "missing.mp4")], 1)
    ok, new_files, error = rename_downloaded_files(item, "新", store)
    assert not ok
    assert "不存在" in error
    assert new_files == []


def test_rename_without_record(work_temp):
    store = _store(work_temp)
    item = MediaItem(
        "douyin", "douyin:2", "video", "旧", "作者", 1700000000, payload={"aweme_id": "2"}
    )
    ok, new_files, error = rename_downloaded_files(item, "新", store)
    assert not ok
    assert "下载记录" in error
