"""配置读写：热键、悬停时长、气泡外观、翻译源。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _app_dir() -> Path:
    """确定配置和缓存该放在哪。

    打包成 exe 之后 __file__ 指向 PyInstaller 的临时解压目录（_MEIxxxx），
    那个目录退出即删。用它会导致配置和缓存每次都丢，
    所以冻结状态下改用 exe 自己所在的目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = _app_dir()
CONFIG_PATH = BASE_DIR / "config.json"
CACHE_PATH = BASE_DIR / "cache.sqlite3"


@dataclass
class Config:
    # 触发
    hover_seconds: float = 1.0          # 鼠标停住多久触发翻译
    poll_interval: float = 0.08         # 鼠标位置轮询间隔（秒）
    move_tolerance: int = 4             # 位移小于该像素数视为"没动"
    min_text_length: int = 2            # 少于这么多字符不翻译（避免误触）
    cooldown_seconds: float = 1.5       # 触发一次后歇多久再重新判定

    # 取词
    use_uia: bool = True                # 优先用 UI Automation 取词
    use_ocr: bool = True                # UIA 取不到时用 OCR 兜底
    ocr_padding: int = 14               # OCR 截图在鼠标周围扩多少像素
    ocr_width: int = 420                # OCR 截图宽度
    ocr_height: int = 90                # OCR 截图高度

    # 翻译
    source_lang: str = "en"
    target_lang: str = "zh-CN"
    providers: list[str] = field(default_factory=lambda: ["iciba", "youdao", "bing", "mymemory"])
    timeout: float = 6.0

    # 气泡外观
    font_size: int = 13
    opacity: float = 0.96
    max_width: int = 460
    offset_x: int = 6                   # 气泡相对鼠标热点的偏移，越小越贴近
    offset_y: int = 10                  # 故意比 x 大一点，避开鼠标指针本身
    auto_hide_seconds: float = 6.0      # 鼠标移开后多久隐藏

    # 行为
    enabled: bool = True                # 总开关

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return cls()
            known = {f for f in cls.__dataclass_fields__}
            data = {k: v for k, v in raw.items() if k in known}
            # 老版本配置里可能残留已废弃的翻译源名（比如 google），
            # 过滤掉无效项，全是无效项就回退到默认
            from translate import _PROVIDERS

            providers = [p for p in data.get("providers", []) if p in _PROVIDERS]
            data["providers"] = providers or cls().providers
            return cls(**data)
        cfg = cls()
        cfg.save()
        return cfg

    def save(self) -> None:
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
