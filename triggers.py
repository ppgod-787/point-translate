"""触发层：鼠标悬停检测。

工作线程每 poll_interval 秒看一次鼠标位置：
- 鼠标在动 → 重置计时，顺便通知主线程收气泡；
- 鼠标停住超过 hover_seconds → 认为用户"指"在某处了，开始取词翻译。

线程模型上有个细节：UI Automation 取词前**必须确保气泡已经隐藏**，
否则命中的可能是气泡自身。所以隐藏是同步的 —— 工作线程发信号出去，
阻塞等主线程确认之后再取词。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

import winutil
from extractors import ocr as ocr_module
from extractors import uia as uia_module
from translate import Translator, cleanup_ocr, screen_key


@dataclass
class _LastHit:
    text: str
    at: float


class HoverWorker(QObject):
    """跑在普通线程里，通过信号和主线程通信。"""

    found = Signal(str, str, str)      # 原文, 译文, 来源
    pending = Signal(str)              # 原文（开始翻译，先显示"翻译中"）
    miss = Signal(str, str)            # 提示信息, 补充说明（可为空）
    bubble_hide = Signal()

    def __init__(self, config, translator: Translator):
        super().__init__()
        self.config = config
        self.translator = translator
        self._stop = threading.Event()
        self._hide_ack = threading.Event()
        self._thread: threading.Thread | None = None
        self._ocr = ocr_module.OcrExtractor(
            pad=config.ocr_padding, width=config.ocr_width, height=config.ocr_height
        )
        # 同一个控件短时间内不重复翻译（很多控件文字不变）
        self._recent: dict[str, _LastHit] = {}
        self._recent_lock = threading.Lock()
        self.stats = {"hits": 0, "cache_hits": 0, "misses": 0}

    # ---------- 生命周期 ----------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="hover")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._hide_ack.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def acknowledge_hide(self) -> None:
        """主线程隐藏完气泡后调用，放行工作线程。"""
        self._hide_ack.set()

    # ---------- 主循环 ----------

    def _run(self) -> None:
        cfg = self.config
        last_x, last_y = winutil.cursor_pos()
        still_since = time.monotonic()
        fired_for: tuple[int, int] | None = None

        while not self._stop.is_set():
            time.sleep(cfg.poll_interval)
            if not cfg.enabled:
                still_since = time.monotonic()
                fired_for = None
                continue

            x, y = winutil.cursor_pos()
            moved = abs(x - last_x) > cfg.move_tolerance or abs(y - last_y) > cfg.move_tolerance

            if moved:
                last_x, last_y = x, y
                still_since = time.monotonic()
                fired_for = None
                self.bubble_hide.emit()
                continue

            # 同一个位置只触发一次
            if fired_for == (x, y):
                continue

            if time.monotonic() - still_since < cfg.hover_seconds:
                continue

            fired_for = (x, y)
            try:
                self._handle(x, y)
            except Exception as exc:
                # 单个位置出错不能让整个线程死掉，否则功能会永久失效
                self.miss.emit(f"出错：{type(exc).__name__}", str(exc))

            # 处理完歇一下再重新开始判定。
            # 否则鼠标停在原处不动时，计时器会立刻再次满足条件，反复取同一个词。
            still_since = time.monotonic() + cfg.cooldown_seconds

    # ---------- 取词 + 翻译 ----------

    def _handle(self, x: int, y: int) -> None:
        # 1) 先确保气泡隐藏，避免 UIA 命中的是我们自己的气泡
        #    正常情况下回执一到 wait 就返回；这里的超时只是"主线程卡住"时的
        #    兜底，给到 1 秒，免得主线程偶尔忙一下就带着气泡去取词。
        self._hide_ack.clear()
        self.bubble_hide.emit()
        self._hide_ack.wait(timeout=1.0)

        if self._stop.is_set():
            return

        # 2) 取词
        text, source = self._extract(x, y)
        if not text:
            self.stats["misses"] += 1
            self.miss.emit("这里没找到可翻译的英文", "")
            return

        text = cleanup_ocr(text)
        if len(text) < self.config.min_text_length:
            # 取到了东西但没信息量（占位符、单个字符等），提示一下方便排查
            self.miss.emit("这里没有可翻译的文字", f"取到的是 {text!r}")
            return

        # 3) 同一处刚翻过就直接复用，省掉网络往返。
        #    用忽略空格大小写的键，免得 OCR 每次多认个空格就重新翻译一遍。
        key = screen_key(text)
        now = time.monotonic()
        with self._recent_lock:
            prev = self._recent.get(key)
            if prev and now - prev.at < 30:
                self.stats["cache_hits"] += 1
                self.found.emit(text, prev.text, "cache")
                return

        # 4) 翻译。OCR 来的文字可能认错，不让它写进缓存
        self.pending.emit(text)
        try:
            result = self.translator.translate(
                text,
                reliable=(source != "ocr"),
                word_only=not self.config.whole_sentence,
            )
        except Exception as exc:
            self.miss.emit(f"翻译失败：{type(exc).__name__}", str(exc))
            return

        if result is None:
            self.stats["misses"] += 1
            self.miss.emit("这句没有可翻译的内容", text)
            return

        translated, provider = result
        with self._recent_lock:
            self._recent[key] = _LastHit(text=translated, at=time.monotonic())
            if len(self._recent) > 200:
                cutoff = time.monotonic() - 60
                for k in [k for k, v in self._recent.items() if v.at < cutoff]:
                    self._recent.pop(k, None)
        self.stats["hits"] += 1
        self.found.emit(text, translated, provider)

    def _extract(self, x: int, y: int) -> tuple[str | None, str]:
        """按 UIA → OCR 的顺序取词，返回 (文本, 来源)。"""
        # 整句模式取控件/整行的文字；单词模式只取指针下的那个词
        word_only = not self.config.whole_sentence
        if self.config.use_uia:
            hit = uia_module.text_from_uia(x, y, word_only=word_only)
            if hit is not None and hit.text.strip():
                # UIA 常把整段窗口文本都返回，取第一行更像用户在指的东西
                text = hit.text.splitlines()[0] if "\n" in hit.text else hit.text
                return text, hit.source

        if self.config.use_ocr:
            if not self._ocr.available:
                err = self._ocr.error or "OCR 不可用"
                self.miss.emit("OCR 不可用", err)
                return None, "ocr"
            result = self._ocr.read_at(x, y, word_only=word_only)
            if result is not None and result.text.strip():
                return result.text, "ocr"

        return None, "none"
