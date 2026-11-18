"""配置读写：热键、悬停时长、气泡外观、翻译源。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
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


# json 里的数字和布尔值没有类型约束，手改配置很容易写错类型：
# 比如 "hover_seconds": "1" 会让 time.monotonic() 的比较直接 TypeError。
# 所以加载时按字段默认值的类型归一化，对明显越界的数值做夹取。
_TRUE_WORDS = {"true", "yes", "on", "1"}
_FALSE_WORDS = {"false", "no", "off", "0"}

# 字段 -> (下限, 上限)。只夹取标量，避免手滑写个 0 或 99999 把程序弄成不可用。
_RANGES: dict[str, tuple[float, float]] = {
    "hover_seconds": (0.1, 10.0),
    "poll_interval": (0.02, 1.0),
    "move_tolerance": (0, 64),
    "min_text_length": (1, 32),
    "cooldown_seconds": (0.0, 30.0),
    "ocr_padding": (0, 200),
    "ocr_width": (40, 2000),
    "ocr_height": (20, 2000),
    "timeout": (0.5, 60.0),
    "font_size": (8, 40),
    "opacity": (0.2, 1.0),
    "max_width": (200, 1600),
    "offset_x": (-50, 200),
    "offset_y": (-50, 200),
    "auto_hide_seconds": (0.0, 120.0),
}


def _clamp(name: str, number, cast):
    bounds = _RANGES.get(name)
    if bounds is None:
        return number
    low, high = bounds
    return cast(min(max(number, low), high))


def _coerce_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    return None


def _coerce_value(name: str, value: object, default: object) -> object | None:
    """按默认值的类型归一一个 json 值；归一不了就返回 None（调用方保留默认值）。"""
    if isinstance(default, bool):
        return _coerce_bool(value)
    if isinstance(default, int):
        if isinstance(value, bool):
            return None
        try:
            number = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return _clamp(name, number, int)
    if isinstance(default, float):
        if isinstance(value, bool):
            return None
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return _clamp(name, number, float)
    if isinstance(default, str):
        return value if isinstance(value, str) else None
    if isinstance(default, list):
        if not isinstance(value, list):
            return None
        return [item for item in value if isinstance(item, str)]
    return None


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
            if not isinstance(raw, dict):
                return cls()

            defaults = cls()
            known = {f.name for f in fields(cls)}
            data: dict[str, object] = {}
            for key, value in raw.items():
                if key not in known:
                    continue
                coerced = _coerce_value(key, value, getattr(defaults, key))
                if coerced is not None:
                    data[key] = coerced

            # 老版本配置里可能残留已废弃的翻译源名（比如 google），
            # 过滤掉无效项，全是无效项就回退到默认
            from translate import _PROVIDERS

            providers = [p for p in data.get("providers", []) if p in _PROVIDERS]
            data["providers"] = providers or defaults.providers
            return cls(**data)
        cfg = cls()
        cfg.save()
        return cfg

    def save(self) -> None:
        """原子写：先写临时文件再替换。

        直接覆盖写的话，写一半被关机/崩溃打断就会留下半个 json，
        下次启动解析失败、整份配置回退默认值——用户改的东西全丢。
        """
        payload = json.dumps(asdict(self), ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".config-", suffix=".tmp", dir=str(CONFIG_PATH.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
            os.replace(tmp_name, CONFIG_PATH)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
