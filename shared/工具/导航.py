#!/usr/bin/env python3
"""
2.0 导航层 —— SessionStart 只注入这个，不再 cat 三个文件的全文。

为什么（2026-09-01）：
    1.x 的 inject-context.sh 无脑注入 学习日志上半 + deadlines 全文 + lecture台账全文
    = 180 KB ≈ 5 万 token，而其中 95 KB 是已结清的历史。
    根因是「会话开始时不知道要干嘛，所以只能全给」。

    2.0 把这件事倒过来：开场只给一张**导航层**（几 KB），
    要什么由 /学 /作业 /总控 三个 slash command 按类型自己去取。

输出（全部现算，不抄任何手写的「现在是第几周」）：
    · 今天是学期第几周（自己按 deadlines 的周表算）
    · 未来 14 天的硬截止（只要 tute 和 due date，每条压成一行）
    · 各门课一行状态（从学习日志的速查表取，**不含页数** —— 页数只有台账说了算）
    · 当前断点状态

课程列表、学期年份一律读 shared/配置.json（经 shared/工具/配置.py），脚本里不写死课程名。

🔒 健壮性：任何账本缺失 / 某一节解析炸了，都只打一行 ⚠️ 然后继续往下，**不许抛 traceback**
（模板刚 clone 下来时账本不全是常态；开场 hook 靠这个脚本的输出，它崩了整个导航层就没了）。

用法：python3 shared/工具/导航.py
"""
import datetime
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------- 读配置（课程别名 / 学期年份）----------
# 配置.py 在 shared/配置.json 缺失或写错时会 sys.exit —— 这里接住，降级成「没有课程」继续往下跑。
CFG_WARN = ''
try:
    import 配置
    ALIAS = dict(配置.ALIAS)
    COURSES = tuple(配置.COURSES)
    YEAR = 配置.YEAR or datetime.date.today().year
except (SystemExit, Exception) as _e:      # noqa: BLE001
    配置 = None
    ALIAS, COURSES = {}, ()
    YEAR = datetime.date.today().year
    CFG_WARN = f'⚠️ 读不到课程配置（shared/配置.json）：{str(_e)[:120]} —— 课程相关的推荐先跳过'
HORIZON = 14  # 未来多少天算「近的」

# 🔴 2026-09-03 加：deadlines 里标了已交的，就不再是「硬截止」也不是「现在可以做的」。
#    起因：某份 Written A1 在 09-02 23:24 已提交，但 09-03 早上的导航层
#    还把它列在「未来 14 天的硬截止」和「🎯 现在可以做的」第 3 条里。
SETTLED = re.compile(r'已交|已提交|已结清|这笔结清|已考完|✅ \*\*已考\*\*')


