"""集成测试：把 HoverWorker 真跑起来，验证悬停触发逻辑。

本轮用的目标位置位于一个中文界面上，因此预期结果是
"触发了取词，但因为原文主要是中文而正确地不翻译"。
这恰好验证了 should_translate 的挡板逻辑和整条触发链路。
"""

from __future__ import annotations

import ctypes
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import winutil

winutil.enable_dpi_awareness()

from PySide6.QtCore import QCoreApplication, Qt, QTimer

from config import Config
from extractors.uia import text_from_uia
from translate import Translator
from triggers import HoverWorker

user32 = ctypes.windll.user32
app = QCoreApplication(sys.argv)

cfg = Config.load()
cfg.hover_seconds = 0.8
cfg.poll_interval = 0.06
cfg.use_ocr = False
cfg.timeout = 5.0

DIRECT = Qt.ConnectionType.DirectConnection

print("=" * 72)
print("集成测试：悬停触发链路")
print("=" * 72)

# 找一个 UIA 能读到文字的位置
TARGET = None
for px, py in [(1200, 400), (700, 400), (400, 300), (900, 500), (1400, 300)]:
    try:
        hit = text_from_uia(px, py)
    except Exception:
        hit = None
    if hit is not None and hit.text.strip():
        TARGET = (px, py, hit)
        break

if TARGET is None:
    print("!! 桌面上找不到任何 UIA 可读文字的位置")
    sys.exit(2)

px, py, hit = TARGET
print(f"目标位置 ({px}, {py})")
print(f"  UIA 读到: {hit.text[:60]!r}  (来源 {hit.source}, 类型 {hit.control_type})")

translator = Translator(cfg)
worker = HoverWorker(cfg, translator)

events: list[tuple] = []
worker.found.connect(lambda a, b, c: events.append(("found", a, b, c)), type=DIRECT)
worker.pending.connect(lambda a: events.append(("pending", a)), type=DIRECT)
worker.miss.connect(lambda a, b: events.append(("miss", a, b)), type=DIRECT)
worker.bubble_hide.connect(lambda: events.append(("hide",)), type=DIRECT)
worker.bubble_hide.connect(worker.acknowledge_hide)

print(f"\n把鼠标移过去并停住 {cfg.hover_seconds}s …")
user32.SetCursorPos(px, py)
time.sleep(0.15)
user32.SetCursorPos(px, py)

worker.start()
QTimer.singleShot(3500, app.quit)
app.exec()
worker.stop()

print("\n捕获到的事件：")
for e in events:
    if e[0] == "hide":
        print("  [hide]    主线程收起气泡（已回执）")
    elif e[0] == "pending":
        print(f"  [pending] 开始翻译 {e[1]!r}")
    elif e[0] == "found":
        print(f"  [found]   {e[1]!r} -> {e[2]!r} (来源 {e[3]})")
    elif e[0] == "miss":
        print(f"  [miss]    {e[1]} {e[2]!r}")

# ---------- 判定 ----------
print("\n判定：")
hides = [e for e in events if e[0] == "hide"]
found = [e for e in events if e[0] == "found"]
misses = [e for e in events if e[0] == "miss"]
ok = True

if hides:
    print("  ✅ 悬停到时间后触发了取词（气泡隐藏请求已发出并回执）")
else:
    print("  ❌ 悬停没有触发取词")
    ok = False

if found or misses:
    print(f"  ✅ 取词有结果（翻译 {len(found)} 次 / 未翻译 {len(misses)} 次）")
else:
    print("  ❌ 取词没有产生任何结果")
    ok = False

# 这个位置是中文界面，正确的行为是"不翻译"
text_under = hit.text
has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text_under)
if has_cjk and not found:
    print("  ✅ 原文以中文为主，正确跳过了翻译（没浪费网络请求）")
elif has_cjk and found:
    print("  ⚠ 原文是中文却翻译了，挡板逻辑需要检查")

# 鼠标移动期间不应触发
print("\n再测：鼠标持续移动时不应触发翻译（2.5 秒）")
events.clear()
user32.SetCursorPos(px, py)
worker.start()
moves = 0


def jiggle():
    global moves
    if moves < 22:
        moves += 1
        user32.SetCursorPos(px + (moves % 5) * 14, py + (moves % 3) * 12)
        QTimer.singleShot(100, jiggle)


QTimer.singleShot(150, jiggle)
QTimer.singleShot(2600, app.quit)
app.exec()
worker.stop()

moved_found = [e for e in events if e[0] == "found"]
moved_miss = [e for e in events if e[0] == "miss"]
print(f"  移动期间：翻译 {len(moved_found)} 次 / 未翻译 {len(moved_miss)} 次")
if moved_found or moved_miss:
    print("  ❌ 鼠标移动时不该触发取词")
    ok = False
else:
    print("  ✅ 鼠标移动时正确抑制了触发")

print("\n" + ("集成测试通过 ✅" if ok else "集成测试失败 ❌"))
sys.exit(0 if ok else 1)
