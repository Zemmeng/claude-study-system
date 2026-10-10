#!/usr/bin/env python3
"""按 shared/配置.json 初始化一个新学期 —— clone 下来之后跑的第一条命令。

用法：
    python3 shared/工具/初始化.py            # 预演：只打印会做什么，不动任何文件
    python3 shared/工具/初始化.py --apply    # 真的做

它做五件事：
  ① 删掉示例课目录（algorithms/ networks/ —— 认 `<课程>/CLAUDE.md` 里的「示例数据」标记）
     🔒 目录里有 git 没跟踪或改过的文件（= 你自己放进去的东西）就不删，报出来让你先挪走
  ② 给配置里的每门课建目录骨架 + 一份 `<课程>/CLAUDE.md` 模板
  ③ 重置 shared/ 的账本：带「示例数据」标记的 → 换成空白骨架；
     不带标记的（= 已经是你自己的数据）→ 一行不删，只补上缺的课程节
  ④ `<课程>/课件/` 里已经放了 `wNN-LN.pdf` 的 → 自动登记进台账（页数用 pdfinfo 数），没预扫的顺手预扫
  ⑤ 跑一遍 检查.py

可以反复跑：加了一门课、放了新课件，再跑一次 --apply 就会补上，不会覆盖已有内容。
"""
import datetime as dt
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import 配置  # noqa: E402

ROOT = 配置.ROOT
APPLY = "--apply" in sys.argv
MARK = "示例数据"
GEN = "（由 `shared/工具/初始化.py` 生成）"
SUBDIRS = ["课件/_预扫", "字幕", "笔记", "收尾卷", "作业", "tute", "教材", "例卷", "录音"]
DECK_PDF = re.compile(r"^w(\d{2})-L\d+[a-z]?\.pdf$")

actions = []
EXAMPLE_STATE = None   # main() 开头定：学习日志还带「示例数据」标记 = 整个仓库还是示例状态


def p(*parts):
    return os.path.join(ROOT, *parts)


def rel(path):
    return os.path.relpath(path, ROOT)


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def write(path, text, why):
    actions.append(f"写  {rel(path)}  —— {why}")
    if APPLY:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def is_example(path):
    t = read(path)
    return t is not None and MARK in t


# ------------------------------------------------------------------ 校验配置
def check_config():
    errs = []
    if not 配置.COURSE_LIST:
        errs.append("courses 是空的 —— 至少配一门课")
    for c in 配置.COURSE_LIST:
        k = c.get("key", "")
        if not re.fullmatch(r"\w+", k):
            errs.append(f"课程 key {k!r} 只能用字母 / 数字 / 下划线 / 中文（它同时是目录名和台账节标题的第一个词）")
        if k in ("shared", "docs"):
            errs.append(f"课程 key 不能叫 {k!r}（和项目目录撞名）")
    sem = 配置.CFG.get("semester", {})
    if sem.get("start"):
        try:
            d = dt.date.fromisoformat(sem["start"])
            if d.weekday() != 0:
                errs.append(f"semester.start = {sem['start']} 不是周一 —— 填第 1 教学周的周一")
        except ValueError:
            errs.append(f"semester.start = {sem['start']!r} 不是 YYYY-MM-DD")
    for b in sem.get("breaks", []):
        try:
            dt.date.fromisoformat(b["from"]), dt.date.fromisoformat(b["to"])
        except (KeyError, ValueError):
            errs.append(f"semester.breaks 里这一项格式不对：{b}（要 {{\"from\": \"YYYY-MM-DD\", \"to\": \"YYYY-MM-DD\"}}）")
    if errs:
        print("❌ shared/配置.json 有问题，先改好再跑：")
        for e in errs:
            print("   ·", e)
        sys.exit(1)


# ------------------------------------------------------------------ ① 示例课
def untracked_or_modified(d):
    """目录里 git 没跟踪 / 改过的文件 —— 那是用户自己放的，不许删。"""
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", d],
                             cwd=ROOT, capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return [l[3:] for l in out.stdout.splitlines() if l.strip() and not l[3:].endswith(".gitkeep")]


