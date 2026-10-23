# -*- coding: utf-8 -*-
"""DrissionPage 浏览器会话封装（登录态持久化、Cookie 导出）。"""

from __future__ import annotations

from pathlib import Path

from .config import BROWSER_PROFILE_DIR

try:  # 延迟导入，方便在没有浏览器的环境下跑单测
    from DrissionPage import ChromiumOptions, ChromiumPage
    from DrissionPage.errors import BrowserConnectError
except Exception:  # pragma: no cover - 依赖缺失时给出明确提示
    ChromiumOptions = None  # type: ignore[assignment]
    ChromiumPage = None  # type: ignore[assignment]
    BrowserConnectError = Exception  # type: ignore[assignment,misc]


class BrowserError(RuntimeError):
    pass


class BrowserSession:
    """with BrowserSession(...) as page: ... 自动打开/关闭 Chromium。"""

    def __init__(
        self,
        profile_dir: Path | str = BROWSER_PROFILE_DIR,
        headless: bool = False,
        load_timeout: float = 30,
    ):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.load_timeout = load_timeout
        self.page = None

    # ---------- 生命周期 ----------
    def __enter__(self):
        if ChromiumPage is None:
            raise BrowserError(
                "未安装 DrissionPage，请先执行: pip install -r requirements.txt"
            )
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        co = ChromiumOptions()
        co.set_user_data_path(str(self.profile_dir))
        co.set_local_port(9222)
        co.set_argument("--disable-blink-features", "AutomationControlled")
        co.set_argument("--no-first-run")
        co.set_argument("--no-default-browser-check")
        co.set_argument("--lang", "zh-CN")
        co.mute(True)
        if self.headless:
            co.headless(True)
        try:
            self.page = ChromiumPage(co)
        except BrowserConnectError as exc:  # 最常见：Chrome 被占用 / 未安装
            raise BrowserError(
                "启动 Chrome 失败。请检查：\n"
                "1. 已安装 Google Chrome 浏览器；\n"
                "2. 先关闭由本工具打开过、可能仍残留的 Chrome 窗口；\n"
                "3. 若 Chrome 正常但连不上，重启电脑后重试。\n"
                f"原始错误: {exc}"
            ) from exc
        except Exception as exc:  # pragma: no cover - 不同环境报错各异
            raise BrowserError(f"启动 Chrome 失败: {exc}") from exc

        try:
            self.page.set.timeouts(base=self.load_timeout)
        except Exception:
            pass
        return self.page

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self.page is None:
            return
        page, self.page = self.page, None
        try:
            page.quit()
        except Exception:
            pass

    # ---------- Cookie ----------
    def cookies_header(self, all_domains: bool = True) -> str:
        """把浏览器当前登录 Cookie 导出成请求头字符串，供 CDN 下载复用。"""
        if self.page is None:
            return ""
        try:
            cookies = self.page.cookies(all_domains=all_domains)
            return cookies.as_str()
        except Exception:
            return ""
