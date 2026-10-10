#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一致性检查脚本 —— 长暗号复查表里「机械可查」的部分交给它。
用法：python3 shared/工具/检查.py   （在项目根目录跑，或任意位置跑都行）

🔒 2.0（2026-09-01）：从 14 项减到 10 项。删掉的四项用 319 个 git 回合快照回放验证过，
   全部零命中：[1 全文]（守卫把 99.1% 候选行挡掉，输出「比对了 0 处」= 假绿灯）·
   [6] 旧代号（唯一命中行被自己的豁免规则排除，永远不会红）·
   [1 部分] 相对时间词（它治的三次错都是口头汇报出的，不是写进文件的）·
   [G2]（判据太宽，已被 [G3] 覆盖）。
   另外 [7] 和 [4] 的 dashboard 子警告降为静默 —— 它们分别在 38% / 29% 的回合里报，
   而正确反应永远是「不用管」。

盖的项：
  [2] 学习日志速查表里不许有页码/欠N页
  [3] 引用的路径存不存在（含 ~ 路径 —— 治「只查 PATH 就说没装」那条）
  [4] 学时累计三处一致（顶部累计表 vs 各科小计 vs 总计求和；dashboard 数字若能找到也对）
  [7] dashboard.pdf 是否比数据源旧（⚠️ 提示级，不算失败）
  [1 语义] 速查表状态句 vs 台账「讲到」列 —— 说「全清」但台账有没讲完的 deck（反之亦然）

台账自身 vs 现实（2026-08-14 加 —— 台账是唯一数据源，所以它自己必须可信）：
  [8] 台账「页数」列 vs pdfinfo 实数（带 mtime 缓存，每条消息跑也不慢）
  [9] 台账「字幕」列 vs 磁盘 —— 引用的 srt/txt/mp3 都在？写 ⏳待发 的是不是其实已收（收了没改表）？
      磁盘上有没有台账没记的字幕（反向孤儿检查，⚠️ 级）
  [0]  核心五张表必须存在、非空、能解码（❌ 级 —— 空文件会让别的项全部「核了 0 份」然后打 ✅）
  [10] 收件箱/ 应该是空的（⚠️ 级 —— 有文件说明有东西写了没合并）
  [14] 还没讲完的 deck 必须有 _预扫/ 摘要（⚠️ 级 —— next.md 排页码要读它的「前置警告」节）
  [12] shared/交接/ 应该是空的（⚠️ 级 —— 学习会话留给总控的清账单，不清就是拿过期状态排计划）
  [13] 主 CLAUDE.md 的规则，下游流程文件有没有跟上（❌ 级 —— 表在 shared/工具/规则同步.tsv）
  [11] 断点节长度上限 40 行（⚠️ 级，2.0 加 —— 治「断点胀成 424 行的杂物间」）
  [4·明细] 学时明细表逐行求和 vs 顶部累计表（⚠️ 级，2.0 加 —— [4] 本身只比两个手写数字）

退出码：有 ❌ 则非 0。⚠️ 不影响退出码。

git worktree（2026-08-16 加）：[3] 和 [9] 查路径时，`录音/` 下的东西在当前工作副本里
找不到会再去主 worktree 找一次（`录音/` 被 .gitignore 排除 → worktree 副本里根本没有）。
只放宽这一类，找到了也会在输出里标一句「在主 worktree」，不静默通过。见 probe()。

课程列表（开源版）：不再写死课程名，一律读 `shared/配置.json`（经 `shared/工具/配置.py`）。
健壮性（开源版）：账本缺失 · 某门课目录不存在 · 没有任何 PDF · git 还没有任何 commit · 不在 worktree 里
—— 这些都只会报 ❌/⚠️，**不许抛 traceback**，而且必须走到最后的「汇总」节
（`inject-context.sh` 靠这一节判断脚本有没有跑完；没打印汇总 = 脚本中途死了 = 不能当全绿）。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

# 终端/hook 的 stdout 编码不一定是 UTF-8；输出里有 emoji，编码失败也不许炸
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[2]
FAIL = []
WARN = []
OK = []

# ---------------------------------------------------------------- 课程配置（读 shared/配置.json）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import 配置                      # noqa: E402
    COURSES = tuple(配置.COURSES)
    EXTRA_CORE = tuple(配置.EXTRA_CORE)
    CFG_ERROR = None
except (Exception, SystemExit) as _e:   # 配置.py 在找不到/写坏 json 时是 sys.exit(...)，也要接住
    COURSES, EXTRA_CORE = (), ()
    CFG_ERROR = str(_e).lstrip("❌ ").strip() or type(_e).__name__


def _main_worktree_root():
    """当前在 git worktree 里 → 返回主 worktree 的根；本来就在主仓库 → None。
    靠 git 问路，不硬编码任何绝对路径（项目规矩：脚本内零绝对路径）。
    `--git-common-dir` 在主仓库返回 `.git`（相对），在 worktree 里返回主仓库的
    `.git` 绝对路径 —— 它的父目录就是主 worktree 根。"""
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        p = Path(r.stdout.strip())
        if not p.is_absolute():
            p = ROOT / p
        main = p.resolve().parent
        return main if main != ROOT.resolve() else None
    except Exception:
        return None


MAIN_ROOT = _main_worktree_root()

# 🔒 只对「.gitignore 掉的大文件」放宽路径检查，别顺手放宽别的（2026-08-16 加）。
# 为什么要放宽：`.gitignore` 排掉了 `录音/`（1.2GB 音频不进版本库，转录产物 `字幕/` 已入库），
# 所以 `git worktree add` 建出来的工作副本里**没有** `录音/`，而主仓库里那些 mp3/m4a 全在。
# 不放宽的话每个 worktree 会话开场就是 15 个 ❌（[3] 8 处 + [9] 7 处），Stop 门禁直接拦住收尾——
# 而那些引用本身是对的，错的是「拿当前工作副本当唯一现实」这个假设。
RELAX_RE = re.compile(r"(^|/)录音(/|$)")


def probe(rel: str):
    """查项目根相对路径 `rel` 存不存在。三态返回，不静默通过：
      ""              → 就在当前工作副本里
      "（在主 worktree）" → 当前副本没有、但主 worktree 的同一相对路径有（只限 RELAX_RE 那类）
      None            → 真的不存在，该报 ❌
    标注而不是静默，是为了「音频真丢了」也还能在输出里看出来。"""
    if (ROOT / rel).exists():
        return ""
    if MAIN_ROOT and RELAX_RE.search(rel) and (MAIN_ROOT / rel).exists():
        return "（在主 worktree）"
    return None


def section(title):
    print(f"\n{'=' * 8} {title} {'=' * 8}")


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


