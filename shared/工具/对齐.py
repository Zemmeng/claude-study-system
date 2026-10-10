#!/usr/bin/env python3
"""
对齐 —— 总控用：一条命令看清「别的对话现在在干什么、进行到哪」。

🔴 为什么有这个脚本（2026-09-03 21:5x，他提的）：
    他的原话：「**在总控里好像不是很了解我其他对话的进度 …… 你又不知道在总控里
    两次发言之间发生的事 或者说是没去查**」。

    原来只有两条路：
      · `shared/活动会话.tsv` —— 靠会话自己登记，**不是实况**，而且忘登记就没有
      · MCP 的 list_sessions + list_events —— 准，但每次几千 token，只在开场跑一次

    这个脚本直接读 CCD 的 transcript 文件（`~/.claude/projects/<slug>/<sessionId>.jsonl`），
    **零 token、毫秒级**，可以每次他发言前都跑一遍。

    <slug> = 项目根绝对路径里的 `/`（以及其他非字母数字字符）换成 `-`，例如
    `/home/me/study` → `-home-me-study`。项目根取 `$CLAUDE_PROJECT_DIR`，没有就用脚本位置推。
    找不到 transcript 目录时只打一行友好提示（没开过会话、或 Claude Code 的配置目录不在默认位置）。

用法：
    python3 shared/工具/对齐.py            # 今天动过的会话
    python3 shared/工具/对齐.py --hours 3  # 最近 3 小时
    python3 shared/工具/对齐.py --full     # 每个会话多给几轮上下文
"""
import datetime
import json
import os
import re
import subprocess
import sys

