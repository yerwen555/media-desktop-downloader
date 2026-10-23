# -*- coding: utf-8 -*-
"""程序入口：

    python app.py           # 打开图形界面（推荐）
    python app.py --cli     # 命令行交互模式
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _check_dependencies() -> list[str]:
    """检查核心第三方依赖，返回缺失的模块名列表。"""
    missing: list[str] = []
    try:
        import requests  # noqa: F401
    except ImportError:
        missing.append("requests")
    try:
        import DrissionPage  # noqa: F401
    except ImportError:
        missing.append("DrissionPage")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        missing.append("tkinter（Python 自带的图形库）")
    return missing


def _pause_if_interactive() -> None:
    """双击运行时让错误信息停留，避免窗口一闪而过。"""
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            input("按回车键退出…")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from media_crawler.config import AppConfig

    missing = _check_dependencies()
    if missing:
        print("缺少运行依赖：" + "、".join(missing))
        print("\n请在项目目录执行：\n  pip install -r requirements.txt\n")
        print("如果使用 PyCharm：请把项目解释器切换为已安装上述依赖的 Python（如 Anaconda 3.13）。")
        _pause_if_interactive()
        return 1

    cfg = AppConfig.load()
    cfg.ensure_dirs()

    if "--cli" in argv or "-c" in argv:
        from media_crawler.cli import run_cli

        return run_cli(cfg)

    from media_crawler.ui.app import run_gui

    return run_gui(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
