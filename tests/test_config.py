# -*- coding: utf-8 -*-
"""配置保存/加载：保存目录相对路径（便于整个文件夹移动）。"""

from __future__ import annotations

import json
from pathlib import Path

from media_crawler.config import DOWNLOAD_DIR, PROJECT_ROOT, AppConfig


def test_default_download_dir_saved_relative(work_temp):
    cfg = AppConfig()
    config_path = work_temp / "config.json"
    cfg.save(config_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["download_dir"] == "downloads"

    loaded = AppConfig.load(config_path)
    assert Path(loaded.download_dir) == PROJECT_ROOT / "downloads"


def test_relative_value_in_config_is_resolved(work_temp):
    config_path = work_temp / "config.json"
    config_path.write_text(
        json.dumps({"download_dir": "我的下载", "max_workers": 3}, ensure_ascii=False),
        encoding="utf-8",
    )
    cfg = AppConfig.load(config_path)
    assert Path(cfg.download_dir) == PROJECT_ROOT / "我的下载"
    assert cfg.max_workers == 3


def test_external_download_dir_stays_absolute(work_temp):
    external = Path.home() / "外部下载目录"
    cfg = AppConfig()
    cfg.download_dir = str(external)
    config_path = work_temp / "config.json"
    cfg.save(config_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert Path(raw["download_dir"]).is_absolute()

    loaded = AppConfig.load(config_path)
    assert Path(loaded.download_dir) == external


def test_missing_download_dir_falls_back(work_temp):
    blocker = work_temp / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    cfg = AppConfig()
    cfg.download_dir = str(blocker / "download")

    cfg.ensure_dirs()

    assert Path(cfg.download_dir) == DOWNLOAD_DIR
    assert "保存目录不可用" in cfg.dir_warning


def test_quality_text_and_login_flag():
    from media_crawler.config import AppConfig

    cfg = AppConfig()
    assert cfg.bili_quality == 127  # 默认“最高可用”，实际取账号权限内最高流
    assert "最高可用" in cfg.quality_text()

    cfg.bili_quality = 120
    assert cfg.quality_text() == "4K"
    cfg.bili_quality = 80
    assert cfg.quality_text() == "1080P"
    cfg.bili_quality = 32
    assert cfg.quality_text() == "480P"

    cfg.bili_cookie = ""
    assert cfg.has_bili_login() is False
    cfg.bili_cookie = "buvid3=1; SESSDATA=deadbeef; bili_jct=x"
    assert cfg.has_bili_login() is True
    cfg.bili_cookie = "SESSDATA=; bili_jct=x"
    assert cfg.has_bili_login() is False
