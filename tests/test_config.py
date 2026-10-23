# -*- coding: utf-8 -*-
"""配置保存/加载：保存目录相对路径（便于整个文件夹移动）。"""

from __future__ import annotations

import json
from pathlib import Path

from media_crawler.config import PROJECT_ROOT, AppConfig


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
