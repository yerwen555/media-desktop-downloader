# -*- coding: utf-8 -*-
"""可勾选的结果列表（Treeview + 复选框列 + 筛选 + 排序）。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..models import MediaItem

_COLUMNS = (
    ("sel", "选", 40, "center", False),
    ("kind", "类型", 86, "center", False),
    ("title", "标题", 330, "w", True),
    ("id", "作品ID", 150, "center", False),
    ("length", "时长/张数", 80, "center", False),
    ("date", "日期", 102, "center", False),
    ("stat", "点赞/播放", 82, "center", False),
    ("author", "作者", 190, "w", False),
    ("status", "状态", 105, "center", False),
)
_CHECK = "√"
_SORTABLE = {"title", "date", "length", "stat", "kind", "id"}


class ResultsTable(ttk.Frame):
    """线程安全边界：所有方法都应由 Tk 主线程调用。"""

    def __init__(
        self,
        master,
        *,
        kind_choices: tuple[str, ...] = ("全部", "视频", "图集"),
        on_change: Callable[[], None] | None = None,
        on_open_url: Callable[[str], None] | None = None,
        on_rename: Callable[[str], None] | None = None,
        on_details: Callable[[str], None] | None = None,
    ):
        super().__init__(master)
        self.items: dict[str, MediaItem] = {}
        self.order: list[str] = []
        self.selected: set[str] = set()
        self.downloaded: dict[str, str] = {}
        self.status_text: dict[str, str] = {}
        self.status_tag: dict[str, str] = {}
        self._on_change = on_change
        self._on_open_url = on_open_url
        self._on_rename = on_rename
        self._on_details = on_details
        self._sort_column: str | None = None
        self._sort_reverse = False
        self._filter_job: str | None = None

        self._build_toolbar(kind_choices)
        self._build_tree()
        self._bind_events()

    # ---------------- 构建 ----------------
    def _build_toolbar(self, kind_choices: tuple[str, ...]) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(6, 4))
        ttk.Label(bar, text="筛选：").pack(side="left")
        self.keyword_var = tk.StringVar()
        keyword_entry = ttk.Entry(bar, textvariable=self.keyword_var, width=22)
        keyword_entry.pack(side="left", padx=(0, 8))
        keyword_entry.insert(0, "")
        ttk.Label(bar, text="类型：").pack(side="left")
        self.kind_var = tk.StringVar(value=kind_choices[0])
        kind_box = ttk.Combobox(
            bar, textvariable=self.kind_var, values=list(kind_choices), width=6, state="readonly"
        )
        kind_box.pack(side="left", padx=(0, 10))
        self.only_new_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="仅看未下载", variable=self.only_new_var).pack(side="left")

        self.count_label = ttk.Label(bar, text="已选 0 / 0")
        self.count_label.pack(side="right", padx=(8, 0))
        self.select_all_btn = ttk.Button(bar, text="全选", width=7, command=self.select_all)
        self.select_all_btn.pack(side="right", padx=(6, 0))
        self.invert_btn = ttk.Button(bar, text="反选", width=7, command=self.invert)
        self.invert_btn.pack(side="right", padx=(6, 0))
        self.select_none_btn = ttk.Button(bar, text="全不选", width=8, command=self.select_none)
        self.select_none_btn.pack(side="right", padx=(6, 0))
        self.select_new_btn = ttk.Button(
            bar, text="只选未下载", width=11, command=self.select_undownloaded
        )
        self.select_new_btn.pack(side="right", padx=(6, 0))

        self.keyword_var.trace_add("write", lambda *_: self._schedule_refresh())
        self.kind_var.trace_add("write", lambda *_: self.refresh())
        self.only_new_var.trace_add("write", lambda *_: self.refresh())

    def _build_tree(self) -> None:
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        columns = [col[0] for col in _COLUMNS]
        self.tree = ttk.Treeview(
            container, columns=columns, show="headings", selectmode="extended"
        )
        for key, title, width, anchor, stretch in _COLUMNS:
            heading = title
            if key in _SORTABLE:
                heading = f"{title}"
            self.tree.heading(
                key,
                text=heading,
                anchor=anchor,
                command=(lambda k=key: self._sort_by(k)) if key in _SORTABLE else "",
            )
            self.tree.column(key, width=width, anchor=anchor, stretch=stretch, minwidth=30)
        scroll_y = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        scroll_x = ttk.Scrollbar(container, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.tree.tag_configure("sel", background="#E8F1FF")
        self.tree.tag_configure("done", foreground="#6E6E6E")
        self.tree.tag_configure("fail", foreground="#B03A2E")
        self.tree.tag_configure("downloading", foreground="#0B61A4")

        self.empty_label = ttk.Label(container, text="暂无可下载内容", anchor="center")
        self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def _bind_events(self) -> None:
        self.tree.bind("<Button-1>", self._on_single_click)
        self.tree.bind("<space>", self._on_space)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<F2>", self._on_rename_key)

    # ---------------- 数据 ----------------
    def set_items(self, items: list[MediaItem], downloaded: dict[str, str] | None = None) -> None:
        self.items = {item.uid: item for item in items}
        self.order = [item.uid for item in items]
        self.downloaded = dict(downloaded or {})
        self.selected = set(self.order)
        self.status_text.clear()
        self.status_tag.clear()
        self._sort_column = None
        self._sort_reverse = False
        self.refresh()

    def clear(self) -> None:
        self.items.clear()
        self.order.clear()
        self.selected.clear()
        self.downloaded.clear()
        self.status_text.clear()
        self.status_tag.clear()
        self.refresh()

    def mark_downloaded(self, uid: str, path: str = "") -> None:
        self.downloaded[uid] = path
        self.status_text[uid] = "已下载"
        self.status_tag[uid] = "done"
        self._update_row(uid)

    def mark_failed(self, uid: str, message: str = "失败") -> None:
        self.status_text[uid] = message
        self.status_tag[uid] = "fail"
        self._update_row(uid)

    def set_status(self, uid: str, text: str, tag: str | None = None) -> None:
        self.status_text[uid] = text
        if tag:
            self.status_tag[uid] = tag
        self._update_row(uid)

    def get_selected_items(self) -> list[MediaItem]:
        return [self.items[uid] for uid in self.order if uid in self.selected and uid in self.items]

    def selected_count(self) -> int:
        return sum(1 for uid in self.order if uid in self.selected)

    def total_count(self) -> int:
        return len(self.order)

    # ---------------- 选择 ----------------
    def toggle(self, uid: str) -> None:
        if uid not in self.items:
            return
        if uid in self.selected:
            self.selected.discard(uid)
        else:
            self.selected.add(uid)
        self._update_row(uid)
        self._changed()

    def select_all(self) -> None:
        visible = self._visible_items()
        self.selected.update(item.uid for item in visible)
        self.refresh()

    def select_none(self) -> None:
        visible = self._visible_items()
        for item in visible:
            self.selected.discard(item.uid)
        self.refresh()

    def invert(self) -> None:
        visible = self._visible_items()
        for item in visible:
            if item.uid in self.selected:
                self.selected.discard(item.uid)
            else:
                self.selected.add(item.uid)
        self.refresh()

    def select_undownloaded(self) -> None:
        """只勾选尚未下载的作品；同时把筛选切换回“全部”，方便看到结果。"""
        self.kind_var.set("全部")
        self.only_new_var.set(False)
        self.keyword_var.set("")
        self.selected = {
            uid for uid in self.order if uid in self.items and uid not in self.downloaded
        }
        self.refresh()

    # ---------------- 渲染 ----------------
    def refresh(self) -> None:
        visible = self._visible_items()
        existing = set(self.tree.get_children(""))
        target = [item.uid for item in visible]
        for uid in existing - set(target):
            self.tree.delete(uid)
        for index, item in enumerate(visible):
            if item.uid in existing:
                self.tree.move(item.uid, "", index)
                self.tree.item(item.uid, values=self._row_values(item), tags=self._row_tags(item))
            else:
                self.tree.insert(
                    "", index, iid=item.uid, values=self._row_values(item), tags=self._row_tags(item)
                )
        if visible:
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
        self._update_count()

    def _visible_items(self) -> list[MediaItem]:
        keyword = self.keyword_var.get().strip().lower()
        kind = self.kind_var.get()
        only_new = self.only_new_var.get()
        result: list[MediaItem] = []
        for uid in self.order:
            item = self.items.get(uid)
            if item is None:
                continue
            if kind == "视频" and item.kind != "video":
                continue
            if kind == "图集" and item.kind != "image":
                continue
            if keyword:
                haystacks = (
                    item.title.lower(),
                    item.author.lower(),
                    item.item_id.lower(),
                    " ".join(item.collaborators).lower(),
                )
                if not any(keyword in text for text in haystacks):
                    continue
            if only_new and uid in self.downloaded:
                continue
            result.append(item)
        if self._sort_column:
            result.sort(key=self._sort_key, reverse=self._sort_reverse)
        return result

    def _sort_key(self, item: MediaItem):
        column = self._sort_column
        if column == "title":
            return item.title
        if column == "date":
            return item.create_time
        if column == "length":
            return item.duration_ms if item.kind == "video" else item.image_count
        if column == "stat":
            return item.likes + item.views
        if column == "kind":
            return item.kind
        if column == "id":
            return item.item_id
        return 0

    def _sort_by(self, column: str) -> None:
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False
        self.refresh()

    def _row_values(self, item: MediaItem) -> tuple:
        selected = _CHECK if item.uid in self.selected else ""
        status = self.status_text.get(item.uid)
        if status is None:
            status = "已下载" if item.uid in self.downloaded else "未下载"
        author = item.author_table
        if len(author) > 18:
            author = author[:17] + "…"
        kind_text = item.type_text + ("·合作" if item.is_collab else "")
        return (
            selected,
            kind_text,
            item.title,
            item.id_short,
            item.length_text,
            item.date_text,
            item.stat_text,
            author,
            status,
        )

    def _row_tags(self, item: MediaItem) -> tuple[str, ...]:
        tags: list[str] = []
        if item.uid in self.selected:
            tags.append("sel")
        tag = self.status_tag.get(item.uid)
        if tag:
            tags.append(tag)
        elif item.uid in self.downloaded:
            tags.append("done")
        return tuple(tags)

    def _update_row(self, uid: str) -> None:
        item = self.items.get(uid)
        if item is None or not self.tree.exists(uid):
            self._update_count()
            return
        self.tree.item(uid, values=self._row_values(item), tags=self._row_tags(item))
        self._update_count()

    def _update_count(self) -> None:
        downloaded = sum(1 for uid in self.order if uid in self.downloaded)
        self.count_label.config(
            text=f"已选 {self.selected_count()} / 共 {self.total_count()} · 已下载 {downloaded}"
        )

    def _changed(self) -> None:
        self._update_count()
        if self._on_change is not None:
            self._on_change()

    def _schedule_refresh(self) -> None:
        if self._filter_job is not None:
            try:
                self.after_cancel(self._filter_job)
            except Exception:
                pass
        self._filter_job = self.after(220, self._do_scheduled_refresh)

    def _do_scheduled_refresh(self) -> None:
        self._filter_job = None
        self.refresh()

    # ---------------- 事件 ----------------
    def _on_single_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return None
        column = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if row and column == "#1":
            self.toggle(row)
            return "break"
        return None

    def _on_space(self, _event=None):
        for uid in self.tree.selection():
            self.toggle(uid)
        return "break"

    def _on_double_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return None
        column = self.tree.identify_column(event.x)
        if column == "#1":
            return None
        if column == "#3":
            self._request_rename(row)
            return "break"
        if column in ("#4", "#8"):
            self._request_details(row)
            return "break"
        item = self.items.get(row)
        if item is not None and item.page_url and self._on_open_url is not None:
            self._on_open_url(item.page_url)
        return "break"

    def _on_rename_key(self, _event=None):
        selection = self.tree.selection()
        if selection:
            self._request_rename(selection[0])
        return "break"

    def _request_rename(self, uid: str) -> None:
        if self._on_rename is not None and uid in self.items:
            self._on_rename(uid)

    def _request_details(self, uid: str) -> None:
        if self._on_details is not None and uid in self.items:
            self._on_details(uid)

    def _on_right_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return None
        self.tree.selection_set(row)
        item = self.items.get(row)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label="查看详情 / 合作者…",
            command=lambda: self._request_details(row),
        )
        menu.add_command(label="重命名标题…", command=lambda: self._request_rename(row))
        menu.add_command(
            label="在浏览器中打开",
            command=(lambda: self._on_open_url(item.page_url))
            if item and item.page_url and self._on_open_url
            else None,
        )
        menu.add_separator()
        menu.add_command(
            label="复制作品ID",
            command=lambda: self._copy_to_clipboard(item.item_id if item else ""),
        )
        menu.add_command(
            label="复制标题",
            command=lambda: self._copy_to_clipboard(item.title if item else ""),
        )
        menu.add_command(
            label="复制链接",
            command=lambda: self._copy_to_clipboard(item.page_url if item else ""),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _copy_to_clipboard(self, text: str) -> None:
        if not text:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except tk.TclError:
            pass
