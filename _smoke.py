"""冒烟测试：验证各层能正常初始化。"""

from __future__ import annotations

import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import winutil

winutil.enable_dpi_awareness()

print("1) 导入各模块…")
from config import Config
from extractors.ocr import OcrExtractor
from extractors.uia import text_from_uia
from translate import Translator

print("   ok")

print("\n2) 配置")
cfg = Config.load()
print(f"   悬停 {cfg.hover_seconds}s | UIA={cfg.use_uia} OCR={cfg.use_ocr} 源={cfg.providers}")

print("\n3) OCR 引擎")
t0 = time.perf_counter()
ocr = OcrExtractor(pad=cfg.ocr_padding, width=cfg.ocr_width, height=cfg.ocr_height)
avail = ocr.available
print(f"   available={avail} ({(time.perf_counter()-t0):.2f} s)")
print("   使用的引擎语言:", ocr.language_tag)
if ocr.error:
    print("   错误:", ocr.error)
if ocr.warning:
    print("   警告:", ocr.warning)

from winsdk.windows.media.ocr import OcrEngine

print("\n   各语言 OCR 支持情况:")
for tag in ("en-US", "en-GB", "en", "zh-Hans-CN", "ja-JP"):
    try:
        from winsdk.windows.globalization import Language

        supported = OcrEngine.is_language_supported(Language(tag))
    except Exception as exc:
        supported = f"检测失败 {exc}"
    print(f"     {tag:14} {supported}")

print("\n4) 翻译器预热")
tr = Translator(cfg)
t0 = time.perf_counter()
tr.warm_up(background=False)
print(f"   Bing token 就绪 ({(time.perf_counter()-t0)*1000:.0f} ms)")

print("\n5) 截屏 + OCR 实测（截鼠标周围一块）")
x, y = winutil.cursor_pos()
print(f"   鼠标位置: ({x}, {y})")
t0 = time.perf_counter()
got = winutil.grab_region_raw(x - 200, y - 40, 400, 80)
print(f"   截图: {(time.perf_counter()-t0)*1000:.0f} ms, {'成功' if got else '失败'}")

if got:
    from extractors.ocr import _recognize

    engine = ocr._get_engine()
    if engine is None:
        print("   拿不到 OCR 引擎")
    else:
        bmp = winutil.raw_to_software_bitmap(*got)
        import asyncio

        t0 = time.perf_counter()
        result = asyncio.run(_recognize(engine, bmp))
        print(f"   OCR 耗时 {(time.perf_counter()-t0)*1000:.0f} ms")
        lines = [line.text for line in result.lines]
        print("   识别到的行数:", len(lines))
        for line in lines[:6]:
            print("     -", repr(line))

    print("\n   用 OcrExtractor 完整取词:")
    print("   ", ocr.read_at(x, y))

print("\n6) UIA 取词实测（鼠标当前指向处）")
hit = text_from_uia(x, y)
print("   结果:", hit)

print("\n冒烟测试结束")
