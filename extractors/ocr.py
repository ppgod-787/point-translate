"""取词层 B：Windows 内置 OCR。

UIA 拿不到文字时的兜底方案（游戏画面、图片、自绘界面）。
用的是系统自带的 Windows.Media.Ocr，不联网、不需要额外模型文件，
但需要系统里装了对应的 OCR 语言包（见 ensure_language 的提示）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import winutil

try:
    from winsdk.windows.globalization import Language
    from winsdk.windows.media.ocr import OcrEngine
except ImportError:
    Language = None
    OcrEngine = None

# 这些语言包对识别英文界面最合适，按优先级尝试
ENGLISH_TAGS = ("en-US", "en-GB", "en")


@dataclass
class OcrResult:
    text: str
    lines: list[str]


class OcrExtractor:
    def __init__(self, pad: int = 14, width: int = 420, height: int = 90):
        self.pad = pad
        self.width = width
        self.height = height
        self._engine = None
        self._probed = False
        self._error: str | None = None
        self._warning: str | None = None
        self.language_tag: str | None = None

    # ---------- 引擎初始化 ----------

    def _get_engine(self):
        if self._probed:
            return self._engine
        self._probed = True

        if OcrEngine is None:
            self._error = "winsdk 未安装，OCR 不可用"
            return None

        # 1) 首选英文引擎：识别英文界面最准
        for tag in ENGLISH_TAGS:
            if _is_supported(tag):
                engine = _try_create(tag)
                if engine is not None:
                    self._engine = engine
                    self.language_tag = tag
                    return engine

        # 2) 没有英文包，退而求其次用系统现有的引擎（中文引擎也能认英文，
        #    只是准确率会打折，所以给个提示）
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is not None:
            self._engine = engine
            self.language_tag = _engine_tag(engine)
            self._warning = (
                f"系统未安装英文 OCR 语言包，正在用 {self.language_tag} 引擎识别英文，"
                "准确率会打折。建议按下面的“安装英文 OCR 包”操作。"
            )
            return engine

        self._error = (
            "系统没有任何 OCR 语言包。请打开"
            " 设置 → 时间和语言 → 语言和区域 → 添加语言，"
            "安装 English (United States)，并在语言选项里勾选"
            "“光学字符识别 (OCR)”。"
        )
        return None

    @property
    def available(self) -> bool:
        return self._get_engine() is not None

    @property
    def error(self) -> str | None:
        self._get_engine()
        return self._error

    @property
    def warning(self) -> str | None:
        self._get_engine()
        return self._warning

    # ---------- 取词 ----------

    def read_at(self, x: int, y: int) -> OcrResult | None:
        """截取鼠标周围一小块区域并识别文字。"""
        engine = self._get_engine()
        if engine is None:
            return None

        # pad 是字形上下沿的余量：紧贴光标的框容易切掉字母的顶和底。
        # 同时先用 clamp_to_virtual 算出真实区域，才能知道光标落在截图的哪一行——
        # 贴边或多显示器负坐标时区域会被裁小，光标不在正中央，按定值猜会挑错行。
        left, top, w, h = winutil.clamp_to_virtual(
            x - self.width // 2 - self.pad,
            y - self.height // 2 - self.pad,
            self.width + 2 * self.pad,
            self.height + 2 * self.pad,
        )
        grabbed = winutil.grab_region_raw(left, top, w, h)
        if not grabbed:
            return None
        raw, w, h = grabbed

        bitmap = winutil.raw_to_software_bitmap(raw, w, h)
        if bitmap is None:
            return None

        try:
            result = asyncio.run(_recognize(engine, bitmap))
        except Exception:
            return None
        if result is None:
            return None

        lines = [line.text.strip() for line in result.lines if line.text.strip()]
        if not lines:
            return None

        # 按"光标在截图里的真实纵坐标"挑最靠近的那一行：
        # 直接拼全文会把旁边无关的文字也翻进来。
        best = _closest_line(result.lines, y - top)
        text = best or " ".join(lines)
        return OcrResult(text=text, lines=lines)


async def _recognize(engine, bitmap):
    """必须包一层协程：winsdk 的 recognize_async 返回的是 IAsyncOperation，
    它是 awaitable 但不是标准协程，asyncio.run 只接受协程。"""
    return await engine.recognize_async(bitmap)


def _is_supported(tag: str) -> bool:
    """系统是否支持该语言的 OCR。"""
    try:
        return bool(OcrEngine.is_language_supported(Language(tag)))
    except Exception:
        return False


def _try_create(tag: str):
    try:
        return OcrEngine.try_create_from_language(Language(tag))
    except Exception:
        return None


def _engine_tag(engine) -> str:
    try:
        lang = engine.recognizer_language
        return lang.language_tag if lang else "未知"
    except Exception:
        return "未知"


def _closest_line(lines, target_y: int) -> str | None:
    """挑出垂直方向上离鼠标最近的一行文字。"""
    best_text, best_dist = None, None
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        words = list(line.words)
        if words:
            rect = words[0].bounding_rect
            center_y = rect.y + rect.height / 2
            # 同一行可能有多个词，取整行的垂直中位
            ys = [wd.bounding_rect.y + wd.bounding_rect.height / 2 for wd in words]
            center_y = sum(ys) / len(ys)
        else:
            center_y = target_y
        dist = abs(center_y - target_y)
        if best_dist is None or dist < best_dist:
            best_text, best_dist = text, dist
    return best_text


if __name__ == "__main__":
    import time

    winutil.enable_dpi_awareness()
    print("把鼠标移到一段英文上，3 秒后识别…")
    time.sleep(3)
    x, y = winutil.cursor_pos()
    extractor = OcrExtractor()
    print("OCR 可用:", extractor.available, "| 错误:", extractor.error)
    print("鼠标位置:", (x, y), "前台窗口:", winutil.foreground_title())
    result = extractor.read_at(x, y)
    print("识别结果:", result)
