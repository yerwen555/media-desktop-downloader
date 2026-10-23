# -*- coding: utf-8 -*-
"""pytest 公共夹具。"""

from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def work_temp():
    """工作区内的临时目录（避免使用系统 temp，兼容受限环境）。"""
    from media_crawler.config import TMP_DIR

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    directory = TMP_DIR / f"pytest_{uuid.uuid4().hex[:10]}"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)