def read(rel):
    p = os.path.join(ROOT, rel)
    try:
        with open(p, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return ''


def strip_md(s):
    """去掉 markdown 强调记号和 emoji 噪音，压成一行。"""
    s = re.sub(r'<br\s*/?>', ' ; ', s)
    s = re.sub(r'\*\*|`|~~', '', s)
    s = re.sub(r'[🔴🟡🟠🟢⚪⬜✅❌⚠️📌📍🆕🎉⭐🔒🔑📄📅🗓️📥📬⏰⏸️⏭️🔁🔗🎯💡➡️→]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


# ---------- 学期第几周 ----------
def week_table(dl):
    """从 deadlines 的周表解析 {周次: (起, 止)}。

    周表只写 MM-DD，年份从 配置.YEAR 起算。🔒 学期可能跨年（12 月开学、次年 1 月还在上）——
    按周号排好序，月份一往回走就把年份 +1。不能按出现顺序判：原项目的周表一行并排两周（W1 和 W7 同一行）。
    """
    raw = {}
    for m in re.finditer(r'\|\s*\*{0,2}(W\d{1,2})\*{0,2}\s*\|\s*\*{0,2}(\d{2})-(\d{2})\s*~\s*(\d{2})-(\d{2})',
                         dl):
        w, m1, d1, m2, d2 = m.groups()
        raw[w] = tuple(int(x) for x in (m1, d1, m2, d2))
    weeks, year, prev = {}, YEAR, None
    for w in sorted(raw, key=lambda k: int(k[1:])):
        m1, d1, m2, d2 = raw[w]
        if prev is not None and m1 < prev:
            year += 1
        prev = m1
        try:
            a = datetime.date(year, m1, d1)
            b = datetime.date(year + (1 if m2 < m1 else 0), m2, d2)
            weeks[w] = (a, b)
        except ValueError:
            pass
    return weeks


_SPAN = None


def md(month, day):
    """MM-DD → 日期。年份取离学期最近的那一个（学期跨年时 1 月的 DDL 落在下一年）。"""
    global _SPAN
    if _SPAN is None:
        wk = week_table(read('shared/deadlines.md') or '')
        _SPAN = (min(a for a, _ in wk.values()), max(b for _, b in wk.values())) if wk else ()
    cands = []
    for y in (YEAR - 1, YEAR, YEAR + 1):
        try:
            cands.append(datetime.date(y, month, day))
        except ValueError:
            pass
    if not cands:
        raise ValueError(f'{month:02d}-{day:02d}')
    if not _SPAN:
        return datetime.date(YEAR, month, day)
    lo, hi = _SPAN

    def dist(d):
        return 0 if lo <= d <= hi else min(abs((d - lo).days), abs((d - hi).days))
    return min(cands, key=dist)


def which_week(weeks, today):
    for w, (a, b) in weeks.items():
        if a <= today <= b:
            return w, b
    return None, None


# ---------- 近期硬截止 ----------
# 🔒 只认 tute 和 due date。lecture 一律不算硬截止 —— 他不去，那不是时间盒。
LECTURE_ONLY = re.compile(r'lecture|讲座')
HARD = re.compile(r'due|截止|tute|tutorial|workshop|quiz|test|MST|exam|考|交|提交|报名|申请')


def upcoming(dl, today):
    """从时间线表抽未来 HORIZON 天内的行，每条压成一行。"""
    out = []
    for line in dl.split('\n'):
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 4:
            continue
        m = re.search(r'(\d{2})-(\d{2})', cells[0])
        if not m:
            continue
        try:
            d = md(int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        delta = (d - today).days
        if not (0 <= delta <= HORIZON):
            continue
        # 已经交掉的不是硬截止了（「🎉 刚收尾的」那一节会显示它）
        if SETTLED.search(cells[0]) or SETTLED.search(cells[3]):
            continue
        event = strip_md(cells[3])
        # 只留第一句 —— 备注在 deadlines 原文里，要细节自己去翻
        event = re.split(r'\s;\s', event)[0][:110]
        if LECTURE_ONLY.search(event) and not HARD.search(event):
            continue
        weight = strip_md(cells[4]) if len(cells) > 4 else ''
        weight = '' if weight in ('—', '-', '') else f'  [{weight}]'
        when = strip_md(cells[0])
        course = strip_md(cells[2])
        tag = '🔴' if delta <= 3 else '  '
        out.append(f'{tag} {when}（还有 {delta} 天） · {course} · {event}{weight}')
    return out


# ---------- 各门课状态 ----------
def course_status(log):
    """取学习日志的「当前进度速查」表，每门一行。不含页数 —— 页数只有台账说了算。"""
    m = re.search(r'#\s*📍?\s*当前进度速查(.*?)(?=\n#{1,2}\s)', log, re.S)
    if not m:
        return []
    rows = []
    for line in m.group(1).split('\n'):
        if not line.startswith('|') or re.match(r'\|[\s\-:|]+\|$', line.strip()):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if cells[0].startswith('课'):
            continue
        # 🔒 列数不对要出声，不能静默跳过 —— 2026-09-01 某一行少了「优先级」列，
        #    导航层就这么把整门课漏掉了，而且没有任何提示。
        if len(cells) != 3:
            rows.append(f'⚠️ 速查表这一行列数不对（{len(cells)} 列，应为 3）：{strip_md(cells[0])[:30]}')
            continue
        course = strip_md(cells[0])
        state = strip_md(cells[1])
        pri = strip_md(cells[2])
        if not course:
            continue
        # 只取第一句 —— 展开的细节去学习日志看，导航层只负责指路
        first = re.split(r'\s;\s', state)[0]
        pri = re.split(r'[（(]', pri)[0].strip()[:18]   # 优先级列常带一长串理由，只要前缀（18 字够放「W05 Quiz 09-04」这种长度）
        rows.append(f'· {course:<24} [{pri}] {first[:96]}')
    return rows


# ---------- 断点 ----------
def breakpoint_state(log):
    m = re.search(r'##\s*⏸️?\s*当前断点[^\n]*\n(.*?)(?=\n##\s)', log, re.S)
    if not m:
        return '（找不到断点节）'
    body = m.group(1)
    lines = [strip_md(l) for l in body.split('\n') if l.strip() and not l.strip().startswith('>')]
    lines = [l for l in lines if l]
    if not lines:
        return '无'
    head = ' / '.join(lines[:2])
    n = len(body.split('\n'))
    warn = f'   ⚠️ 断点节现在 {n} 行 —— 超过 40 行说明该归档了' if n > 40 else ''
    return head[:200] + warn


# ---------- 活动会话 ----------
# 🔒 2026-09-02 他要的：「每次总控对话开的时候 你得知道我现在有哪几个对话还活着 正在学什么」。
#    为什么要登记表而不是直接查：CCD 的 list_sessions 有 isRunning 字段，但**实测全是 false**
#    （空闲等输入的会话也算 false），而且 hook 是 shell，根本调不了 MCP 工具。
#    所以改成会话自己登记：/学 /作业 /总控 开场写一行，收尾把状态改成 done。
# 36 h 而不是 12 h：他的模式是「一个对话学一整块」，跨一夜很正常（09-01 晚上开的复习会话
# 到 09-02 下午还在用）。真正的活动时间只有 CCD 的 lastActivityAt 知道，登记表看不到，
# 所以这里只做粗筛，精确核对留给总控里跑 list_sessions。
STALE_H = 36
DONE_H = 30   # 「最近收尾的」显示窗口（小时）—— 跨一夜也看得见


def _sessions(now):
    """返回 (还开着的, 最近收尾的)。

    🔴 2026-09-03 00:1x 加「最近收尾的」—— 血的教训：
    09-02 23:24 某份作业在另一个会话提交了，登记表那行已改成 done 并写明「已提交」，
    但导航层只显示 active，**done 的行看不见** → 23:57 出看板时照着过期的学习日志，
    把已经交掉的作业排成了周六 8 h 的主攻。**「刚刚完成了什么」是开场最该知道的事之一。**
    """
    live, done = [], []
    for ln in read('shared/活动会话.tsv').splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('#'):
            continue
        parts = ln.split('\t')
        if len(parts) < 4:
            continue
        started, kind, what, state = (parts[0].strip(), parts[1].strip(),
                                      parts[2].strip(), parts[3].strip())
        try:
            t = datetime.datetime.strptime(started, '%Y-%m-%d %H:%M')
            hrs = (now - t).total_seconds() / 3600
        except ValueError:
            hrs = None
        if state == 'active':
            mark = ''
            if hrs is not None and hrs > STALE_H:
                mark = f'  ⏳ 挂了 {hrs:.0f} h 没收尾 —— 问他这个还开着吗，没开就注销'
            live.append(f'· [{kind}] {what}（{started} 起）{mark}')
        elif state == 'done':
            # 🔴 2026-09-03 修：原来拿 parts[0]（**开始时间**）当收尾时间用，
            #    「最近 30 h 收尾的」实际成了「最近 30 h 开始的」——
            #    一个开了 3 天、刚收尾的会话会被漏掉。
            #    tsv 第 5 列（可选）= 收尾时间；没有就退回用开始时间，并标出来。
            fin_h, approx = hrs, True
            if len(parts) >= 5 and parts[4].strip():
                try:
                    ft = datetime.datetime.strptime(parts[4].strip(), '%Y-%m-%d %H:%M')
                    fin_h, approx = (now - ft).total_seconds() / 3600, False
                except ValueError:
                    pass
            if fin_h is not None and fin_h <= DONE_H:
                done.append(f'· [{kind}] {what}' + ('　(按开始时间估)' if approx else ''))
    return live, done


def pending_handovers():
    """`shared/交接/` 里没处理的交接单。

    🔴 同一次事故的另一半：学习/作业会话不许改 shared/ 账本，只能写交接单留给总控。
    但交接目录**没有任何门禁**（`收件箱/` 有 [10] 盯着，这里没有）——
    09-01 那两份躺了两天没人碰。**没有门禁的待办等于不存在。**
    """
    d = os.path.join(ROOT, 'shared/交接')
    try:
        return sorted(f for f in os.listdir(d)
                      if f.endswith('.md') and f != 'README.md')
    except OSError:
        return []


# ---------- 候选动作：开场直接让他选，不用背命令 ----------
# 🔒 2026-09-01 他说「好多命令我记不住 能不能对话开始你让我选」。
#    所以这里现算出「今天可以做的几件事」，开场摆成菜单让他点。
#    排序：没讲完的块 > 3 天内的硬截止 > 一周内的 > 下一份没讲的 deck > 总控。

# 课程别名（key / 中文名 / aliases → key）来自 shared/配置.json，见文件开头的 ALIAS
DECK_RE = re.compile(r'`(w\d{2}[-\w]*)`')


def busy_decks():
    """登记表里 active 会话正在讲的 deck —— 别推荐他再开一个会话讲同一份。

    🔴 2026-09-03 加：两个会话同讲一份 deck 会同写 `<课程>/字幕/<deck>-对照.md`
    （主 CLAUDE.md 说那是「唯一有硬门禁的文件」）。
    """
    busy = set()
    for ln in read('shared/活动会话.tsv').splitlines():
        parts = ln.split('\t')
        if len(parts) >= 4 and parts[3].strip() == 'active':
            for m in re.finditer(r'w\d{2}-L\d+', parts[2]):
                busy.add(m.group(0))
    return busy


def next_decks(ledger, urgent=()):
    """每门课「下一份还没讲完的 deck」。

    🔴 2026-09-03 改：原来只取「第一个不是 ✅全 的行」，**不看硬截止** ——
    实测它推的是 W1 的一份 47 页残段（**无硬截止**），
    而 5 天后的 Quiz 真正缺的是 W4 的一份 deck。
    现在：**近 10 天硬截止点名的 deck 优先**，其次才是「第一个缺口」。
    """
    busy = busy_decks()
    out, cur = {}, None
    for line in ledger.split('\n'):
        h = re.match(r'^##\s+(\w+)', line)
        if h:
            cur = ALIAS.get(h.group(1))          # 台账的「## <课程>」标题 → 课程 key；不认识的节跳过
            continue
        if not cur or cur in out or not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 4:
            continue
        m = DECK_RE.search(cells[1])
        if not m:
            continue                      # 「两份 diagnostic quiz」这类没代号的跳过
        taught = strip_md(cells[3])
        # 讲完了的、行政/不需要讲的，一律跳过 —— 它们不是「下一份要学的」
        if re.match(r'^全|^已做|^已讲', taught) or re.search(r'行政|不需要|已提取|➖', taught):
            continue
        deck = m.group(1)
        if deck in busy:
            continue                      # 已经有活会话在讲它，别推荐再开一个
        pages = strip_md(cells[2])
        partial = bool(re.search(r'p\.\d|🔄', taught))
        hot = deck in urgent              # 近 10 天硬截止点名的
        # 优先级：近期硬截止点名的 > 讲了一半的 > 还没开始的
        if cur not in out:
            out[cur] = (deck, pages, taught, partial, hot)
        else:
            _, _, _, p0, h0 = out[cur]
            if (hot and not h0) or (not h0 and partial and not p0):
                out[cur] = (deck, pages, taught, partial, hot)
    return out


def actions(dl, ledger, log, today):
    acts = []

    # ① 没讲完的块（断点）—— 回来要重讲那一块，优先级最高
    bp = breakpoint_state(log)
    if bp and '无' not in bp[:12] and '找不到' not in bp:
        acts.append(('接着上次没讲完的那一块', bp[:70], '/学'))

    # ② 近期硬截止 → 作业 / 考试
    seen = set()
    for line in dl.split('\n'):
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 4:
            continue
        m = re.search(r'(\d{2})-(\d{2})', cells[0])
        if not m:
            continue
        try:
            d = md(int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        delta = (d - today).days
        if not (0 <= delta <= 10):
            continue
        # 已经交掉的不该出现在「现在可以做的」里（09-03 早上 A1 还挂在第 3 条）
        if SETTLED.search(cells[0]) or SETTLED.search(cells[3]):
            continue
        ev = strip_md(cells[3]); ev = re.split(r'\s;\s', ev)[0]
        # 砍掉「✅ 2026-08-xx 从 Canvas 确认」这类出处尾巴，只留事件本身
        ev = re.split(r'\s*(?:20\d{2}-\d{2}-\d{2}|\d{2}-\d{2}\s|✅|——|（原记)', ev)[0].strip(' ·,，')
        ev = ev if len(ev) <= 38 else ev[:37] + '…'
        course = ALIAS.get(strip_md(cells[2]), strip_md(cells[2]))
        w = strip_md(cells[4]) if len(cells) > 4 else ''
        w = f' · {w}' if w not in ('—', '-', '') else ''
        key = (course, ev[:16])
        if key in seen:
            continue
        seen.add(key)
        kind = '/作业' if re.search(r'due|assignment|A\d|project', ev, re.I) else '/学'
        when = re.sub(r'\s*;\s*', ' ', strip_md(cells[0])).strip()[:20]
        acts.append((f'{course} —— {ev}',
                     f'{delta} 天后（{when}）{w}', kind))

    # ③ 每门课下一份没讲的 deck
    #    🔴 2026-09-03：先从近 10 天的硬截止行里抓被点名的 deck 代号，
    #    那些优先 —— 否则会推 W1 的残段，而 5 天后的 Quiz 缺的是 W4 那份 deck。
    urgent = set()
    for line in dl.split('\n'):
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 4:
            continue
        m = re.search(r'(\d{2})-(\d{2})', cells[0])
        if not m:
            continue
        try:
            d = md(int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        if 0 <= (d - today).days <= 10:
            urgent.update(re.findall(r'w\d{2}-L\d+', line))

    decks = sorted(next_decks(ledger, urgent).items(),
                   key=lambda kv: (not kv[1][4], not kv[1][3], kv[0]))
    for c, (deck, pages, taught, partial, hot) in decks:
        st = f'讲到 {taught[:18]}，回来要重讲那一块' if partial else '还没开始'
        tag = ' 🔴 近期硬截止点名的' if hot else ''
        acts.append((f'学 {c} {deck}', f'{pages} 页 · {st}{tag}', '/学'))

    # 🔒 封顶：他说「好多命令我记不住」，摆 11 条和背命令一样累。
    #    只留最前面 5 条 + 总控 —— 剩下的他直接说名字也能起。
    acts = acts[:5]
    acts.append(('总控：扫邮箱 · 归档新材料 · 预扫 · 出看板 · 跑体检', '杂活攒着就一起清', '/总控'))
    return acts


def _safe(label, fn, default):
    """跑一节；炸了就打一行 ⚠️ 并返回 default —— 导航层任何一节出错都不许拖垮整张图。"""
    try:
        return fn()
    except Exception as e:                      # noqa: BLE001
        print(f'  ⚠️ 「{label}」这一节解析出错（{type(e).__name__}: {str(e)[:80]}）—— 已跳过，去核对应的账本格式')
        return default


# 开场导航依赖的账本。缺了就在最前面点名，该节照常往下（read() 缺文件时返回空串）。
LEDGERS = [
    ('shared/deadlines.md', '周次 · 硬截止'),
    ('shared/学习日志.md', '各门课状态 · 断点'),
    ('shared/lecture台账.md', '下一份要学的 deck'),
]


def missing_ledgers():
    return [(rel, what) for rel, what in LEDGERS
            if not os.path.isfile(os.path.join(ROOT, rel))]


def main():
    today = datetime.date.today()
    dl = read('shared/deadlines.md')
    log = read('shared/学习日志.md')
    ledger = read('shared/lecture台账.md')

    print(f'📅 {today:%Y-%m-%d %A}', end='')
    weeks = _safe('周次', lambda: week_table(dl), {})
    w, wend = which_week(weeks, today)
    if w:
        wk = f'{w}'
    elif not weeks:
        wk = '（周次算不出来：deadlines.md 里没有周次表 —— 配好 shared/配置.json 的 semester 再跑 初始化.py --apply）'
    else:
        first = min(a for a, _ in weeks.values())
        last = max(b for _, b in weeks.values())
        if today < first:
            wk = f'开学前（W1 从 {first:%m-%d} 开始，还有 {(first - today).days} 天）'
        elif today > last:
            wk = f'学期已结束（最后一周到 {last:%m-%d}）'
        else:
            wk = '非教学周'
    left = f'（这周还剩 {(wend - today).days} 天）' if wend else ''
    print(f' · 学期 {wk} {left}')
    print()

    if CFG_WARN:
        print(CFG_WARN)
    for rel, what in missing_ledgers():
        print(f'⚠️ 缺 {rel} —— 「{what}」这块先跳过（从示例复制一份，或让 /总控 建）')
    if CFG_WARN or missing_ledgers():
        print()

    up = _safe('硬截止', lambda: upcoming(dl, today), [])
    print(f'⏳ 未来 {HORIZON} 天的硬截止（只列 tute 和 due date）')
    if up:
        for line in up:
            print('  ' + line)
    else:
        print('  （没有）')
    print()

    cs = _safe('各门课状态', lambda: course_status(log), [])
    if cs:
        n_course = f'{len(COURSES)} 门课' if COURSES else '各门课'
        print(f'📚 {n_course}（只写优先级和为什么；页数一律去 shared/lecture台账.md 查）')
        for line in cs:
            print('  ' + line)
        print()

    act, fin = _safe('活动会话', lambda: _sessions(datetime.datetime.now()), ([], []))
    print('🖥️ 还开着的会话（登记表 shared/活动会话.tsv；同一时间只该有一个在写共享账本）')
    if act:
        for line in act:
            print('  ' + line)
        if len(act) > 1:
            print('  🔴 **多于一个** —— 学习/作业会话只写自己那门课的目录，'
                  'shared/ 的账本（台账·日志·deadlines）只许总控会话动')
    else:
        print('  （没有登记在案的）')
    if fin:
        print(f'  🎉 **刚收尾的（{DONE_H} h 内）—— 别再把这些当成没做完**：')
        for line in fin:
            print('    ' + line)
    ho = _safe('交接单', pending_handovers, [])
    if ho:
        print(f'  🔴🔴 **`shared/交接/` 有 {len(ho)} 份交接单没处理** —— '
              '学习会话改不了 shared/ 账本，把要改的写在这里留给总控：')
        for f in ho:
            print(f'    · {f}')
        print('    🔒 **总控必须先清掉它们再做别的**（尤其出看板之前）—— '
              '不清就是拿过期状态排计划（09-02 23:57 栽过一次）')
    print('  📌 这张表靠会话自己登记，不是实时探测。'
          '总控里要核实就跑 list_sessions 看 lastActivityAt 对一遍')
    print()

    print('⏸️ 断点：' + _safe('断点', lambda: breakpoint_state(log), '（解析出错）'))
    # 🔒 2026-09-01 体检发现：各门课那几行取的是速查表的第一句，常常是半个月前的庆功句；
    #    真正「还没做完的事」躺在学习日志的「当前状态」节里，导航层带不动。留个明确指针。
    print('   ⏭️ **没做完的事（作业停在哪 / blocker）不在导航层里** '
          '—— 去 shared/学习日志.md 的「## ⏭️ 当前状态」节读')
    print()

    print('🎯 现在可以做的（现算的，开场把这个摆给他选，别让他背命令）')
    for i, (title, why, cmd) in enumerate(
            _safe('候选动作', lambda: actions(dl, ledger, log, today),
                  [('总控：扫邮箱 · 归档新材料 · 预扫 · 出看板 · 跑体检', '杂活攒着就一起清', '/总控')]), 1):
        print(f'  {i}. {title}')
        print(f'     {why}   → {cmd}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:                      # noqa: BLE001  最后一道网：任何漏网的异常都不许变成 traceback
        print(f'⚠️ 导航.py 出了没料到的错（{type(e).__name__}: {str(e)[:100]}）—— 导航层不完整，去 shared/工具/ 看看')
        sys.exit(0)
