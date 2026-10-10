#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收件箱扫描 —— 「我说『更新』= 扫 Downloads 和 Zotero 找新课程文件」的机械部分。
只扫描和汇报，不移动任何文件（归档判断仍是人/AI 的活）。

用法：
    python3 shared/工具/收件箱.py            # 默认 48 小时内
    python3 shared/工具/收件箱.py 24         # 最近 24 小时

默认扫 ~/Downloads（不递归）和 ~/Zotero/storage（递归）。想换目录 / 加文件类型，
在 shared/配置.json 里加可选字段（不加就用默认）：
    "inbox_dirs": [{"path": "~/Desktop/课件", "recursive": false}, ...]
    "inbox_extra_ext": {".mwb": "🗄️ 模型文件", ".zip": "📦 压缩包"}
"""
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import 配置
    _CFG = 配置.CFG
except (SystemExit, Exception):             # noqa: BLE001  配置缺失不挡扫描，退回默认
    _CFG = {}

try:
    HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 48
except ValueError:
    sys.exit(f"用法: python3 shared/工具/收件箱.py [小时数]   （收到的参数不是数字：{sys.argv[1]!r}）")
CUTOFF = time.time() - HOURS * 3600

KIND = {
    ".pdf": "📄 PDF（课件/tute/作业?）",
    ".mp3": "🎙️ 音频", ".m4a": "🎙️ 音频", ".wav": "🎙️ 音频", ".mp4": "🎙️ 音/视频", ".mov": "🎙️ 音/视频",
    ".png": "🖼️ 图片", ".jpg": "🖼️ 图片", ".jpeg": "🖼️ 图片", ".heic": "🖼️ 图片",
    ".srt": "📝 字幕", ".txt": "📝 文本", ".md": "📝 文本",
    ".epub": "📄 epub",
}
KIND.update({k.lower(): v for k, v in _CFG.get("inbox_extra_ext", {}).items()})

SCAN = [
    (Path.home() / "Downloads", False),      # 不递归
    (Path.home() / "Zotero/storage", True),  # 递归（一层 key 目录）
]
if _CFG.get("inbox_dirs"):
    SCAN = [(Path(os.path.expanduser(d["path"])), bool(d.get("recursive", False)))
            for d in _CFG["inbox_dirs"] if isinstance(d, dict) and d.get("path")]


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}TB"


def rows_for(base: Path, recursive: bool):
    if not base.exists():
        print(f"（{base} 不存在，跳过 —— 换机器了的话正常）")
        return []
    it = base.rglob("*") if recursive else base.iterdir()
    out = []
    for p in it:
        try:
            if not p.is_file() or p.name.startswith("."):
                continue
            st = p.stat()
            if st.st_mtime < CUTOFF:
                continue
            kind = KIND.get(p.suffix.lower())
            if kind is None:
                continue
            out.append((st.st_mtime, p, kind, st.st_size))
        except OSError:
            continue
    return sorted(out, reverse=True)


if __name__ == "__main__":
    print(f"最近 {HOURS:.0f} 小时的新文件（现在 {datetime.now():%m-%d %H:%M}）：\n")
    total = 0
    for base, rec in SCAN:
        rows = rows_for(base, rec)
        total += len(rows)
        if rows:
            print(f"── {base}")
            for mt, p, kind, size in rows:
                rel = p.relative_to(base)
                print(f"  {datetime.fromtimestamp(mt):%m-%d %H:%M}  {kind:<18} {fmt_size(size):>7}  {rel}")
            print()
    if total == 0:
        print("（没有新文件）")
    else:
        print("⚠️ 只扫描不移动。归档去向按 CLAUDE.md「资料流」那节判断；音频转录用 shared/工具/转录.sh。")
