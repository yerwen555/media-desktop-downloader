# -*- coding: utf-8 -*-
"""B 站：视频/UP 主投稿列表采集 + DASH 音视频自动合并下载。

相比原 re_bilbil.py（手动复制两条 m4s 地址），这里支持：
  - 粘贴视频链接 / BV 号 / av 号，自动解析标题与分 P；
  - 粘贴 UP 主空间链接，自动滚动抓取投稿列表；
  - WBI 签名自动取流，ffmpeg 无转码合并（速度快、无画质损失）；
  - 浏览器登录态 Cookie 自动复用，可下载更高清晰度。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import requests

from .browser import BrowserError, BrowserSession
from .config import AppConfig
from .models import CrawlResult, ItemOutcome, MediaItem
from .net import build_session, download_any
from .utils import parse_length_text, sanitize_name, strip_html

ProgressCb = Callable[[int, str], None]
ItemProgressCb = Callable[[float], None]

API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_PLAYURL = "https://api.bilibili.com/x/player/playurl"
API_PLAYURL_WBI = "https://api.bilibili.com/x/player/wbi/playurl"
API_NAV = "https://api.bilibili.com/x/web-interface/nav"
API_SPI = "https://api.bilibili.com/x/frontend/finger/spi"

_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_AV_RE = re.compile(r"(?<![0-9A-Za-z])[aA][vV](\d+)")
_SPACE_RE = re.compile(r"space\.bilibili\.com/(\d+)", re.I)
_MID_RE = re.compile(r"[?&]mid=(\d+)")
_QUALITY_NAMES = {
    16: "360P",
    32: "480P",
    64: "720P",
    74: "720P60",
    80: "1080P",
    112: "1080P+",
    116: "1080P60",
    120: "4K",
    125: "HDR",
    126: "杜比视界",
    127: "8K",
}
# codecid: 7=AVC(H.264) 12=HEVC(H.265) 13=AV1；同清晰度优先 AVC（兼容性最好、不黑屏）
_CODEC_RANK = {7: 3, 0: 2, 12: 1, 13: 0}
_CODEC_NAMES = {7: "AVC", 12: "HEVC", 13: "AV1"}

# WBI 签名混淆表（B 站前端固定表）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


class BiliApiError(RuntimeError):
    pass


# ==================== WBI 签名 ====================
def _mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def enc_wbi(params: dict[str, Any], img_key: str, sub_key: str) -> dict[str, Any]:
    """按 B 站 WBI 规则给参数加 wts / w_rid。"""
    import hashlib
    import urllib.parse

    mixin = _mixin_key(img_key + sub_key)
    payload = dict(params)
    payload["wts"] = int(time.time())
    payload = {
        key: "".join(ch for ch in str(value) if ch not in "!'()*")
        for key, value in sorted(payload.items())
    }
    query = urllib.parse.urlencode(payload)
    payload["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
    return payload


# ==================== 纯函数：输入解析 ====================
def extract_video_refs(text: str) -> list[str]:
    """从粘贴内容中提取 BV 号/av 号，去重并保持顺序。"""
    refs: list[str] = []
    for bv in _BV_RE.findall(text or ""):
        if bv not in refs:
            refs.append(bv)
    for av in _AV_RE.findall(text or ""):
        av_ref = f"av{av}"
        if av_ref not in refs:
            refs.append(av_ref)
    return refs


def parse_space_mid(text: str) -> int | None:
    space = _SPACE_RE.search(text or "")
    if space:
        return int(space.group(1))
    mid = _MID_RE.search(text or "")
    if mid:
        return int(mid.group(1))
    return None


def _normalize_space_input(text: str) -> int | None:
    """只填纯数字 UID 时也当作 UP 主空间。"""
    text = (text or "").strip()
    if text.isdigit():
        return int(text)
    return parse_space_mid(text)


def parse_space_input(text: str) -> int | None:
    """公开入口：把用户输入识别为 UP 主 mid（纯数字/空间链接/带 mid 参数）。"""
    return _normalize_space_input(text)


def _strip_risk_notice(message: str) -> str:
    return message or "未知错误"


def extract_staff(view: dict[str, Any]) -> tuple[str, str, list[str], bool]:
    """从 B 站 view 接口数据里提取合作信息。

    返回 (主 UP 名, 主 UP mid, 合作者标签列表, 是否联合投稿)。
    联合投稿的 view["staff"] 形如 [{"mid":..., "name":"字幕君GOUBA", "title":"UP主"},
                                   {"mid":..., "name":"碧诗", "title":"参演"}]。
    """
    owner = view.get("owner") if isinstance(view.get("owner"), dict) else {}
    owner_name = str(owner.get("name") or "").strip()
    owner_mid = str(owner.get("mid") or "")
    staff = view.get("staff")
    collaborators: list[str] = []
    if isinstance(staff, list):
        for member in staff:
            if not isinstance(member, dict):
                continue
            name = str(member.get("name") or "").strip()
            if not name:
                continue
            mid = str(member.get("mid") or "")
            if owner_mid and mid == owner_mid:
                continue
            if owner_name and name == owner_name:
                continue
            role = str(member.get("title") or "").strip()
            label = f"{name}（{role}）" if role and role != "UP主" else name
            if label not in collaborators:
                collaborators.append(label)
    is_union = bool(collaborators) or (isinstance(staff, list) and len(staff) > 1)
    return owner_name, owner_mid, collaborators, is_union


# ==================== API 客户端 ====================
class BiliClient:
    def __init__(
        self,
        cfg: AppConfig,
        cookies: str = "",
        logger: logging.Logger | None = None,
    ):
        self.cfg = cfg
        self.log = logger or logging.getLogger("media_crawler")
        self.cookies = cookies or cfg.bili_cookie or ""
        self.session = build_session(cfg.user_agent)
        self.session.headers.update({"Referer": "https://www.bilibili.com/"})
        self._load_cookies(self.cookies)
        self._wbi_lock = threading.Lock()
        self._wbi_keys: tuple[str, str] | None = None
        self._prepared = False

    # ---------- 会话准备 ----------
    def _load_cookies(self, cookie_str: str) -> None:
        if not cookie_str:
            return
        from http.cookies import SimpleCookie

        jar = SimpleCookie()
        try:
            jar.load(cookie_str)
        except Exception:
            jar = SimpleCookie()
        if not jar:
            # Cookie 串格式特殊时，直接作为请求头兜底
            self.session.headers["Cookie"] = cookie_str
            return
        for name, morsel in jar.items():
            self.session.cookies.set(name, morsel.value, domain=".bilibili.com")
            self.session.cookies.set(name, morsel.value, domain=".bilibili.com", path="/")

    def _prepare(self) -> None:
        """补 buvid 指纹 Cookie，降低被风控概率。"""
        if self._prepared:
            return
        self._prepared = True
        try:
            resp = self.session.get(API_SPI, timeout=15)
            data = (resp.json() or {}).get("data") or {}
            b3, b4 = data.get("b_3"), data.get("b_4")
            names = {c.name for c in self.session.cookies}
            if b3 and "buvid3" not in names:
                self.session.cookies.set("buvid3", b3, domain=".bilibili.com")
            if b4 and "buvid4" not in names:
                self.session.cookies.set("buvid4", b4, domain=".bilibili.com")
            for name, value in (("b_nut", str(int(time.time()))), ("CURRENT_FNVAL", "4048")):
                if name not in names:
                    self.session.cookies.set(name, value, domain=".bilibili.com")
        except Exception as exc:  # 失败不影响主流程
            self.log.debug("获取 B 站指纹 Cookie 失败: %s", exc)

    def _get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        wbi: bool = False,
        referer: str | None = None,
        timeout: int = 20,
    ) -> dict[str, Any]:
        self._prepare()
        query = dict(params or {})
        if wbi:
            img_key, sub_key = self._get_wbi_keys()
            query = enc_wbi(query, img_key, sub_key)
        headers = {"Referer": referer} if referer else None
        try:
            resp = self.session.get(url, params=query, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise BiliApiError(f"请求 B 站接口失败: {exc}") from exc

    def _get_wbi_keys(self) -> tuple[str, str]:
        with self._wbi_lock:
            if self._wbi_keys:
                return self._wbi_keys
            last_error = ""
            for _ in range(3):
                try:
                    payload = self._get_json(API_NAV, timeout=15)
                    wbi_img = ((payload.get("data") or {}).get("wbi_img") or {})
                    img_url = wbi_img.get("img_url") or ""
                    sub_url = wbi_img.get("sub_url") or ""
                    if img_url and sub_url:
                        img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
                        sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
                        self._wbi_keys = (img_key, sub_key)
                        return self._wbi_keys
                    last_error = str(payload.get("message") or "nav 接口无 wbi_img")
                except BiliApiError as exc:
                    last_error = str(exc)
                time.sleep(1.2)
            raise BiliApiError(f"无法获取 WBI 签名密钥: {last_error}")

    # ---------- 视频信息 ----------
    def view_info(self, bvid: str | None = None, aid: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if bvid:
            params["bvid"] = bvid
        elif aid:
            params["aid"] = aid
        else:
            raise ValueError("bvid 与 aid 至少提供一个")
        payload = self._get_json(API_VIEW, params, referer="https://www.bilibili.com/")
        if payload.get("code") != 0:
            raise BiliApiError(
                f"获取视频信息失败: {_strip_risk_notice(str(payload.get('message')))}"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BiliApiError("获取视频信息失败: 返回数据为空")
        return data

    def video_items(
        self,
        refs: list[str],
        progress: ProgressCb | None = None,
        *,
        expand_pages: bool = True,
    ) -> CrawlResult:
        """把 BV/av 号解析成可勾选的视频条目。

        expand_pages=True 时，多 P 视频会在列表阶段就拆成“每 P 一行”，
        用户可以直接勾选想下载的某几集，而不是整段视频一起下。
        """
        result = CrawlResult()
        total = len(refs)
        for index, ref in enumerate(refs):
            if progress is not None:
                progress(index, f"解析视频 {index + 1}/{total}…")
            try:
                if ref.lower().startswith("av"):
                    data = self.view_info(aid=int(ref[2:]))
                else:
                    data = self.view_info(bvid=ref)
                result.items.extend(self._view_to_items(data, expand_pages=expand_pages))
            except (BiliApiError, ValueError) as exc:
                result.warnings.append(f"{ref}: {exc}")
                self.log.warning("解析 %s 失败: %s", ref, exc)
            time.sleep(0.25)
        if progress is not None:
            progress(total, f"解析完成，共 {len(result.items)} 个可下载条目")
        return result

    # ---------- 视频信息 → 列表条目 ----------
    @staticmethod
    def _view_pages(view: dict[str, Any]) -> list[dict[str, Any]]:
        pages_raw = view.get("pages")
        pages: list[dict[str, Any]] = []
        if isinstance(pages_raw, list) and pages_raw:
            for page in pages_raw:
                cid = page.get("cid")
                if not cid:
                    continue
                pages.append(
                    {
                        "cid": int(cid),
                        "page": int(page.get("page") or len(pages) + 1),
                        "part": str(page.get("part") or "").strip(),
                        "duration": int(page.get("duration") or 0),
                    }
                )
        if not pages:
            cid = view.get("cid")
            if cid:
                pages = [
                    {
                        "cid": int(cid),
                        "page": 1,
                        "part": "",
                        "duration": int(view.get("duration") or 0),
                    }
                ]
        return pages

    @staticmethod
    def _common_view_fields(view: dict[str, Any]) -> dict[str, Any]:
        """view 接口里与分 P 无关的公共字段。"""
        bvid = str(view.get("bvid") or "").strip()
        owner = view.get("owner") if isinstance(view.get("owner"), dict) else {}
        owner_name, owner_mid, collaborators, is_union = extract_staff(view)
        stat = view.get("stat") if isinstance(view.get("stat"), dict) else {}
        pic = str(view.get("pic") or "")
        if pic.startswith("//"):
            pic = "https:" + pic
        return {
            "bvid": bvid,
            "aid": int(view.get("aid") or 0),
            "title": str(view.get("title") or f"视频_{bvid}").strip(),
            "author": str(owner.get("name") or "").strip(),
            "owner_mid": owner_mid,
            "collaborators": list(collaborators),
            "is_union": bool(is_union),
            "views": int(stat.get("view") or 0),
            "pubdate": int(view.get("pubdate") or 0),
            "pic": pic,
        }

    @classmethod
    def _view_to_items(
        cls, view: dict[str, Any], *, expand_pages: bool = True
    ) -> list[MediaItem]:
        """把一个 view 响应当成列表条目返回。

        - 多 P 且 expand_pages=True：每个分 P 一个条目（uid 带 cid）；
        - 其它情况：整段视频一个条目（uid 只有 BV 号，兼容旧记录）。
        """
        pages = cls._view_pages(view)
        if not pages:
            return []
        if not expand_pages or len(pages) <= 1:
            item = cls._view_to_item(view)
            return [item] if item is not None else []
        page_count = len(pages)
        return [cls._page_item(view, page, page_count) for page in pages]

    @classmethod
    def _page_item(
        cls, view: dict[str, Any], page: dict[str, Any], page_count: int
    ) -> MediaItem:
        common = cls._common_view_fields(view)
        bvid = common["bvid"]
        page_no = int(page.get("page") or 1)
        part = str(page.get("part") or "").strip()
        prefix = f"P{page_no} {part}".strip()
        # 分P 名放最前面：多P视频标题往往很长，列表里才能一眼看出是第几集
        title = f"{prefix} - {common['title']}" if prefix else common["title"]
        return MediaItem(
            platform="bilibili",
            uid=f"bilibili:{bvid}:{int(page['cid'])}",
            kind="video",
            title=title,
            author=common["author"],
            create_time=common["pubdate"],
            duration_ms=int(page.get("duration") or 0) * 1000,
            views=common["views"],
            cover_url=common["pic"],
            page_url=f"https://www.bilibili.com/video/{bvid}?p={page_no}",
            payload={
                "bvid": bvid,
                "aid": common["aid"],
                "cid": int(page["cid"]),
                "page": page_no,
                "page_count": page_count,
                "part": part,
                "video_title": common["title"],
                "pages": [page],
            },
            author_id=common["owner_mid"],
            collaborators=common["collaborators"],
            collab_flag=common["is_union"],
        )

    def expand_item(self, item: MediaItem) -> list[MediaItem]:
        """把列表里的“整段视频”条目展开成按分 P 的条目。"""
        if item.page_label:
            return [item]
        bvid = str(item.payload.get("bvid") or item.uid.split(":")[-1])
        view = self.view_info(bvid=bvid)
        items = self._view_to_items(view, expand_pages=True)
        if not items:
            raise BiliApiError("该视频没有可用的分P信息")
        return items

    @staticmethod
    def _view_to_item(view: dict[str, Any]) -> MediaItem | None:
        """整段视频条目（旧结构：uid 只带 BV 号，payload 里放全部分 P）。"""
        bvid = str(view.get("bvid") or "").strip()
        if not bvid:
            return None
        pages = BiliClient._view_pages(view)
        if not pages:
            return None

        title = str(view.get("title") or f"视频_{bvid}").strip()
        owner = view.get("owner") if isinstance(view.get("owner"), dict) else {}
        owner_name, owner_mid, collaborators, is_union = extract_staff(view)
        stat = view.get("stat") if isinstance(view.get("stat"), dict) else {}
        duration = sum(int(p.get("duration") or 0) for p in pages) or int(view.get("duration") or 0)
        display_title = title
        if len(pages) > 1:
            display_title = f"{title}（{len(pages)}P）"
        pic = str(view.get("pic") or "")
        if pic.startswith("//"):
            pic = "https:" + pic
        page_url = f"https://www.bilibili.com/video/{bvid}"
        if len(pages) > 1:
            page_url += "?p=1"
        return MediaItem(
            platform="bilibili",
            uid=f"bilibili:{bvid}",
            kind="video",
            title=display_title,
            author=str(owner.get("name") or "").strip(),
            create_time=int(view.get("pubdate") or 0),
            duration_ms=duration * 1000,
            views=int(stat.get("view") or 0),
            cover_url=pic,
            page_url=page_url,
            payload={
                "bvid": bvid,
                "aid": int(view.get("aid") or 0),
                "pages": pages,
            },
            author_id=owner_mid,
            collaborators=collaborators,
            collab_flag=is_union,
        )

    # ---------- UP 主空间列表 ----------
    def space_items(
        self,
        mid: int,
        progress: ProgressCb | None = None,
        cancel: threading.Event | None = None,
        max_count: int | None = None,
    ) -> CrawlResult:
        max_count = int(max_count or self.cfg.bili_max_count)
        result = CrawlResult()
        seen: set[str] = set()
        no_new = 0
        author = ""

        def report(message: str) -> None:
            if progress is not None:
                try:
                    progress(len(result.items), message)
                except Exception:
                    pass

        self.log.info("启动浏览器抓取 UP 主 %s 的投稿列表…", mid)
        with BrowserSession(headless=not self.cfg.show_browser) as page:
            try:
                page.listen.start("arc/search")
                report("正在打开 UP 主空间…")
                page.get(f"https://space.bilibili.com/{mid}/video")
                time.sleep(4.0)
                while no_new < 4:
                    if cancel is not None and cancel.is_set():
                        result.warnings.append("抓取被用户取消")
                        break
                    if len(result.items) >= max_count:
                        self.log.info("已达到抓取上限 %s 个视频", max_count)
                        break

                    response = page.listen.wait(timeout=8)
                    vlist, api_code = _parse_arc_response(
                        response.response.body if response else None
                    )
                    if vlist:
                        new_count = 0
                        for raw in vlist:
                            if len(result.items) >= max_count:
                                break
                            item = parse_space_item(raw, author)
                            if item is None or item.uid in seen:
                                continue
                            seen.add(item.uid)
                            result.items.append(item)
                            new_count += 1
                            if not author and item.author:
                                author = item.author
                        if new_count:
                            no_new = 0
                            self.log.info(
                                "UP 主 %s：新增 %s 个视频，累计 %s 个",
                                mid,
                                new_count,
                                len(result.items),
                            )
                            report(f"已抓取 {len(result.items)} 个视频")
                        else:
                            no_new += 1
                    else:
                        no_new += 1
                        if api_code == -352:
                            msg = "B 站风控拦截（-352），若列表抓不全请先在浏览器中登录 B 站。"
                            if msg not in result.warnings:
                                result.warnings.append(msg)
                                self.log.warning(msg)
                        else:
                            self.log.info("未监听到投稿数据（%s/4），继续滚动…", no_new)

                    if len(result.items) >= max_count:
                        break
                    _scroll_page(page, 1200)
                    time.sleep(2.0)

                # API 一直拿不到时用页面 DOM 兜底
                if not result.items:
                    report("接口未返回数据，尝试从页面元素提取…")
                    dom_items = _dom_items(page)
                    for item in dom_items:
                        if item.uid not in seen:
                            seen.add(item.uid)
                            result.items.append(item)
                    if result.items:
                        result.warnings.append("投稿列表来自页面元素解析，部分信息（日期/播放量）可能缺失。")

                if not author:
                    author = _up_name_from_page(page, mid)
                result.author = author
                result.cookies = _cookies(page)

                # 空间列表只带 is_union_video 标记，补全联合投稿的合作者名单
                union_items = [
                    item for item in result.items if item.collab_flag and not item.collaborators
                ]
                if union_items:
                    report(f"正在补全 {len(union_items)} 个联合投稿的合作者…")
                    for item in union_items[:40]:
                        if cancel is not None and cancel.is_set():
                            break
                        try:
                            view = self.view_info(
                                bvid=str(item.payload.get("bvid") or item.uid.split(":")[-1])
                            )
                            owner_name, owner_mid, collaborators, is_union = extract_staff(view)
                            if owner_name:
                                item.author = owner_name
                            if owner_mid:
                                item.author_id = owner_mid
                            item.collaborators = collaborators
                            item.collab_flag = bool(is_union)
                        except Exception as exc:
                            self.log.debug("补全合作者失败 %s: %s", item.uid, exc)
                        time.sleep(0.2)

                report(f"抓取完成，共 {len(result.items)} 个视频")
                self.log.info("UP 主空间抓取完成：%s 个视频", len(result.items))
                return result
            except BrowserError:
                raise
            except Exception as exc:
                self.log.exception("抓取 UP 主空间失败: %s", exc)
                raise RuntimeError(f"抓取 UP 主空间失败: {exc}") from exc

    # ---------- 取流 ----------
    def playurl(self, bvid: str, cid: int, qn: int) -> dict[str, Any]:
        """返回 {"type": "dash"|"durl", "video": [...], "audio": [...], "quality": str}。

        fnval=4048 才会返回 4K/1080P60/HDR/杜比/AV1 等全部 DASH 特性；
        只会返回账号权限内的清晰度（未登录最高 480P）。
        """
        attempts = [
            (True, 4048),  # WBI + DASH 全特性（4K/60fps/HDR/8K）
            (True, 16),    # 老版 DASH 兜底
            (False, 1),    # 普通 MP4 兜底
        ]
        errors: list[str] = []
        for use_wbi, fnval in attempts:
            params = {
                "bvid": bvid,
                "cid": cid,
                "qn": qn,
                "fnval": fnval,
                "fnver": 0,
                "fourk": 1,
            }
            try:
                if use_wbi:
                    payload = self._get_json(API_PLAYURL_WBI, params, wbi=True)
                else:
                    payload = self._get_json(API_PLAYURL, params)
            except BiliApiError as exc:
                errors.append(str(exc))
                continue
            if payload.get("code") != 0:
                errors.append(str(payload.get("message") or f"code={payload.get('code')}"))
                continue
            data = payload.get("data") or {}
            dash = data.get("dash") if isinstance(data.get("dash"), dict) else None
            if dash and not (fnval & 1):
                stream = self._pick_dash_streams(dash, qn)
                if stream:
                    return stream
            durl = data.get("durl")
            if isinstance(durl, list) and durl:
                urls: list[str] = []
                for segment in durl:
                    if isinstance(segment, dict) and segment.get("url"):
                        urls.append(segment["url"])
                if urls:
                    return {
                        "type": "durl",
                        "video": urls,
                        "audio": [],
                        "quality": _QUALITY_NAMES.get(int(data.get("quality") or 0), "MP4"),
                    }
                errors.append("durl 为空")
            else:
                errors.append("返回中没有可用的视频流")
        raise BiliApiError("取流失败：" + "；".join(dict.fromkeys(errors)))

    @staticmethod
    def _pick_dash_streams(dash: dict[str, Any], qn: int) -> dict[str, Any] | None:
        """在接口返回的 DASH 流里挑“不超过 qn 的最高清晰度”。

        qn 是上限（127 表示最高可用）；B 站只返回账号权限内的流，
        所以未登录时这里最多只能挑到 480P。
        """
        videos = [v for v in (dash.get("video") or []) if isinstance(v, dict)]
        audios = [a for a in (dash.get("audio") or []) if isinstance(a, dict)]
        if not videos:
            return None
        eligible = [v for v in videos if 0 < int(v.get("id") or 0) <= qn] or videos
        best = max(
            eligible,
            key=lambda v: (
                int(v.get("id") or 0),
                _CODEC_RANK.get(int(v.get("codecid") or 0), 0),
                int(v.get("bandwidth") or 0),
            ),
        )
        video_urls = _stream_urls(best)
        if not video_urls:
            return None
        audio_urls: list[str] = []
        if audios:
            best_audio = max(audios, key=lambda a: int(a.get("bandwidth") or 0))
            audio_urls = _stream_urls(best_audio)
        stream_id = int(best.get("id") or 0)
        quality = _QUALITY_NAMES.get(stream_id, f"qn{stream_id}")
        width = best.get("width")
        height = best.get("height")
        fps = best.get("frameRate") or best.get("frame_rate")
        detail = quality
        if width and height:
            detail = f"{quality}（{width}×{height}"
            if fps:
                detail += f"@{int(float(fps))}fps"
            detail += "）"
        return {
            "type": "dash",
            "id": stream_id,
            "codecid": int(best.get("codecid") or 0),
            "video": video_urls,
            "audio": audio_urls,
            "quality": detail,
        }

    # ---------- 下载 ----------
    def download_item(
        self,
        item: MediaItem,
        cfg: AppConfig,
        progress: ItemProgressCb | None = None,
        cancel: threading.Event | None = None,
    ) -> ItemOutcome:
        try:
            pages = self._resolve_pages(item)
        except BiliApiError as exc:
            return ItemOutcome(item.uid, item.title, False, error=str(exc))
        if not pages:
            return ItemOutcome(item.uid, item.title, False, error="没有可下载的分 P")

        bvid = str(item.payload.get("bvid") or item.uid.split(":")[-1])
        author = sanitize_name(item.author or "未知作者", 40)
        out_dir = cfg.platform_dir("bilibili") / author
        out_dir.mkdir(parents=True, exist_ok=True)

        # 列表阶段就展开成“单 P 条目”时，payload 里带 page_count（总分 P 数），
        # 用文件名仍保留 _P{n} 后缀，保持与整段下载一致、也避免各 P 互相覆盖。
        total_pages = int(item.payload.get("page_count") or 0) or len(pages)
        planned: list[tuple[dict[str, Any], Path]] = []
        for page in pages:
            filename = build_page_filename(item, page, total_pages)
            planned.append((page, out_dir / filename))

        files: list[str] = []
        errors: list[str] = []
        total_bytes = 0
        last_quality = ""
        for page_index, (page, dest) in enumerate(planned):
            if cancel is not None and cancel.is_set():
                errors.append("已取消")
                break
            # 勾选“跳过已下载”时，已存在的分 P 直接复用（支持中断后续传）
            if cfg.skip_downloaded and dest.exists() and dest.stat().st_size > 0:
                files.append(str(dest))
                continue

            def page_progress(fraction: float, pi: int = page_index) -> None:
                if progress is not None:
                    progress((pi + max(0.0, min(1.0, fraction))) / total_pages)

            self.log.info(
                "下载 B 站视频: %s%s",
                dest.name,
                f"（P{page['page']}）" if total_pages > 1 else "",
            )
            try:
                written, quality = self._download_page(
                    bvid=bvid,
                    cid=int(page["cid"]),
                    dest=dest,
                    cfg=cfg,
                    progress=page_progress,
                    cancel=cancel,
                )
                files.append(str(dest))
                total_bytes += written
                last_quality = quality
            except (BiliApiError, OSError) as exc:
                errors.append(f"P{page['page']}: {exc}")
                self.log.error("B 站视频下载失败 %s: %s", dest.name, exc)
            time.sleep(cfg.bili_download_delay)

        if files:
            if last_quality:
                self.log.info("实际下载清晰度：%s", last_quality)
            return ItemOutcome(item.uid, item.title, True, files, error="；".join(errors))
        return ItemOutcome(
            item.uid, item.title, False, error="；".join(errors) or "下载失败"
        )

    def _resolve_pages(self, item: MediaItem) -> list[dict[str, Any]]:
        pages = item.payload.get("pages")
        if isinstance(pages, list) and pages:
            return [p for p in pages if isinstance(p, dict) and p.get("cid")]
        bvid = str(item.payload.get("bvid") or item.uid.split(":")[-1])
        view = self.view_info(bvid=bvid)
        refreshed = self._view_to_item(view)
        if refreshed is None:
            return []
        item.payload["pages"] = refreshed.payload["pages"]
        item.author = item.author or refreshed.author
        item.author_id = item.author_id or refreshed.author_id
        if refreshed.collaborators:
            item.collaborators = refreshed.collaborators
        item.collab_flag = bool(item.collab_flag or refreshed.collab_flag)
        item.duration_ms = item.duration_ms or refreshed.duration_ms
        item.views = item.views or refreshed.views
        return refreshed.payload["pages"]

    def _download_page(
        self,
        *,
        bvid: str,
        cid: int,
        dest: Path,
        cfg: AppConfig,
        progress: ItemProgressCb | None,
        cancel: threading.Event | None,
    ) -> tuple[int, str]:
        stream = self.playurl(bvid, cid, int(cfg.bili_quality))
        headers = {
            "Referer": f"https://www.bilibili.com/video/{bvid}",
            "Origin": "https://www.bilibili.com",
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        quality = str(stream.get("quality") or "未知")
        self.log.info("清晰度: %s", quality)
        stream_id = int(stream.get("id") or 0)
        if stream_id and stream_id <= 32 and not self.cookies:
            self.log.warning(
                "当前未登录 B 站，接口最高只给 %s（网页版能看高清是因为浏览器已登录）。"
                "在 B 站标签页点“登录B站”后再下载即可拿到 1080P/1080P60/4K。",
                quality,
            )

        def scaled(start: float, span: float):
            def callback(done: int, total: int) -> None:
                if progress is not None:
                    progress(start + (done / total if total else 0) * span)
            return callback

        if stream["type"] == "durl":
            result = download_any(
                self.session,
                stream["video"],
                dest,
                headers=headers,
                progress=scaled(0.0, 1.0),
                cancel=cancel,
                timeout=cfg.request_timeout,
            )
            if not result.ok:
                raise BiliApiError(result.error)
            return result.bytes_written, quality

        # 注意：这里刻意不用 tempfile.mkdtemp（某些受限环境会给它生成无法写入的 ACL），
        # 改用应用自己的临时目录，并在 finally 中尽力清理。
        tmp_dir = cfg.temp_dir() / f"bili_{uuid.uuid4().hex[:12]}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        video_tmp = tmp_dir / "video.m4s"
        audio_tmp = tmp_dir / "audio.m4s"
        try:
            video_res = download_any(
                self.session,
                stream["video"],
                video_tmp,
                headers=headers,
                progress=scaled(0.0, 0.45),
                cancel=cancel,
                timeout=cfg.request_timeout,
            )
            if not video_res.ok:
                raise BiliApiError(f"视频流下载失败: {video_res.error}")
            audio_bytes = 0
            if stream["audio"]:
                audio_res = download_any(
                    self.session,
                    stream["audio"],
                    audio_tmp,
                    headers=headers,
                    progress=scaled(0.45, 0.30),
                    cancel=cancel,
                    timeout=cfg.request_timeout,
                )
                if not audio_res.ok:
                    raise BiliApiError(f"音频流下载失败: {audio_res.error}")
                audio_bytes = audio_res.bytes_written
            if progress is not None:
                progress(0.8)
            _merge_av(video_tmp, audio_tmp if stream["audio"] else None, dest)
            if progress is not None:
                progress(1.0)
            return video_res.bytes_written + audio_bytes, quality
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ==================== 采集辅助 ====================
def build_page_filename(item: MediaItem, page: dict[str, Any], page_count: int) -> str:
    """分 P 文件名：日期_标题_BV号[_P{n}].mp4（多 P 视频必须带 P 号，避免互相覆盖）。"""
    bvid = str(item.payload.get("bvid") or item.uid.split(":")[-1])
    # 分P 条目的 title 前面带了“P1 xxx - ”，文件名用原始视频标题；
    # 多 P 时补上“（94P）”，与旧版整段下载的文件名保持一致，升级后不会重复下载。
    raw_title = str(item.payload.get("video_title") or item.title)
    if page_count > 1:
        marker = f"（{page_count}P）"
        if marker not in raw_title:
            raw_title = f"{raw_title}{marker}"
    title = sanitize_name(strip_html(raw_title), 70)
    date_prefix = item.date_text if item.date_text != "-" else ""
    suffix = f"_P{page['page']}" if page_count > 1 else ""
    return sanitize_name(f"{date_prefix}_{title}_{bvid}{suffix}.mp4".lstrip("_"), 150)


def _parse_arc_response(body: Any) -> tuple[list[dict[str, Any]], int | None]:
    """从 UP 主空间监听到的 arc/search 响应里取 vlist。"""
    if isinstance(body, (bytes, bytearray)):
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            return [], None
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return [], None
    if not isinstance(body, dict):
        return [], None
    code = body.get("code")
    data = body.get("data")
    if not isinstance(data, dict):
        return [], code
    vlist = ((data.get("list") or {}).get("vlist")) if isinstance(data.get("list"), dict) else None
    if isinstance(vlist, list):
        return [v for v in vlist if isinstance(v, dict)], code
    return [], code


def parse_space_item(raw: dict[str, Any], fallback_author: str = "") -> MediaItem | None:
    bvid = str(raw.get("bvid") or "").strip()
    if not bvid:
        return None
    title = strip_html(str(raw.get("title") or "")).strip() or f"视频_{bvid}"
    pic = str(raw.get("pic") or "")
    if pic.startswith("//"):
        pic = "https:" + pic
    author = str(raw.get("author") or fallback_author or "").strip()
    author_id = str(raw.get("mid") or raw.get("author_mid") or "")
    is_union = bool(
        raw.get("is_union_video") or raw.get("is_cooperate") or raw.get("is_union")
    )
    return MediaItem(
        platform="bilibili",
        uid=f"bilibili:{bvid}",
        kind="video",
        title=title,
        author=author,
        create_time=int(raw.get("created") or 0),
        duration_ms=parse_length_text(str(raw.get("length") or "")),
        views=int(raw.get("play") or 0),
        cover_url=pic,
        page_url=f"https://www.bilibili.com/video/{bvid}",
        payload={"bvid": bvid, "aid": int(raw.get("aid") or 0), "pages": None},
        author_id=author_id,
        collab_flag=is_union,
    )


def _scroll_page(page, step: int) -> None:
    try:
        page.scroll.down(step)
    except Exception:
        pass
    time.sleep(0.3)
    try:
        page.run_js(
            f"""
            (function() {{
                window.scrollBy(0, {step});
                document.documentElement.scrollTop += {step};
                document.body.scrollTop += {step};
                window.dispatchEvent(new Event('scroll'));
            }})();
            """
        )
    except Exception:
        pass


def _up_name_from_page(page, mid: int) -> str:
    try:
        title = page.title or ""
        if "的个人空间" in title:
            name = title.split("的个人空间")[0].strip()
            if name:
                return name
    except Exception:
        pass
    return f"UP{mid}"


def _dom_items(page) -> list[MediaItem]:
    """接口被风控时的兜底：从页面上的视频链接提取 BV 号与标题。"""
    try:
        cards = page.run_js(
            """
            var out = [];
            var seen = {};
            document.querySelectorAll("a[href*='/video/BV']").forEach(function(a) {
                var m = (a.href || '').match(/BV[0-9A-Za-z]{10}/);
                if (!m) return;
                var bv = m[0];
                if (seen[bv]) return;
                seen[bv] = 1;
                var title = a.getAttribute('title') || a.innerText || '';
                out.push({bvid: bv, title: title.trim()});
            });
            return out;
            """
        )
    except Exception:
        return []
    items: list[MediaItem] = []
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        bvid = str(card.get("bvid") or "")
        if not bvid:
            continue
        title = strip_html(str(card.get("title") or "")).strip() or f"视频_{bvid}"
        items.append(
            MediaItem(
                platform="bilibili",
                uid=f"bilibili:{bvid}",
                kind="video",
                title=title,
                page_url=f"https://www.bilibili.com/video/{bvid}",
                payload={"bvid": bvid, "aid": 0, "pages": None},
            )
        )
    return items


def has_login_cookie(cookie_str: str) -> bool:
    """Cookie 串里是否带有效的 SESSDATA（B 站登录标志）。"""
    for part in (cookie_str or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name.strip() == "SESSDATA" and value.strip():
            return True
    return False


def fetch_login_cookie(
    cfg: AppConfig,
    logger: logging.Logger | None = None,
    timeout: float = 180.0,
) -> str:
    """打开浏览器让用户扫码登录 B 站，返回导出的 Cookie 串（失败返回空串）。

    未登录时 B 站 playurl 只给 360P/480P，登录后才有 1080P/1080P60/4K。
    """
    log = logger or logging.getLogger("media_crawler")
    with BrowserSession(headless=False) as page:
        log.info("已打开浏览器，请在页面中登录 B 站（最多等待 %d 秒）…", int(timeout))
        page.get("https://www.bilibili.com/")
        deadline = time.time() + max(20.0, timeout)
        while time.time() < deadline:
            time.sleep(2.0)
            cookie_str = _cookies(page)
            if has_login_cookie(cookie_str):
                log.info("检测到 B 站登录态，已导出 Cookie")
                return cookie_str
        log.warning("等待登录超时，没有拿到 SESSDATA")
        return ""


def _cookies(page) -> str:
    try:
        return page.cookies(all_domains=True).as_str()
    except Exception:
        return ""


# ==================== 合并 ====================
def _stream_urls(stream: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("baseUrl", "base_url"):
        value = stream.get(key)
        if isinstance(value, str) and value:
            urls.append(value)
    for key in ("backupUrl", "backup_url"):
        value = stream.get(key)
        if isinstance(value, list):
            urls.extend(u for u in value if isinstance(u, str) and u)
    return list(dict.fromkeys(urls))


def find_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and Path(path).exists():
            return path
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _merge_av(video: Path, audio: Path | None, output: Path) -> None:
    """优先用 ffmpeg -c copy 无损合并；失败再退回 moviepy 重编码。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        command = [ffmpeg, "-y", "-loglevel", "error", "-i", str(video)]
        if audio is not None:
            command += ["-i", str(audio)]
        command += ["-c", "copy", "-movflags", "+faststart", str(output)]
        kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": 900,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.run(command, **kwargs)
            if proc.returncode == 0 and output.exists() and output.stat().st_size > 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass

    _merge_with_moviepy(video, audio, output)


def _merge_with_moviepy(video: Path, audio: Path | None, output: Path) -> None:
    try:
        from moviepy.editor import AudioFileClip, VideoFileClip
    except Exception as exc:  # pragma: no cover - 依赖缺失
        raise BiliApiError(
            "合并失败：未找到可用的 ffmpeg，且 moviepy 不可用。请安装 ffmpeg 或 moviepy。"
        ) from exc
    clip = None
    audio_clip = None
    try:
        clip = VideoFileClip(str(video))
        if audio is not None:
            audio_clip = AudioFileClip(str(audio))
            clip = clip.set_audio(audio_clip)
        clip.write_videofile(
            str(output),
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=str(output.with_suffix(".temp-audio.m4a")),
            remove_temp=True,
            logger=None,
        )
    except Exception as exc:
        raise BiliApiError(f"合并音视频失败: {exc}") from exc
    finally:
        for obj in (audio_clip, clip):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
