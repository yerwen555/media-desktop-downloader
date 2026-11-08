# -*- coding: utf-8 -*-
"""设置对话框。"""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from ..config import DATA_DIR, AppConfig
from ..storage import DownloadStore

# 键是界面文案，值是清晰度“上限”，实际取不超过它的最高流；未登录时 B 站只会给 480P
_QUALITY_OPTIONS = {
    "最高可用（4K/1080P60，需登录）": 127,
    "4K（需大会员）": 120,
    "1080P60（高帧率）": 116,
    "1080P": 80,
    "720P": 64,
    "480P": 32,
}
_QUALITY_REVERSE = {value: key for key, value in _QUALITY_OPTIONS.items()}


class SettingsDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        cfg: AppConfig,
        store: DownloadStore,
        on_saved: Callable[[], None] | None = None,
    ):
        super().__init__(master)
        self.cfg = cfg
        self.store = store
        self.on_saved = on_saved
        self.title("设置")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        self._center_on_parent(master)

    # ---------------- 界面 ----------------
    def _build(self) -> None:
        padding = {"padx": 10, "pady": 5}
        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)

        # 基础设置
        basic = ttk.LabelFrame(container, text="基础设置", padding=10)
        basic.pack(fill="x", pady=(0, 8))
        self.download_dir_var = tk.StringVar(value=self.cfg.download_dir)
        ttk.Label(basic, text="保存目录：").grid(row=0, column=0, sticky="w", **padding)
        ttk.Entry(basic, textvariable=self.download_dir_var, width=52).grid(
            row=0, column=1, sticky="we", **padding
        )
        ttk.Button(basic, text="浏览…", command=self._browse_dir).grid(row=0, column=2, **padding)

        self.workers_var = tk.IntVar(value=self.cfg.max_workers)
        self.douyin_max_var = tk.IntVar(value=self.cfg.douyin_max_count)
        self.bili_max_var = tk.IntVar(value=self.cfg.bili_max_count)
        ttk.Label(basic, text="并发下载数：").grid(row=1, column=0, sticky="w", **padding)
        ttk.Spinbox(basic, from_=1, to=16, textvariable=self.workers_var, width=8).grid(
            row=1, column=1, sticky="w", **padding
        )
        ttk.Label(basic, text="抖音最多抓取：").grid(row=2, column=0, sticky="w", **padding)
        ttk.Spinbox(basic, from_=1, to=5000, textvariable=self.douyin_max_var, width=8).grid(
            row=2, column=1, sticky="w", **padding
        )
        ttk.Label(basic, text="B站最多抓取：").grid(row=3, column=0, sticky="w", **padding)
        ttk.Spinbox(basic, from_=1, to=5000, textvariable=self.bili_max_var, width=8).grid(
            row=3, column=1, sticky="w", **padding
        )
        self.skip_downloaded_var = tk.BooleanVar(value=self.cfg.skip_downloaded)
        ttk.Checkbutton(
            basic, text="跳过已下载的作品（推荐）", variable=self.skip_downloaded_var
        ).grid(row=4, column=0, columnspan=2, sticky="w", **padding)
        self.show_browser_var = tk.BooleanVar(value=self.cfg.show_browser)
        ttk.Checkbutton(
            basic,
            text="抓取列表时显示浏览器窗口（首次登录需要；登录后可不显示）",
            variable=self.show_browser_var,
        ).grid(row=5, column=0, columnspan=3, sticky="w", **padding)

        # 下载参数
        advanced = ttk.LabelFrame(container, text="下载参数", padding=10)
        advanced.pack(fill="x", pady=(0, 8))
        self.quality_var = tk.StringVar(
            value=_QUALITY_REVERSE.get(int(self.cfg.bili_quality), "最高可用（4K/1080P60，需登录）")
        )
        ttk.Label(advanced, text="B站清晰度：").grid(row=0, column=0, sticky="w", **padding)
        ttk.Combobox(
            advanced,
            textvariable=self.quality_var,
            values=list(_QUALITY_OPTIONS.keys()),
            width=26,
            state="readonly",
        ).grid(row=0, column=1, sticky="w", **padding)
        ttk.Label(
            advanced,
            text="（取不超过此上限的最高流；未登录只能拿到 480P）",
            foreground="#777777",
        ).grid(row=0, column=2, sticky="w", **padding)

        self.douyin_delay_var = tk.DoubleVar(value=self.cfg.douyin_download_delay)
        self.bili_delay_var = tk.DoubleVar(value=self.cfg.bili_download_delay)
        ttk.Label(advanced, text="抖音下载间隔(秒)：").grid(row=1, column=0, sticky="w", **padding)
        ttk.Spinbox(
            advanced, from_=0, to=10, increment=0.1, textvariable=self.douyin_delay_var, width=8
        ).grid(row=1, column=1, sticky="w", **padding)
        ttk.Label(advanced, text="B站下载间隔(秒)：").grid(row=2, column=0, sticky="w", **padding)
        ttk.Spinbox(
            advanced, from_=0, to=10, increment=0.1, textvariable=self.bili_delay_var, width=8
        ).grid(row=2, column=1, sticky="w", **padding)

        # Cookie
        cookie_box = ttk.LabelFrame(container, text="登录 Cookie（可选，留空则自动读取浏览器登录态）", padding=10)
        cookie_box.pack(fill="x", pady=(0, 8))
        ttk.Label(cookie_box, text="抖音 Cookie：").grid(row=0, column=0, sticky="nw", **padding)
        self.douyin_cookie_text = tk.Text(cookie_box, height=2, width=70, wrap="char")
        self.douyin_cookie_text.grid(row=0, column=1, sticky="we", **padding)
        self.douyin_cookie_text.insert("1.0", self.cfg.douyin_cookie)
        ttk.Label(cookie_box, text="B站 Cookie：").grid(row=1, column=0, sticky="nw", **padding)
        self.bili_cookie_text = tk.Text(cookie_box, height=3, width=70, wrap="char")
        self.bili_cookie_text.grid(row=1, column=1, sticky="we", **padding)
        self.bili_cookie_text.insert("1.0", self.cfg.bili_cookie)
        self.login_btn = ttk.Button(
            cookie_box, text="用浏览器登录 B 站并保存登录态", command=self._bili_login
        )
        self.login_btn.grid(row=2, column=1, sticky="w", **padding)
        ttk.Label(
            cookie_box,
            text="提示：B 站未登录只给 480P（画面偏糊）。点上面的按钮打开浏览器扫码登录，"
            "登录后会写入 Cookie，下载即可到 1080P/1080P60/4K。",
            foreground="#777777",
            wraplength=620,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", **padding)

        # 底部按钮
        footer = ttk.Frame(container)
        footer.pack(fill="x", pady=(4, 0))
        ttk.Button(footer, text="打开数据目录", command=self._open_data_dir).pack(side="left")
        ttk.Button(footer, text="清空下载记录", command=self._clear_records).pack(side="left", padx=8)
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(footer, text="保存", command=self._save).pack(side="right")

        for column in (0, 1, 2):
            basic.columnconfigure(column, weight=1 if column == 1 else 0)

    def _center_on_parent(self, master: tk.Misc) -> None:
        try:
            parent_x = master.winfo_rootx()
            parent_y = master.winfo_rooty()
            parent_w = master.winfo_width()
            parent_h = master.winfo_height()
            width = self.winfo_width()
            height = self.winfo_height()
            x = parent_x + max(0, (parent_w - width) // 2)
            y = parent_y + max(0, (parent_h - height) // 3)
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

    # ---------------- 动作 ----------------
    def _browse_dir(self) -> None:
        initial = self.download_dir_var.get() or str(DATA_DIR)
        chosen = filedialog.askdirectory(parent=self, initialdir=initial, title="选择保存目录")
        if chosen:
            self.download_dir_var.set(os.path.normpath(chosen))

    def _open_data_dir(self) -> None:
        try:
            os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _bili_login(self) -> None:
        """后台打开浏览器等登录，主线程轮询结果（避免 Tk 跨线程操作）。"""
        self.login_thread_result = None
        self.login_btn.config(state="disabled", text="等待浏览器登录…")
        cfg = self.cfg

        def work() -> None:
            try:
                from ..bilibili import fetch_login_cookie

                cookie = fetch_login_cookie(cfg, timeout=180)
                self.login_thread_result = ("ok", cookie)
            except Exception as exc:  # 浏览器/依赖问题都提示出来
                self.login_thread_result = ("err", str(exc))

        threading.Thread(target=work, daemon=True).start()
        self.after(500, self._poll_login)

    def _poll_login(self) -> None:
        result = getattr(self, "login_thread_result", None)
        if result is None:
            try:
                self.after(500, self._poll_login)
            except tk.TclError:
                pass
            return
        status, payload = result
        self.login_btn.config(state="normal", text="用浏览器登录 B 站并保存登录态")
        if status == "ok" and payload:
            self.bili_cookie_text.delete("1.0", "end")
            self.bili_cookie_text.insert("1.0", payload)
            messagebox.showinfo(
                "已获取登录态",
                "已把 B 站登录 Cookie 填进下面的文本框，点“保存”后生效。\n"
                "之后下载会按上面的清晰度上限取流（1080P/1080P60/4K）。",
                parent=self,
            )
        else:
            messagebox.showwarning(
                "未获取到登录态",
                (payload or "没有检测到 SESSDATA，请在弹出的浏览器里扫码登录后重试。"),
                parent=self,
            )

    def _clear_records(self) -> None:
        if not messagebox.askyesno(
            "清空下载记录",
            "确定要清空“已下载”记录吗？\n"
            "（不会删除已下载的文件，只影响去重、状态显示和手动改过的标题）",
            parent=self,
        ):
            return
        count = self.store.clear()
        if self.on_saved is not None:
            self.on_saved()
        messagebox.showinfo("完成", f"已清空 {count} 条下载记录。", parent=self)

    def _save(self) -> None:
        download_dir = self.download_dir_var.get().strip()
        if not download_dir:
            messagebox.showerror("保存失败", "保存目录不能为空。", parent=self)
            return
        try:
            Path(download_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(
                "保存目录不可用",
                f"无法创建/访问保存目录：\n{download_dir}\n\n{exc}\n\n"
                "请插回移动硬盘/网络盘，或换一个可用目录。",
                parent=self,
            )
            return
        try:
            workers = int(self.workers_var.get())
            douyin_max = int(self.douyin_max_var.get())
            bili_max = int(self.bili_max_var.get())
            douyin_delay = float(self.douyin_delay_var.get())
            bili_delay = float(self.bili_delay_var.get())
        except (TypeError, ValueError):
            messagebox.showerror("保存失败", "数字格式不正确，请检查后重试。", parent=self)
            return
        if not 1 <= workers <= 16:
            messagebox.showerror("保存失败", "并发下载数需在 1~16 之间。", parent=self)
            return
        if not 1 <= douyin_max <= 5000 or not 1 <= bili_max <= 5000:
            messagebox.showerror("保存失败", "抓取上限需在 1~5000 之间。", parent=self)
            return
        if not 0 <= douyin_delay <= 10 or not 0 <= bili_delay <= 10:
            messagebox.showerror("保存失败", "下载间隔需在 0~10 秒之间。", parent=self)
            return
        self.cfg.download_dir = download_dir
        self.cfg.max_workers = workers
        self.cfg.douyin_max_count = douyin_max
        self.cfg.bili_max_count = bili_max
        self.cfg.douyin_download_delay = douyin_delay
        self.cfg.bili_download_delay = bili_delay
        self.cfg.skip_downloaded = bool(self.skip_downloaded_var.get())
        self.cfg.show_browser = bool(self.show_browser_var.get())
        self.cfg.bili_quality = _QUALITY_OPTIONS.get(
            self.quality_var.get(), self.cfg.bili_quality
        )
        self.cfg.douyin_cookie = self.douyin_cookie_text.get("1.0", "end-1c").strip()
        self.cfg.bili_cookie = self.bili_cookie_text.get("1.0", "end-1c").strip()
        try:
            self.cfg.ensure_dirs()
            self.cfg.save()
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法写入配置文件：{exc}", parent=self)
            return
        if self.on_saved is not None:
            self.on_saved()
        self.destroy()