# ---------------------------------------------------------------- [0] 核心数据源可读（2026-09-03 加）
CORE = [
    "CLAUDE.md",
    "shared/lecture台账.md",
    "shared/学习日志.md",
    "shared/deadlines.md",
    "shared/学时记录.md",
] + list(EXTRA_CORE)   # 某门课有自己的唯一数据源时，登记在 配置.json 的 extra_core_files


def check_core_readable():
    """核心五张表（+ 配置里登记的 extra_core_files）：必须存在、非空、能按 UTF-8 解码。

    🔴🔴 为什么有这一项（2026-09-03 22:2x，fable 审查复现出来的）：
    `read()` 把所有异常（文件不存在 · 目录 · 非 UTF-8）都吞成空串，
    而下游每一项对「0 行数据」都打 ✅ ——
      [8] 台账页数「核了 0 份 deck」→ ✅
      [1·语义] 「核了 0 份 deck」→ ✅
      [G] 「本回合台账推进 0 处」→ ✅
      [13] 「0 条规则的下游全部跟上了」→ ✅
    **实测：把 lecture台账.md 清空，12 项全绿 RC=0。**
    唯一数据源没了本该是最红的状态，结果是最绿的 —— 而 stop-check.sh 全绿就
    `git add -A && commit`，等于把空台账固化成「上一个通过检查的快照」，
    下个会话的导航层和 [G] 锁的基线全建立在空数据上。
    """
    section("[0] 核心数据源可读")
    bad = []
    if CFG_ERROR:
        bad.append(f"shared/配置.json 读不了：{CFG_ERROR} —— 课程列表是空的，下面按课程查的各项都会「核了 0 份」")
    elif not COURSES:
        WARN.append("[0] shared/配置.json 里没有任何课程（courses 是空的）")
        print("⚠️ 配置里没有课程，按课程查的各项会跳过")
    for rel in CORE:
        f = ROOT / rel
        if not f.is_file():
            bad.append(f"{rel} 不存在")
            continue
        try:
            raw = f.read_bytes()
        except OSError as e:
            bad.append(f"{rel} 读不了：{e}")
            continue
        if len(raw.strip()) == 0:
            bad.append(f"{rel} 是空的（0 字节）—— 唯一数据源没了，别的检查项会全部「核了 0 份」然后打 ✅")
            continue
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as e:
            bad.append(f"{rel} 不是合法 UTF-8：{e}")
    for b in bad:
        FAIL.append(f"[0] {b}")
        print(f"❌ {b}")
    if not bad:
        OK.append("[0]")
        print(f"✅ 核心 {len(CORE)} 个文件都在、非空、能解码")


def strip_fences(text: str) -> str:
    """去掉 ``` 围栏块，避免把代码块里的东西当路径/数字。"""
    return re.sub(r"```.*?```", "", text, flags=re.S)


# ---------------------------------------------------------------- [2] 速查表禁数字
def check_quicktable():
    section("[2] 速查表禁页码")
    log = read(ROOT / "shared/学习日志.md")
    m = re.search(r"# 📍 当前进度速查(.*?)\n## ", log, flags=re.S)
    if not m:
        WARN.append("[2] 没找到「当前进度速查」节，跳过")
        print("⚠️ 没找到速查表")
        return
    bad = []
    for i, line in enumerate(m.group(1).split("\n")):
        if not line.lstrip().startswith("|"):
            continue  # 只查表格行；引用块里讲历史教训的不算
        if re.search(r"p\.\d|\d+\s*页|欠\s*\d", line):
            bad.append(line.strip()[:80])
    if bad:
        FAIL.append(f"[2] 速查表出现数字（{len(bad)} 行）")
        for b in bad:
            print(f"❌ {b}")
    else:
        OK.append("[2]")
        print("✅ 速查表无页码")


# ---------------------------------------------------------------- [3] 路径存在
PATH_SCOPE = [
    "CLAUDE.md",
] + [f"{c}/CLAUDE.md" for c in COURSES] + [
    "shared/学习日志.md",
    "shared/lecture台账.md",
    "shared/deadlines.md",
    "shared/学时记录.md",
    "shared/公告.md",
    # 🆕 2.0（2026-09-01）新增的文件也要被扫死链接：
    "README.md",
    "shared/工具手册.md",
    "shared/规则档案.md",
    ".claude/commands/学.md",
    ".claude/commands/作业.md",
    ".claude/commands/总控.md",
    # 🔴 2026-09-03 补：这四个原本一直没进扫描范围 —— next.md 里的路径引用
    #    从建档到现在**一次都没被扫过死链接**。而 next.md 恰恰是出看板时逐条照做的文件。
    ".claude/commands/next.md",
    ".claude/commands/week.md",
    ".claude/commands/drill.md",
    ".claude/commands/预扫.md",
] + list(EXTRA_CORE)    # 单门课专属的文件（原来写死在这里）改读配置的 extra_core_files
TOP_DIRS = tuple(f"{c}/" for c in COURSES) + ("shared/", ".claude/")


def expand_token(tok: str):
    """`x.srt/.txt` 这种合并写法拆成两个路径。"""
    m = re.match(r"^(.*)\.(\w+)/\.(\w+)$", tok)
    if m:
        return [f"{m.group(1)}.{m.group(2)}", f"{m.group(1)}.{m.group(3)}"]
    return [tok]


def check_paths():
    section("[3] 引用路径存在")
    seen, bad, relaxed = set(), [], []
    for rel in PATH_SCOPE:
        text = strip_fences(read(ROOT / rel))
        for tok in re.findall(r"`([^`\n]+)`", text):
            tok = tok.strip()
            if any(c in tok for c in "<>*«…") or "NN" in tok or "://" in tok or " " in tok:
                continue
            # ~/ 开头的是本机环境路径（Downloads · Zotero · texlive …），换台电脑必然不存在，不归仓库管 —— 不查
            if not tok.startswith(TOP_DIRS):
                continue
            for t in expand_token(tok):
                key = (rel, t)
                if key in seen:
                    continue
                seen.add(key)
                if t.startswith("~/"):
                    if not Path(t).expanduser().exists():
                        bad.append(f"{rel} → `{t}`")
                    continue
                mark = probe(t)
                if mark is None:
                    bad.append(f"{rel} → `{t}`")
                elif mark:
                    relaxed.append(t)
    if bad:
        FAIL.append(f"[3] 有引用的路径不存在（{len(bad)} 处）")
        for b in bad:
            print(f"❌ {b}")
    else:
        OK.append("[3]")
        print(f"✅ 全部存在（查了 {len(seen)} 处引用）")
    if relaxed:
        print(f"· 其中 {len(relaxed)} 处在主 worktree（`录音/` 被 .gitignore 排除，"
              f"worktree 副本里没有）：{'、'.join(relaxed[:3])}…")


# ---------------------------------------------------------------- [4] 学时三处一致
DUR = re.compile(r"(\d+)\s*h(?:\s*(\d+)\s*min)?")


