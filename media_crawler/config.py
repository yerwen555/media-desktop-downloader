# -*- coding: utf-8 -*-
"""应用配置：默认值、config.json 持久化、运行目录管理。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

APP_TITLE = "抖音 / B站 视频下载助手"
APP_VERSION = "2.4"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DOWNLOAD_DIR = PROJECT_ROOT / "downloads"
LOGS_DIR = DATA_DIR / "logs"
TMP_DIR = DATA_DIR / "tmp"
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"
DB_PATH = DATA_DIR / "media.db"
CONFIG_PATH = PROJECT_ROOT / "config.json"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


QUALITY_TEXT = {
    127: "最高可用（4K/1080P60 自动降级）",
    126: "杜比视界",
    125: "HDR",
    120: "4K",
    116: "1080P60",
    112: "1080P+",
    80: "1080P",
    74: "720P60",
    64: "720P",
    32: "480P",
    16: "360P",
}


@dataclass
class AppConfig:
    """用户可调整的全部配置。字段名会原样写进 config.json。"""

    # ---- 通用 ----
    download_dir: str = str(DOWNLOAD_DIR)
    max_workers: int = 4
    request_timeout: int = 30
    skip_downloaded: bool = True
    show_browser: bool = True  # 抓取列表时显示浏览器窗口（首次登录需要）

    # ---- 抖音 ----
    douyin_max_count: int = 600
    douyin_scroll_wait: float = 4.0
    douyin_max_empty: int = 3
    douyin_download_delay: float = 0.5
    douyin_cookie: str = ""  # 可选：手动 Cookie，留空则自动读取浏览器登录态

    # ---- B 站 ----
    bili_max_count: int = 600
    # 清晰度“上限”：127=最高可用（4K/1080P60/DASH 全特性），120=4K，116=1080P60，
    # 112=1080P+，80=1080P，74=720P60，64=720P，32=480P；未登录时 B 站只会给到 480P。
    bili_quality: int = 127
    bili_download_delay: float = 0.5
    bili_cookie: str = ""  # 可选：手动 Cookie，填了可下载更高清晰度

    # ---- 高级 ----
    user_agent: str = DEFAULT_USER_AGENT

    # ---------- 持久化 ----------
    @classmethod
    def load(cls, path: Path | str = CONFIG_PATH) -> "AppConfig":
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        cfg = cls()
        for key, value in raw.items():
            if not hasattr(cfg, key):
                continue
            default = getattr(cfg, key)
            try:
                if isinstance(default, bool):
                    setattr(cfg, key, bool(value))
                elif isinstance(default, int):
                    setattr(cfg, key, int(value))
                elif isinstance(default, float):
                    setattr(cfg, key, float(value))
                elif isinstance(default, str):
                    setattr(cfg, key, str(value))
            except (TypeError, ValueError):
                continue
        # 保存目录支持相对路径：这样整个文件夹搬到别处也不会失效
        cfg.download_dir = _resolve_download_dir(cfg.download_dir)
        return cfg

    def save(self, path: Path | str = CONFIG_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["download_dir"] = _portable_download_dir(self.download_dir)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---------- 路径 ----------
    def ensure_dirs(self) -> None:
        # 程序数据目录必须可用（项目内）
        for directory in (DATA_DIR, LOGS_DIR, TMP_DIR):
            directory.mkdir(parents=True, exist_ok=True)

        # 下载目录可能在移动硬盘/网络盘上：不可用时临时回退，保证程序能启动
        previous_warning = getattr(self, "dir_warning", "")
        self.dir_warning = previous_warning
        try:
            Path(self.download_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            original = self.download_dir
            try:
                DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise exc
            self.download_dir = str(DOWNLOAD_DIR)
            self.dir_warning = (
                f"保存目录不可用：{original}（{exc}）。"
                f"本次已临时改用项目内的 {DOWNLOAD_DIR}；"
                "插回磁盘/恢复网络盘后重新启动即可恢复，也可以到“设置”里修改保存目录。"
            )

    def platform_dir(self, platform: str) -> Path:
        name = "抖音" if platform == "douyin" else "B站"
        return Path(self.download_dir) / name

    def quality_text(self) -> str:
        return QUALITY_TEXT.get(int(self.bili_quality), f"qn={self.bili_quality}")

    def has_bili_login(self) -> bool:
        """配置里是否已经存了可用的 B 站登录 Cookie（未登录最高只有 480P）。"""
        for part in (self.bili_cookie or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name.strip() == "SESSDATA" and value.strip():
                return True
        return False

    def temp_dir(self) -> Path:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        return TMP_DIR

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolve_download_dir(value: str) -> str:
    """配置文件里的相对保存目录 → 基于项目根目录的绝对路径。"""
    try:
        path = Path(value).expanduser()
    except (OSError, ValueError):
        return str(DOWNLOAD_DIR)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)


def _portable_download_dir(value: str) -> str:
    """项目内目录写成相对路径，项目外目录保持绝对路径。"""
    try:
        path = Path(value).expanduser().resolve()
    except (OSError, ValueError):
        return str(DOWNLOAD_DIR)
    try:
        relative = path.relative_to(PROJECT_ROOT)
    except ValueError:
        return str(path)
    return str(relative) if relative.parts else "."


def config_summary(cfg: AppConfig) -> str:
    """供界面/命令行展示的一行配置摘要。"""
    return (
        f"保存目录: {cfg.download_dir} | 并发: {cfg.max_workers} | "
        f"B站清晰度: {cfg.quality_text()} | 跳过已下载: {'是' if cfg.skip_downloaded else '否'}"
    )