def remove_examples():
    keep = "--keep-examples" in sys.argv
    for name in sorted(os.listdir(ROOT)):
        d = p(name)
        if not os.path.isdir(d) or name.startswith((".", "_")) or name in ("shared", "docs"):
            continue
        cm = read(os.path.join(d, "CLAUDE.md"))
        if not cm or "示例数据（虚构的课）" not in cm:
            continue
        if keep:
            actions.append(f"留  {name}/  —— 示例课（--keep-examples）")
            continue
        dirty = untracked_or_modified(name)
        if dirty is None:
            print(f"⚠️ {name}/ 是示例课，但这里不是 git 仓库，没法确认里面有没有你自己的文件 —— 没删。确认后手动 rm -rf {name}")
            continue
        if dirty:
            print(f"⚠️ {name}/ 是示例课，但里面有你自己放的文件，没删：")
            for f in dirty[:8]:
                print(f"     {f}")
            print(f"   把这些挪走（或把示例标记从 {name}/CLAUDE.md 里删掉表示要留着用），再跑一次。")
            continue
        actions.append(f"删  {name}/  —— 示例课")
        if APPLY:
            shutil.rmtree(d)
    for f in sorted(os.listdir(p("shared", "交接", "已处理")) if os.path.isdir(p("shared", "交接", "已处理")) else []):
        fp = p("shared", "交接", "已处理", f)
        if f.endswith(".md") and EXAMPLE_STATE and is_example(fp):
            actions.append(f"删  {rel(fp)}  —— 示例交接单")
            if APPLY:
                os.remove(fp)


# ------------------------------------------------------------------ ② 课程目录
COURSE_CLAUDE = """# {key}（{code}）—— 课程说明

> ✏️ 每门课一份，放这门课的**考核、hurdle、考试形式、周计划**。{gen}
> 主 `CLAUDE.md` 规定：**回答考核权重、hurdle、考试范围之前必须先读这份** —— 记错 hurdle 会排错优先级。

## 考核构成

| 项 | 权重 | 形式 | 备注 |
|---|---|---|---|
| ✏️ | | | |

## 考试形式要点

- ✏️ 闭卷 / 开卷？手写 / 机考？时长？

## 周计划

| 周 | 主题 | deck |
|---|---|---|
| W1 | ✏️ | `w01-L1` |

## 这门课的特殊约定

- ✏️ 例：页脚号和物理页差多少（offset 记在每份 deck 的对照表顶部）· weekly quiz 的放出 / 截止规律
"""


def make_course_dirs():
    for c in 配置.COURSE_LIST:
        k = c["key"]
        for sd in SUBDIRS:
            d = p(k, sd)
            if not os.path.isdir(d):
                actions.append(f"建  {k}/{sd}/")
                if APPLY:
                    os.makedirs(d, exist_ok=True)
                    if sd != "课件/_预扫":
                        open(os.path.join(d, ".gitkeep"), "w").close()
        if read(p(k, "CLAUDE.md")) is None:
            write(p(k, "CLAUDE.md"), COURSE_CLAUDE.format(key=k, code=c.get("code", ""), gen=GEN), "课程说明模板")


# ------------------------------------------------------------------ ④ 已放进去的课件
def pdf_pages(path):
    exe = shutil.which("pdfinfo")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, path], capture_output=True, text=True, timeout=30).stdout
        m = re.search(r"^Pages:\s+(\d+)", out, re.M)
        return int(m.group(1)) if m else None
    except subprocess.TimeoutExpired:
        return None


def deck_rows(key):
    d = p(key, "课件")
    rows = []
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        m = DECK_PDF.match(f)
        if not m:
            continue
        deck = f[:-4]
        n = pdf_pages(os.path.join(d, f))
        rows.append((int(m.group(1)), deck, n))
    return rows


def prescan_missing():
    if not shutil.which("pdfinfo"):
        print("⚠️ 没装 poppler（pdfinfo / pdftotext）—— 台账页数留空，预扫跳过。装好后再跑一次 --apply 会补上")
        return
    for c in 配置.COURSE_LIST:
        for _, deck, _ in deck_rows(c["key"]):
            if not os.path.isfile(p(c["key"], "课件", "_预扫", f"{deck}.md")):
                actions.append(f"扫  {c['key']}/课件/{deck}.pdf  —— 预扫落盘到 _预扫/{deck}.md")
                if APPLY:
                    subprocess.run([sys.executable, p("shared", "工具", "预扫.py"),
                                    p(c["key"], "课件", f"{deck}.pdf"), "--save"],
                                   cwd=ROOT, capture_output=True, text=True)


