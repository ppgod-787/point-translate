"""翻译层：多翻译源自动回退 + SQLite 缓存。

选源策略是实测出来的（国内网络）：
- translate.googleapis.com 连不通，已弃用；
- 单词走国内词典接口，60~200ms 秒回，释义也比机翻准；
- 句子走 Bing 翻译，约 700ms；
- MyMemory 兜底。

另外所有结果都进 SQLite 缓存，同一个词第二次是 0ms 本地命中。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
import urllib.parse

import requests

from config import CACHE_PATH, Config

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_ASCII_LETTER = re.compile(r"[A-Za-z]")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")

# 单词 vs 句子的分界：没有空格且不太长，就当单词查词典
_WORD_MAX_LEN = 24

# 译文采信闸门。词典源返回的是"释义串"，天然比原文长；但超过上限基本可以
# 断定对方返回了无关内容（实测有道会把 MFABD2 这类串答成一段百科式长文，
# 结果被永久写进缓存）。机翻那边则是防止把整段话抄回来。
_MAX_RESULT_LEN = {"iciba": 160, "youdao": 100}
_SHORT_RESULT_LEN = 60
_DICT_POS = re.compile(r"^(?:[a-z]{1,5}\.(?:/[a-z]{1,5}\.)?|网络释义|词组短语|柯林斯)", re.I)
_SENTENCE_MAX_GROWTH = 6.0
_SENTENCE_MAX_EXTRA = 120
_MAX_RESULT_LINES = 3

# OCR 常见误识：中文 OCR 引擎认英文时尤其爱犯这几个错
_OCR_CHAR_FIXES = (
    ("|", "l"),
    ("《", "I"),
    ("」", "I"),
    ("〔", "I"),
)

# 这些修正只在词首/整体匹配时做，避免误伤正常单词
_OCR_WORD_FIXES = {
    "lnstall": "Install",
    "lnstalling": "Installing",
    "lmage": "Image",
    "0n": "On",
    "0ff": "Off",
}


class _Cache:
    """SQLite 翻译缓存，带内存热层。"""

    def __init__(self, path=CACHE_PATH, max_memory: int = 2000):
        self._lock = threading.Lock()
        self._memory: dict[tuple[str, str, str], str] = {}
        self._max_memory = max_memory
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translations (
                key        TEXT PRIMARY KEY,
                source     TEXT NOT NULL,
                target     TEXT NOT NULL,
                original   TEXT NOT NULL,
                translated TEXT NOT NULL,
                provider   TEXT NOT NULL,
                hits       INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                used_at    REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_used ON translations(used_at DESC)"
        )
        self._conn.commit()

    @staticmethod
    def _key(text: str, source: str, target: str) -> str:
        return hashlib.sha256(f"{source}|{target}|{text}".encode()).hexdigest()

    def get(self, text: str, source: str, target: str) -> str | None:
        mem_key = (source, target, text)
        with self._lock:
            if mem_key in self._memory:
                return self._memory[mem_key]
            row = self._conn.execute(
                "SELECT translated FROM translations WHERE key = ?",
                (self._key(text, source, target),),
            ).fetchone()
            if not row:
                return None
            self._conn.execute(
                "UPDATE translations SET hits = hits + 1, used_at = ? WHERE key = ?",
                (time.time(), self._key(text, source, target)),
            )
            self._conn.commit()
            self._remember(mem_key, row[0])
            return row[0]

    def put(
        self, text: str, source: str, target: str, translated: str, provider: str
    ) -> None:
        key = self._key(text, source, target)
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO translations
                    (key, source, target, original, translated, provider, hits, created_at, used_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    translated = excluded.translated,
                    provider   = excluded.provider,
                    used_at    = excluded.used_at
                """,
                (key, source, target, text, translated, provider, now, now),
            )
            self._conn.commit()
            self._remember((source, target, text), translated)

    def _remember(self, mem_key: tuple[str, str, str], value: str) -> None:
        """必须在持锁状态下调用。"""
        if len(self._memory) >= self._max_memory:
            for old in list(self._memory)[: self._max_memory // 4]:
                self._memory.pop(old, None)
        self._memory[mem_key] = value

    def stats(self) -> dict[str, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
            hits = self._conn.execute(
                "SELECT COALESCE(SUM(hits), 0) FROM translations"
            ).fetchone()[0]
        return {"entries": int(total), "hits": int(hits)}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class Translator:
    # 单词优先走词典（快 + 释义准），句子走机翻
    WORD_CHAIN = ("iciba", "youdao", "bing", "mymemory")
    SENTENCE_CHAIN = ("bing", "mymemory")

    def __init__(self, config: Config):
        self.config = config
        self.cache = _Cache()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        self._bing_auth: tuple[int, str] | None = None
        self._bing_auth_at: float = 0.0
        self._bing_ig_value: str = ""
        self._bing_lock = threading.Lock()

    # ---------- 对外主入口 ----------

    def translate(
        self, text: str, reliable: bool = True, word_only: bool = False
    ) -> tuple[str, str] | None:
        """返回 (译文, 来源标记)，取不到返回 None。

        reliable=False 表示原文来自 OCR，可能认错字。这种情况下
        只读缓存、不写缓存，免得把识别错的垃圾结果永久存进去。
        word_only=True 表示用户只想要"指针下这个单词"，此时一律走词典链
        （哪怕这个词长过 _WORD_MAX_LEN，机翻单词的意义也不大）。
        """
        text = cleanup_ocr(text)
        if not self.should_translate(text):
            return None

        src, dst = self.config.source_lang, self.config.target_lang

        cached = self.cache.get(text, src, dst)
        if cached is not None:
            return cached, "cache"

        for name in self._chain_for(text, word_only=word_only):
            fn = _PROVIDERS.get(name)
            if fn is None:
                continue
            try:
                result = fn(self, text, src, dst)
            except Exception:
                continue
            if not result:
                continue
            result = result.strip()
            if _result_ok(name, text, result):
                if reliable:
                    self.cache.put(text, src, dst, result, name)
                return result, name
        return None

    def _chain_for(self, text: str, word_only: bool = False) -> tuple[str, ...]:
        """按 config.providers 决定候选源和顺序（README 里承诺过可以改顺序）。

        以前 providers 只是被 Config.load 校验一遍就没人用了，改它没有任何效果。
        现在它真正生效：不在列表里的源跳过，列表顺序就是尝试顺序；列表里写的源
        如果都不适用于当前的文本类型（比如只留了词典源但这句是句子），
        退回该类型的内置链，而不是直接返回"翻不了"。
        word_only=True（用户只要单词）时一律按单词处理。
        """
        chain = (
            self.WORD_CHAIN if (word_only or _is_word(text)) else self.SENTENCE_CHAIN
        )
        preferred = tuple(self.config.providers or ())
        if not preferred:
            return chain
        filtered = tuple(name for name in preferred if name in chain)
        return filtered or chain

    # ---------- 预热 ----------

    def warm_up(self, background: bool = True) -> None:
        """提前把 Bing 鉴权 token 拿好。

        第一次抓 token 要 ~600ms，如果拖到用户悬停时才做，
        第一次翻译会明显卡顿。开机就在后台把这事办了。
        """

        def _work() -> None:
            try:
                self._bing_token(force=True)
            except Exception:
                pass

        if background:
            threading.Thread(target=_work, daemon=True, name="translator-warmup").start()
        else:
            _work()

    # ---------- 文本预判 ----------

    @staticmethod
    def should_translate(text: str) -> bool:
        """挡掉纯数字、纯符号、已经是中文、以及文件名/路径。

        这些在界面上很常见（资源管理器、IDE、下载列表），
        翻译它们没有意义，还会白白发一次网络请求。
        """
        if len(text) < 2:
            return False
        if not _ASCII_LETTER.search(text):
            return False
        # 只要掺了少量中文就放过：界面上"Epic游戏英文界面怎么调中文"这类
        # 中英混合标题很常见，用户显然不需要把它翻一遍。
        # 纯英文界面不受影响（中文字符数为 0）。
        cjk = len(_CJK.findall(text))
        if cjk and cjk / max(len(text), 1) > 0.1:
            return False
        # 中英混排、英文只是零星几个词的标题（"编辑 DeepSeek (deepseek-official)"），
        # 机翻基本只会把英文原样抄回来、还多塞空格。英文词少于 4 个就当它不需要翻。
        if cjk and len(_ASCII_WORD.findall(text)) < 4:
            return False
        if _looks_like_path_or_filename(text):
            return False
        return True

    # ---------- 词典类源（适合单词） ----------

    def _iciba(self, text: str, src: str, dst: str) -> str | None:
        """金山词霸，实测最快（60~200ms）。"""
        resp = self.session.get(
            "https://dict-mobile.iciba.com/interface/index.php",
            params={
                "c": "word",
                "m": "getsuggest",
                "nums": 1,
                "is_need_mean": 1,
                "word": text,
            },
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        message = (resp.json().get("message") or [{}])[0]
        return _format_dictionary_entry(message.get("means") or [], message.get("paraphrase"))

    def _youdao(self, text: str, src: str, dst: str) -> str | None:
        """有道词典建议接口，约 220ms，返回中文释义串。"""
        resp = self.session.get(
            "https://dict.youdao.com/suggest",
            params={"num": 1, "doctype": "json", "q": text},
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if int((data.get("result") or {}).get("code", 0)) != 200:
            return None
        entries = (data.get("data") or {}).get("entries") or []
        if not entries:
            return None
        explain = (entries[0].get("explain") or "").strip()
        return explain or None

    # ---------- 机翻类源（适合句子） ----------

    def _bing_token(self, force: bool = False) -> tuple[int, str] | None:
        """拿 Bing 的鉴权参数。

        Bing 页面上暴露的是 params_AbusePreventionHelper = [key, token, ttl]，
        key 是页面生成时的毫秒时间戳，token 有效期 1 小时。
        实测：token 必须从页面抓；自己拿时间戳造假会返回 205 被拒。
        所以抓不到就返回 None，让上层直接换下一个翻译源，不浪费时间。
        """
        now = time.time()
        with self._bing_lock:
            if (
                not force
                and self._bing_auth
                and now - self._bing_auth_at < 3000  # 50 分钟内复用
            ):
                return self._bing_auth

            key, token = 0, ""
            try:
                page = self.session.get(
                    "https://cn.bing.com/translator", timeout=self.config.timeout
                )
                page.raise_for_status()
                m = re.search(
                    r"params_AbusePreventionHelper\s*=\s*\[\s*(\d+)\s*,\s*\"([^\"]+)\"",
                    page.text,
                )
                if m:
                    key, token = int(m.group(1)), m.group(2)
                ig = re.search(r'IG:"([^"]+)"', page.text)
                if ig:
                    self._bing_ig_value = ig.group(1)
            except Exception:
                pass

            if not token:
                return None

            self._bing_auth = (key, token)
            self._bing_auth_at = now
            return self._bing_auth

    def _bing_ig(self) -> str:
        if self._bing_ig_value:
            return self._bing_ig_value
        try:
            page = self.session.get(
                "https://cn.bing.com/translator", timeout=self.config.timeout
            )
            m = re.search(r'IG:"([^"]+)"', page.text)
            self._bing_ig_value = m.group(1) if m else ""
        except Exception:
            self._bing_ig_value = ""
        return self._bing_ig_value

    def _bing(self, text: str, src: str, dst: str) -> str | None:
        auth = self._bing_token()
        if not auth:
            return None
        key, token = auth
        params = {
            "isVertical": "1",
            "IG": self._bing_ig(),
            "IID": "translator.5023",
        }
        payload = {
            "fromLang": src or "en",
            "text": text,
            "to": "zh-Hans" if dst.startswith("zh") else dst,
            "token": token,
            "key": key,
        }
        resp = self.session.post(
            "https://cn.bing.com/ttranslatev3",
            params=params,
            data=payload,
            timeout=self.config.timeout,
            headers={"Referer": "https://cn.bing.com/translator"},
        )
        resp.raise_for_status()
        data = resp.json()

        # token 过期时会回 205，强刷一次再试
        if isinstance(data, dict) and int(data.get("statusCode", 0)) == 205:
            auth = self._bing_token(force=True)
            if auth:
                payload["key"], payload["token"] = auth
                resp = self.session.post(
                    "https://cn.bing.com/ttranslatev3",
                    params=params,
                    data=payload,
                    timeout=self.config.timeout,
                    headers={"Referer": "https://cn.bing.com/translator"},
                )
                data = resp.json()

        if not isinstance(data, list) or not data:
            return None
        first = data[0]
        if not isinstance(first, dict):
            return None
        translations = first.get("translations") or []
        if not translations:
            return None
        return (translations[0].get("text") or "").strip() or None

    def _mymemory(self, text: str, src: str, dst: str) -> str | None:
        params = {
            "q": text[:480],
            "langpair": f"{src or 'en'}|{'zh-CN' if dst.startswith('zh') else dst}",
        }
        resp = self.session.get(
            "https://api.mymemory.translated.net/get",
            params=params,
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if int(data.get("responseStatus", 0)) != 200:
            return None
        translated = (data.get("responseData") or {}).get("translatedText") or ""
        if not translated or "MYMEMORY WARNING" in translated.upper():
            return None
        return translated


_PROVIDERS = {
    "iciba": Translator._iciba,
    "youdao": Translator._youdao,
    "bing": Translator._bing,
    "mymemory": Translator._mymemory,
}


def _result_ok(name: str, text: str, result: str) -> bool:
    """判断某个源给的译文值不值得采信（长度、行数、是否原地踏步）。"""
    if not result:
        return False
    if result.lower() == text.lower():
        return False
    if result.count("\n") >= _MAX_RESULT_LINES:
        return False
    limit = _MAX_RESULT_LEN.get(name)
    if limit is not None:
        if len(result) > limit:
            return False
        # 词典源的长结果必须是"词性 + 释义"的形态（n. / vt. / adj. …）；
        # 否则多半是接口把无关的网络释义、百科段落塞进了释义字段。
        return len(result) <= _SHORT_RESULT_LEN or bool(_DICT_POS.match(result))
    return len(result) <= len(text) * _SENTENCE_MAX_GROWTH + _SENTENCE_MAX_EXTRA


def _is_word(text: str) -> bool:
    """没空格且不太长 → 当单词处理，走词典接口。"""
    if len(text) > _WORD_MAX_LEN:
        return False
    return " " not in text.strip()


# 形如 xxx.py / setup.exe / README.md 的文件名
_FILENAME = re.compile(r"^[\w\-. ]+\.(py|exe|dll|md|txt|json|ini|cfg|log|zip|rar|7z|png|jpg|jpeg|gif|webp|mp4|mkv|mp3|pdf|docx?|xlsx?|pptx?|csv|xml|ya?ml|toml|js|ts|tsx|jsx|html?|css|scss|c|cpp|h|hpp|cs|java|go|rs|rb|php|sh|bat|ps1|sql|db|iso|bin|dat|sav|pak|uasset|url|lnk|torrent)$", re.I)

# 形如 C:\Users\... 或 /usr/local/... 的路径
_PATHLIKE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/[\w.]+/)")

# 形如 owner/repo 的标识符（GitHub 列表、终端输出里很常见）
_SLUG = re.compile(r"^[\w.\-]+/[\w.\-]+$")

# 英文单词（含缩写和连字符），用来判断中英混排里"英文到底有多少"
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def _looks_like_path_or_filename(text: str) -> bool:
    """文件名、路径、版本号、URL 之类的内容翻不出有意义的结果。"""
    stripped = text.strip()
    if not stripped:
        return True
    if _PATHLIKE.match(stripped):
        return True
    # 以文件名结尾（可能带路径前缀）
    tail = stripped.split("\\")[-1].split("/")[-1]
    if _FILENAME.match(tail):
        return True
    # 纯 URL
    if re.match(r"^https?://", stripped, re.I):
        return True
    # owner/repo 这类标识符。带 "/" 但本身是正常词组的（and/or、24/7）
    # 不该被挡，所以额外要求里面有字母、且有数字或连字符/下划线。
    if (
        _SLUG.match(stripped)
        and _ASCII_LETTER.search(stripped)
        and any(ch.isdigit() or ch in "-_" for ch in stripped)
    ):
        return True
    # 版本号：v1.2.3 / 1.0.0
    if re.match(r"^v?\d+(\.\d+){1,3}$", stripped, re.I):
        return True
    return False


def _format_dictionary_entry(means: list[dict], paraphrase: str = "") -> str | None:
    """把词典返回的词性+释义拼成一行紧凑中文。"""
    parts: list[str] = []
    for group in means:
        pos = (group.get("part") or "").strip()
        words = [w.strip() for w in (group.get("means") or []) if w and w.strip()]
        if not words:
            continue
        joined = "，".join(words[:3])
        parts.append(f"{pos} {joined}".strip())
    if parts:
        return "；".join(parts[:2])
    return (paraphrase or "").strip() or None


def normalize(text: str) -> str:
    """压掉 OCR / UIA 带出来的多余空白和装饰字符。"""
    # U+FFFC 是富文本控件的"对象占位符"，没有任何信息量
    text = text.replace("\ufffc", " ").replace("\u200b", " ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\r\n·•|_-—")


_ALNUM_SPACED = re.compile(r"\b(?:[A-Za-z] ){2,}[A-Za-z]\b")
_ALL_CAPS_WORD = re.compile(r"\b[A-Z]{3,}\b")


def cleanup_ocr(text: str) -> str:
    """修正中文 OCR 引擎识别英文时的典型错误。

    实测最影响查词的三种错误：
    1. 大写 I 被认成《、「、| 之类；
    2. 字母被拆开，比如 "n Ot"、"N ow"；
    3. Install 首字母 I 被认成小写 l。
    """
    text = normalize(text)
    for bad, good in _OCR_CHAR_FIXES:
        text = text.replace(bad, good)
    text = text.replace("l'", "I'").replace("l’", "I’")

    # 把被拆散的字母拼回去："N ow" -> "Now"，"n Ot" -> "not"
    def _join(match: re.Match[str]) -> str:
        return match.group(0).replace(" ", "")

    text = _ALNUM_SPACED.sub(_join, text)

    # 整词修正常见误识
    def _fix_word(match: re.Match[str]) -> str:
        word = match.group(0)
        if word in _OCR_WORD_FIXES:
            return _OCR_WORD_FIXES[word]
        low = word.lower()
        if low in _OCR_WORD_FIXES:
            return _OCR_WORD_FIXES[low]
        # 词首 "ln" 基本是 "In" 被认错
        if low.startswith("ln") and len(word) > 3:
            return "In" + word[2:]
        return word

    text = re.sub(r"\b[A-Za-z]{2,}\b", _fix_word, text)
    return normalize(text)


def screen_key(text: str) -> str:
    """生成用于"这行字刚翻过"判断的比较键：忽略大小写和空格。"""
    return re.sub(r"[^a-z0-9]", "", text.lower())


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = Config()
    cfg.timeout = 5.0
    tr = Translator(cfg)
    samples = [
        "Library",
        "Install",
        "Free Now",
        "This game is not available in your region.",
        "12345",
        "库",
    ]
    for s in samples:
        t0 = time.perf_counter()
        out = tr.translate(s)
        dt = (time.perf_counter() - t0) * 1000
        print(f"{s!r:45} -> {out}   ({dt:.0f} ms)")
    print("\n再跑一遍（验证缓存）:")
    for s in samples[:2]:
        t0 = time.perf_counter()
        out = tr.translate(s)
        print(f"{s!r:45} -> {out}   ({(time.perf_counter()-t0)*1000:.0f} ms)")
    print("\ncache stats:", tr.cache.stats())
