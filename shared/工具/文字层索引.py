#!/usr/bin/env python3
"""文字层索引 —— 零 LLM token 的预处理，治「预扫 agent 在自己上下文里逐页 pdftotext + 逐词 grep 十份 deck」那个烧钱点。

他 2026-09-04 12:55 问「为什么这次扫 deck 消耗那么多 token 是不是架构有 bug」：
实测每个 build agent 45–60 次工具调用、累计输入 10–14 M token —— 上下文涨到二十万字后每调一次工具就重发一遍。
病根 = agent 自己读 PDF。改法 = 先把文字层落成文件，agent 只读文件、只 grep 索引。

产出（每门课）：
  <课程>/课件/_预扫/<deck>.txt          每份 deck 的全文，带页标 `===== <deck> viewer p.N =====`
  <课程>/课件/_预扫/_全课文字层.txt      该课所有 deck 的全文按周序拼在一起 —— 查「这个词首次出现在哪」只要一次 grep -n

用法：
  python3 shared/工具/文字层索引.py                 # shared/配置.json 里所有课程全跑（PDF 没变的跳过）
  python3 shared/工具/文字层索引.py algorithms      # 只跑一门（课程 key / 中文名 / 别名都行）
  python3 shared/工具/文字层索引.py --force         # 全部重生成

规矩：
  · 跳过 `-animated` 版和 `_旧版/`（重复内容会把索引灌满）
  · 只读不写别的东西；不动 .md；可以随时重跑
"""
import re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
# 课程列表读 shared/配置.json。配置缺失/写错时 配置.py 会 sys.exit —— 这里接住：
# 预扫.py 会 import 本文件的 dump_deck，不能因为配置没写好就把预扫一起带走。
try:
    import 配置
    COURSES = list(配置.COURSES)
    ALIAS = dict(配置.ALIAS)
    CFG_ERR = ''
except (SystemExit, Exception) as _e:   # noqa: BLE001
    COURSES, ALIAS, CFG_ERR = [], {}, str(_e)


def need_poppler() -> bool:
    """pdfinfo / pdftotext 来自 poppler。缺了就给安装提示，别抛 FileNotFoundError。"""
    miss = [t for t in ('pdfinfo', 'pdftotext') if not shutil.which(t)]
    if miss:
        print(f'❌ 找不到 {" / ".join(miss)}（poppler 工具包）。macOS: brew install poppler · '
              f'Debian/Ubuntu: sudo apt install poppler-utils')
    return not miss


def page_count(pdf: Path) -> int:
    out = subprocess.run(['pdfinfo', str(pdf)], capture_output=True, text=True).stdout
    m = re.search(r'^Pages:\s+(\d+)', out, re.M)
    return int(m.group(1)) if m else 0


def dump_deck(pdf: Path, dest: Path) -> int:
    """把一份 deck 逐页落成带页标的 txt，返回页数。"""
    n = page_count(pdf)
    deck = pdf.stem
    parts = [f'# {deck} 文字层（pdftotext -layout，{n} 页；图片页读不出是正常的，别据此判空页）\n']
    for p in range(1, n + 1):
        raw = subprocess.run(['pdftotext', '-layout', '-f', str(p), '-l', str(p), str(pdf), '-'],
                             capture_output=True, text=True).stdout
        body = '\n'.join(ln.rstrip() for ln in raw.splitlines() if ln.strip())
        parts.append(f'\n===== {deck} viewer p.{p} =====\n{body}\n')
    dest.write_text(''.join(parts), encoding='utf-8')
    return n


def deck_key(p: Path):
    m = re.match(r'w(\d+)-L(\d+)', p.stem)
    return (int(m.group(1)), int(m.group(2))) if m else (99, 99)


def run(course: str, force: bool):
    src = ROOT / course / '课件'
    if not src.is_dir():
        print(f'⚠️ {course}: 没有 {course}/课件/ 目录，跳过')
        return
    out = src / '_预扫'
    pdfs = sorted([p for p in src.glob('*.pdf') if not p.stem.endswith('-animated')], key=deck_key)
    if not pdfs:
        print(f'⚠️ {course}: {course}/课件/ 里还没有 PDF，跳过')
        return
    out.mkdir(exist_ok=True)
    done, skipped = [], []
    for pdf in pdfs:
        dest = out / (pdf.stem + '.txt')
        if dest.exists() and not force and dest.stat().st_mtime >= pdf.stat().st_mtime:
            skipped.append(pdf.stem); continue
        n = dump_deck(pdf, dest)
        done.append(f'{pdf.stem}({n}p)')
    # 全课索引
    idx = out / '_全课文字层.txt'
    head = (f'# {course} 全课文字层索引 —— {len(pdfs)} 份 deck 按周序拼接（不含 -animated / _旧版）\n'
            f'# 用法：grep -n -i "term" 本文件 | head  → 行号往上找最近的 `===== <deck> viewer p.N =====` 就是出处\n'
            f'# ⚠️ 只有文字层：图片里的字读不出来，「零命中」≠ 从没出现过\n')
    with idx.open('w', encoding='utf-8') as f:
        f.write(head)
        for pdf in pdfs:
            f.write((out / (pdf.stem + '.txt')).read_text(encoding='utf-8'))
            f.write('\n')
    print(f'{course}: 新写 {len(done)} 份 {" ".join(done)}' + (f' · 跳过 {len(skipped)} 份（PDF 没变）' if skipped else '')
          + f' · 索引 {idx.name} {idx.stat().st_size // 1024} KB')


if __name__ == '__main__':
    if CFG_ERR:
        print(f'❌ 读不到课程配置：{CFG_ERR}'); sys.exit(1)
    if not need_poppler():
        sys.exit(1)
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    force = '--force' in sys.argv
    for c in (args or COURSES):
        c = ALIAS.get(c, c)
        if c not in COURSES:
            print(f'❌ 不认识的课程 {c}（shared/配置.json 里有：{", ".join(COURSES) or "（空）"}）'); sys.exit(1)
        run(c, force)