def ledger_row(week, deck, pages):
    return (f"| W{week} | `{deck}` | {pages if pages else '✏️'} | ❌未开始 | ⏳待发 | ?未问 | "
            f"预扫 `课件/_预扫/{deck}.md` |")


# ------------------------------------------------------------------ ③ 账本骨架
LEDGER_HEAD = """# Lecture 台账

> **这是「欠帐表」的唯一数据源。** 问进度、排优先级、算还欠多少页，先查这张表，不要凭印象。
> **每讲完一段、每收到一份字幕、每问出一次「去没去」，当场回来改这里。**
> 一门课一个 `## <课程key> <课程代码> · lecture … · tute …` 节，节标题第一个词必须是 `shared/配置.json` 里的 `key`。{gen}

## 四列的语义（别搞混）

| 列 | 取值 | 含义 |
|---|---|---|
| **讲到** | `❌未开始` / `p.N` / `✅全` | **只指「在对话里一块一块讲过」**。这一列决定欠帐 |
| **字幕** | `⏳待发` / `✅已收` / `➖不需要` | `➖` 只用于纯行政 deck 或没有录音的课 |
| **到场** | `去了` / `去了·没听进` / `没去` / `?未问` | 影响**要不要录播/字幕**，**不影响欠帐** |
| **deck** | 文件名 / `无slides` | `无slides` 的课，字幕是唯一来源，优先级最高 |

> 🆕 **新课件到手先「预扫」**（`/预扫` · 工具 `shared/工具/预扫.py --save`）。
> 摘要在各课的 `课件/_预扫/<deck>.md` —— 含 offset 实测、续篇判定、动画帧去重、逐页骨架、前置警告。
> 🔴 **判续篇必须看 p.2** —— 有些课每份 deck 的**封面页脚都印 `1`**，看 p.1 会被骗。

**⚠️ 四条硬规矩**

0. 🔒🔒 **「讲到」列里只许出现「已经讲+答+批完」的页码。** 不许写未来页码 —— `检查.py` 的 [G3] 重讲锁从这一列读最大页号。
   要指下一块，写「**见 `学习日志` 断点**」，页码留在断点里。
1. **「到场」不抵扣欠帐。** 只有「在对话里讲过」才算学过。
2. **字幕永远不写「缺失/丢了」。** 没收到就是 `⏳待发`，开讲前问他要。
3. **`?未问` 要主动问掉**，问一次存一次，同一节不问第二遍。

---
"""

TABLE_HEAD = "| 周 | deck | 页数 | 讲到 | 字幕 | 到场 | 备注 |\n|---|---|---|---|---|---|---|\n"


def course_title(c):
    bits = [c["key"]]
    if c.get("code"):
        bits.append(c["code"])
    t = " ".join(bits)
    extra = [f"lecture {c['lecture']}" if c.get("lecture") else "", f"tute {c['tute']}" if c.get("tute") else ""]
    extra = [e for e in extra if e]
    return t + (" · " + " · ".join(extra) if extra else "")


def ledger_section(c):
    rows = "\n".join(ledger_row(w, d, n) for w, d, n in deck_rows(c["key"]))
    return f"## {course_title(c)}\n\n{TABLE_HEAD}{rows}{chr(10) if rows else ''}\n"


def week_table():
    sem = 配置.CFG.get("semester", {})
    if not sem.get("start"):
        return ("> ✏️ **在 `shared/配置.json` 的 `semester` 里填 `start`（第 1 教学周的周一）、`teaching_weeks`、`breaks`，"
                "再跑一次 `初始化.py --apply` 生成周次表** —— 没有它，导航层算不出「今天第几周」。\n"), None
    d = dt.date.fromisoformat(sem["start"])
    n = int(sem.get("teaching_weeks", 12))
    breaks = sorted((dt.date.fromisoformat(b["from"]), dt.date.fromisoformat(b["to"])) for b in sem.get("breaks", []))
    lines = ["| 周 | 日期 |", "|---|---|"]
    w = 1
    while w <= n:
        br = next((b for b in breaks if b[0] <= d <= b[1]), None)
        if br:
            lines.append(f"| — | {br[0]:%m-%d} ~ {br[1]:%m-%d} *break* |")
            d = br[1] + dt.timedelta(days=1)
            while d.weekday() != 0:
                d += dt.timedelta(days=1)
            continue
        lines.append(f"| W{w} | {d:%m-%d} ~ {d + dt.timedelta(days=6):%m-%d} |")
        d += dt.timedelta(days=7)
        w += 1
    end = d - dt.timedelta(days=1)
    return "\n".join(lines) + "\n", (dt.date.fromisoformat(sem["start"]), end)