def to_min(s: str):
    """支持三种写法：`12 h 28 min` · `35 min`（不足一小时）· `0`"""
    s = s.replace("*", "").replace("≈", "").strip()
    m = DUR.search(s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2) or 0)
    m2 = re.search(r"(\d+)\s*min", s)   # 纯分钟，没有 h
    if m2:
        return int(m2.group(1))
    if re.match(r"^0([^\d]|$)", s):
        return 0
    return None


def check_hours():
    section("[4] 学时三处一致")
    text = read(ROOT / "shared/学时记录.md")
    lines = text.split("\n")
    courses = COURSES

    # ① 顶部累计表
    top, total_top = {}, None
    m = re.search(r"## 累计(.*?)\n## ", text, flags=re.S)
    if not m:
        FAIL.append("[4] 没找到「## 累计」表")
        print("❌ 没找到累计表")
        return
    for line in m.group(1).split("\n"):
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 3:
            continue
        name = cells[1].replace("*", "").strip()
        for c in courses:
            if name.startswith(c):
                top[c] = to_min(cells[2])
        if name.startswith("总计"):
            total_top = to_min(cells[2])

    # ② 各科小计（跟踪当前 ## 节）
    sub, cur = {}, None
    for line in lines:
        h = re.match(r"^## (\w+)", line)
        if h:
            cur = h.group(1) if h.group(1) in courses else None
        m2 = re.search(r"\*\*小计：([^*（]+)", line)
        if m2 and cur:
            sub[cur] = to_min(m2.group(1))

    ok = True
    for c in courses:
        t, s = top.get(c), sub.get(c)
        if s is None and t == 0:
            s = t  # 累计 0 且没有小计节（还没开始学的课），视为一致
        if t is None or s is None or t != s:
            ok = False
            print(f"❌ {c}: 累计表 {fmt(t)} vs 小计 {fmt(s)}")
    ssum = sum(v for v in top.values() if v is not None)
    if not courses:
        print("⚠️ 配置里没有课程，学时无从核对")
    if total_top != ssum:
        ok = False
        print(f"❌ 总计 {fmt(total_top)} ≠ 各科之和 {fmt(ssum)}")
    if ok:
        OK.append("[4]")
        print(f"✅ 顶部累计 = 各科小计，总计 {fmt(total_top)} = 求和")
    else:
        FAIL.append("[4] 学时对不上")

    # ④ 明细逐行求和 —— 🆕 2026-09-01 加。
    #    ①② 比的是「顶部累计表」和「各科小计」两个**手写数字**，两个都抄错就一起绿。
    #    这一项才真的去数明细表。⚠️ 级：差几分钟是记账精度问题，不该拦住讲课。
    detail, cur2 = {}, None
    for line in lines:
        h = re.match(r"^## (\w+)", line)
        if h:
            cur2 = h.group(1) if h.group(1) in courses else None
            continue
        if not cur2 or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not re.match(r"\**\s*\d{2}-\d{2}", cells[0]):
            continue
        v = to_min(cells[2])
        if v:
            detail[cur2] = detail.get(cur2, 0) + v
    drift = []
    for c in courses:
        t, d = top.get(c), detail.get(c)
        if t is None or d is None or t == d:
            continue
        drift.append(f"{c} 表里 {fmt(t)}，明细求和 {fmt(d)}（差 {fmt(abs(t - d))}）")
    if drift:
        WARN.append("[4·明细] 累计表和明细逐行求和对不上：" + "；".join(drift))
        for x in drift:
            print(f"⚠️ {x}")
    elif detail:
        print(f"✅ 明细逐行求和 = 累计表（数了 {sum(detail.values()) // 60} 小时）")

    # ③ dashboard.tex 里若写了总计，也对一下
    tex = read(ROOT / "shared/dashboard.tex")
    if tex:
        for line in tex.split("\n"):
            if ("总计" in line or "累计" in line) and DUR.search(line):
                v = to_min(line)
                if v is not None and total_top is not None and v != total_top:
                    # 🔇 2026-09-01 降静默：319 个回合里 29% 会报，而正确反应永远是
                    #    「不用管」（下一次 /总控 重编译看板就自动修好）。只 print，不进 WARN。
                    print(f"（提示）dashboard.tex: {fmt(v)} ≠ {fmt(total_top)}，下次出看板时会自动对上")
                break


def fmt(mins):
    if mins is None:
        return "?"
    return f"{mins // 60} h {mins % 60:02d} min"


# ---------------------------------------------------------------- [7] dashboard 新旧
def check_dashboard():
    section("[7] dashboard 新旧（提示级）")
    pdf = ROOT / "shared/dashboard.pdf"
    if not pdf.exists():
        print("（提示）shared/dashboard.pdf 不存在")
        return
    stale = []
    for src in ("学习日志.md", "学时记录.md", "lecture台账.md", "deadlines.md", "公告.md"):
        sp = ROOT / "shared" / src
        if sp.exists() and sp.stat().st_mtime > pdf.stat().st_mtime:
            stale.append(src)
    if stale:
        # 🔇 2026-09-01 降静默：38% 的回合都在报它，而 /总控 每次本来就会重出看板。
        print(f"（提示）数据源比看板 PDF 新：{'、'.join(stale)} —— 下次 /总控 会重编译")
        OK.append("[7]")
    else:
        OK.append("[7]")
        print("✅ dashboard 不比数据源旧")


# ---------------------------------------------------------------- [8][9] 台账自身 vs 现实
def _find_pdfinfo():
    """找 pdfinfo：PATH 里没有就试几个常见位置；都没有 → None（[8] 会降级成 ⚠️ 跳过，不崩）。"""
    p = shutil.which("pdfinfo")
    if p:
        return p
    for cand in ("/opt/homebrew/bin/pdfinfo", "/usr/local/bin/pdfinfo", "/usr/bin/pdfinfo"):
        if os.path.isfile(cand):
            return cand
    return None


PDFINFO = _find_pdfinfo()
# 缓存放系统临时目录，文件名带项目根路径的短哈希 —— 多个项目同时用这套架构时不会串缓存
_ROOT_TAG = hashlib.sha1(str(ROOT).encode("utf-8", "replace")).hexdigest()[:8]
PAGES_CACHE = Path(tempfile.gettempdir()) / f"claude-study-pdfpages-{_ROOT_TAG}.json"


def pdf_pages(p: Path):
    """pdfinfo 数页数，带 mtime 缓存（inject-time.sh 每条消息都跑，不能慢）。
    pdfinfo 不可用 / 文件读不了 / 输出解析不了 → 返回 None（调用方当「核不了」，不当「对不上」）。"""
    if not PDFINFO:
        return None
    try:
        cache = json.loads(PAGES_CACHE.read_text()) if PAGES_CACHE.exists() else {}
        if not isinstance(cache, dict):
            cache = {}
    except Exception:
        cache = {}
    try:
        key, mt = str(p), p.stat().st_mtime
    except OSError:
        return None
    if key in cache and isinstance(cache[key], list) and len(cache[key]) == 2 and cache[key][0] == mt:
        return cache[key][1]
    try:
        out = subprocess.run([PDFINFO, str(p)], capture_output=True, text=True, timeout=15).stdout
        m = re.search(r"^Pages:\s+(\d+)", out, flags=re.M)
        pages = int(m.group(1)) if m else None
    except Exception:
        pages = None
    cache[key] = [mt, pages]
    try:
        PAGES_CACHE.write_text(json.dumps(cache))
    except Exception:
        pass
    return pages


