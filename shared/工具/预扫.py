#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
预扫 —— 拿到一份新课件时，先机械扫清楚「它是什么」，再排计划、再开讲。

用法：
    python3 shared/工具/预扫.py <课件.pdf> [--full]

输出（stdout，人读 + 直接抄进 <课程>/课件/_预扫/<deck>.md）：
    1. 物理页数
    2. 页脚号 vs 物理页的 offset（实测，多数表决）
    3. 动画版检测：按文字签名对相邻页去重 → 真内容有多少个不同画面
    4. 是不是续篇（第一个带页脚号的正文页，页脚号是不是 1）
    5. 逐页骨架：每页标题 + 一句话内容

判据全部来自 pdftotext -layout 的实际文字，不猜。
"""
import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 版权页脚行：标题/正文判定时要滤掉。基础词表是通用的；学校自己的页脚措辞
# （例如 "University of XXX"）写进 shared/配置.json 的可选字段 "prescan_footer_patterns": ["university of xxx", ...]
_FOOTER = [r"copyright", r"creative commons", r"©", r"all rights reserved"]
try:
    import 配置
    _FOOTER += [str(x) for x in 配置.CFG.get("prescan_footer_patterns", [])]
except (SystemExit, Exception):             # noqa: BLE001  配置缺失不挡预扫
    pass
COPYRIGHT_PAT = re.compile("|".join(_FOOTER), re.I)
INT_TOKEN = re.compile(r"^\d{1,3}$")


def page_count(pdf: str) -> int:
    out = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def page_texts(pdf: str, n: int):
    """逐页取正文，返回 list[list[str]]（每页的非空行）"""
    pages = []
    for p in range(1, n + 1):
        raw = subprocess.run(
            ["pdftotext", "-layout", "-f", str(p), "-l", str(p), pdf, "-"],
            capture_output=True, text=True).stdout
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        pages.append(lines)
    return pages


def int_tokens(lines):
    """这一页出现的所有「独立整数」—— 页脚号一定在里面"""
    toks = set()
    for ln in lines:
        for t in re.split(r"\s+", ln):
            t = t.strip(".)：:[]-–—")   # 🔒 database 那套 deck 页脚印成 `-12-`，不剥短横会整份判成「没有页脚页码」（09-24 加）
            if INT_TOKEN.match(t):
                toks.add(int(t))
    return toks


FRAME_RE = re.compile(r"\b(\d{1,3})\s*/\s*(\d{1,3})\b")


def detect_frame_counter(pages):
    """🔒 beamer 的 `N / M` 页脚是 **frame 计数**，不是页码（2026-09-24 加）。

    不识别它会连错两件事：① offset 多数表决把 N 当页号，判出假的「viewer 页 = 页脚页」
    ② 动画重复帧算不出来（algorithms 那套 beamer deck 上 w08-L1 / w08-L2 / w09-L1 三份全判错）。
    判据：同一个 M 在过半的页上出现，且 M ≤ 物理页数。返回 (M, 命中页数) 或 (None, 0)。
    """
    from collections import Counter
    c = Counter()
    for lines in pages:
        ms = {int(m.group(2)) for ln in lines for m in [FRAME_RE.search(ln)] if m}
        for m in ms:
            c[m] += 1
    if not c:
        return None, 0
    m, hits = c.most_common(1)[0]
    n = len(pages)
    if m <= n and hits >= max(3, n * 0.5):
        return m, hits
    return None, 0


def detect_offset(pages):
    """页脚号 = 物理页 + offset。对每个候选 offset 做多数表决。"""
    best, best_hits = 0, -1
    for k in range(-5, 80):
        hits = sum(1 for i, lines in enumerate(pages, start=1)
                   if (i + k) in int_tokens(lines))
        if hits > best_hits:
            best, best_hits = k, hits
    return best, best_hits


def signature(lines):
    """
    文字签名：滤掉页脚号、版权行、图里的单字母节点，
    剩下的正文小写去空白。相邻页签名相同 = 同一张 slide 的动画帧。
    """
    keep = []
    for ln in lines:
        if COPYRIGHT_PAT.search(ln):
            continue
        toks = [t for t in re.split(r"\s+", ln)
                if len(t) > 1 and not INT_TOKEN.match(t.strip(".)"))]
        if toks:
            keep.append(" ".join(toks))
    s = re.sub(r"[^a-z0-9一-鿿]+", "", " ".join(keep).lower())
    return s


def title_of(lines):
    for ln in lines:
        if COPYRIGHT_PAT.search(ln):
            continue
        t = ln.strip()
        if INT_TOKEN.match(t):
            continue
        if len(t) > 1:
            return t[:70]
    return "(无文字 —— 纯图/空白页)"


def body_of(lines, title):
    out = []
    for ln in lines:
        if COPYRIGHT_PAT.search(ln) or ln.strip() == title or INT_TOKEN.match(ln.strip()):
            continue
        t = re.sub(r"\s+", " ", ln.strip())
        t = re.sub(r"^[◼❑•\-–▪‣*]+\s*", "", t)
        if len(t) > 1:
            out.append(t)
    return " / ".join(out)


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith('-'):
        print(__doc__)
        sys.exit(1)
    pdf = sys.argv[1]
    full = "--full" in sys.argv
    if not Path(pdf).is_file():
        sys.exit(f"❌ 找不到 PDF：{pdf}")
    miss = [t for t in ("pdfinfo", "pdftotext") if not shutil.which(t)]
    if miss:
        sys.exit(f"❌ 找不到 {' / '.join(miss)}（poppler 工具包）。macOS: brew install poppler · "
                 f"Debian/Ubuntu: sudo apt install poppler-utils")

    # 🔒 2026-09-02 加的：--save 直接落盘到 <课程>/课件/_预扫/<deck>.md。
    #    为什么：脚本本来只打 stdout，靠人手抄，结果**漏抄过两次**
    #    （08-26 algorithms w05-L1 只落台账没存文件，09-02 W6 七份全没落盘）。
    #    机械的事交给机械做。
    if "--save" in sys.argv:
        dest = Path(pdf).parent / '_预扫' / (Path(pdf).stem + '.md')
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 🔒 2026-09-04 加的：--save 顺手把全文落成 <deck>.txt（带页标），不管 .md 在不在。
        #    为什么：预扫 agent 自己逐页 pdftotext 把上下文撑到二十万字，每个 agent 烧 10–14 M token；
        #    文字层先落盘，agent 只读文件、只 grep `_全课文字层.txt`。索引由 shared/工具/文字层索引.py 重建。
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from 文字层索引 import dump_deck  # noqa
            dump_deck(Path(pdf), dest.with_suffix('.txt'))
            print(f'✅ 文字层已写入 {dest.with_suffix(".txt")}（记得跑 python3 shared/工具/文字层索引.py {Path(pdf).parts[-3]} 重建索引）')
        except Exception as e:  # 别让 txt 失败挡住摘要
            print(f'⚠️ 文字层 txt 没写成：{e}')
        # 🔴 2026-09-03 加的存在性保护：这个函数是整份 write_text()，
        #    而已有的预扫文件里常常有**只存在于那儿的人工内容**
        #    （人工三项 · 📗 其他资料对应段落 · 估时 · 前置警告的人工判断）。
        #    09-03 审计当场抓到：algorithms/课件/_预扫/w05-L1.md 就是这种。
        #    直接覆写 = 无声丢掉人工层。要重扫必须显式 --force，并自己先把人工节抄出来。
        if dest.exists() and "--force" not in sys.argv:
            print(f'⚠️ {dest} 已存在，**没有覆盖**。')
            print('   它可能含只在那儿的人工内容（人工三项 · 📗 其他资料 · 估时 · 前置警告）。')
            print('   要重扫：先把人工节抄出来，再加 --force 重跑。')
            return
        buf = io.StringIO()
        _real = sys.stdout
        sys.stdout = buf
        try:
            _emit(pdf, full)
        finally:
            sys.stdout = _real
        dest.write_text(buf.getvalue(), encoding='utf-8')
        print(f'✅ 预扫摘要已写入 {dest}')
        return
    _emit(pdf, full)


def _emit(pdf, full):
    n = page_count(pdf)
    if n <= 0:
        sys.exit(f"❌ pdfinfo 读不出页数：{pdf}（文件损坏或加密？）")
    pages = page_texts(pdf, n)
    off, hits = detect_offset(pages)
    frames, fhits = detect_frame_counter(pages)

    print(f"# 预扫 {Path(pdf).stem}")
    print(f"\n- 物理页数：**{n}**")
    has_footer = hits >= max(3, n * 0.5) and frames is None
    if frames is not None:
        print(f"- 🔴🔴 **这份 deck 的页脚是 beamer 的 frame 计数 `N / {frames}`，不是页码**"
              f"（{fhits}/{n} 页命中）→ **不存在页脚页码，也不存在 offset；viewer 物理页是唯一一套页码**")
        print(f"- 🔑 **真内容 = {frames} 个 frame**，物理 {n} 页里有 **{n - frames} 页是动画重复帧** —— "
              f"分块和估时按 {frames} 算，不是按 {n}")
        off = 0
    elif has_footer:
        print(f"- **页脚 offset = {off:+d}**（页脚号 = 物理页 {off:+d}；"
              f"{hits}/{n} 页实测对得上）")
    else:
        print(f"- **这份 deck 没有页脚页码**（只 {hits}/{n} 页找得到号）"
              f" → **viewer 页就是唯一一套页码，不存在 offset**")
        off = 0

    # 续篇检测
    # 从 p.2 起找第一个带页脚号的正文页 —— p.1 是封面，
    # networks 每份 deck 的封面都印 "1"，只看 p.1 会把续篇误判成新一章。
    first_footer = None
    for i, lines in enumerate(pages, start=1) if has_footer else []:
        if i == 1 and n > 1:
            continue
        if (i + off) in int_tokens(lines):
            first_footer = (i, i + off)
            break
    if first_footer:
        i, f = first_footer
        if off <= 2:
            flag = "\u2705 **\u9875\u811a\u4ece 1/2/3 \u8d77\u6b65 \u2192 \u65b0\u4e00\u7ae0**\uff08\u524d\u9762\u53ea\u6709\u5c01\u9762/\u81f4\u8f9e\u9875\uff09"
        else:
            flag = (f"\U0001f534\U0001f534 **\u7eed\u7bc7** \u2014\u2014 \u6b63\u6587\u9875\u811a\u4ece `{f}` \u8d77\uff0c"
                    f"**\u524d\u9762\u8fd8\u6709 {f-1} \u9875\u5728\u522b\u7684 deck \u91cc**")
        print(f"- \u7b2c\u4e00\u4e2a\u5e26\u9875\u811a\u53f7\u7684\u9875\uff1a\u7269\u7406 p.{i} \u2192 \u9875\u811a `{f}` \u2014\u2014 {flag}")

    # 动画检测
    sigs = [signature(p) for p in pages]
    groups, dup_runs = [], []
    start = 1
    for i in range(1, n):
        if sigs[i] and sigs[i] == sigs[i - 1]:
            continue
        groups.append((start, i))
        if i - start + 1 >= 2:
            dup_runs.append((start, i))
        start = i + 1
    groups.append((start, n))
    if n - start + 1 >= 2:
        dup_runs.append((start, n))
    distinct = len(groups)
    print(f"- **不同画面 = {distinct}**（物理 {n} 页 → 动画重复帧 {n - distinct} 页）")
    if dup_runs:
        print("- 动画段（连续同签名）：")
        for a, b in dup_runs:
            print(f"    - **p.{a}–{b}**（{b-a+1} 帧）→ {title_of(pages[a-1])}")
    else:
        print("- 无动画重复帧 —— **页数就是真内容页数**")

    print("\n## 逐页骨架\n")
    print("| viewer 页 | 页脚 | 标题 | 一句话内容 |")
    print("|---|---|---|---|")
    for a, b in groups:
        lines = pages[a - 1]
        t = title_of(lines)
        body = body_of(lines, t)
        if not full:
            body = body[:160] + ("…" if len(body) > 160 else "")
        rng = f"{a}" if a == b else f"**{a}–{b}**"
        foot = f"{a+off}" if a == b else f"{a+off}–{b+off}"
        anim = " 🎞️" if a != b else ""
        print(f"| {rng}{anim} | {foot} | {t.replace('|','/')} | {body.replace('|','/')} |")


if __name__ == "__main__":
    main()
