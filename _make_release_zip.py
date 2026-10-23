"""用规范的 ZIP 格式打包发布压缩包。

为什么不用 PowerShell 的 Compress-Archive：
它在 Windows 上会把条目路径写成反斜杠（PointTranslate\\start.bat），
这违反 ZIP 规范（APPNOTE 要求正斜杠）。Windows 资源管理器能容忍，
但 7-Zip、macOS、Linux 的解压工具可能解出一个名字里带反斜杠的畸形文件。
zipfile 模块始终使用正斜杠，且能保留 UTF-8 文件名标志。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DIST = Path(r"C:\Users\65121\Documents\dsh workspace\point-translate\dist")
SRC = DIST / "PointTranslate"
OUT = DIST / "PointTranslate-v0.1.0-win64.zip"

# 这些是本机运行时产物，不该发给用户
SKIP_NAMES = {"cache.sqlite3", "cache.sqlite3-journal", "selftest.txt", "config.json"}
SKIP_SUFFIXES = {".pyc", ".log"}

if not SRC.is_dir():
    print(f"源目录不存在: {SRC}")
    raise SystemExit(1)

if OUT.exists():
    OUT.unlink()

count = 0
skipped = []
with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for path in sorted(SRC.rglob("*")):
        if path.is_dir():
            continue
        if path.name in SKIP_NAMES:
            skipped.append(path.name)
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            skipped.append(path.name)
            continue

        # 压缩包内路径：外层包一层 PointTranslate/，条目用正斜杠
        arcname = Path("PointTranslate") / path.relative_to(SRC)
        arcname = arcname.as_posix()
        # 中文文件名需要 UTF-8 标志，zipfile 对非 ASCII 名称会自动设置 flag bit 11
        zf.write(path, arcname)
        count += 1

size_mb = OUT.stat().st_size / 1024 / 1024
print(f"已生成: {OUT.name}")
print(f"  条目数: {count}")
print(f"  大小:   {size_mb:.1f} MB")
if skipped:
    print(f"  跳过:   {', '.join(sorted(set(skipped)))}")

# 自检：确认所有条目都用正斜杠
bad = []
with zipfile.ZipFile(OUT) as zf:
    for info in zf.infolist():
        if "\\" in info.filename:
            bad.append(info.filename)
        if info.filename.count("/") == 0:
            bad.append(f"(无目录层级) {info.filename}")
print(f"  路径规范检查: {'通过，全部正斜杠' if not bad else '失败 ' + str(bad[:5])}")

# 自检：确认中文文件名能正确读回
with zipfile.ZipFile(OUT) as zf:
    names = zf.namelist()
exe = [n for n in names if n.endswith(".exe")]
print(f"  exe 条目: {exe[0] if exe else '未找到'}")
