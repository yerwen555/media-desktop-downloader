# -*- coding: utf-8 -*-
"""SQLite 下载记录测试。"""

from __future__ import annotations

from media_crawler.models import MediaItem
from media_crawler.storage import DownloadStore


def _item(uid: str = "douyin:1") -> MediaItem:
    return MediaItem(
        platform="douyin",
        uid=uid,
        kind="video",
        title="测试视频",
        author="作者",
    )


def test_mark_and_get(work_temp):
    store = DownloadStore(work_temp / "media.db")
    item = _item()
    video_file = work_temp / "video.mp4"
    video_file.write_bytes(b"12345")
    store.mark(item, [str(video_file)], size=5)

    record = store.get(item.uid)
    assert record is not None
    assert record["title"] == "测试视频"
    assert record["files"] == [str(video_file)]
    assert store.is_downloaded(item.uid) is True
    downloaded = store.downloaded_map()
    assert downloaded == {item.uid: str(video_file)}


def test_stale_record_cleanup(work_temp):
    store = DownloadStore(work_temp / "media.db")
    item = _item("douyin:2")
    missing = work_temp / "missing.mp4"
    store.mark(item, [str(missing)], size=0)
    assert store.downloaded_map(check_files=True) == {}
    assert store.get(item.uid) is None


def test_stats_and_clear(work_temp):
    store = DownloadStore(work_temp / "media.db")
    video_file = work_temp / "v.mp4"
    video_file.write_bytes(b"1")
    store.mark(_item("douyin:3"), [str(video_file)], size=1)
    image_item = MediaItem(
        platform="douyin", uid="douyin:4", kind="image", title="图集", image_count=2
    )
    image_file = work_temp / "i.jpg"
    image_file.write_bytes(b"1")
    store.mark(image_item, [str(image_file)], size=1)

    stats = store.stats()
    assert stats["total"] == 2
    assert stats["video"] == 1
    assert stats["image"] == 1
    assert stats["size"] == 2

    assert store.clear() == 2
    assert store.stats()["total"] == 0


def test_same_uid_overwrites(work_temp):
    store = DownloadStore(work_temp / "media.db")
    item = _item("douyin:5")
    first = work_temp / "a.mp4"
    first.write_bytes(b"1")
    store.mark(item, [str(first)], size=1)
    second = work_temp / "b.mp4"
    second.write_bytes(b"22")
    store.mark(item, [str(second)], size=2)
    record = store.get(item.uid)
    assert record is not None
    assert record["files"] == [str(second)]
    assert store.stats()["total"] == 1


def test_update_record(work_temp):
    store = DownloadStore(work_temp / "media.db")
    item = _item("douyin:6")
    old_file = work_temp / "old.mp4"
    old_file.write_bytes(b"123")
    store.mark(item, [str(old_file)], size=3)

    new_file = work_temp / "new.mp4"
    new_file.write_bytes(b"12345")
    assert store.update_record(item.uid, title="新标题", files=[str(new_file)]) is True

    record = store.get(item.uid)
    assert record is not None
    assert record["title"] == "新标题"
    assert record["files"] == [str(new_file)]
    assert record["size"] == 5
    assert store.update_record("not-exist", title="x") is False


def test_custom_titles(work_temp):
    store = DownloadStore(work_temp / "media.db")
    store.set_custom_title("douyin:1", "我的名字")
    store.set_custom_title("bilibili:BV1xx411c7mD", "B站名字")
    assert store.custom_titles() == {
        "douyin:1": "我的名字",
        "bilibili:BV1xx411c7mD": "B站名字",
    }
    # 更新同名记录
    store.set_custom_title("douyin:1", "换一个名字")
    assert store.custom_titles()["douyin:1"] == "换一个名字"
    # 清空下载记录时一并清空自定义标题
    store.clear()
    assert store.custom_titles() == {}
