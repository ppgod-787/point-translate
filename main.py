"""指译 PointTranslate —— 鼠标悬停即翻译。

启动后常驻系统托盘。把鼠标停在任意英文上约 1 秒，旁边就弹出中文。
托盘图标右键可以开关、看缓存、退出。

直接运行：  .venv\\Scripts\\python.exe main.py
调试模式：  .venv\\Scripts\\python.exe main.py --verbose
"""

from __future__ import annotations

import argparse
import sys
import threading
import traceback
from pathlib import Path

# DPI 感知必须在任何窗口创建之前设置
import winutil

winutil.enable_dpi_awareness()

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from config import CONFIG_PATH, Config
from overlay import TranslationBubble
from translate import Translator
from triggers import HoverWorker


def make_icon() -> QIcon:
    """画一个简单的托盘图标，免得依赖外部图片文件。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(58, 120, 240))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setPen(QColor(255, 255, 255))
    font = QFont("Microsoft YaHei UI", 30, QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "译")
    painter.end()
    return QIcon(pixmap)


class App:
    def __init__(self, argv: list[str], verbose: bool = False):
        self.verbose = verbose
        self.config = Config.load()
        self.qt = QApplication(argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setApplicationName("指译 PointTranslate")

        self.translator = Translator(self.config)
        self.translator.warm_up(background=True)

        self.bubble = TranslationBubble(self.config)
        self.bubble.apply_config()

        self.worker = HoverWorker(self.config, self.translator)
        self.worker.found.connect(self._on_found)
        self.worker.pending.connect(self._on_pending)
        self.worker.miss.connect(self._on_miss)
        self.worker.bubble_hide.connect(self._on_bubble_hide)

        self.tray = self._build_tray()
        self.worker.start()

        self._log(
            f"已启动 · 悬停 {self.config.hover_seconds}s 触发 · "
            f"UIA={'开' if self.config.use_uia else '关'} "
            f"OCR={'开' if self.config.use_ocr else '关'}"
        )
        self._check_ocr()

    # ---------- 气泡回调（主线程） ----------

    def _on_found(self, original: str, translated: str, provider: str) -> None:
        if self.verbose:
            self._log(f"[{provider}] {original[:60]!r} -> {translated[:60]!r}")
        self.bubble.show_result(original, translated, provider)
        self.bubble.start_auto_hide()

    def _on_pending(self, original: str) -> None:
        self.bubble.show_loading(original)

    def _on_miss(self, message: str, detail: str = "") -> None:
        if self.verbose and detail:
            self._log(f"未命中: {message} {detail}")
        # 没找到文字是很正常的事（鼠标停在空白处），不弹气泡打扰
        if message == "这里没找到可翻译的英文":
            return
        self.bubble.show_message(message, detail)
        self.bubble.start_auto_hide()

    def _on_bubble_hide(self) -> None:
        """工作线程要求隐藏气泡，隐藏完必须回执，否则它会一直等。"""
        try:
            self.bubble.move_away()
        finally:
            self.worker.acknowledge_hide()

    # ---------- 托盘 ----------

    def _build_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(make_icon())
        tray.setToolTip("指译 · 鼠标悬停翻译")

        menu = QMenu()

        self.toggle_action = QAction("暂停翻译", menu, checkable=True)
        self.toggle_action.setChecked(not self.config.enabled)
        self.toggle_action.triggered.connect(self._toggle_enabled)
        menu.addAction(self.toggle_action)

        menu.addSeparator()

        stats_action = QAction("查看缓存统计", menu)
        stats_action.triggered.connect(self._show_stats)
        menu.addAction(stats_action)

        clear_action = QAction("清空翻译缓存", menu)
        clear_action.triggered.connect(self._clear_cache)
        menu.addAction(clear_action)

        config_action = QAction("打开配置文件", menu)
        config_action.triggered.connect(self._open_config)
        menu.addAction(config_action)

        menu.addSeparator()

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_enabled(not self.config.enabled)

    def _toggle_enabled(self, disabled: bool) -> None:
        self.config.enabled = not disabled
        self.toggle_action.setChecked(disabled)
        if disabled:
            self.bubble.move_away()
        self._log("翻译已暂停" if disabled else "翻译已开启")
        # 暂停/恢复要落盘，否则重启后又读回配置文件里的旧值
        try:
            self.config.save()
        except OSError as exc:
            self._log(f"保存配置失败: {exc}")

    def _show_stats(self) -> None:
        stats = self.translator.cache.stats()
        self.tray.showMessage(
            "指译 · 缓存统计",
            f"已缓存 {stats['entries']} 条\n命中 {stats['hits']} 次\n"
            f"本次会话翻译 {self.worker.stats['hits']} 次",
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )

    def _clear_cache(self) -> None:
        try:
            self.translator.cache._conn.execute("DELETE FROM translations")
            self.translator.cache._conn.commit()
            self.translator.cache._memory.clear()
            self._log("缓存已清空")
        except Exception as exc:
            self._log(f"清空缓存失败: {exc}")

    def _open_config(self) -> None:
        import os

        os.startfile(str(CONFIG_PATH))  # noqa: S606

    # ---------- 杂项 ----------

    def _check_ocr(self) -> None:
        if not self.config.use_ocr:
            return
        from extractors import ocr as ocr_module

        extractor = ocr_module.OcrExtractor()
        if not extractor.available:
            self._log("提示: " + (extractor.error or "OCR 不可用"))
            self.tray.showMessage(
                "指译 · OCR 未就绪",
                extractor.error or "OCR 不可用",
                QSystemTrayIcon.MessageIcon.Warning,
                8000,
            )
        elif extractor.warning:
            self._log("提示: " + extractor.warning)
            self.tray.showMessage(
                "指译 · OCR 精度提示",
                extractor.warning,
                QSystemTrayIcon.MessageIcon.Information,
                8000,
            )

    def _log(self, message: str) -> None:
        print(f"[指译] {message}", flush=True)

    def quit(self) -> None:
        self.worker.stop()
        self.translator.cache.close()
        self.tray.hide()
        self.qt.quit()

    def run(self) -> int:
        return self.qt.exec()


def _selftest(verbose: bool = True) -> int:
    """不打图形界面，逐项验证核心功能。

    打包成 exe 之后特别有用：能确认依赖项真的被装进包了，
    而不是打出一个"能启动、一取词就崩"的空壳。
    """
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append((name, bool(ok), str(detail)))

    # 1) 配置
    def _cfg():
        cfg = Config.load()
        return True, f"悬停 {cfg.hover_seconds}s, 源 {','.join(cfg.providers)}"

    # 2) 截屏（GDI）
    def _capture():
        winutil.enable_dpi_awareness()
        got = winutil.grab_region_raw(0, 0, 200, 60)
        if not got:
            return False, "截图失败"
        raw, w, h = got
        bmp = winutil.raw_to_software_bitmap(raw, w, h)
        return bmp is not None, f"{w}x{h}, SoftwareBitmap={'ok' if bmp else '失败'}"

    # 3) OCR 引擎
    def _ocr():
        from extractors.ocr import OcrExtractor

        ex = OcrExtractor()
        if not ex.available:
            return False, ex.error or "不可用"
        return True, f"引擎 {ex.language_tag}" + (f"（{ex.warning}）" if ex.warning else "")

    # 4) UI Automation
    def _uia():
        from extractors.uia import text_from_uia

        x, y = winutil.cursor_pos()
        hit = text_from_uia(x, y)
        return True, f"控件类型读取正常（当前指向: {hit.text[:20]!r}）" if hit else "可调用（当前指向处无文字）"

    # 5) 翻译（含网络）
    def _translate():
        cfg = Config.load()
        cfg.timeout = 8.0
        tr = Translator(cfg)
        out = tr.translate("Settings", reliable=False)
        if out is None:
            return False, "翻译失败，检查网络"
        return True, f"Settings -> {out[0]!r} (来源 {out[1]})"

    # 6) GUI 依赖
    def _gui():
        from PySide6.QtWidgets import QApplication  # noqa: F401
        from overlay import TranslationBubble, _provider_label  # noqa: F401

        return True, "PySide6 与气泡模块导入正常"

    check("配置读取", _cfg)
    check("屏幕截取 (GDI)", _capture)
    check("OCR 引擎", _ocr)
    check("UI Automation", _uia)
    check("GUI 依赖", _gui)
    check("在线翻译", _translate)

    lines = ["=" * 64, "指译 自检", "=" * 64]
    failed = 0
    for name, ok, detail in results:
        if not ok:
            failed += 1
        lines.append(f"  {'[OK]' if ok else '[!!]'} {name:18} {detail}")
    lines.append("=" * 64)
    lines.append(f"结果：{len(results) - failed}/{len(results)} 项通过")
    report = "\n".join(lines)

    # 打成 exe 之后是 console=False，没有标准输出，
    # 所以结果必须同时落到文件里，否则等于什么都没测。
    # 固定写到 exe / 源码所在目录，而不是当前工作目录：
    # 从快捷方式、计划任务或其他目录启动时工作目录会变，
    # 写在 CWD 里用户根本找不到那份报告。
    try:
        print(report, flush=True)
    except Exception:
        pass

    from config import BASE_DIR

    written = False
    for target in (BASE_DIR / "selftest.txt", Path.home() / "指译-selftest.txt"):
        try:
            target.write_text(report, encoding="utf-8")
            try:
                print(f"报告已写入: {target.resolve()}", flush=True)
            except Exception:
                pass
            written = True
            break
        except Exception:
            continue
    if not written:
        return 1

    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="指译 —— 鼠标悬停翻译")
    parser.add_argument("--verbose", action="store_true", help="打印每次翻译的明细")
    parser.add_argument("--disabled", action="store_true", help="启动时先暂停")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="只做功能自检，不启动界面（打包后验证用）",
    )
    args = parser.parse_args()

    if args.selftest:
        return _selftest()

    app = App(sys.argv, verbose=args.verbose)
    if args.disabled:
        app.config.enabled = False
        app.toggle_action.setChecked(True)

    def _excepthook(exc_type, exc, tb) -> None:
        print("[指译] 未捕获异常:", flush=True)
        traceback.print_exception(exc_type, exc, tb)

    sys.excepthook = _excepthook
    threading.excepthook = lambda args: _excepthook(
        args.exc_type, args.exc_value, args.exc_traceback
    )
    try:
        return app.run()
    finally:
        app.worker.stop()


if __name__ == "__main__":
    sys.exit(main())