ROOT_REPO = os.path.abspath(os.environ.get("CLAUDE_PROJECT_DIR")
                            or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BASENAME = os.path.basename(ROOT_REPO.rstrip(os.sep)) or ROOT_REPO
# Claude Code 的配置目录：默认 ~/.claude，可被 CLAUDE_CONFIG_DIR 改
PROJECTS = os.path.join(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"), "projects")


def slug_candidates(root):
    """项目根路径 → Claude Code 的项目目录名。

    规则：路径里的 `/` 换成 `-`。实际上 Claude Code 对所有非字母数字字符都这么换（`.` `_` 空格 …），
    所以两种写法都试，哪个目录真存在用哪个；都不存在就用第二种（更接近实际行为）。
    """
    outs = []
    for r in (root, os.path.realpath(root)):
        for sl in (r.replace(os.sep, "-"), re.sub(r"[^A-Za-z0-9]", "-", r)):
            if sl not in outs:
                outs.append(sl)
    return outs


def find_proj():
    cands = slug_candidates(ROOT_REPO)
    for sl in cands:
        d = os.path.join(PROJECTS, sl)
        if os.path.isdir(d):
            return d
    return os.path.join(PROJECTS, cands[1] if len(cands) > 1 else cands[0])


PROJ = find_proj()
# 当前会话自己的 id —— CCD 放在环境变量里（2026-09-03 实测确认）
SELF = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
if "--self" in sys.argv:                      # 兜底：手动指定
    try:
        SELF = sys.argv[sys.argv.index("--self") + 1]
    except Exception:
        pass


def text_of(msg):
    """把一条消息压成一行纯文本；工具调用/结果一律折叠掉。"""
    c = (msg or {}).get("content")
    if isinstance(c, str):
        return c.strip()
    out = []
    if isinstance(c, list):
        for b in c:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                out.append(str(b.get("text", "")).strip())
            elif t == "tool_use":
                out.append(f"⟨调用 {b.get('name', '?')}⟩")
            elif t == "tool_result":
                out.append("⟨工具结果⟩")
    return one_line(" ".join(x for x in out if x))


def one_line(t):
    """压成单行 —— transcript 里的回复常常带表格和代码块，原样打会刷屏。"""
    t = re.sub(r"```[\s\S]*?```", "⟨代码块⟩", t or "")
    t = re.sub(r"\n\s*\|", " ｜", t)          # markdown 表格行
    t = re.sub(r"\s*\n+\s*", " ⏎ ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def tail_msgs(path, n=1500):
    """只读文件末尾 n 行 —— transcript 动辄 10–30 MB，不许全读。

    n=1500 而不是 400：尾部有大量 tool_use / tool_result / attachment 行，
    实测某个作业会话最后一条真用户消息离文件末尾 383 行（400 的 96%），
    工具跑得再密一点就会掉出窗口，输出成「他什么都没说」。
    """
    try:
        raw = subprocess.run(["tail", "-n", str(n), path], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return []
    out = []
    for ln in raw.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if d.get("isSidechain"):          # 子 agent 的对话，不是他和主线
            continue
        if d.get("type") not in ("user", "assistant"):
            continue
        out.append(d)
    return out


def last_real_ts(msgs):
    """最后一条 user/assistant 消息的时间戳（本地时区）。

    🔴 2026-09-03 22:1x 修的核心 bug：**不能拿文件 mtime 当活动时间**。
    CCD 会往 .jsonl 追加 last-prompt / custom-title / atis-latch / mode 这类
    **不带 timestamp 的元数据行**，它们不代表任何对话发生，却会刷新 mtime。
    实测：某个会话的 mtime 是 09-03 21:46，而最后一条真消息是 **09-02 18:26**（27 h 前）；
    另一个早已废弃的会话同样被报成「13 min 前还在动」。
    → 照那个结果办，总控会把死会话当活的，还反过来「修」登记表。
    """
    for d in reversed(msgs):
        ts = d.get("timestamp")
        if not ts:
            continue
        try:
            return datetime.datetime.fromisoformat(
                ts.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        except Exception:
            continue
    return None


def is_real_user(d):
    """真的是他说的话 —— 排除 tool_result 和 hook 注入的系统提示。"""
    if d.get("type") != "user":
        return False
    t = text_of(d.get("message"))
    if not t or t.startswith("⟨"):
        return False
    if t.startswith(("Caveat:", "<command-", "<system-reminder", "⏰ 当前时间")):
        return False
    return True


def main():
    hours = 24
    if "--hours" in sys.argv:
        try:
            hours = float(sys.argv[sys.argv.index("--hours") + 1])
        except Exception:
            pass
    full = "--full" in sys.argv
    now = datetime.datetime.now()
    cutoff = now.timestamp() - hours * 3600

    # 🔒 2026-09-26 加：他会在别的项目目录里开本项目的会话（有一次误开在另一个无关项目目录下，
    #    整整 9 h 没被这个脚本扫到）。
    #    所以除了本项目自己的目录，再扫 ~/.claude/projects/ 下**目录名里带项目根目录名**的那些。
    dirs = [PROJ]
    parent = os.path.dirname(PROJ)
    try:
        for d in os.listdir(parent):
            full_d = os.path.join(parent, d)
            if full_d == PROJ or not os.path.isdir(full_d):
                continue
            if BASENAME.lower() in d.lower():
                dirs.append(full_d)
    except OSError:
        pass

    files = []
    for d in dirs:
        try:
            files += [(os.path.getmtime(os.path.join(d, f)), os.path.join(d, f), f[:-6])
                      for f in os.listdir(d) if f.endswith(".jsonl")]
        except OSError:
            continue

    # 🔒 目录名里没有项目名的也可能是本项目的会话（他在别的 repo 目录下开的）——
    #    对**最近动过**的 transcript 读开头一段，看里面有没有项目根的绝对路径。只读开头，开销可忽略。
    seen = {f[1] for f in files}
    try:
        for d in os.listdir(parent):
            full_d = os.path.join(parent, d)
            if full_d in dirs or not os.path.isdir(full_d):
                continue
            for f in os.listdir(full_d):
                if not f.endswith(".jsonl"):
                    continue
                path = os.path.join(full_d, f)
                if path in seen:
                    continue
                try:
                    mt = os.path.getmtime(path)
                    if mt < cutoff - hours * 3600:
                        continue
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        head = fh.read(20000)      # 首行的 cwd 之外，也能抓到他早期消息里贴的项目路径
                except OSError:
                    continue
                if ROOT_REPO in head:
                    files.append((mt, path, f[:-6]))
    except OSError:
        pass
    if not files:
        print("⚠️ 找不到本项目的 Claude Code 对话记录（transcript），没法对齐别的对话在干什么。")
        print(f"   找的目录：{PROJ}")
        print(f"   项目根：{ROOT_REPO}（目录名规则：路径里的 / 换成 -）")
        print("   常见原因：这个项目还没开过会话 · Claude Code 的配置目录不在默认的 ~/.claude"
              "（可用环境变量 CLAUDE_CONFIG_DIR 指过去）· 项目被移动过路径。")
        print("   不影响别的功能；以 shared/活动会话.tsv 的登记为准。")
        return 0

    # mtime 只当**预筛**（放宽一倍，因为元数据行会把它推后，不会推前）
    cand = sorted([x for x in files if x[0] >= cutoff - hours * 3600], key=lambda x: -x[0])
    print(f"🖥️ 最近 {hours:g} h 动过的对话（读 transcript 实测，不是登记表）"
          f"　—— 现在 {now:%m-%d %H:%M}")

    rows = []
    for mt, path, sid in cand:
        if sid == SELF:
            continue
        msgs = tail_msgs(path)
        if not msgs:
            continue
        when = last_real_ts(msgs)          # 🔑 真实活动时间，不是 mtime
        if when is None or when.timestamp() < cutoff:
            continue
        rows.append((when, path, sid, msgs))
    rows.sort(key=lambda x: -x[0].timestamp())

    if not rows:
        print("  （没有）")
        return 0
    shown = 0
    for when, path, sid, msgs in rows:
        users = [m for m in msgs if is_real_user(m)]
        asts = [m for m in msgs if m.get("type") == "assistant" and text_of(m.get("message"))]
        ago = (now - when).total_seconds() / 60
        ago_s = f"{ago:.0f} min 前" if ago < 90 else f"{ago/60:.1f} h 前"
        stale = "　💤 已停" if ago > 180 else ""
        print(f"\n· [{when:%m-%d %H:%M}　{ago_s}{stale}]　{sid[:8]}")
        take = 3 if full else 1
        for u in users[-take:]:
            print(f"    他说：{text_of(u.get('message'))[:150]}")
        if asts:
            print(f"    我说：{text_of(asts[-1].get('message'))[:220]}")
        shown += 1

    if not shown:
        print("  （除了本会话没有别的）")
    # 🔴 2026-09-03 22:3x 加：长跑的总控在两次发言之间收不到新交接单
    #    （[12] 是 ⚠️ 级、inject-time.sh 只注入 ❌、导航层只在 SessionStart 跑一次）。
    #    这个脚本本来就每条消息都跑，顺手把它列出来，零成本。
    ho = os.path.join(ROOT_REPO, "shared/交接")
    try:
        pend = sorted(f for f in os.listdir(ho)
                      if f.endswith(".md") and f != "README.md")
    except OSError:
        pend = []
    if pend:
        print(f"\n🔴🔴 `shared/交接/` 有 {len(pend)} 份交接单**没处理** —— 落账前别排计划：")
        for f in pend:
            print(f"    · {f}")

    print("\n📌 时间取自**最后一条真消息的 timestamp**，不是文件 mtime")
    print("　 （CCD 会追加无时间戳的元数据行刷新 mtime，那会把死会话报成刚动过 —— 09-03 栽过）。")
    print("　 标 💤 的是 3 h 以上没动的。这比 `shared/活动会话.tsv` 准，不一致时信这个。")
    print("　 登记表管的是「谁登记过、收尾时写了什么」，两者对照着看。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
