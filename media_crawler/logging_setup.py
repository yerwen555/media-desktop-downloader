# -*- coding: utf-8 -*-
"""日志：控制台 + 滚动文件 + 可注入界面的回调 Handler。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"
DATE_FORMAT = "%H:%M:%S"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3


class CallbackHandler(logging.Handler):
    """把日志转给界面（通过线程安全队列由主线程轮询）。"""

    def __init__(self, callback: Callable[[str], None], level: int = logging.INFO):
        super().__init__(level)
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(self.format(record))
        except Exception:  # pragma: no cover - 日志本身不能反噬业务
            self.handleError(record)


def setup_logging(log_dir: Path | str, level: int = logging.INFO) -> logging.Logger:
    """幂等地初始化根日志（文件 + 控制台）。"""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("media_crawler")
    logger.setLevel(level)
    logger.propagate = False

    if getattr(logger, "_configured", False):
        return logger

    log_file = log_dir / "app.log"
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 命令行/窗口模式：控制台可能被 pythonw 置空，故用 try 保护
    try:
        if sys.stderr is not None:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(formatter)
            logger.addHandler(stream)
    except Exception:  # pragma: no cover
        pass

    logger._configured = True  # type: ignore[attr-defined]
    return logger


def add_ui_handler(logger: logging.Logger, callback: Callable[[str], None]) -> CallbackHandler:
    handler = CallbackHandler(callback)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return handler


def remove_handler(logger: logging.Logger, handler: logging.Handler | None) -> None:
    if handler is None:
        return
    logger.removeHandler(handler)
    handler.close()
