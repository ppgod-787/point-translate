"""显示层：贴着鼠标显示的翻译气泡。

最关键的一点是"鼠标穿透"：气泡必须看得见但点不到、也取不到词。
否则鼠标一旦移向气泡，取词目标就从原文变成了气泡本身，
表现为结果疯狂闪烁或者卡死在无意义的内容上。

穿透靠两层保险：
1. WS_EX_TRANSPARENT + WS_EX_NOACTIVATE 扩展样式；
2. Qt 层面的 WindowTransparentForInput。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

import winutil

STYLE = """
#bubble {
    background-color: rgba(28, 30, 36, 242);
    border: 1px solid rgba(120, 170, 255, 110);
    border-radius: 10px;
}
#original {
    color: #9aa4b2;
    font-size: 11px;
}
#translation {
    color: #f2f5fa;
}
#footer {
    color: #6f7a8a;
    font-size: 10px;
}
"""


class TranslationBubble(QWidget):
    def __init__(self, config):
        super().__init__(None)
        self.config = config
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.frame = QFrame(self)
        self.frame.setObjectName("bubble")
        outer.addWidget(self.frame)

        inner = QVBoxLayout(self.frame)
        inner.setContentsMargins(12, 9, 12, 9)
        inner.setSpacing(5)

        self.original_label = QLabel(self.frame)
        self.original_label.setObjectName("original")
        self.original_label.setWordWrap(True)
        inner.addWidget(self.original_label)

        self.translation_label = QLabel(self.frame)
        self.translation_label.setObjectName("translation")
        self.translation_label.setWordWrap(True)
        self.translation_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction
        )
        inner.addWidget(self.translation_label)

        self.footer_label = QLabel(self.frame)
        self.footer_label.setObjectName("footer")
        inner.addWidget(self.footer_label)

        self.setStyleSheet(STYLE)
        self._apply_font()
        self.hide()

    # ---------- 外观 ----------

    def _apply_font(self) -> None:
        font = QFont("Microsoft YaHei UI", self.config.font_size)
        self.translation_label.setFont(font)
        small = QFont("Microsoft YaHei UI", max(self.config.font_size - 2, 9))
        self.original_label.setFont(small)
        self.footer_label.setFont(small)

    def apply_config(self) -> None:
        self._apply_font()
        self.setWindowOpacity(self.config.opacity)

    # ---------- 显示内容 ----------

    def show_result(self, original: str, translation: str, provider: str) -> None:
        source = _provider_label(provider)
        self.original_label.setText(_elide(original, 120))
        self.translation_label.setText(translation)
        self.footer_label.setText(source)
        self.footer_label.setVisible(bool(source))
        self._place_at_cursor()

    def show_message(self, text: str, detail: str = "") -> None:
        self.original_label.setVisible(False)
        self.footer_label.setVisible(False)
        self.translation_label.setText(text)
        if detail:
            self.original_label.setVisible(True)
            self.original_label.setText(detail)
        self._place_at_cursor()

    def show_loading(self, original: str) -> None:
        self.original_label.setText(_elide(original, 120))
        self.original_label.setVisible(True)
        self.translation_label.setText("翻译中…")
        self.footer_label.setVisible(False)
        self._place_at_cursor()

    # ---------- 定位 ----------

    def _place_at_cursor(self) -> None:
        self._reset_text_visibility()
        self.adjustSize()
        self.resize(self._bounded_size())

        x, y = winutil.cursor_pos()
        screen = QGuiApplication.screenAt(QPoint(x, y)) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()

        w, h = self.width(), self.height()
        # 偏移量夹一下，防止配置里写了个离谱的值把气泡甩到天边
        off_x = max(0, min(int(self.config.offset_x), 200))
        off_y = max(0, min(int(self.config.offset_y), 200))

        # 默认贴在鼠标右下方一点。y 偏移故意比 x 大，
        # 因为鼠标指针本身有十几像素高，刚好避开它。
        px = x + off_x
        py = y + off_y

        # 右边放不下就翻到鼠标左侧，下面放不下就翻到上方
        if px + w > area.right():
            px = x - w - off_x
        if py + h > area.bottom():
            py = y - h - off_y
        # 还是放不下（气泡比屏幕还大）就退回贴边
        px = max(area.left() + 2, min(px, area.right() - w - 2))
        py = max(area.top() + 2, min(py, area.bottom() - h - 2))

        self.move(px, py)
        self._show_click_through()

    def _bounded_size(self):
        from PySide6.QtCore import QSize

        size = self.sizeHint()
        width = min(max(size.width(), 140), self.config.max_width)
        # 交给 QLabel 按 max_width 重新排版，再取真实高度
        self.translation_label.setMaximumWidth(width - 24)
        self.original_label.setMaximumWidth(width - 24)
        return QSize(width, self.sizeHint().height())

    def _reset_text_visibility(self) -> None:
        self.original_label.setVisible(True)
        self.footer_label.setVisible(True)

    def _show_click_through(self) -> None:
        self.show()
        # 必须在窗口真正创建之后再设扩展样式
        winutil.make_click_through(int(self.winId()))

    # ---------- 生命周期 ----------

    def start_auto_hide(self) -> None:
        if self.config.auto_hide_seconds > 0:
            self._hide_timer.start(int(self.config.auto_hide_seconds * 1000))

    def cancel_auto_hide(self) -> None:
        self._hide_timer.stop()

    def is_showing(self) -> bool:
        return self.isVisible()

    def move_away(self) -> None:
        """鼠标挪开时立刻收起。"""
        self.cancel_auto_hide()
        self.hide()


def _provider_label(provider: str) -> str:
    return {
        "cache": "本地缓存",
        "iciba": "金山词霸",
        "youdao": "有道词典",
        "bing": "必应翻译",
        "mymemory": "MyMemory",
    }.get(provider, provider)


def _elide(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
