# -*- coding: utf-8 -*-
"""tkinter 主界面：抓取列表 → 勾选 → 下载。"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from ..bilibili import (
    BiliClient,
    extract_staff,
    extract_video_refs,
    fetch_login_cookie,
    has_login_cookie,
    parse_space_input,
)
from ..config import (
    APP_TITLE,
    APP_VERSION,
    DATA_DIR,
    DB_PATH,
    LOGS_DIR,
    PROJECT_ROOT,
    AppConfig,
    config_summary,
)
from ..douyin import DouyinCrawler
from ..downloader import DownloadManager
from ..logging_setup import add_ui_handler, remove_handler, setup_logging
from ..models import CrawlResult, DownloadSummary, ItemOutcome, MediaItem, format_size
from ..storage import DownloadStore
from .settings import SettingsDialog
from .table import ResultsTable

_FONT_FAMILY = "Microsoft YaHei UI"


class PlatformPanel(ttk.Frame):
    """一个平台标签页：输入框 + 结果列表 + 操作按钮。"""

    def __init__(self, master, app: "MainWindow", platform: str):
        super().__init__(master, padding=8)
        self.app = app
        self.platform = platform
        self._build()

    # ---------------- 构建 ----------------
    def _build(self) -> None:
        if self.platform == "douyin":
            label_text = "抖音用户主页 / 作品链接："
            hint = (
                "支持：用户主页链接（douyin.com/user/…）、单个作品链接。"
                "首次使用请保持“显示浏览器窗口”，在弹出的浏览器里扫码登录（登录态会保存）。"
            )
            kind_choices = ("全部", "视频", "图集")
        else:
            label_text = "B站视频链接 / BV 号 / UP 主空间："
            hint = (
                "支持：视频链接或 BV/av 号（可多行粘贴），或 UP 主空间链接"
                "（space.bilibili.com/数字）、纯数字 UID。多 P 视频会按集列出，可直接勾选要下的分集。"
            )
            kind_choices = ("全部", "视频")

        source_frame = ttk.Frame(self)
        source_frame.pack(fill="x", pady=(0, 4))
        ttk.Label(source_frame, text=label_text).pack(anchor="w")
        input_row = ttk.Frame(source_frame)
        input_row.pack(fill="x", pady=(2, 0))
        self.source_text = tk.Text(input_row, height=3, wrap="word", undo=True)
        self.source_text.pack(side="left", fill="x", expand=True)
        source_scroll = ttk.Scrollbar(input_row, orient="vertical", command=self.source_text.yview)
        source_scroll.pack(side="left", fill="y")
        self.source_text.configure(yscrollcommand=source_scroll.set)

        button_col = ttk.Frame(input_row)
        button_col.pack(side="left", fill="y", padx=(8, 0))
        self.crawl_btn = ttk.Button(
            button_col,
            text="获取列表",
            width=12,
            command=lambda: self.app.start_crawl(self),
        )
        self.crawl_btn.pack(fill="x")
        self.stop_btn = ttk.Button(
            button_col,
            text="停止",
            width=12,
            state="disabled",
            command=self.app.stop_task,
        )
        self.stop_btn.pack(fill="x", pady=(6, 0))
        self.login_btn: ttk.Button | None = None
        if self.platform == "bilibili":
            self.login_btn = ttk.Button(
                button_col,
                text="登录B站",
                width=12,
                command=lambda: self.app.bili_login(self),
            )
            self.login_btn.pack(fill="x", pady=(6, 0))

        ttk.Label(source_frame, text=hint, foreground="#777777", wraplength=1000).pack(
            anchor="w", pady=(2, 0)
        )

        self.table = ResultsTable(
            self,
            kind_choices=kind_choices,
            on_change=self._on_selection_change,
            on_open_url=self.app.open_url,
            on_rename=lambda uid: self.app.rename_item(self, uid),
            on_details=lambda uid: self.app.show_item_details(self, uid),
            on_expand=lambda uid: self.app.expand_pages(self, [uid]),
        )
        self.table.pack(fill="both", expand=True, pady=(4, 0))

        action_row = ttk.Frame(self)
        action_row.pack(fill="x", pady=(6, 0))
        self.download_btn = ttk.Button(
            action_row,
            text="下载选中（0）",
            command=lambda: self.app.start_download(self),
            state="disabled",
        )
        self.download_btn.pack(side="left")
        self.expand_btn: ttk.Button | None = None
        if self.platform == "bilibili":
            self.expand_btn = ttk.Button(
                action_row,
                text="展开选中为分P",
                command=lambda: self.app.expand_pages(self),
            )
            self.expand_btn.pack(side="left", padx=(8, 0))
        ttk.Button(action_row, text="打开下载目录", command=self.app.open_download_dir).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(action_row, text="清空列表", command=self.clear).pack(side="left", padx=(8, 0))
        ttk.Label(
            action_row,
            text="提示：默认全选，取消勾选不想要即可；多 P 视频可右键“展开为分P”后按集勾选",
            foreground="#777777",
        ).pack(side="right")

    # ---------------- 数据 ----------------
    def get_source_text(self) -> str:
        return self.source_text.get("1.0", "end-1c").strip()

    def clear(self) -> None:
        self.table.clear()
        self.app.cookies.pop(self.platform, None)
        self.update_download_button()

    def _on_selection_change(self) -> None:
        self.update_download_button()

    def update_download_button(self) -> None:
        count = self.table.selected_count()
        busy = self.app.busy
        state = "normal" if (count > 0 and not busy) else "disabled"
        self.download_btn.config(text=f"下载选中（{count}）", state=state)

    def set_busy(self, busy: bool) -> None:
        self.crawl_btn.config(state="disabled" if busy else "normal")
        self.stop_btn.config(state="normal" if busy else "disabled")
        if self.expand_btn is not None:
            self.expand_btn.config(state="disabled" if busy else "normal")
        if self.login_btn is not None:
            self.login_btn.config(state="disabled" if busy else "normal")
        try:
            self.source_text.configure(state="disabled" if busy else "normal")
        except tk.TclError:
            pass
        self.update_download_button()


class MainWindow:
    def __init__(self, root: tk.Tk, cfg: AppConfig):
        self.root = root
        self.cfg = cfg
        self.busy = False
        self.cancel = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.cookies: dict[str, str] = {}
        self.dl_fractions: dict[str, float] = {}
        self.dl_total = 0
        self._poll_job: str | None = None

        warning_before = getattr(self.cfg, "dir_warning", "")
        self.cfg.ensure_dirs()
        self.dir_warning = warning_before or getattr(self.cfg, "dir_warning", "")
        self.store = DownloadStore(DB_PATH)
        self.log = setup_logging(LOGS_DIR)
        self.log_handler = add_ui_handler(self.log, lambda msg: self._emit("log", msg))

        self._build_style()
        self._build_window()
        self._build_menu()
        self._refresh_stats()

        self.log.info("%s v%s 启动", APP_TITLE, APP_VERSION)
        self.log.info("%s", config_summary(self.cfg))
        if self.dir_warning:
            self.log.warning("%s", self.dir_warning)
            self._append_log("警告：" + self.dir_warning)
            self._set_status("保存目录不可用，本次已改用项目内 downloads")
        self._append_log(
            "提示：抖音首次使用需要在弹出的浏览器中扫码登录；B 站空间抓取同样会打开浏览器。"
        )
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_job = self.root.after(120, self._poll_events)

    # ---------------- 界面 ----------------
    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            style.theme_use("clam")

        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=_FONT_FAMILY, size=10)
            except tk.TclError:
                pass
        style.configure(".", font=(_FONT_FAMILY, 10))
        style.configure("Treeview", rowheight=28, font=(_FONT_FAMILY, 10))
        style.configure("Treeview.Heading", font=(_FONT_FAMILY, 10, "bold"))
        style.configure("Title.TLabel", font=(_FONT_FAMILY, 16, "bold"))
        style.configure("Subtitle.TLabel", foreground="#666666")
        style.configure("Accent.TButton", font=(_FONT_FAMILY, 10, "bold"))

    def _build_window(self) -> None:
        self.root.title(f"{APP_TITLE} v{APP_VERSION}")
        width, height = 1240, 900
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(1000, 720)
        try:
            self.root.option_add("*Text.Font", (_FONT_FAMILY, 10))
        except tk.TclError:
            pass

        header = ttk.Frame(self.root, padding=(14, 10, 14, 4))
        header.pack(fill="x")
        title_box = ttk.Frame(header)
        title_box.pack(side="left")
        ttk.Label(title_box, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_box,
            text="抓取列表 → 勾选想下载的作品 → 一键下载；已下载会自动去重",
            style="Subtitle.TLabel",
        ).pack(anchor="w")
        self.db_label = ttk.Label(header, text="历史记录：0 个", style="Subtitle.TLabel")
        self.db_label.pack(side="right", anchor="e")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(4, 6))
        self.douyin_panel = PlatformPanel(self.notebook, self, "douyin")
        self.bili_panel = PlatformPanel(self.notebook, self, "bilibili")
        self.notebook.add(self.douyin_panel, text="  抖音  ")
        self.notebook.add(self.bili_panel, text="  B站  ")
        self.panels = {"douyin": self.douyin_panel, "bilibili": self.bili_panel}

        status_frame = ttk.Frame(self.root, padding=(14, 0, 14, 2))
        status_frame.pack(fill="x")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(status_frame, textvariable=self.status_var, width=46).pack(side="left")
        self.progressbar = ttk.Progressbar(status_frame, mode="determinate", maximum=100)
        self.progressbar.pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Button(status_frame, text="打开下载目录", command=self.open_download_dir).pack(
            side="right"
        )

        log_frame = ttk.LabelFrame(self.root, text="运行日志", padding=4)
        log_frame.pack(fill="x", padx=12, pady=(2, 8))
        self.log_text = tk.Text(log_frame, height=5, wrap="word", state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.pack(side="left", fill="y")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.tag_configure("warn", foreground="#B9770E")
        self.log_text.tag_configure("error", foreground="#B03A2E")
        log_buttons = ttk.Frame(log_frame)
        log_buttons.pack(side="right", fill="y", padx=(6, 0))
        ttk.Button(log_buttons, text="打开日志文件", command=self.open_log_file).pack(fill="x")
        ttk.Button(log_buttons, text="清空显示", command=self._clear_log).pack(fill="x", pady=(6, 0))

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开下载目录", command=self.open_download_dir)
        file_menu.add_command(label="打开数据目录", command=lambda: self._open_path(DATA_DIR))
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="参数设置…", command=self.open_settings)
        tools_menu.add_command(label="打开日志文件", command=self.open_log_file)
        menubar.add_cascade(label="设置", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="使用说明", command=self.show_help)
        help_menu.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.config(menu=menubar)

    # ---------------- 日志/状态 ----------------
    def _append_log(self, message: str) -> None:
        try:
            self.log_text.configure(state="normal")
            tag = ()
            if "| ERROR" in message:
                tag = ("error",)
            elif "| WARNING" in message:
                tag = ("warn",)
            self.log_text.insert("end", message + "\n", tag)
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > 1500:
                self.log_text.delete("1.0", "500.0")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass

    def _clear_log(self) -> None:
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _set_progress(self, value: float | None) -> None:
        try:
            if value is None:
                self.progressbar.stop()
                self.progressbar.configure(mode="indeterminate")
                self.progressbar.start(12)
            else:
                self.progressbar.stop()
                self.progressbar.configure(mode="determinate")
                self.progressbar["value"] = max(0.0, min(100.0, value))
        except tk.TclError:
            pass

    def _refresh_stats(self) -> None:
        stats = self.store.stats()
        self.db_label.config(
            text=f"历史下载：{stats['total']} 个作品 / {format_size(stats['size'])}"
        )

    # ---------------- 事件循环 ----------------
    def _emit(self, kind: str, *payload) -> None:
        self.events.put((kind, payload))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._dispatch(kind, payload)
        except queue.Empty:
            pass
        try:
            self._poll_job = self.root.after(120, self._poll_events)
        except tk.TclError:
            self._poll_job = None

    def _dispatch(self, kind: str, payload: tuple) -> None:
        if kind == "log":
            self._append_log(payload[0])
        elif kind == "status":
            self._set_status(payload[0])
        elif kind == "crawl_done":
            self._on_crawl_done(*payload)
        elif kind == "crawl_error":
            self._on_crawl_error(*payload)
        elif kind == "dl_start":
            self._on_download_start(*payload)
        elif kind == "dl_progress":
            self._on_download_progress(*payload)
        elif kind == "dl_done":
            self._on_item_done(*payload)
        elif kind == "dl_finish":
            self._on_download_finish(*payload)
        elif kind == "expand_done":
            self._on_expand_done(*payload)
        elif kind == "bili_login_done":
            self._on_bili_login_done(*payload)

    # ---------------- 抓取 ----------------
    def start_crawl(self, panel: PlatformPanel) -> None:
        if self.busy:
            return
        text = panel.get_source_text()
        if not text:
            messagebox.showinfo("提示", "请先粘贴链接，再点击“获取列表”。")
            return
        self.busy = True
        self.cancel = threading.Event()
        self._set_busy(True)
        panel.table.clear()
        panel.update_download_button()
        self._set_progress(None)
        self._set_status("正在抓取列表…")
        thread = threading.Thread(target=self._crawl_worker, args=(panel, text), daemon=True)
        thread.start()

    def _crawl_worker(self, panel: PlatformPanel, text: str) -> None:
        platform = panel.platform
        try:
            if platform == "douyin":
                crawler = DouyinCrawler(self.cfg, self.log)
                result = crawler.collect(
                    text,
                    progress=lambda _n, message: self._emit("status", message),
                    cancel=self.cancel,
                )
            else:
                client = BiliClient(self.cfg, cookies=self.cfg.bili_cookie, logger=self.log)
                mid = parse_space_input(text)
                if mid:
                    result = client.space_items(
                        mid,
                        progress=lambda _n, message: self._emit("status", message),
                        cancel=self.cancel,
                        max_count=self.cfg.bili_max_count,
                    )
                else:
                    refs = extract_video_refs(text)
                    if not refs:
                        raise ValueError(
                            "没有识别到有效的 BV 号/视频链接。请粘贴视频页链接，"
                            "或 UP 主空间链接（space.bilibili.com/数字）。"
                        )
                    result = client.video_items(
                        refs,
                        progress=lambda _n, message: self._emit("status", message),
                    )
            self._emit("crawl_done", panel, result)
        except Exception as exc:
            self.log.exception("抓取失败: %s", exc)
            self._emit("crawl_error", panel, str(exc))

    def _on_crawl_done(self, panel: PlatformPanel, result: CrawlResult) -> None:
        self._finish_busy()
        downloaded = self.store.downloaded_map()
        self._apply_custom_titles(result.items)
        panel.table.set_items(result.items, downloaded)
        if result.cookies:
            self.cookies[panel.platform] = result.cookies
            # 浏览器里登录过 B 站：把登录态写进配置，下载时才能拿到 1080P/4K
            if (
                panel.platform == "bilibili"
                and has_login_cookie(result.cookies)
                and not self.cfg.has_bili_login()
            ):
                self.cfg.bili_cookie = result.cookies
                try:
                    self.cfg.save()
                    self.log.info("已保存浏览器 B 站登录态（下载可获取更高清晰度）")
                except OSError as exc:
                    self.log.warning("保存 B 站登录态失败: %s", exc)
        for warning in result.warnings:
            self.log.warning("%s", warning)
        if result.items:
            self._set_progress(0)
            multi = sum(1 for item in result.items if item.page_index)
            if multi:
                self._set_status(
                    f"抓取完成：共 {len(result.items)} 个可下载条目（含 {multi} 个分P），请勾选后下载"
                )
            else:
                self._set_status(f"抓取完成：共 {len(result.items)} 个作品，请勾选后下载")
            self.log.info(
                "抓取完成：%s 个作品（作者：%s）",
                len(result.items),
                result.author or "未知",
            )
        else:
            self._set_progress(0)
            self._set_status("没有抓取到作品")
            box = messagebox.showwarning
            box(
                "没有获取到内容",
                "没有抓取到作品。可能原因：\n"
                "1. 链接不正确，或该用户没有公开作品；\n"
                "2. 未登录（抖音只展示少量内容），请在弹出的浏览器中登录后重试；\n"
                "3. 平台风控/网络波动，稍后重试。\n\n"
                + ("\n".join(result.warnings[:3]) if result.warnings else ""),
            )
        self._refresh_stats()
        panel.update_download_button()

    def _on_crawl_error(self, panel: PlatformPanel, message: str) -> None:
        self._finish_busy()
        self._set_progress(0)
        self._set_status("抓取失败")
        messagebox.showerror("抓取失败", message)

    def _apply_custom_titles(self, items: list[MediaItem]) -> None:
        """把用户改过的标题套回列表条目（下载记录里按 uid 存）。"""
        custom_titles = self.store.custom_titles()
        if not custom_titles:
            return
        for item in items:
            title = custom_titles.get(item.uid)
            if title:
                item.title = title

    # ---------------- B 站登录（提升清晰度） ----------------
    def bili_login(self, panel: PlatformPanel | None = None) -> None:
        """打开浏览器等用户登录 B 站，并把 Cookie 保存到配置。"""
        if self.busy:
            return
        self.busy = True
        self.cancel = threading.Event()
        self._set_busy(True)
        self._set_progress(None)
        self._set_status("请在弹出的浏览器窗口里登录 B 站（最多等 3 分钟）…")
        self.log.info("等待 B 站登录（未登录只能 480P）…")
        threading.Thread(target=self._bili_login_worker, daemon=True).start()

    def _bili_login_worker(self) -> None:
        cookie = ""
        try:
            cookie = fetch_login_cookie(self.cfg, self.log, timeout=180)
        except Exception as exc:
            self.log.error("浏览器登录 B 站失败: %s", exc)
        self._emit("bili_login_done", cookie)

    def _on_bili_login_done(self, cookie: str) -> None:
        self._finish_busy()
        self._set_progress(0)
        if cookie:
            self.cfg.bili_cookie = cookie
            try:
                self.cfg.save()
            except OSError as exc:
                self.log.warning("保存配置失败: %s", exc)
            self._set_status(
                f"B 站已登录：下载清晰度上限 {self.cfg.quality_text()}"
            )
            self.log.info("B 站登录成功，已保存 Cookie")
            messagebox.showinfo(
                "登录成功",
                "已保存 B 站登录态。\n\n现在重新点“下载选中”，就会按设置里的清晰度上限取流"
                f"（当前：{self.cfg.quality_text()}）。",
                parent=self.root,
            )
        else:
            self._set_status("未检测到 B 站登录，仍按 480P 下载")
            messagebox.showwarning(
                "登录未完成",
                "没有检测到 SESSDATA。\n请在弹出的 Chrome 窗口里扫码登录 B 站后，"
                "再点一次“登录B站”。",
                parent=self.root,
            )

    # ---------------- 分P展开 ----------------
    def expand_pages(self, panel: PlatformPanel, uids: list[str] | None = None) -> None:
        """把“整段视频”条目展开成按分 P 的条目，便于只勾选其中几集。"""
        if self.busy or panel.platform != "bilibili":
            return
        targets = uids if uids else [item.uid for item in panel.table.get_selected_items()]
        items = [
            panel.table.items[uid]
            for uid in targets
            if uid in panel.table.items and not panel.table.items[uid].page_label
        ]
        if not items:
            messagebox.showinfo(
                "提示",
                "没有可展开的条目：请先勾选 B 站整段视频条目。\n"
                "（已经按分P列出的条目无需再展开）",
                parent=self.root,
            )
            return
        if len(items) > 3 and not messagebox.askyesno(
            "展开分P",
            f"将逐条请求 B 站接口展开 {len(items)} 个视频，可能需要一些时间，是否继续？",
            parent=self.root,
        ):
            return
        self.busy = True
        self.cancel = threading.Event()
        self._set_busy(True)
        self._set_progress(None)
        self._set_status(f"正在展开分P（0/{len(items)}）…")
        thread = threading.Thread(target=self._expand_worker, args=(panel, items), daemon=True)
        thread.start()

    def _expand_worker(self, panel: PlatformPanel, items: list[MediaItem]) -> None:
        client = BiliClient(
            self.cfg,
            cookies=self.cookies.get("bilibili") or self.cfg.bili_cookie,
            logger=self.log,
        )
        total = len(items)
        expanded: dict[str, list[MediaItem]] = {}
        warnings: list[str] = []
        for index, item in enumerate(items):
            if self.cancel.is_set():
                warnings.append("已取消")
                break
            self._emit("status", f"正在展开分P（{index + 1}/{total}）：{item.title[:24]}…")
            try:
                pages = client.expand_item(item)
            except Exception as exc:
                warnings.append(f"{item.title}：{exc}")
                continue
            if pages and (len(pages) > 1 or pages[0].uid != item.uid):
                expanded[item.uid] = pages
            else:
                warnings.append(f"{item.title}：只有一个分P，无需展开")
            time.sleep(0.3)
        self._emit("expand_done", panel, expanded, warnings)

    def _on_expand_done(
        self,
        panel: PlatformPanel,
        expanded: dict[str, list[MediaItem]],
        warnings: list[str],
    ) -> None:
        self._finish_busy()
        all_pages = [item for pages in expanded.values() for item in pages]
        self._apply_custom_titles(all_pages)
        total_new = 0
        for parent_uid, page_items in expanded.items():
            downloaded = self._page_downloaded_map(parent_uid, page_items)
            total_new += len(panel.table.expand_item(parent_uid, page_items, downloaded))
        for warning in warnings:
            self.log.warning("展开分P: %s", warning)
        self._set_progress(0)
        if expanded:
            self._set_status(
                f"已展开 {len(expanded)} 个视频，共 {total_new} 个分P，可逐集勾选下载"
            )
            self._append_log(f"展开分P：{len(expanded)} 个视频 / {total_new} 个分P")
        else:
            self._set_status("没有可展开的分P")
            if warnings:
                messagebox.showinfo("展开分P", "\n".join(warnings[:6]), parent=self.root)
        panel.update_download_button()
        self._refresh_stats()

    def _page_downloaded_map(
        self, parent_uid: str, page_items: list[MediaItem]
    ) -> dict[str, str]:
        """判断哪些分 P 之前已经下载过（整段下载会存成 xxx_P3.mp4 这样的文件名）。"""
        result: dict[str, str] = {}
        for item in page_items:
            record = self.store.get(item.uid)
            files = (record or {}).get("files") or []
            if files:
                result[item.uid] = str(files[0])
        parent = self.store.get(parent_uid)
        parent_files = [str(path) for path in ((parent or {}).get("files") or [])]
        if parent_files:
            for item in page_items:
                if item.uid in result:
                    continue
                page_no = item.page_index
                if not page_no:
                    continue
                marker = f"_P{page_no}."
                for path in parent_files:
                    if marker in Path(path).name:
                        result[item.uid] = path
                        break
        return result

    # ---------------- 下载 ----------------
    def start_download(self, panel: PlatformPanel) -> None:
        if self.busy:
            return
        items = panel.table.get_selected_items()
        if not items:
            messagebox.showinfo("提示", "请先在列表中勾选要下载的作品。")
            return
        if (
            panel.platform == "bilibili"
            and not self.cfg.has_bili_login()
            and not has_login_cookie(self.cookies.get("bilibili", ""))
        ):
            if messagebox.askyesno(
                "下载清晰度",
                "当前没有 B 站登录态。\n\n"
                "未登录时 B 站只提供 480P，所以下载会比网页版糊。\n"
                f"设置里的清晰度是「{self.cfg.quality_text()}」，登录后才会生效。\n\n"
                "是否现在打开浏览器登录？（登录完成后再点一次“下载选中”）",
                parent=self.root,
            ):
                self.bili_login(panel)
                return
        if not messagebox.askyesno(
            "确认下载",
            f"即将下载 {len(items)} 个作品。\n\n保存到：{self.cfg.download_dir}\n\n继续吗？",
        ):
            return
        self.busy = True
        self.cancel = threading.Event()
        self._set_busy(True)
        self.dl_fractions = {item.uid: 0.0 for item in items}
        self.dl_total = len(items)
        self._set_progress(0)
        self._set_status(f"开始下载（0/{self.dl_total}）…")
        for item in items:
            panel.table.set_status(item.uid, "等待下载", "downloading")

        platform = panel.platform
        cookies = self.cookies.get(platform, "")
        if not cookies:
            cookies = self.cfg.douyin_cookie if platform == "douyin" else self.cfg.bili_cookie
        thread = threading.Thread(
            target=self._download_worker,
            args=(items, cookies),
            daemon=True,
        )
        thread.start()

    def _download_worker(self, items: list[MediaItem], cookies: str) -> None:
        manager = DownloadManager(self.cfg, self.store, self.log)
        finished = {"n": 0}

        def on_start(item: MediaItem) -> None:
            self._emit("dl_start", item.uid)

        def on_progress(uid: str, fraction: float) -> None:
            self._emit("dl_progress", uid, fraction)

        def on_done(outcome: ItemOutcome) -> None:
            finished["n"] += 1
            self._emit("dl_done", outcome)

        try:
            summary = manager.run(
                items,
                cookies=cookies,
                cancel=self.cancel,
                on_start=on_start,
                on_progress=on_progress,
                on_done=on_done,
            )
            self._emit("dl_finish", summary)
        except Exception as exc:
            self.log.exception("下载任务异常: %s", exc)
            self._emit("dl_finish", DownloadSummary(total=len(items), failed=len(items)))

    def _on_download_start(self, uid: str) -> None:
        panel = self._panel_of(uid)
        if panel is not None:
            panel.table.set_status(uid, "下载中…", "downloading")

    def _on_download_progress(self, uid: str, fraction: float) -> None:
        self.dl_fractions[uid] = fraction
        panel = self._panel_of(uid)
        if panel is not None:
            panel.table.set_status(uid, f"下载中 {int(fraction * 100)}%", "downloading")
        if self.dl_total:
            overall = sum(self.dl_fractions.values()) / self.dl_total
            self._set_progress(overall * 100)
            done = sum(1 for value in self.dl_fractions.values() if value >= 1.0)
            self._set_status(f"下载中（{done}/{self.dl_total}）…")

    def _on_item_done(self, outcome: ItemOutcome) -> None:
        panel = self._panel_of(outcome.uid)
        if panel is None:
            return
        if outcome.ok:
            if outcome.skipped:
                panel.table.mark_downloaded(outcome.uid)
                self.log.info("已下载，跳过：%s", outcome.title)
            else:
                panel.table.mark_downloaded(outcome.uid)
                if outcome.error:
                    panel.table.set_status(outcome.uid, "部分完成", "done")
                    self.log.warning("部分完成：%s（%s）", outcome.title, outcome.error)
                else:
                    self.log.info("下载完成：%s", outcome.title)
        else:
            panel.table.mark_failed(outcome.uid)
            self.log.error("下载失败：%s - %s", outcome.title, outcome.error)

    def _on_download_finish(self, summary: DownloadSummary) -> None:
        self._finish_busy()
        self._set_progress(100 if summary.failed == 0 else 0)
        self._set_status(summary.text)
        for panel in self.panels.values():
            panel.table.downloaded = self.store.downloaded_map()
            panel.table.refresh()
            panel.update_download_button()
        self._refresh_stats()
        if summary.cancelled:
            messagebox.showinfo(
                "已取消",
                "下载任务已取消。\n" + summary.text + "\n\n已完成的文件会保留，可稍后继续。",
            )
        elif summary.failed:
            messagebox.showwarning(
                "下载完成（有失败项）",
                summary.text + "\n\n失败的条目仍保持勾选，可重新点击下载重试。",
            )
        elif summary.succeeded or summary.skipped:
            messagebox.showinfo(
                "下载完成",
                summary.text + f"\n\n保存目录：{self.cfg.download_dir}",
            )

    # ---------------- 状态切换 ----------------
    def _panel_of(self, uid: str):
        for panel in self.panels.values():
            if uid in panel.table.items:
                return panel
        return None

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        for panel in self.panels.values():
            panel.set_busy(busy)

    def _finish_busy(self) -> None:
        self._set_busy(False)

    def stop_task(self) -> None:
        if not self.busy:
            return
        self.cancel.set()
        self._set_status("正在停止…（当前任务结束后退出）")
        self.log.warning("用户请求停止当前任务")

    # ---------------- 菜单动作 ----------------
    def open_download_dir(self) -> None:
        try:
            Path(self.cfg.download_dir).mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.cfg.download_dir))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def open_log_file(self) -> None:
        log_file = Path(LOGS_DIR) / "app.log"
        if log_file.exists():
            self._open_path(log_file)
        else:
            messagebox.showinfo("提示", "日志文件还没有生成。")

    def _open_path(self, path: Path) -> None:
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def open_url(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def rename_item(self, panel: PlatformPanel, uid: str) -> None:
        """重命名作品标题；如果已下载，可同时重命名本地文件。"""
        item = panel.table.items.get(uid)
        if item is None:
            return
        record = self.store.get(uid)
        initial = "" if item.title.startswith("无标题作品") else item.title
        new_title = simpledialog.askstring(
            "重命名标题",
            f"作品 ID：{uid}\n\n请输入新的标题（用于列表显示和下载文件名）：",
            initialvalue=initial,
            parent=self.root,
        )
        if new_title is None:
            return
        new_title = " ".join(new_title.split()).strip()
        if not new_title:
            messagebox.showinfo("提示", "标题不能为空。", parent=self.root)
            return
        new_title = new_title[:120]

        rename_files = False
        if record is not None and record.get("files"):
            rename_files = messagebox.askyesno(
                "同时重命名文件",
                "该作品已经下载。\n是否同时重命名本地文件，并同步更新下载记录？\n"
                "（选择“否”只改列表里的标题，不改文件名）",
                parent=self.root,
            )

        if rename_files:
            from ..renamer import rename_downloaded_files

            ok, new_files, error = rename_downloaded_files(item, new_title, self.store)
            if ok:
                panel.table.downloaded[uid] = new_files[0]
                panel.table.set_status(uid, "已重命名", "done")
                self.log.info("已重命名文件：%s -> %s", item.title, new_title)
            else:
                messagebox.showerror(
                    "重命名文件失败",
                    f"{error}\n\n（列表标题仍会更新，本地文件名保持不变）",
                    parent=self.root,
                )
                if record is not None:
                    self.store.update_record(uid, title=new_title)
        elif record is not None:
            self.store.update_record(uid, title=new_title)

        self.store.set_custom_title(uid, new_title)
        item.title = new_title
        panel.table.refresh()
        self.log.info("重命名标题：%s -> %s", uid, new_title)
        self._set_status(f"已重命名为：{new_title}")

    def show_item_details(self, panel: PlatformPanel, uid: str) -> None:
        """查看作品完整信息、合作者名单和本地文件。"""
        item = panel.table.items.get(uid)
        if item is None:
            return
        record = self.store.get(uid)
        error = ""
        if item.platform == "bilibili":
            self._set_status("正在读取作品详情/合作者…")
            try:
                self.root.update_idletasks()
            except tk.TclError:
                pass
            try:
                client = BiliClient(
                    self.cfg,
                    cookies=self.cookies.get("bilibili") or self.cfg.bili_cookie,
                    logger=self.log,
                )
                bvid = str(item.payload.get("bvid") or uid.split(":")[-1])
                view = client.view_info(bvid=bvid)
                owner_name, owner_mid, collaborators, is_union = extract_staff(view)
                if owner_name:
                    item.author = owner_name
                if owner_mid:
                    item.author_id = owner_mid
                item.collaborators = collaborators
                item.collab_flag = is_union
                stat = view.get("stat") if isinstance(view.get("stat"), dict) else {}
                item.views = int(stat.get("view") or item.views)
                panel.table.refresh()
            except Exception as exc:  # 详情读取失败不影响主流程
                error = str(exc)
            finally:
                self._set_status("就绪")
        messagebox.showinfo(
            "作品详情 / 合作者",
            self._build_detail_text(item, record, error),
            parent=self.root,
        )

    @staticmethod
    def _build_detail_text(item: MediaItem, record: dict | None, error: str = "") -> str:
        type_text = item.display_kind
        lines = [
            f"标题：{item.title}",
            f"平台：{item.platform_text}    类型：{type_text}",
            f"作品ID：{item.item_id}",
            f"主作者：{item.author_safe}" + (f"（ID：{item.author_id}）" if item.author_id else ""),
        ]
        if item.page_label:
            part = str(item.payload.get("part") or "").strip()
            lines.append(f"分P：{item.page_label}" + (f"（{part}）" if part else ""))
        elif item.platform == "bilibili" and item.is_multi_page:
            lines.append(
                f"分P：共 {item.page_count}P —— 这是整段条目，"
                "可右键“展开为分P”后只下其中几集"
            )
        if item.collaborators:
            lines.append("合作者：" + "、".join(item.collaborators))
        elif item.platform == "bilibili":
            lines.append("合作者：未检测到（不是联合投稿，或接口未返回）")
        else:
            lines.append("合作者：抖音接口未提供合作者名单，可点“在浏览器中打开”查看")
        lines.extend(
            [
                f"发布日期：{item.date_text}",
                f"时长/数量：{item.length_text}",
                f"数据：{item.stat_text}（{'点赞' if item.platform == 'douyin' else '播放'}）",
                f"链接：{item.page_url}",
            ]
        )
        if record:
            files = record.get("files") or []
            lines.append(f"本地文件（{len(files)} 个）：")
            lines.extend("  " + str(path) for path in files[:8])
            if len(files) > 8:
                lines.append("  …")
        else:
            lines.append("本地文件：尚未下载")
        if error:
            lines.append(f"\n读取最新信息失败：{error}")
        return "\n".join(lines)

    def open_settings(self) -> None:
        if self.busy:
            messagebox.showinfo("提示", "请等待当前任务结束后再修改设置。")
            return
        SettingsDialog(self.root, self.cfg, self.store, on_saved=self._on_settings_saved)

    def _on_settings_saved(self) -> None:
        self._refresh_stats()
        # 设置里可能清空过下载记录，刷新列表中的“已下载”状态
        downloaded = self.store.downloaded_map()
        for panel in self.panels.values():
            panel.table.downloaded = downloaded
            panel.table.refresh()
            panel.update_download_button()
        self._append_log("设置已保存。")
        self._set_status("设置已保存")

    def show_help(self) -> None:
        readme = Path(PROJECT_ROOT) / "README.md"
        if readme.exists():
            self._open_path(readme)
        else:
            messagebox.showinfo(
                "使用说明",
                "1. 选择平台标签页；\n"
                "2. 粘贴抖音用户主页 / B站视频链接或 UP 主空间链接；\n"
                "3. 点击“获取列表”，在结果中勾选想下载的作品；\n"
                "4. 点击“下载选中”。\n\n"
                "首次使用抖音/B站空间抓取时，请保持浏览器窗口显示并登录。",
            )

    def show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            f"{APP_TITLE} v{APP_VERSION}\n\n"
            "整合原抖音（all_douyin_ai / re_douyin_ai / images_douyin）"
            "与 B站（re_bilbil）下载脚本，\n"
            "新增“抓取列表 + 勾选下载”的图形界面。\n\n"
            "仅供个人学习研究使用，请遵守平台用户协议与相关法律法规。",
        )

    # ---------------- 退出 ----------------
    def _on_close(self) -> None:
        if self.busy:
            if not messagebox.askyesno(
                "任务进行中",
                "当前还有任务在运行，确定要退出吗？\n"
                "会先尝试取消任务并关闭浏览器，最多等待 15 秒。",
            ):
                return
            self.cancel.set()
            self._set_status("正在取消任务并退出，请稍候…")
            self._close_deadline = time.time() + 15
            self.root.after(200, self._wait_then_destroy)
            return
        self._destroy()

    def _wait_then_destroy(self) -> None:
        if self.busy and time.time() < getattr(self, "_close_deadline", 0):
            self.root.after(200, self._wait_then_destroy)
            return
        self._destroy()

    def _destroy(self) -> None:
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        remove_handler(self.log, self.log_handler)
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def run_gui(cfg: AppConfig) -> int:
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    MainWindow(root, cfg)
    root.mainloop()
    return 0
