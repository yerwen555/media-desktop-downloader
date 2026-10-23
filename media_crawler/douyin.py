# -*- coding: utf-8 -*-
"""抖音：浏览器滚动采集作品列表 + 下载视频/图集。

核心采集逻辑沿用原 all_douyin_ai.py（防检测、多段滚动、监听 aweme/post），
新增：
  - 采集结果返回结构化 MediaItem，供界面勾选；
  - 从浏览器导出登录 Cookie 供 CDN 下载，无需手动粘贴；
  - 支持作品链接（视频/图文）直接解析。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Callable

from .browser import BrowserError, BrowserSession
from .config import AppConfig
from .models import CrawlResult, ItemOutcome, MediaItem
from .net import download_any
from .utils import format_bytes, sanitize_name, strip_html

ProgressCb = Callable[[int, str], None]
ItemProgressCb = Callable[[float], None]

_ANTIDETECT_JS = """
(function() {
    try {
        Object.defineProperty(document, 'hidden', {
            get: function() { return false; },
            configurable: false
        });
    } catch(e) {}
    try {
        Object.defineProperty(document, 'visibilityState', {
            get: function() { return 'visible'; },
            configurable: false
        });
    } catch(e) {}
    var origAddEventListener = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function(type, listener, options) {
        if (type === 'visibilitychange' || type === 'blur' || type === 'focusout'
            || type === 'pagehide' || type === 'freeze') {
            return;
        }
        return origAddEventListener.call(this, type, listener, options);
    };
    window.onblur = null;
    window.onfocus = null;
    window.onpagehide = null;
    window.onfreeze = null;
    document.hasFocus = function() { return true; };
    if (!window._antiDetectInterval) {
        window._antiDetectInterval = setInterval(function() {
            window.dispatchEvent(new Event('focus'));
            document.dispatchEvent(new Event('focus'));
        }, 3000);
    }
})();
"""

_WORK_URL = re.compile(r"(?:douyin\.com/(?:video|note|share/video|share/note)/)", re.I)


# ==================== 纯函数：解析（便于单测） ====================
def classify_content(item: dict[str, Any]) -> str | None:
    """返回 'video' | 'image' | None。"""
    images = item.get("images")
    if isinstance(images, list) and images:
        return "image"
    video = item.get("video")
    if isinstance(video, dict) and video.get("play_addr"):
        return "video"
    return None


def _urls_of(container: Any) -> list[str]:
    if not isinstance(container, dict):
        return []
    urls = container.get("url_list") or []
    return [u for u in urls if isinstance(u, str) and u]


def _collect_video_urls(video: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    urls.extend(_urls_of(video.get("play_addr")))
    # bit_rate 里通常有更高清的地址，按码率从高到低补充
    bit_rates = video.get("bit_rate")
    if isinstance(bit_rates, list):
        for entry in sorted(
            (b for b in bit_rates if isinstance(b, dict)),
            key=lambda b: b.get("bit_rate") or 0,
            reverse=True,
        ):
            urls.extend(_urls_of(entry.get("play_addr")))
    # 去重，保持顺序
    return list(dict.fromkeys(urls))


def _cover_url(item: dict[str, Any]) -> str:
    video = item.get("video")
    if isinstance(video, dict):
        for key in ("cover", "origin_cover", "dynamic_cover"):
            urls = _urls_of(video.get(key))
            if urls:
                return urls[0]
    images = item.get("images")
    if isinstance(images, list):
        for image in images:
            urls = _urls_of(image)
            if urls:
                return urls[0]
    return ""


def extract_title(item: dict[str, Any], aweme_id: str = "") -> str:
    """尽量从 aweme JSON 里提取一个可读标题。

    抖音有些作品 desc 为空（纯视频、被清理的文案等），依次尝试备用字段：
    preview_title / caption / item_title / seo_info（OCR 文本）/ 话题标签。
    """
    def clean(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return re.sub(r"\s+", " ", value).strip()

    desc = clean(item.get("desc"))
    if desc:
        return desc
    for key in ("preview_title", "caption", "item_title", "title"):
        value = clean(item.get(key))
        if value:
            return value[:80]
    seo_info = item.get("seo_info")
    if isinstance(seo_info, dict):
        for key in ("ocr_content", "title"):
            value = clean(seo_info.get(key))
            if value:
                return value[:80]
    text_extra = item.get("text_extra")
    if isinstance(text_extra, list):
        tags: list[str] = []
        for entry in text_extra:
            if not isinstance(entry, dict):
                continue
            tag = clean(entry.get("hashtag_name"))
            if tag and tag not in tags:
                tags.append(tag)
        if tags:
            return " ".join("#" + tag for tag in tags[:3])[:80]
    return f"无标题作品_{aweme_id}" if aweme_id else "无标题作品"


_COLLAB_FIELDS = (
    "co_creator_list",
    "co_creator",
    "co_creators",
    "co_author_list",
    "co_authors",
    "cooperate_list",
    "cooperation_list",
    "collaborator_list",
    "collaborators",
    "cooperators",
    "contribute_list",
    "author_list",
    "collab_list",
)


def _collect_nicknames(value: Any, output: list[str]) -> None:
    """从合作作者字段里递归收集昵称（兼容 dict/list/嵌套 author）。"""
    if isinstance(value, dict):
        for key in ("nickname", "name", "user_name"):
            name = value.get(key)
            if isinstance(name, str) and name.strip():
                output.append(name.strip())
                break
        for key in ("author", "user", "user_info", "creator", "co_creator"):
            if key in value:
                _collect_nicknames(value[key], output)
    elif isinstance(value, list):
        for entry in value:
            _collect_nicknames(entry, output)


def extract_collaborators(item: dict[str, Any], main_author: str = "") -> list[str]:
    """尽量提取抖音“共创/合作”作者昵称；接口不提供时返回空列表。"""
    names: list[str] = []
    for field in _COLLAB_FIELDS:
        if field in item:
            _collect_nicknames(item[field], names)
    video = item.get("video")
    if isinstance(video, dict):
        for field in _COLLAB_FIELDS:
            if field in video:
                _collect_nicknames(video[field], names)
    result: list[str] = []
    for name in names:
        if not name or name == main_author or name in result:
            continue
        result.append(name)
    return result[:8]


def parse_aweme(item: dict[str, Any]) -> MediaItem | None:
    """把一条抖音 aweme JSON 解析成 MediaItem；不认识的类型返回 None。"""
    if not isinstance(item, dict):
        return None
    aweme_id = str(item.get("aweme_id") or "").strip()
    if not aweme_id:
        return None
    kind = classify_content(item)
    if kind is None:
        return None
    title = extract_title(item, aweme_id)
    author = ""
    author_id = ""
    author_obj = item.get("author")
    if isinstance(author_obj, dict):
        author = (author_obj.get("nickname") or "").strip()
        for key in ("uid", "mid", "sec_uid", "short_id"):
            value = author_obj.get(key)
            if value:
                author_id = str(value)
                break
    collaborators = extract_collaborators(item, author)
    statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    likes = int(statistics.get("digg_count") or 0)
    create_time = int(item.get("create_time") or 0)
    cover = _cover_url(item)
    page_url = f"https://www.douyin.com/{'note' if kind == 'image' else 'video'}/{aweme_id}"

    if kind == "video":
        video = item.get("video") or {}
        urls = _collect_video_urls(video)
        if not urls:
            return None
        return MediaItem(
            platform="douyin",
            uid=f"douyin:{aweme_id}",
            kind="video",
            title=title,
            author=author,
            create_time=create_time,
            duration_ms=int(video.get("duration") or 0),
            likes=likes,
            cover_url=cover,
            page_url=page_url,
            payload={"aweme_id": aweme_id, "urls": urls},
            author_id=author_id,
            collaborators=collaborators,
            collab_flag=bool(collaborators),
        )

    images = item.get("images") or []
    image_urls: list[list[str]] = []
    for image in images:
        if isinstance(image, dict):
            urls = _urls_of(image)
            if not urls:
                # 部分图集把下载地址放在 download_url_list
                urls = _urls_of({"url_list": image.get("download_url_list")})
            if urls:
                image_urls.append(urls)
    if not image_urls:
        return None
    return MediaItem(
        platform="douyin",
        uid=f"douyin:{aweme_id}",
        kind="image",
        title=title,
        author=author,
        create_time=create_time,
        image_count=len(image_urls),
        likes=likes,
        cover_url=cover,
        page_url=page_url,
        payload={"aweme_id": aweme_id, "image_urls": image_urls},
        author_id=author_id,
        collaborators=collaborators,
        collab_flag=bool(collaborators),
    )


def extract_aweme_list(body: Any) -> list[dict[str, Any]]:
    """从监听到的响应体里取出作品列表（列表接口 / 详情接口）。"""
    if isinstance(body, (bytes, bytearray)):
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            return []
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return []
    if not isinstance(body, dict):
        return []
    aweme_list = body.get("aweme_list")
    if isinstance(aweme_list, list):
        return [item for item in aweme_list if isinstance(item, dict)]
    detail = body.get("aweme_detail")
    if isinstance(detail, dict):
        return [detail]
    details = body.get("aweme_details")
    if isinstance(details, list):
        return [item for item in details if isinstance(item, dict)]
    return []


# ==================== 采集器 ====================
class DouyinCrawler:
    def __init__(self, cfg: AppConfig, logger: logging.Logger | None = None):
        self.cfg = cfg
        self.log = logger or logging.getLogger("media_crawler")

    # ---------- 浏览器操作 ----------
    @staticmethod
    def _inject_antidetect(page) -> None:
        try:
            page.run_js(_ANTIDETECT_JS)
        except Exception:
            pass

    @staticmethod
    def _scroll(page, step: int) -> None:
        try:
            page.scroll.down(step)
        except Exception:
            pass
        time.sleep(0.5)
        try:
            page.run_js(
                f"""
                (function() {{
                    var step = {step};
                    var targets = [document, window, document.scrollingElement,
                                   document.documentElement, document.body];
                    var el = document.elementFromPoint(window.innerWidth/2, window.innerHeight/2);
                    while (el) {{ targets.push(el); el = el.parentElement; }}
                    var seen = [];
                    targets.forEach(function(t) {{
                        if (t && seen.indexOf(t) === -1) seen.push(t);
                    }});
                    seen.forEach(function(t) {{
                        try {{
                            if (t.scrollBy) t.scrollBy({{top: step, behavior: 'smooth'}});
                            if (t.scrollTop !== undefined) t.scrollTop += step;
                        }} catch(e) {{}}
                        try {{
                            t.dispatchEvent(new WheelEvent('wheel', {{
                                deltaY: step, deltaMode: 0, bubbles: true, cancelable: true,
                                clientX: window.innerWidth/2, clientY: window.innerHeight/2
                            }}));
                        }} catch(e) {{}}
                    }});
                    window.dispatchEvent(new Event('scroll'));
                }})();
                """
            )
        except Exception:
            pass

    def _multi_scroll(self) -> None:
        self._scroll(self.page, 900)
        time.sleep(2.0)
        self._scroll(self.page, 550)
        time.sleep(1.5)
        self._scroll(self.page, 300)
        time.sleep(1.2)

    # ---------- 主流程 ----------
    def collect(
        self,
        url: str,
        progress: ProgressCb | None = None,
        cancel: threading.Event | None = None,
    ) -> CrawlResult:
        url = (url or "").strip()
        if not url:
            raise ValueError("请先填入抖音用户主页或作品链接")
        if "douyin.com" not in url and not url.startswith("http"):
            raise ValueError("链接格式不对，请粘贴抖音网页版链接（douyin.com）")

        result = CrawlResult()
        seen: set[str] = set()
        no_new = 0
        login_warned = False
        is_work_link = bool(_WORK_URL.search(url)) or "v.douyin.com" in url
        max_count = max(1, int(self.cfg.douyin_max_count))

        def report(done: int, message: str) -> None:
            if progress is not None:
                try:
                    progress(done, message)
                except Exception:
                    pass

        self.log.info("启动浏览器，准备采集抖音%s…", "作品" if is_work_link else "主页作品")
        with BrowserSession(
            headless=not self.cfg.show_browser,
        ) as page:
            self.page = page
            try:
                page.listen.start(["aweme/post/", "aweme/detail/"])
                report(0, "正在打开页面…")
                page.get(url)
                self._inject_antidetect(page)
                time.sleep(5.0)
                report(len(result.items), "页面已打开，开始滚动加载…")

                while no_new < max(1, int(self.cfg.douyin_max_empty)):
                    if cancel is not None and cancel.is_set():
                        self.log.info("已取消采集")
                        result.warnings.append("采集被用户取消")
                        break
                    if len(result.items) >= max_count:
                        self.log.info("已达到抓取上限 %s 个作品，停止滚动", max_count)
                        break

                    response = page.listen.wait(timeout=10)
                    body = response.response.body if response else None
                    aweme_list = extract_aweme_list(body)
                    if isinstance(body, dict) and (
                        body.get("aweme_detail") or body.get("aweme_details")
                    ):
                        is_work_link = True
                    if aweme_list:
                        new_count = 0
                        for raw in aweme_list:
                            if len(result.items) >= max_count:
                                break
                            item = parse_aweme(raw)
                            if item is None or item.uid in seen:
                                continue
                            seen.add(item.uid)
                            result.items.append(item)
                            new_count += 1
                            if not result.author:
                                result.author = item.author
                        if new_count:
                            no_new = 0
                            self.log.info(
                                "新增 %s 个作品，累计 %s 个", new_count, len(result.items)
                            )
                            report(len(result.items), f"已抓取 {len(result.items)} 个作品")
                        else:
                            no_new += 1
                    else:
                        no_new += 1
                        self.log.info(
                            "本轮未获取到新数据（%s/%s），继续滚动…",
                            no_new,
                            self.cfg.douyin_max_empty,
                        )

                    if (
                        len(result.items) <= 20
                        and no_new >= 3
                        and not login_warned
                        and not is_work_link
                    ):
                        msg = (
                            "只抓到少量作品，通常是未登录导致。若浏览器中未登录抖音，"
                            "请扫码登录后重新抓取。"
                        )
                        result.warnings.append(msg)
                        self.log.warning(msg)
                        login_warned = True

                    if len(result.items) >= max_count:
                        break
                    if is_work_link and result.items:
                        break

                    self._inject_antidetect(page)
                    self._multi_scroll()
                    time.sleep(self.cfg.douyin_scroll_wait)

                # 采集结束后再导出一次 Cookie（用户可能在过程中扫码登录）
                result.cookies = self._cookies(page)
                if not result.author and result.items:
                    result.author = result.items[0].author
                report(len(result.items), f"采集完成，共 {len(result.items)} 个作品")
                self.log.info("抖音采集完成：%s 个作品", len(result.items))
                return result
            except BrowserError:
                raise
            except Exception as exc:
                self.log.exception("抖音采集失败: %s", exc)
                raise RuntimeError(f"抖音采集失败: {exc}") from exc

    @staticmethod
    def _cookies(page) -> str:
        try:
            return page.cookies(all_domains=True).as_str()
        except Exception:
            return ""

    # ---------- 下载 ----------
    def download_item(
        self,
        item: MediaItem,
        session,
        cfg: AppConfig,
        progress: ItemProgressCb | None = None,
        cancel: threading.Event | None = None,
    ) -> ItemOutcome:
        """下载一个抖音作品（视频或图集）。"""
        base_dir = cfg.platform_dir("douyin") / sanitize_name(item.author_safe, 40)
        date_prefix = item.date_text if item.date_text != "-" else ""
        title = sanitize_name(strip_html(item.title), 60)
        aweme_id = str(item.payload.get("aweme_id") or item.uid.split(":")[-1])
        headers = {
            "Referer": "https://www.douyin.com/",
            "Origin": "https://www.douyin.com",
        }

        if item.kind == "video":
            target_dir = base_dir / "视频"
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{date_prefix}_{title}_{aweme_id[-8:]}.mp4".lstrip("_")
            dest = target_dir / filename
            urls = item.payload.get("urls") or []
            if not urls:
                return ItemOutcome(item.uid, item.title, False, error="没有可用的视频地址")
            self.log.info("下载视频: %s", filename)

            def on_progress(done: int, total: int) -> None:
                if progress is not None:
                    progress(done / total if total else 0.0)

            res = download_any(
                session,
                urls,
                dest,
                headers=headers,
                progress=on_progress,
                cancel=cancel,
                timeout=cfg.request_timeout,
            )
            if not res.ok:
                return ItemOutcome(item.uid, item.title, False, error=res.error)
            self.log.info("视频完成: %s (%s)", dest.name, format_bytes(res.bytes_written))
            return ItemOutcome(item.uid, item.title, True, [str(dest)])

        # 图集
        target_dir = base_dir / "图集" / sanitize_name(
            f"{date_prefix}_{title}_{aweme_id[-8:]}".lstrip("_"), 80
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        image_urls: list[list[str]] = item.payload.get("image_urls") or []
        files: list[str] = []
        written_total = 0
        for index, urls in enumerate(image_urls):
            if cancel is not None and cancel.is_set():
                break
            dest = target_dir / f"{index + 1:02d}.jpg"
            if cfg.skip_downloaded and dest.exists() and dest.stat().st_size > 0:
                files.append(str(dest))
                continue

            def on_progress(done: int, total: int, idx=index) -> None:
                if progress is not None:
                    base = idx / max(1, len(image_urls))
                    progress(base + (done / total if total else 0.0) / max(1, len(image_urls)))

            res = download_any(
                session,
                urls,
                dest,
                headers=headers,
                progress=on_progress,
                cancel=cancel,
                timeout=cfg.request_timeout,
            )
            if res.ok:
                files.append(str(dest))
                written_total += res.bytes_written
                time.sleep(cfg.douyin_download_delay)
            else:
                self.log.warning("图集第 %s 张下载失败: %s", index + 1, res.error)

        if not files:
            return ItemOutcome(item.uid, item.title, False, error="图集下载失败")
        self.log.info(
            "图集完成: %s (%s 张, %s)",
            target_dir.name,
            len(files),
            format_bytes(written_total),
        )
        return ItemOutcome(item.uid, item.title, True, files)