def deadlines_text():
    table, span = week_table()
    sem = 配置.CFG.get("semester", {})
    span_line = f"- {sem.get('name', '本学期')}：**{span[0]} ~ {span[1]}**\n" if span else ""
    brk = "".join(f"- 非教学周：**{b['from']} ~ {b['to']}**\n" for b in sem.get("breaks", []))
    return f"""# 关键日期

> 硬日期来自课件 / LMS 明写；标 **约** 的是只给了周次、按学期日历推的。{GEN}
> 🔒 **周次表（下面「学期」节）是 `shared/工具/导航.py` 算「今天第几周」的唯一依据** —— 格式别动：`| W1 | 07-27 ~ 08-02 |`。
> 🔒 **时间线只放 quiz · assignment · project · 考试 · tute，不放 lecture**。
> 🔒 **已提交的行在日期或事项列写「已提交」**，导航层看见这个词就不再把它当硬截止。

## 学期

{span_line}{brk}
{table}
## 时间线

| 日期 | 周 | 课程 | 事项 | 权重 |
|---|---|---|---|---|

## 待补

- ✏️ 每门课的 assignment / quiz / 考试日期放出来就加进时间线
"""


def log_text():
    rows = "\n".join(f"| **{c['key']}** {c.get('code', '')} | 刚开学，等第一份课件 | — |" for c in 配置.COURSE_LIST)
    return f"""# 学习日志

> **结构：`===== 历史流水 =====` 以上是当前状态，以下是流水账。** 读状态只看上半部分。{GEN}
> ⚠️ 每次往下半部分追加记录之后，回头把上半部分逐行读一遍，改掉和新内容矛盾的旧句子。

---

# 📍 当前进度速查

> ⚠️ **页数、讲到哪、字幕、到场 一律去 `shared/lecture台账.md` 查，那是唯一数据源。**
> 这里只写「优先级和为什么」—— 🔒 **表格行里一个页码都不许出现**（`检查.py [2]` 盯着）。

| 课 | 状态一句话（**只写优先级和为什么**）| 优先级 |
|---|---|---|
{rows}

## ⏭️ 当前状态 —— **新会话从这里接手**

> 🔴 **接下来的，按截止排**（只写日期，不写「还有几天」）：
> （还没有）

## ⏸️ 当前断点（**每次开讲一块时更新这里；块完成后清空写「无」**）

> 🔒 开讲任何一块，先在这里写下：哪门课、哪个 deck、哪几页、**出的题全文（没答完的）**。块完成（讲+答+批齐）→ 进台账，这里清回「无」。

⬜ **断点：无**

## 🔁 反复出现的毛病（不是知识点，是习惯）

（还没有）

===== 历史流水 =====
"""


def hours_section(c):
    return f"""## {c['key']} {c.get('code', '')}

| 日期 | 时段 | 时长 | 内容 | 产出 |
|---|---|---|---|---|

**小计：0 h 00 min**

---

"""


def hours_text():
    rows = "\n".join(f"| **{c['key']}** | **0 h 00 min** |" for c in 配置.COURSE_LIST)
    body = "".join(hours_section(c) for c in 配置.COURSE_LIST)
    return f"""# 学时记录

> **只记真正在学内容的时间**，不记建项目、归档文件、装软件、改配置这些基建。{GEN}
> 时间按对话活跃时段估算，标 `≈` 的是估算，标 `实测` 的有明确起止。
> 🔒 **学习会话（`/学` `/作业`）只追加明细行；顶部累计表和各科「小计」留给 `/总控` 重算**（`检查.py [4]` 核三处一致）。

---

## 累计

| | 时长 |
|---|---|
{rows}
| **总计** | **0 h 00 min** |

---

{body}## 不计入学时的基建（仅记录，不算学习）

| 日期 | 时长 | 做了什么 |
|---|---|---|
"""