def iter_ledger_rows():
    """逐行走台账的课程表格：yield (课程, cells, 原始行)。
    cells 下标：2=deck 3=页数 4=讲到 5=字幕 6=到场（和 ledger_status 一致）"""
    text = read(ROOT / "shared/lecture台账.md")
    cur = None
    for line in text.split("\n"):
        h = re.match(r"^##\s+(\w+)", line)
        if h:
            cur = h.group(1) if h.group(1) in COURSES else None
            continue
        if not cur or not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) >= 7:
            yield cur, cells, line


def check_ledger_pages():
    section("[8] 台账页数 vs 课件实际页数（pdfinfo）")
    if not PDFINFO:
        WARN.append("[8] 找不到 pdfinfo，跳过（macOS: brew install poppler · Debian/Ubuntu: apt install poppler-utils）")
        print("⚠️ 没有 pdfinfo，跳过")
        return
    bad, n = [], 0
    for cur, cells, _ in iter_ledger_rows():
        deck_m = re.search(r"w\d{2}-L\d+", cells[2])
        pages_s = cells[3].replace("*", "").strip()
        if not deck_m or not re.fullmatch(r"\d+", pages_s):
            continue  # 无slides / 补录视频(47 min) / diagnostic(—) 这类行不查
        f = ROOT / cur / "课件" / f"{deck_m.group(0)}.pdf"
        n += 1
        if not f.exists():
            bad.append(f"{cur} `{deck_m.group(0)}`：台账有这行，`{cur}/课件/{deck_m.group(0)}.pdf` 却不存在")
            continue
        real = pdf_pages(f)
        if real is not None and real != int(pages_s):
            bad.append(f"{cur} `{deck_m.group(0)}`：台账写 {pages_s} 页，pdfinfo 实数 {real} 页")
    if bad:
        FAIL.append(f"[8] 台账页数和课件对不上（{len(bad)} 处）")
        for b in bad:
            print(f"❌ {b}")
    else:
        OK.append("[8]")
        print(f"✅ 台账页数 = 课件实数（核了 {n} 份 deck）")


SUB_TOKEN = re.compile(r"`([^`\n]*\.(?:srt|txt|mp3|m4a)(?:/\.\w+)?)`")


def check_subs():
    section("[9] 台账字幕列 vs 磁盘")
    bad, orphan, relaxed = [], [], []
    full_text = read(ROOT / "shared/lecture台账.md")
    for cur, cells, line in iter_ledger_rows():
        subs = cells[5]
        deck_m = re.search(r"w(\d{2})-L(\d+)", cells[2])
        toks = []
        for t in SUB_TOKEN.findall(line):
            toks += expand_token(t)
        missing = []
        for t in toks:
            if t.startswith(TOP_DIRS):
                continue  # 带完整前缀的 [3] 已查
            sub = f"{cur}/{t}" if t.startswith(("字幕/", "录音/")) else f"{cur}/字幕/{t}"
            mark = probe(sub)  # 录音/ 的在 worktree 里查不到时会回主 worktree 找一次
            if mark is None:
                missing.append(t)
            elif mark:
                relaxed.append(sub)
        if missing:
            bad.append(f"{cur} {cells[2][:24]}：`{'`、`'.join(missing)}` 不在磁盘")
        # deck 代号推出来的候选文件名（有的课按周命名 wNN；有的课一份 deck 拆 a/b 两段）。
        # 不按课程名分支 —— 三种候选对所有课一视同仁，哪个在磁盘上就认哪个
        cands = []
        if deck_m:
            cands = [f"{deck_m.group(0)}.srt", f"w{deck_m.group(1)}.srt", f"{deck_m.group(0)}a.srt"]
        if "⏳" in subs and cands:
            hit = [c for c in cands if (ROOT / cur / "字幕" / c).exists()]
            if hit:
                bad.append(f"{cur} `{deck_m.group(0)}`：台账写 ⏳待发，但 `{cur}/字幕/{hit[0]}` 已在磁盘 —— 收了没改表")
        if "✅" in subs and not toks and cands:
            if not any((ROOT / cur / "字幕" / c).exists() for c in cands):
                bad.append(f"{cur} `{deck_m.group(0)}`：台账写 ✅已收，字幕目录里却找不到对应文件")
    # 反向：磁盘上有、台账没提的字幕（孤儿 → 提示级）
    for cur in COURSES:
        d = ROOT / cur / "字幕"
        if not d.is_dir():
            continue
        stems = {f.stem for f in d.iterdir() if f.suffix in (".srt", ".txt")}
        for s in sorted(stems):
            ok = s in full_text or (s and s[-1] in "ab" and f"{s[:-1]}a/b" in full_text)
            if not ok:
                orphan.append(f"{cur}/字幕/{s}.*")
    if bad:
        FAIL.append(f"[9] 字幕列和磁盘对不上（{len(bad)} 处）")
        for b in bad:
            print(f"❌ {b}")
    else:
        OK.append("[9]")
        print("✅ 字幕/录音引用全在磁盘，无「收了没改表」")
    if relaxed:
        print(f"· 其中 {len(relaxed)} 份录音在主 worktree（`录音/` 不入库，"
              f"worktree 副本里没有）")
    if orphan:
        WARN.append(f"[9] 磁盘上有台账没记的字幕：{'、'.join(orphan)}")
        for o in orphan:
            print(f"⚠️ 台账没记：{o}")


def check_inbox():
    section("[10] 收件箱应清空（提示级）")
    d = ROOT / "收件箱"
    files = [f.name for f in d.iterdir() if not f.name.startswith(".")] if d.is_dir() else []
    if files:
        WARN.append(f"[10] 收件箱还有 {len(files)} 个文件没合并：{'、'.join(files[:5])}")
        print(f"⚠️ 没合并：{'、'.join(files[:5])}")
    else:
        OK.append("[10]")
        print("✅ 收件箱是空的")


