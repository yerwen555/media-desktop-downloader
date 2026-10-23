# -*- coding: utf-8 -*-
"""HTTP 下载基础设施：连接池、重试、流式下载、断点临时文件。"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import requests
from requests.adapters import HTTPAdapter, Retry

ProgressCallback = Callable[[int, int], None]
_CHUNK = 256 * 1024


@dataclass
class DownloadResult:
    ok: bool
    path: str = ""
    bytes_written: int = 0
    error: str = ""


def build_session(user_agent: str, cookie: str = "") -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
    )
    if cookie:
        session.headers["Cookie"] = cookie
    retry = Retry(
        total=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD", "OPTIONS"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=32, max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def download_file(
    session: requests.Session,
    url: str,
    dest: Path | str,
    *,
    headers: dict[str, str] | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    timeout: int = 30,
    retries: int = 3,
    chunk_size: int = _CHUNK,
) -> DownloadResult:
    """流式下载单个 URL 到 DEST。先写 .part，成功后再原子改名。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    last_error = ""
    for attempt in range(1, max(1, retries) + 1):
        if cancel is not None and cancel.is_set():
            _cleanup(part)
            return DownloadResult(False, error="已取消")
        try:
            with session.get(
                url, headers=headers, stream=True, timeout=timeout
            ) as resp:
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    raise requests.HTTPError(last_error, response=resp)
                total = int(resp.headers.get("content-length") or 0)
                written = 0
                with open(part, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if cancel is not None and cancel.is_set():
                            raise _Cancelled()
                        if not chunk:
                            continue
                        fh.write(chunk)
                        written += len(chunk)
                        if progress is not None:
                            try:
                                progress(written, total)
                            except Exception:
                                pass
            if part.stat().st_size == 0:
                last_error = "下载内容为空"
                _cleanup(part)
                continue
            os.replace(part, dest)
            return DownloadResult(True, str(dest), written)
        except _Cancelled:
            _cleanup(part)
            return DownloadResult(False, error="已取消")
        except (requests.RequestException, OSError) as exc:
            last_error = str(exc) or exc.__class__.__name__
            _cleanup(part)
            if attempt < retries:
                wait = min(2 ** attempt, 8)
                if cancel is not None:
                    if cancel.wait(wait):
                        return DownloadResult(False, error="已取消")
                else:
                    time.sleep(wait)
    return DownloadResult(False, error=last_error or "下载失败")


def download_any(
    session: requests.Session,
    urls: Iterable[str],
    dest: Path | str,
    **kwargs,
) -> DownloadResult:
    """依次尝试多个备用 URL（抖音 CDN 常有多条）。"""
    error = ""
    for url in urls:
        if not url:
            continue
        result = download_file(session, url, dest, **kwargs)
        if result.ok:
            return result
        error = result.error
        if error == "已取消":
            return result
    return DownloadResult(False, error=error or "没有可用链接")


class _Cancelled(Exception):
    pass


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