def notice_title(c):
    return f"## {c.get('name') or c['key']} {c.get('code', '')}".rstrip()


def notices_text():
    table = "\n".join(f"| {i + 2} | `{notice_title(c)[3:]}` | {c['key']} |" for i, c in enumerate(配置.COURSE_LIST))
    secs = "\n---\n\n".join(f"{notice_title(c)}\n\n（还没有）\n" for c in 配置.COURSE_LIST)
    return f"""# 公告与口头信息

> 老师口头说的、LMS 公告、论坛答疑、考试范围提示 —— 凡是**课件上没有**的，都记这里。{GEN}
> 每条带 **日期 + 来源**，倒序排，最新在上。讲课时从转录里挖到的 🔴 考点 / ⚪ 不考，同时进对应 deck 的对照表和这里。

**🔒 法定节名 —— 只许用下面这几个，不许新建同义节**

| # | 节名（必须一字不差） | 装什么 |
|---|---|---|
| 1 | `全校 / 行政（一次性事件）` | 系统上线、extension 政策、校级通知 |
{table}

---

## 全校 / 行政（一次性事件）

（还没有）

---

{secs}"""


TSV_HEADS = {
    ("shared", "提醒.tsv"): "# 到点提醒 —— 一行一条：类型<TAB>规则<TAB>文字。改提醒改这里，不用动 提醒.py。过期条目定期删。\n"
                            "# 类型：RANGE 起..止 · WEEKLY 周几;from=;until= · EVERY 间隔天数;from=;days=响几天\n",
    ("shared", "活动会话.tsv"): "# 活动会话登记表 —— /学 /作业 /总控 开场登记一行，收尾时把「状态」改成 done。\n"
                               "# 字段(tab 分隔)：开始时间(YYYY-MM-DD HH:MM)\t类型(学/作业/总控)\t在做什么(一句话)\t状态(active/done)\t[可选]收尾时间\n"
                               "# 🔒 状态列只许是 active 或 done，不许加别的字 —— 导航.py 按它精确匹配。\n",
    ("shared", "工具", "已完成事实.tsv"): "# 行动项审计的「免死牌」—— 正则<TAB>为什么已完成（会原样打给审计输出）。\n"
                                       "# 一件事被确认完成、且外部（邮件/公告）还会反复催它时，加一行进来。事实变了（比如撤销）就删行。\n",
}


def reset_tsv():
    """只在示例状态下清空；之后这几张表是你自己的数据，一行不动。"""
    for parts, head in TSV_HEADS.items():
        fp = p(*parts)
        t = read(fp)
        if t is None or (EXAMPLE_STATE and t != head):
            write(fp, head, "空白表头" if t is None else "清掉示例行")


def insert_table_row(t, anchor, row, before=None):
    """在 anchor 标题下的第一张表里插一行（before 给了就插在以它开头的那行前面，否则插在表尾）。"""
    m = re.search(anchor, t, re.M)
    if not m:
        return t
    lines = t[m.end():].split("\n")
    idx = [i for i, l in enumerate(lines) if l.startswith("|")]
    if not idx:
        return t
    first = idx[0]
    last = first
    while last + 1 < len(lines) and lines[last + 1].startswith("|"):
        last += 1
    pos = last + 1
    if before:
        for i in range(first, last + 1):
            if lines[i].startswith(before):
                pos = i
                break
    lines.insert(pos, row)
    return t[:m.end()] + "\n".join(lines)


def append_missing(fp, sections, label):
    """不是示例的账本：只补缺的课程节（和对应的汇总行），一个字都不删。"""
    t = read(fp)
    for c in 配置.COURSE_LIST:
        if label == "学习日志":
            if not re.search(rf"^\|\s*\**{re.escape(c['key'])}\**", t, re.M):
                actions.append(f"补  {rel(fp)}  —— 速查表加 {c['key']} 一行")
                t = insert_table_row(t, r"^#\s*📍?\s*当前进度速查", f"| **{c['key']}** {c.get('code', '')} | 刚加进来，等第一份课件 | — |")
            continue
        if re.search(rf"^##\s+{re.escape(c['key'])}\b", t, re.M) or (label == "公告" and notice_title(c) in t):
            continue
        actions.append(f"补  {rel(fp)}  —— 加 {c['key']} 一节")
        t = t.rstrip("\n") + "\n\n" + sections(c)
        if label == "学时":
            t = insert_table_row(t, r"^## 累计", f"| **{c['key']}** | **0 h 00 min** |", before="| **总计**")
    if APPLY:
        with open(fp, "w", encoding="utf-8") as f:
            f.write(t)