# ---------------------------------------------------------------- [13] 规则同步（2026-09-03 加）
def check_rule_sync():
    """治「同一条规则在 N 个文件里各有一份自然语言副本，副本之间没有机械联系」。

    🔴 为什么有这一项 —— 一夜之间栽了三次，全是同一个形状：
      ① 主文件 09-02 新增了登记表/交接单，`next.md` 的数据源清单没跟上
         → 出看板时读到过期状态，**把一份已经交掉的作业排成周六 8 h 主攻**
      ② 主文件写「两份 PDF 一起发」，`next.md` 只写发一份 → 漏发 DDL 总表
      ③ SessionStart hook 里的 heredoc 是第 9 份副本，注入的还是 1.x 的
         「同一时间只开一个会话」—— 而主文件 09-02 已把那条作废

    **副本数 = 未来漂移点数。** 这一项不能消灭副本，但能让漂移当场可见。

    盖得住：主文件那条规则被删/改名（下游在引用幽灵）· 依赖文件里落地痕迹不见了 ·
            主文件那段改过而 hash 没更新（提醒去核下游）。
    盖不住：两边都提到同一个词但**说法不同**（语义分叉）—— 那仍然要人工。
    """
    import hashlib
    section("[13] 规则同步（主 CLAUDE.md ↔ 流程文件）")
    tsv = ROOT / "shared/工具/规则同步.tsv"
    if not tsv.is_file():
        WARN.append("[13] 找不到 shared/工具/规则同步.tsv")
        print("⚠️ 找不到规则同步表")
        return
    main = read(ROOT / "CLAUDE.md")
    if not main.strip():
        # 主文件读不了 [0] 已经报 ❌ 了；这里再逐条报「找不到规则」只会刷屏
        WARN.append("[13] 主 CLAUDE.md 不可读，规则同步跳过（见 [0]）")
        print("⚠️ 主 CLAUDE.md 不可读，跳过")
        return
    try:
        tsv_lines = tsv.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        WARN.append(f"[13] 规则同步.tsv 读不了：{e}")
        print(f"⚠️ 规则同步.tsv 读不了：{e}")
        return
    bad, drifted, skipped = [], [], []
    rows = 0
    for ln in tsv_lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split("\t")
        # 🔒 列数不对要出声，不能静默跳过 —— 手写 tsv 最常见的两种错（漏一列 / 用空格代替 tab）
        #    会让那一行被无声丢掉，而末尾照样打「✅ N 条规则的下游全部跟上了」= 门禁在说谎。
        #    （照 导航.py 处理速查表列数的同一套做法）
        if len(parts) < 6:
            bad.append(f"规则同步.tsv 有一行只有 {len(parts)} 列（应为 6，且必须用 tab 分隔）："
                       f"{ln[:40]}")
            continue
        aid, locate, deps, need, want_hash, _desc = parts[:6]
        rows += 1
        # ① 主文件里那条规则还在不在
        # 🔴 规则同步.tsv 是手写文件，正则列少一个括号就会抛 re.error。
        #    这个函数在调用序里排在 [11]/[1·语义]/[G] 凭证锁**之前** ——
        #    抛出去 = 那三项一次都不跑，而且输出里既无 ❌ 也无 ⚠️，
        #    inject-context.sh 的三分支会落进 else 打「✅ 一致性检查全绿」。
        #    **门禁失效还伪装成全绿，是最坏的一种。**（2026-09-03 验证实测抓到）
        try:
            m = re.search(locate, main)
        except re.error as e:
            bad.append(f"{aid}：主文件定位正则写坏了 —— {e}（去修 规则同步.tsv 第 2 列）")
            continue
        if not m:
            bad.append(f"{aid}：主 CLAUDE.md 里找不到 /{locate}/ —— 规则被删或改名了，"
                       f"而 {deps.split(';')[0]} 等还在引用它")
            continue
        # ③ 段落 hash（从命中处到下一个 ## 标题，最多 3000 字符）
        seg = main[m.start(): m.start() + 3000]
        nxt = re.search(r"\n##\s", seg)
        if nxt:
            seg = seg[: nxt.start()]
        h = hashlib.sha1(seg.encode("utf-8")).hexdigest()[:8]
        if want_hash != "-" and want_hash != h:
            drifted.append(f"{aid}：主文件那段改过了（hash {want_hash} → {h}）"
                           f"—— 去核 {deps} 要不要跟着改，核完把新 hash 写回 规则同步.tsv")
        # ② 依赖文件里的落地痕迹
        for dep in deps.split(";"):
            dep = dep.strip()
            if not dep:
                continue
            f = ROOT / dep
            if not f.is_file():
                # 降级成 ⚠️：模板用户删掉/改名了某个流程文件时，不该被这一行永远拦在收尾门口。
                # 删了文件就同步改 规则同步.tsv 对应行的第 3 列。
                skipped.append(f"{aid}：依赖文件不存在 {dep}（删了就同步改 规则同步.tsv）")
                continue
            try:
                hit = re.search(need, read(f))
            except re.error as e:
                bad.append(f"{aid}：依赖正则写坏了 —— {e}（去修 规则同步.tsv 第 4 列）")
                break
            if not hit:
                bad.append(f"{aid}：{dep} 里找不到 /{need}/ —— 主文件有这条规则，这个文件没落实")
    for b in bad:
        FAIL.append(f"[13] {b}")
        print(f"❌ {b}")
    for d in drifted:
        WARN.append(f"[13] {d}")
        print(f"⚠️ {d}")
    for sk in skipped:
        WARN.append(f"[13] {sk}")
        print(f"⚠️ {sk}")
    if not bad and not drifted and not skipped:
        OK.append("[13]")
        print(f"✅ {rows} 条规则的下游全部跟上了")


# ---------------------------------------------------------------- [14] 预扫覆盖（2026-09-03 加）
def check_prescan():
    """台账里还没讲完的 deck，_预扫/ 下必须有摘要。

    🔴 09-03 审计发现：预扫.md 和 总控.md 都说「缺一份补一份」，但**没有任何一项在查**，
    结果 W5 有五份 deck 从没预扫过（两门课各两份 w05-L1/L2 · 另一门一份 w05-L1），
    而 next.md 排页码时要读 _预扫/ 的「前置警告」节 —— 读不到就当「没有前置警告」放行。
    """
    section("[14] 预扫覆盖（还没讲完的 deck）")
    miss, cur = [], None
    for line in read(ROOT / "shared/lecture台账.md").split("\n"):
        h = re.match(r"^##\s+(\w+)", line)
        if h:
            cur = h.group(1) if h.group(1) in COURSES else None
            continue
        if not cur or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        m = DECK_RE.search(cells[1])
        if not m:
            continue
        taught = re.sub(r"\*\*|`", "", cells[3])
        if re.search(r"✅\s*全|✅\s*已|已做|已讲|行政|不需要|➖", taught):
            continue
        deck = m.group(0)
        if not (ROOT / cur / "课件" / f"{deck}.pdf").is_file():
            continue
        if not (ROOT / cur / "课件" / "_预扫" / f"{deck}.md").is_file():
            miss.append(f"{cur} {deck}")
    if miss:
        WARN.append(f"[14] {len(miss)} 份还没讲的 deck 缺预扫摘要：{'、'.join(miss[:5])}"
                    " —— 排页码前要补（python3 shared/工具/预扫.py <pdf> --full --save）")
        print(f"⚠️ 缺预扫：{'、'.join(miss[:5])}")
    else:
        OK.append("[14]")
        print("✅ 还没讲完的 deck 都有预扫摘要")


