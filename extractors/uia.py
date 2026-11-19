"""取词层 A：UI Automation。

鼠标指向某个控件时，从 Windows 无障碍接口里直接读出它的文字。
比 OCR 强的地方在于拿到的是"原始字符串"，不会有识别错字，而且不用截图、极快。

对 Epic 启动器这类标准 Qt / Win32 程序效果最好；
对游戏画面和网页应用（DirectX / 自绘）通常拿不到有效文字，
那种情况交给 ocr.py。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import re
from dataclasses import dataclass

try:
    import uiautomation as auto
except ImportError:  # pragma: no cover - 依赖没装好时给出清楚提示
    auto = None


user32 = ctypes.windll.user32

_LETTER = re.compile(r"[A-Za-z]")

# 这些控件的文字是"可读的正文"，值得翻
TEXTUAL_TYPES = {
    "TextControl",
    "ButtonControl",
    "MenuItemControl",
    "ListItemControl",
    "TabItemControl",
    "CheckBoxControl",
    "RadioButtonControl",
    "HyperlinkControl",
    "HeaderControl",
    "HeaderItemControl",
    "DataItemControl",
    "TreeItemControl",
    "EditControl",
    "ComboBoxControl",
    "SplitButtonControl",
}

# 这些是窗口外壳/容器，本身名字没意义（比如 "Epic Games Launcher"），跳过。
# DocumentControl 尤其要注意：它的 Name 是窗口标题、TextPattern 是整个文档，
# 都跟"鼠标指着哪一行"无关。
SHELL_TYPES = {
    "WindowControl",
    "PaneControl",
    "GroupControl",
    "ToolBarControl",
    "TitleBarControl",
    "ScrollBarControl",
    "ImageControl",
    "CustomControl",
    "DocumentControl",
}

# TextPattern 常常返回整个文档（整个窗口的文字），
# 超过这个长度就不像一个"控件"了，判定为文档级结果，弃用。
_MAX_PATTERN_TEXT = 120
# 单次取词最多接受的字符数，超了说明抓到了大段文字
_MAX_TEXT = 300
# 单词模式下单个词的长度上限（超长英文单词最多也就 30 来个字母）
_MAX_WORD_TEXT = 40


@dataclass
class Hit:
    text: str
    source: str          # 'uia-name' / 'uia-value' / 'uia-text' / 'uia-word'
    control_type: str = ""


def text_from_uia(
    x: int, y: int, max_depth: int = 4, word_only: bool = False
) -> Hit | None:
    """读取屏幕坐标 (x, y) 处的控件文字。取不到返回 None。

    word_only=True 时只要"指针正下方那个词"：用 TextPattern.RangeFromPoint
    定位到该点，再扩到词边界。对没有 TextPattern 的控件（按钮、菜单项）
    退回控件自己的 Name —— 这类控件的标签本身就是最细粒度，再拆就没有依据了。
    """
    if auto is None:
        return None

    try:
        element = auto.ControlFromPoint(x, y)
    except Exception:
        return None
    if element is None:
        return None

    # 从命中点往上爬几层，取第一个有文字的控件。
    # 直接取最底层那个往往是个没名字的容器。
    chain = []
    node = element
    for _ in range(max_depth):
        if node is None:
            break
        chain.append(node)
        try:
            node = node.GetParentControl()
        except Exception:
            break

    # 单词模式先扫完整条链找"一个词"：正文控件的 Name 往往是整段，
    # 而 TextPattern 能精确到指针下的那个词，所以词优先于控件文本。
    if word_only:
        for node in chain:
            hit = _word_of(node, x, y)
            if hit is not None:
                return hit

    for node in chain:
        hit = _text_of(node)
        if hit is not None:
            return hit

    # 往上没结果，退一步看当前控件的子节点里有没有唯一一个有文字的
    try:
        children = element.GetChildren()
    except Exception:
        children = []
    texts = [t for t in (_hit_of(c, x, y, word_only) for c in children) if t is not None]
    if len(texts) == 1:
        return texts[0]
    return None


def _hit_of(control, x: int, y: int, word_only: bool) -> Hit | None:
    """按当前取词粒度从一个控件上取文字。"""
    if word_only:
        hit = _word_of(control, x, y)
        if hit is not None:
            return hit
    return _text_of(control)


def _word_of(control, x: int, y: int) -> Hit | None:
    """用 TextPattern 定位指针落在的那个词。

    这里**不跳过** SHELL_TYPES：Document/Pane 的正文正是 RangeFromPoint
    最有用的地方（PDF、Word、浏览器），而我们只取"一个词"，不会把整篇文档
    拖进来。取不到词（点在空白处）时返回 None，交给上层继续找。
    """
    if auto is None:
        return None
    try:
        ctype = control.ControlTypeName or ""
    except Exception:
        return None

    try:
        pattern = control.GetTextPattern()
    except Exception:
        return None
    if pattern is None:
        return None

    try:
        text_range = pattern.RangeFromPoint(x, y)
        if text_range is None:
            return None
        # 这个范围是退化的（零长度），扩到"词"这一级就是指针下的那个词
        text_range.ExpandToEnclosingUnit(auto.TextUnit.Word)
        word = (text_range.GetText(_MAX_WORD_TEXT + 20) or "").strip()
    except Exception:
        return None

    if not _acceptable_word(word):
        return None
    return Hit(text=word, source="uia-word", control_type=ctype)


def _acceptable_word(text: str) -> bool:
    """判断 TextPattern 定位到的是不是"一个词"。"""
    word = (text or "").strip()
    if not word or len(word) > _MAX_WORD_TEXT:
        return False
    if any(ch.isspace() for ch in word):
        return False
    return bool(_LETTER.search(word))


def _text_of(control) -> Hit | None:
    """尝试从一个控件上取有意义的文字。

    顺序很重要：Name 是每个控件自己的属性，最靠谱；
    TextPattern 有可能是整个文档，所以放最后，并且要检查长度。
    """
    try:
        ctype = control.ControlTypeName or ""
    except Exception:
        return None

    if ctype in SHELL_TYPES:
        return None

    # 1) Name：按钮、菜单、列表项的文字都在这，且一定是这个控件自己的
    try:
        name = (control.Name or "").strip()
    except Exception:
        name = ""
    if _acceptable(name):
        return Hit(text=name, source="uia-name", control_type=ctype)

    # 2) ValuePattern：输入框里的内容
    try:
        vp = control.GetValuePattern()
        if vp is not None:
            value = (vp.Value or "").strip()
            if _acceptable(value):
                return Hit(text=value, source="uia-value", control_type=ctype)
    except Exception:
        pass

    # 3) TextPattern：真正的文本内容，但可能是整篇文档，必须过滤
    try:
        pattern = control.GetTextPattern()
        if pattern is not None:
            content = (pattern.DocumentRange.GetText(_MAX_PATTERN_TEXT + 50) or "").strip()
            # 只接受短而单行的结果；多行或过长说明这是文档级文本，不是鼠标指的那一行
            if _acceptable(content) and "\n" not in content:
                return Hit(text=content, source="uia-text", control_type=ctype)
    except Exception:
        pass

    return None


def _acceptable(text: str) -> bool:
    """判断一段文字是不是"值得翻译的控件文字"。"""
    if not text:
        return False
    stripped = text.strip()
    if len(stripped) < 2 or len(stripped) > _MAX_TEXT:
        return False
    # 只有占位符/控制字符的，扔掉（常见于富文本控件）
    if not any(ch.isalnum() for ch in stripped):
        return False
    return True


if __name__ == "__main__":
    # 自测：把鼠标放在任意窗口文字上，3 秒后打印读到的东西
    import sys
    import time

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import winutil

    winutil.enable_dpi_awareness()

    print("把鼠标移到一段英文上，3 秒后取词…")
    time.sleep(3)
    x, y = winutil.cursor_pos()
    print(f"鼠标位置: ({x}, {y})")
    print("前台窗口:", winutil.foreground_title())
    print("UIA 取词结果:", text_from_uia(x, y))