def append_new_decks():
    """台账已经是你自己的数据时：课件/ 里有、台账里没有的 deck，补到那门课的表尾。"""
    fp = p("shared", "lecture台账.md")
    t = read(fp)
    if t is None or MARK in t:
        return
    for c in 配置.COURSE_LIST:
        m = re.search(rf"^##\s+{re.escape(c['key'])}\b.*?(?=^##\s|\Z)", t, re.M | re.S)
        if not m:
            continue
        sec = m.group(0)
        new = [ledger_row(w, d, n) for w, d, n in deck_rows(c["key"]) if f"`{d}`" not in sec]
        if not new:
            continue
        actions.append(f"补  shared/lecture台账.md  —— {c['key']} 新登记 {len(new)} 份 deck")
        lines = sec.rstrip("\n").split("\n")
        last = max(i for i, l in enumerate(lines) if l.startswith("|"))
        lines[last + 1:last + 1] = new
        t = t[:m.start()] + "\n".join(lines) + "\n\n" + t[m.end():].lstrip("\n")
    if APPLY:
        with open(fp, "w", encoding="utf-8") as f:
            f.write(t)


def reset_ledgers():
    gen = "\n> " + GEN
    plan = [
        (p("shared", "lecture台账.md"), lambda: LEDGER_HEAD.format(gen=gen) + "\n" + "".join(ledger_section(c) for c in 配置.COURSE_LIST),
         ledger_section, "台账"),
        (p("shared", "deadlines.md"), deadlines_text, None, "deadlines"),
        (p("shared", "学习日志.md"), log_text, True, "学习日志"),
        (p("shared", "学时记录.md"), hours_text, hours_section, "学时"),
        (p("shared", "公告.md"), notices_text, lambda c: f"---\n\n{notice_title(c)}\n\n（还没有）\n", "公告"),
    ]
    for fp, full, section, label in plan:
        t = read(fp)
        if t is None or MARK in t:
            write(fp, full(), "空白骨架" if t is None else "示例 → 空白骨架")
        elif section:
            append_missing(fp, section, label)
    append_new_decks()
    reset_tsv()
    stamp = p("shared", "工具", "邮箱简报戳")
    if read(stamp) is None or EXAMPLE_STATE:
        write(stamp, "从未\n", "邮箱简报戳归零")


# ------------------------------------------------------------------ main
def main():
    global EXAMPLE_STATE
    EXAMPLE_STATE = is_example(p("shared", "学习日志.md"))
    check_config()
    remove_examples()
    make_course_dirs()
    reset_ledgers()
    prescan_missing()

    if not actions:
        print("✅ 已经是最新的，没有要做的。")
    else:
        print(("已完成" if APPLY else "预演（加 --apply 才会真的做）") + f"：{len(actions)} 项")
        for a in actions:
            print("  ", a)
    if not APPLY:
        return
    print("\n—— 跑一遍一致性检查 ——")
    r = subprocess.run([sys.executable, p("shared", "工具", "检查.py")], cwd=ROOT, capture_output=True, text=True)
    out = r.stdout
    print(out[out.find("======== 汇总"):] if "======== 汇总" in out else out[-1500:])
    print("下一步：\n"
          "  1. 改 CLAUDE.md 里带 ✏️ 的三节（关于他 · 他的作息 · 他的课表）和各课的 <课程>/CLAUDE.md\n"
          "  2. 在 shared/deadlines.md 的时间线里填上 assignment / quiz / 考试日期\n"
          "  3. 课件放进 <课程>/课件/wNN-LN.pdf 之后，再跑一次 初始化.py --apply 自动登记 + 预扫\n"
          "  4. claude")


if __name__ == "__main__":
    main()