# ---------------------------------------------------------------- [12] 交接单待处理（2026-09-03 加）
def check_handover():
    """`shared/交接/` 里的交接单必须被总控清掉。

    🔴 为什么有这一项（2026-09-03 00:1x 加，起因是一次真事故）：
    学习/作业会话按规矩不许改 `shared/` 账本，完成了工作只能写一份交接单留给总控。
    但这个目录**一直没有门禁** —— `收件箱/` 有 [10] 盯着，这里没有。
    结果：09-02 23:24 algorithms A1 提交完写了交接单，09-01 那两份也还躺着没人碰；
    23:57 出看板时读的是过期的 `学习日志.md`，**把已经交掉的 A1 排成了周六 8 h 的主攻**。

    **没有门禁的待办等于不存在。** 交接单处理完 → 移进 `shared/交接/已处理/`。
    """
    section("[12] 交接单应清空（提示级）")
    d = ROOT / "shared/交接"
    files = ([f.name for f in d.iterdir()
              if f.suffix == ".md" and f.name != "README.md"] if d.is_dir() else [])
    if files:
        WARN.append(f"[12] shared/交接/ 还有 {len(files)} 份没处理：{'、'.join(files[:3])}"
                    " —— 出看板/排计划之前必须先清，否则是拿过期状态排")
        print(f"⚠️ 没处理：{'、'.join(files[:3])}")
        print("   🔒 处理完移进 shared/交接/已处理/，别留在这儿")
    else:
        OK.append("[12]")
        print("✅ 交接目录是空的")


# ---------------------------------------------------------------- [11] 断点节长度（2.0 加）
def check_breakpoint():
    """断点节的契约写在它自己的标题里：「块完成后清空写「无」」。

    2026-09-01 体检：这一节胀到 424 行 / 35 KB，里面塞着测验记录、作业经过、组会分工、
    tute 顺序 —— 全是不同任务在同一个对话里留下的沉积，而且有两块还标着「🔄 进行中」
    但事实上早已结束。它是 1.x 开局注入里最大的单块。

    2.0 之后断点不再进 SessionStart 全文（导航层只取状态行），但它照样会被 /学 会话读到，
    所以给它一个长度闸。⚠️ 级不拦回合 —— 拦住讲课比让断点长几行更糟。
    """
    section("[11] 断点节该是短的")
    log = read(ROOT / "shared/学习日志.md")
    m = re.search(r"##\s*⏸️?\s*当前断点[^\n]*\n(.*?)(?=\n##\s)", log, re.S)
    if not m:
        WARN.append("[11] 找不到「当前断点」节")
        print("⚠️ 找不到断点节")
        return
    n = len(m.group(1).strip().split("\n"))
    if n > 40:
        WARN.append(f"[11] 断点节 {n} 行（上限 40）—— 已完成的块该搬去「历史流水」线下了")
        print(f"⚠️ 断点节 {n} 行，超过 40 行上限")
    else:
        OK.append("[11]")
        print(f"✅ 断点节 {n} 行")


# ---------------------------------------------------------------- [1 语义] 速查表 vs 台账
# （COURSES 在文件开头从 配置.json 读，不再写死）

# 速查表状态句里「声称这门课课件已清空」的说法
CLAIM_CLEAR = r"零欠帐|全清|全部讲完|欠\s*0\b"
# 「声称这门课还欠着」的说法
# ⚠️ 不许放裸的「欠」—— 它会匹配「零欠帐」，把说清空的句子误判成说还欠（写脚本时踩过）
CLAIM_OWING = r"一页没讲|一页没动|一页未动|未开始|没讲|没动|还欠|欠着"


