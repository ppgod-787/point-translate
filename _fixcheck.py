"""离线回归检查：验证本轮修复的纯逻辑（不需要 GUI、不联网、不写仓库文件）。

用法：  .venv\\Scripts\\python.exe _fixcheck.py
覆盖：  配置类型归一/夹取/原子保存、providers 生效、译文采信闸门、
        路径与混排过滤、OCR 截图区域与挑行位置。
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [OK] {label}")
    else:
        print(f"  [!!] {label} {detail}")
        FAILED.append(label)


def eq(label: str, got, want) -> None:
    check(label, got == want, f"(got {got!r}, want {want!r})")


# ---------------------------------------------------------------- 配置
def test_config() -> None:
    print("\n1) config：类型归一 / 范围夹取 / 原子保存")
    import config

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        original_path = config.CONFIG_PATH
        config.CONFIG_PATH = path
        try:
            path.write_text(
                json.dumps(
                    {
                        "hover_seconds": "2.5",
                        "poll_interval": "0.05",
                        "move_tolerance": "8",
                        "use_uia": "false",
                        "enabled": "no",
                        "opacity": "abc",
                        "timeout": 0.1,
                        "providers": ["bing", "google"],
                        "max_width": 99999,
                        "ocr_padding": -5,
                        "unknown_key": 1,
                    }
                ),
                encoding="utf-8",
            )
            cfg = config.Config.load()
            eq("字符串数字 -> float", cfg.hover_seconds, 2.5)
            eq("字符串小数 -> float", cfg.poll_interval, 0.05)
            eq("字符串整数 -> int", cfg.move_tolerance, 8)
            check("字符串 int 字段仍是 int", isinstance(cfg.move_tolerance, int))
            eq('"false" -> False', cfg.use_uia, False)
            eq('"no" -> False', cfg.enabled, False)
            eq("无法归一的值保留默认", cfg.opacity, 0.96)
            eq("小于下限被夹取", cfg.timeout, 0.5)
            eq("大于上限被夹取", cfg.max_width, 1600)
            eq("负值被夹到 0", cfg.ocr_padding, 0)
            eq("无效翻译源被过滤", cfg.providers, ["bing"])
            check("未知键被丢弃", not hasattr(cfg, "unknown_key"))

            cfg.save()
            eq("保存后可原样读回", config.Config.load(), cfg)
            leftovers = [p.name for p in Path(tmp).iterdir() if p.name != "config.json"]
            eq("原子写不留临时文件", leftovers, [])

            path.write_text("{ 这不是 json", encoding="utf-8")
            eq("坏 json 回退默认", config.Config.load().hover_seconds, 1.0)
            path.write_text("[1, 2, 3]", encoding="utf-8")
            eq("非对象 json 回退默认", config.Config.load().enabled, True)

            # 记事本"另存为 UTF-8"会带 BOM：以前会整份回默认，现在要能正常读
            path.write_text(
                '{"hover_seconds": 0.05, "providers": ["bing"]}', encoding="utf-8-sig"
            )
            bom_cfg = config.Config.load()
            eq("带 BOM 的配置能读", bom_cfg.hover_seconds, 0.1)
            eq("带 BOM 的配置项生效", bom_cfg.providers, ["bing"])
        finally:
            config.CONFIG_PATH = original_path


# ---------------------------------------------------------------- 翻译链
def test_providers() -> None:
    print("\n2) translate：providers 真正生效")
    from translate import Translator

    def chain(providers, text):
        tr = Translator.__new__(Translator)  # 不走 __init__，避免建 sqlite 缓存
        tr.config = types.SimpleNamespace(providers=providers)
        return tr._chain_for(text)

    eq("只留 bing：单词链也只剩 bing", chain(["bing"], "Library"), ("bing",))
    eq("句子链按配置过滤", chain(["bing"], "Read the docs"), ("bing",))
    eq("配置里的顺序就是尝试顺序", chain(["mymemory", "bing"], "Read the docs"), ("mymemory", "bing"))
    eq("不适用的源被过滤掉", chain(["iciba", "bing"], "Read the docs"), ("bing",))
    eq("配置里没有可用源 -> 退回完整链", chain(["iciba"], "Read the docs"), Translator.SENTENCE_CHAIN)
    eq("空配置 -> 默认链", chain([], "Library"), Translator.WORD_CHAIN)


# ---------------------------------------------------------------- 采信闸门
def test_result_gate() -> None:
    print("\n3) translate：译文采信闸门")
    from translate import _result_ok

    junk = "澳门发展队（澳门发展队是由澳门足球总会建立的，旨在培养年轻球员，参加澳门甲组足球联赛。其中 6 名 16 岁以上的球员，可以注册为超龄球员）"
    medium = "无关内容" * 16 + "。"  # 超过短阈值但没有词性标记
    check("有道返回的百科式长文被拒", not _result_ok("youdao", "MFABD2", junk), f"len={len(junk)}")
    check("越过短阈值且无词性标记被拒", not _result_ok("youdao", "MFABD2", medium), f"len={len(medium)}")
    check("短结果不做形态校验（刻意取舍）", _result_ok("youdao", "MFABD2", "n. 缩写；编号"))
    check("有道正常短释义放行", _result_ok("youdao", "Notifications", "n. 通知；通告"))
    check(
        "带有词性标记的长释义放行",
        _result_ok("youdao", "overwhelmed", "v. 淹没，漫过（ overwhelm的过去式和过去分词 ），压倒，击败，使受打击，覆盖；adj. 被淹没的，被压倒的，不知所措的"),
    )
    check(
        "词霸长释义放行",
        _result_ok("iciba", "personalized", "v. " + "个性化，" * 25),
    )
    check(
        "词霸超长释义被拒",
        _result_ok("iciba", "personalized", "v. " + "个性化，" * 45) is False,
    )
    check("译文等于原文被拒", not _result_ok("bing", "Clone it", "clone IT"))
    check("多行结果被拒", not _result_ok("bing", "a b c", "x\ny\nz\nw"))
    check("机翻正常长度放行", _result_ok("bing", "Clone a repository from the Internet…", "从互联网上克隆一个仓库…"))
    check("机翻异常膨胀被拒", not _result_ok("mymemory", "x" * 100, "y" * 900))
    check("空结果被拒", not _result_ok("bing", "abc", ""))


# ---------------------------------------------------------------- 过滤
def test_filters() -> None:
    print("\n4) translate：路径 / 标识符 / 中英混排过滤")
    from translate import Translator, _looks_like_path_or_filename

    check("仓库标识符被挡", _looks_like_path_or_filename("ppgod-787/point-translate"))
    check("普通词组 and/or 不被挡", not _looks_like_path_or_filename("and/or"))
    check("数字比 24/7 不被挡", not _looks_like_path_or_filename("24/7"))
    check("文件名被挡", _looks_like_path_or_filename("setup.exe"))
    check("windows 路径被挡", _looks_like_path_or_filename(r"C:\Users\me\a.txt"))
    check("URL 被挡", _looks_like_path_or_filename("https://example.com/a"))
    check("版本号被挡", _looks_like_path_or_filename("v1.2.3"))
    check("正常英文句子放行", not _looks_like_path_or_filename("Read the docs"))

    st = Translator.should_translate
    check("中英混排标题被挡", not st("编辑 DeepSeek (deepseek-official)"))
    check("纯英文按钮放行", st("Notifications"))
    check("正常英文句子放行", st("Clone a repository from the Internet…"))
    check("纯数字被挡", not st("12345"))
    check("纯中文被挡", not st("库"))


# ---------------------------------------------------------------- OCR 区域
def test_ocr_region() -> None:
    print("\n5) ocr：pad 生效 + 光标在截图中的真实行位置")
    from extractors import ocr

    calls: dict[str, tuple] = {}
    real = (ocr.winutil.clamp_to_virtual, ocr.winutil.grab_region_raw,
            ocr.winutil.raw_to_software_bitmap, ocr._recognize)

    class _Rect:
        def __init__(self, x, y, w=120.0, h=20.0):
            self.x = x
            self.y = y
            self.width = w
            self.height = h

    class _Word:
        def __init__(self, text, x, y):
            self.text = text
            self.bounding_rect = _Rect(x, y)

    class _Line:
        def __init__(self, text, y):
            self.text = text
            first, second = text.split()[0], text.split()[-1]
            # 每行两个词：x=20..140 / x=150..270
            self.words = [_Word(first, 20.0, y), _Word(second, 150.0, y)]

    try:
        def fake_clamp(x, y, w, h):
            calls["clamp"] = (x, y, w, h)
            return x, y, w, h

        def fake_grab(left, top, w, h):
            calls["grab"] = (left, top, w, h)
            return b"\0" * 4, w, h

        async def fake_recognize(engine, bitmap):
            # 三行文字，光标对准中间那行
            return types.SimpleNamespace(
                lines=[_Line("top line", 10.0), _Line("middle line", 70.0), _Line("bottom line", 130.0)]
            )

        ocr.winutil.clamp_to_virtual = fake_clamp
        ocr.winutil.grab_region_raw = fake_grab
        ocr.winutil.raw_to_software_bitmap = lambda raw, w, h: object()
        ocr._recognize = fake_recognize

        extractor = ocr.OcrExtractor(pad=14, width=420, height=90)
        extractor._get_engine = lambda: object()  # type: ignore[method-assign]

        result = extractor.read_at(1000, 500)
        eq("截图左上角按 pad 外扩", calls["grab"][:2], (1000 - 210 - 14, 500 - 45 - 14))
        eq("截图尺寸含 pad", calls["grab"][2:], (420 + 28, 90 + 28))
        eq("挑中的是光标所在行", result.text if result else None, "middle line")

        # 光标 (1000, 515) -> 截图中 (224, 74)，落在中间行第二个词上
        word_result = extractor.read_at(1000, 515, word_only=True)
        eq("单词模式取到指针下那个词", word_result.text if word_result else None, "line")
    finally:
        (ocr.winutil.clamp_to_virtual, ocr.winutil.grab_region_raw,
         ocr.winutil.raw_to_software_bitmap, ocr._recognize) = real


# ---------------------------------------------------------------- 单词模式
def test_word_mode() -> None:
    print("\n6) 单词模式：整句翻译关闭时只取指针下的单词")
    import config
    from extractors.ocr import _word_at
    from extractors.uia import _acceptable_word
    from translate import Translator

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        original = config.CONFIG_PATH
        config.CONFIG_PATH = path
        try:
            path.write_text('{"whole_sentence": "false"}', encoding="utf-8")
            eq("config 里的 whole_sentence 生效", config.Config.load().whole_sentence, False)
            path.write_text("{}", encoding="utf-8")
            eq("默认是翻译整句", config.Config.load().whole_sentence, True)
        finally:
            config.CONFIG_PATH = original

    tr = Translator.__new__(Translator)  # 不走 __init__，避免建 sqlite 缓存
    tr.config = types.SimpleNamespace(providers=[])
    long_word = "electroencephalographically"  # 28 字符，超过 _WORD_MAX_LEN
    eq("单词模式下长词仍走词典链", tr._chain_for(long_word, word_only=True), Translator.WORD_CHAIN)
    eq("整句模式下同一串走机翻链", tr._chain_for(long_word), Translator.SENTENCE_CHAIN)

    check("UIA 接受普通单词", _acceptable_word("Install"))
    check("UIA 接受带撇号的词", _acceptable_word("don't"))
    check("UIA 拒绝词组", not _acceptable_word("add collaborators"))
    check("UIA 拒绝空白", not _acceptable_word("   "))
    check("UIA 拒绝纯数字", not _acceptable_word("12345"))
    check("UIA 拒绝超长串", not _acceptable_word("a" * 41))
    check("UIA 会裁掉首尾空白", _acceptable_word("  Word  "))

    class _Rect:
        def __init__(self, x, y, w=100.0, h=20.0):
            self.x = x
            self.y = y
            self.width = w
            self.height = h

    class _Word:
        def __init__(self, text, x, y):
            self.text = text
            self.bounding_rect = _Rect(x, y)

    class _Line:
        def __init__(self, words):
            self.text = " ".join(w.text for w in words)
            self.words = words

    line = _Line([_Word("alpha", 0.0, 40.0), _Word("beta", 120.0, 40.0), _Word("gamma", 240.0, 40.0)])
    eq("命中包围盒内的词", _word_at([line], 150, 50), "beta")
    eq("落在词间空隙时取最近的词", _word_at([line], 235, 50), "gamma")
    eq("完全偏离时仍取最近的词", _word_at([line], 10, 400), "alpha")
    eq("没有词时返回 None", _word_at([_Line([])], 100, 50), None)


# ---------------------------------------------------------------- 入口
def main() -> int:
    print("=" * 60)
    test_config()
    test_providers()
    test_result_gate()
    test_filters()
    test_ocr_region()
    test_word_mode()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for name in FAILED:
            print("  -", name)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