def ledger_status():
    """解析 lecture台账.md，返回 {课程: [(deck, 讲到列原文, 清了没), ...]}。

    「清了没」的判据（只用台账自己的写法，不猜）：
      · 含 ❌            → 未清
      · 含 p. 但不含「全」→ 部分完成，算未清（例：某份 w01-L2 只讲了几段）
      · 其余 ✅          → 清了（含「✅全」「✅行政已提取」「✅已做」）
    """
    text = read(ROOT / "shared/lecture台账.md")
    out, cur = {c: [] for c in COURSES}, None
    for line in text.split("\n"):
        h = re.match(r"^##\s+(\w+)", line)
        if h:
            cur = h.group(1) if h.group(1) in COURSES else None
            continue
        if not cur or not line.lstrip().startswith("|") or line.lstrip().startswith(">"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 6:
            continue
        deck, taught = cells[2], cells[4]
        if not deck or deck in ("deck", "---") or set(deck) <= set("-: "):
            continue          # 表头 / 分隔行
        if "❌" in taught:
            done = False
        elif re.search(r"p\.", taught) and "全" not in taught:
            done = False      # 只讲了部分页
        else:
            done = bool(taught)
        out[cur].append((deck.replace("*", "").strip(), taught, done))
    return out


def check_quicktable_vs_ledger():
    section("[1·语义] 速查表状态句 vs 台账「讲到」列")
    log = read(ROOT / "shared/学习日志.md")
    m = re.search(r"# 📍 当前进度速查(.*?)\n## ", log, flags=re.S)
    if not m:
        WARN.append("[1·语义] 没找到速查表，跳过")
        print("⚠️ 没找到速查表")
        return
    ledger = ledger_status()

    claims = {}
    for line in m.group(1).split("\n"):
        if not line.lstrip().startswith("|") or line.lstrip().startswith(">"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 3:
            continue
        name = cells[1].replace("*", "").strip()
        for c in COURSES:
            if name.startswith(c):
                claims[c] = cells[2]

    bad = False
    for c in COURSES:
        say = claims.get(c)
        if say is None:
            WARN.append(f"[1·语义] 速查表里没有 {c} 这一行")
            print(f"⚠️ 速查表缺 {c}")
            continue
        owing = [d for d, _, done in ledger[c] if not done]
        says_clear = re.search(CLAIM_CLEAR, say)
        if says_clear and owing:
            bad = True
            FAIL.append(f"[1·语义] {c}：速查表说「清了」，台账里却有 {len(owing)} 份没清")
            print(f"❌ {c}：速查表说 —— {say[:60]}")
            for d, taught, _ in [x for x in ledger[c] if not x[2]]:
                print(f"     台账 `{d}` 讲到 = {taught[:40]}")
        elif re.search(CLAIM_OWING, say) and not says_clear and not owing and ledger[c]:
            bad = True
            FAIL.append(f"[1·语义] {c}：速查表说「还欠」，台账里却全部已清")
            print(f"❌ {c}：速查表说 —— {say[:60]}  但台账 {len(ledger[c])} 份全部已清")
    if not bad:
        OK.append("[1·语义]")
        n = sum(len(v) for v in ledger.values())
        print(f"✅ {len(COURSES)} 门的状态句都和台账对得上（核了 {n} 份 deck）")


# ---------------------------------------------------------------- deck 代号正则（[G] 两把锁在用）
# 🔒 2026-09-01：这一行原本长在 [1·全文] 那段里。[1·全文] 已删（319 个回合零命中，
#    两道「防误咬」的守卫把 99.1% 的候选行全挡掉了，输出「✅ 比对了 0 处」= 假绿灯），
#    但 DECK_RE 被 [G1]/[G3] 两把锁在 check_receipts 里的三处用着——**整段删会让两把凭证锁静默失效**。
DECK_RE = re.compile(r"w\d{2}-L\d+")


# ---------------------------------------------------------------- [G] 凭证锁（git 化，2026-08-15）
# 「本回合」= HEAD（上次 Stop 门禁自动提交的快照）到现在的差异。
# 两把锁管的都不是语义，是「语义动作有没有留下凭证」：
# 🔒 2026-09-01：原来是三把，[G2]（出题登记锁）已删 —— 319 个回合零命中，
#    且它的判据太宽（扫整个断点节，只要任何一行提到过页码就算过），
#    它想防的场景已被 [G3] 的 fail-closed 分支覆盖。
#   [G1] 台账推进锁：某 deck 的「讲到」列状态升级（没学→部分→全）时，
#        学时记录.md 必须在同一回合新增包含该 deck 代号、且含「答」和「批」字样的行。
#        —— 防「顺手把讲过记成学过」（完成 = 讲+答+批，凭证就是学时记录那一行）
#   [G3] 重讲锁：断点里挂着「⬜…没答/未答」时，同回合不许把台账推进到**那几页**
#        🔒 2026-08-16 改成按页码区间判断（原来按 deck 名，那样必然误咬）：
#        一份 deck 是分很多块讲的（每块 ≤5 页，讲+答+批齐才算完成），所以「前面的块已进台账
#        + 最后一块挂着没答」是**合法且必然**出现的状态 —— 例：algorithms `w03-L1`
#        p.1–5 / p.6–9 两块真完成 → 台账记 `viewer p.9`，同时 p.10–14 是新开的块、题挂着没答。
#        按 deck 名判的旧逻辑在这种状态下永远红，锁永远红 = 等于没装。
#        现在只在**区间重叠**时才咬：台账推进到的页码 ≥ 断点区间的起始页。
#        （台账 p.9 / 断点 p.10–14 → 放行 ｜ 台账 p.12 或 `✅全` / 断点 p.10–14 → 拦住）
#        ⚠️ 页码解析不出来 → **按老逻辑拦住**（fail-closed）。这把锁的价值在于
#        「宁可误拦不可漏放」—— 它防的是把「我讲过」记成「他学过」。
# 不是 git 仓库 / git 不在 → 整节跳过（迁移新机器时不炸）。

# 页码引用：`p.9` · `p.10–14` · `p. 10 - 14`（`viewer`/`页脚` 这类前缀不参与匹配）。
# 破折号四种都收 —— 台账/日志里 `–`(U+2013) 和 `-` 混着用，`—`(U+2014) 和 `~` 也出现过。
# 只认带 `p.` 前缀的数字：`全 43 页完成` 里那个 43 是**页数**不是页码，绝不能当页码读。
PAGE_RE = re.compile(r"[pP]\.\s*(\d+)\s*(?:[–—~-]\s*(\d+))?")


def _page_refs(s: str):
    """文本 → [(起页, 止页, 原文)]，单页时起止相同。"""
    out = []
    for m in PAGE_RE.finditer(s):
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        # 原文只用于报错时回显，`.strip()` 掉 `\s*` 顺手吞进来的尾空格（`p.9 `）
        out.append((min(lo, hi), max(lo, hi), m.group(0).strip()))
    return out


def _max_page(s: str):
    """文本里提到的最大页码；一个都没有 → None。
    取 max 而不是取「最后一个」是**故意的保守选择**：这个数只用来判「台账推进到哪」，
    数字越大越容易触发拦截，所以取 max 是 fail-closed 的方向。
    （现实例子：algorithms `w03-L1` 的讲到列既有 `viewer p.9` 又有旁注「讲完的是页脚 p.1–6」，
      两个记号系统混在一格里，取 max 得 9 = 真进度。）"""
    ps = [hi for _, hi, _ in _page_refs(s)]
    return max(ps) if ps else None


def _taught_cat(taught: str) -> int:
    """讲到列 → 状态等级：0 没学 · 1 部分 · 2 学完"""
    if not taught.strip() or "❌" in taught:
        return 0
    if re.search(r"p\.", taught) and "全" not in taught:
        return 1
    return 2


def _git(args):
    try:
        r = subprocess.run(["git", "-C", str(ROOT)] + args,
                           capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


def _ledger_rows(text: str):
    """从台账文本解析 {(课程, deck代号): (状态等级, 讲到列原文)}。
    ⚠️ 原文必须留着 —— [G3] 要从里面读页码（等级只有 0/1/2 三档，看不出推进到第几页）。"""
    out, cur = {}, None
    for line in text.split("\n"):
        h = re.match(r"^##\s+(\w+)", line)
        if h:
            cur = h.group(1) if h.group(1) in COURSES else None
            continue
        if not cur or not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 7:
            continue
        m = DECK_RE.search(cells[2])
        if m:
            out[(cur, m.group(0))] = (_taught_cat(cells[4]), cells[4])
    return out


def _bp_pending_pages(bp_text: str, pending: set):
    """断点节 → {deck代号: (进行中那一块的起始页, 区间原文)}。

    只认「进行中」那一行上的页码 —— 断点节里还有别的页码（已讲到哪、已完成的块），
    混进来会把起始页拉小到 p.1，那就又变成「永远红」。断点节的用法本来就规定
    进行中那行要写「哪门课、哪个 deck、哪几页」，所以那一行是唯一权威。
    🔒 那行没写页码 → 这里返回不了区间 → 调用方 fail-closed 拦住（不是放行）。"""
    out = {}
    for line in bp_text.split("\n"):
        if "进行中" not in line:
            continue
        refs = _page_refs(line)
        if not refs:
            continue
        lo = min(r[0] for r in refs)
        disp = " / ".join(r[2] for r in refs)
        # 行里没写 deck 代号 → 落到所有挂着没答的 deck 上（保守：宁可多管）
        for dk in (DECK_RE.findall(line) or sorted(pending)):
            if dk not in out or lo < out[dk][0]:
                out[dk] = (lo, disp)
    return out


def check_receipts():
    section("[G] 凭证锁（台账推进 / 重讲）")
    rc, _ = _git(["rev-parse", "HEAD"])
    if rc != 0:
        WARN.append("[G] 不是 git 仓库或无提交，凭证锁跳过")
        print("⚠️ 无 git 基线，跳过")
        return
    _, head_ledger = _git(["show", "HEAD:shared/lecture台账.md"])
    _, head_hours = _git(["show", "HEAD:shared/学时记录.md"])
    now_ledger = read(ROOT / "shared/lecture台账.md")
    now_hours = read(ROOT / "shared/学时记录.md")
    log = read(ROOT / "shared/学习日志.md")

    bad = []
    notes = []

    # --- 本回合台账里状态升级了的 deck
    old_c, new_c = _ledger_rows(head_ledger), _ledger_rows(now_ledger)
    advanced = [k for k in new_c if new_c[k][0] > old_c.get(k, new_c[k])[0]]
    # 注：old 里没有的 deck（新归档的行）不算推进 —— 新行常常是 ❌未开始，真推进会再触发

    # [G3] 用的「推进」更细一档：等级升级**或**同一等级里页码往前走了（p.9 → p.12）。
    # [G1] 那条继续用等级判（别动它）—— 这里只是让重讲锁能看见「块级」推进。
    advanced_pages = []
    for k, (cat, cell) in new_c.items():
        if k not in old_c:
            continue
        ocat, ocell = old_c[k]
        if cat > ocat:
            advanced_pages.append(k)
            continue
        if cat == ocat:
            nm, om = _max_page(cell), _max_page(ocell)
            if nm is not None and (om is None or nm > om):
                advanced_pages.append(k)

    # --- [G1] 推进必须有学时记录凭证
    added_hours = set(now_hours.split("\n")) - set(head_hours.split("\n"))
    for cur, dk in advanced:
        receipt = any(dk in l and "答" in l and "批" in l for l in added_hours)
        if not receipt:
            bad.append(f"[G1] {cur} `{dk}` 台账推进了，但学时记录没有同回合新增的"
                       f"「{dk} + 答 + 批」行 —— 讲+答+批齐了才许推进，凭证是学时记录那一行")

    # --- 断点节
    m = re.search(r"## ⏸️ 当前断点(.*?)\n## ", log, flags=re.S)
    bp = m.group(1) if m else ""
    bp_lines = [l for l in bp.split("\n") if not l.strip().startswith(">")]
    bp_text = "\n".join(bp_lines)

    # --- [G3] 断点挂着没答完 → 台账不许推进到**那几页**（按区间判，不按 deck 名判）
    if re.search(r"⬜", bp_text) and re.search(r"没答|未答|还没答", bp_text):
        pending = set(DECK_RE.findall(bp_text))
        pend_pages = _bp_pending_pages(bp_text, pending)
        for cur, dk in advanced_pages:
            if dk not in pending:
                continue
            cat, cell = new_c[(cur, dk)]
            rng = pend_pages.get(dk)
            if rng is None:
                # fail-closed：断点说这个 deck 有题没答，但没写清是哪几页 → 按老逻辑拦住
                bad.append(f"[G3] 断点里 `{dk}` 挂着没答完的题，**但页码没解析出来**"
                           f"（进行中那行要写成 `p.10–14` 这种形式），"
                           f"无法判断有没有重叠 → 保守拦住：{cur} `{dk}` 台账先别推进")
                continue
            lo, disp = rng
            if cat >= 2:
                bad.append(f"[G3] 断点里 `{dk}` 的 {disp} 还没答完，"
                           f"台账却把 {cur} `{dk}` 记成了「✅全」—— 没答完的页不许算进度")
            else:
                mp = _max_page(cell)
                if mp is None:
                    bad.append(f"[G3] 断点里 `{dk}` 的 {disp} 还没答完，而 {cur} `{dk}` 的"
                               f"台账「讲到」列**读不出页码**（`{cell[:40]}`）→ 保守拦住")
                elif mp >= lo:
                    bad.append(f"[G3] 断点里 `{dk}` 的 {disp} 还没答完，台账却记到了 p.{mp}"
                               f" —— 重叠了（没答完的块要重讲，不许先记账）")
                else:
                    notes.append(f"[G3] {cur} `{dk}` 台账记到 p.{mp}，断点挂着的是 {disp}"
                                 f" —— 不重叠，放行（分块讲的正常状态）")

    if bad:
        FAIL.append(f"[G] 凭证锁未过（{len(bad)} 处）")
        for b in bad:
            print(f"❌ {b}")
    else:
        OK.append("[G]")
        print(f"✅ 凭证锁全过（本回合台账推进 {len(advanced)} 处，断点"
              f"{'有' if ('进行中' in bp_text and '无进行中' not in bp_text) else '无'}进行中的块）")
        for n in notes:
            print(f"　 ℹ️ {n}")


# ----------------------------------------------------------------
def run(fn):
    """跑一项检查。任何没料到的异常都变成一条 ❌，**绝不让 traceback 漏出去、也绝不跳过后面的项和汇总**。
    （汇总节没打印 = inject-context.sh 判定脚本没跑完。）"""
    try:
        fn()
    except Exception as e:          # noqa: BLE001 —— 兜底
        try:
            ln = traceback.extract_tb(e.__traceback__)[-1].lineno
        except Exception:
            ln = "?"
        tag = fn.__name__.replace("check_", "")
        FAIL.append(f"[{tag}] 检查自己崩了：{type(e).__name__}: {e}（检查.py 第 {ln} 行）")
        print(f"❌ [{tag}] 自己崩了：{type(e).__name__}: {e}（检查.py 第 {ln} 行）")


if __name__ == "__main__":
    print(f"项目根：{ROOT}")
    for _fn in (check_quicktable, check_paths, check_hours, check_dashboard,
                check_core_readable, check_ledger_pages, check_subs, check_inbox,
                check_handover, check_prescan,
                check_rule_sync,      # 🔒 [13] 排在 [11]/[1·语义]/[G] 之前 —— 它崩了也不许拖垮后面的凭证锁（run 兜底）
                check_breakpoint, check_quicktable_vs_ledger, check_receipts):
        run(_fn)

    section("汇总")
    print(f"✅ 通过 {len(OK)} 项")
    for w in WARN:
        print(f"⚠️ {w}")
    for f in FAIL:
        print(f"❌ {f}")
    if FAIL:
        print("\n→ 有硬伤，修完再念暗号。")
        sys.exit(1)
    print("\n→ 机械项全过。剩下这两项脚本盖不住，**必须人工**：")
    print("   [1] 学习日志上半部分的语义过期句 —— 2.0 删掉了 [1·全文]"
          "（它输出 ✅ 但一处都没比过，是假绿灯），这一层现在完全靠人工，别以为有机械保障。")
    print("   [5] 台账「讲到」列本身记得对不对（讲+答+批是否真的齐了 —— 台账记错了脚本看不出来）。")
