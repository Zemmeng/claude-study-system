#!/bin/bash
# SessionStart hook —— 2.0 导航层（2026-09-01 重写）
#
# 🔒 1.x 干的事：无脑 cat 学习日志上半 + deadlines 全文 + lecture台账全文 = **180 KB**
#    体检实测：其中 95 KB 是已结清的历史，而 hook 横幅还宣称「只注入了当前状态」。
#    根因不是文件太大，是**会话开始时不知道这个会话要干嘛，所以只能全给**。
#
# 🔒 2.0 把它倒过来：开场只给一张**导航层**（约 5 KB），
#    要什么由 /学 · /作业 · /总控 三个 slash command 按会话类型自己去取。
#    → 学一份 deck 开局 ≈ 20 KB（1.x 是 180 KB）
#
# ⚠️ 改这个文件之后必须验 JSON：
#   bash .claude/hooks/inject-context.sh | python3 -c 'import json,sys; json.load(sys.stdin)'
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT" || exit 0

# ---------- ① 导航层：周次 / 近期硬截止 / 四门一行状态 / 断点 ----------
NAV="$(python3 shared/工具/导航.py 2>/dev/null || echo '⚠️ 导航.py 跑不起来，去 shared/工具/ 看看')"

# ---------- ② 邮箱简报到期？（2026-08-13 加）----------
STAMP="shared/工具/邮箱简报戳"
TODAY="$(date +%F)"
LAST="$(cat "$STAMP" 2>/dev/null || echo '从未')"
if [[ "$LAST" == "$TODAY" ]]; then
  MAIL_FLAG="📬 邮箱今天已扫（$TODAY）"
else
  MAIL_FLAG="📬 邮箱简报到期（上次：${LAST}）—— 这是 /总控 的活，别在学习会话里做"
fi

# ---------- ③ 到点提醒（数据在 shared/提醒.tsv）----------
REMIND="$(python3 shared/工具/提醒.py 2>/dev/null || true)"
if [[ -n "$REMIND" ]]; then
  REMIND_BLOCK="⏰ 今天到期的提醒（转给他，别吞掉）：
${REMIND}"
else
  REMIND_BLOCK="⏰ 今天没有到期提醒"
fi

# ---------- ④ 一致性体检 ----------
CHECK_OUT="$(python3 shared/工具/检查.py 2>&1 || true)"
CHECK_SUMMARY="$(printf '%s' "$CHECK_OUT" | sed -n '/^======== 汇总/,$p')"
# 🔴 2026-09-03：脚本崩了（抛异常）时输出里既没有 ❌ 也没有 ⚠️，
#    原来的三分支会直接落进 else 打「✅ 一致性检查全绿」—— **门禁失效还伪装成全绿**。
#    「有没有『汇总』那一节」才是「脚本跑完了没有」的判据。
if [ -z "$CHECK_SUMMARY" ]; then
  CHECK_BLOCK="🔴🔴 **一致性检查脚本自己崩了**（输出里没有「汇总」节）—— 先修脚本，别信下面任何状态：
$(printf '%s' "$CHECK_OUT" | tail -6)"
elif printf '%s' "$CHECK_OUT" | grep -q '❌'; then
  CHECK_BLOCK="🔴 一致性检查有硬伤，先修再干活：
${CHECK_SUMMARY}"
elif printf '%s' "$CHECK_OUT" | grep -q '⚠️'; then
  CHECK_BLOCK="✅ 一致性检查通过（$(date '+%H:%M')）
$(printf '%s' "$CHECK_OUT" | grep '⚠️' | head -5)"
else
  CHECK_BLOCK="✅ 一致性检查全绿（$(date '+%H:%M')）"
fi

# ---------- ⑤ 会话类型指引（静态文本，用不做替换的 heredoc，免得反引号被当命令跑）----------
GUIDE=$(cat <<'GUIDE_EOF'
🔒🔒 **先确定这个会话要干什么**（2.0 核心：一个对话只做一件事）

🔴 **他 2026-09-01 18:41 定的：「好多命令 我记不住 能不能对话开始 你让我选呢」**

→ **他第一句没点名要干什么时，立刻用 `AskUserQuestion` 把上面「🎯 现在可以做的」摆成选项让他点。**
　 取前 3 条最紧的 + 一条「总控（杂活）」，他还能选 Other 自己说。
　 🔒 **不许反过来让他背 `/学` `/作业` 的语法** —— 他点完，我自己去起对应的流程。
→ 他自己点名了（「学 algorithms w05-L1」「做 A1」「扫邮箱」「我下了录音」）就**直接开工，别多问一轮**。

🔒🔒 **会话类型表 · 写文件分区 · 三条硬规矩 · 该读哪些文件 —— 一律以 `CLAUDE.md` 为准。**
它是 project instructions，**本来就全文自动加载**，这里不再复制任何一条。

> 🔴 **2026-09-03 审计删掉了原来那段**：它是同一批规则的**第 9 份副本，而且覆盖率 100%**
> （每个会话开场都注入）。它当时还写着 1.x 的「**同一时间只开一个会话**」——
> 而主 CLAUDE.md 09-02 已经把那条改成「两类会话可以并行 + 写文件分区」。
> **在每个会话 100% 命中的位置塞一份过期副本，比不塞更糟。**
GUIDE_EOF
)

# ---------- 拼 ----------
BODY=$(cat <<EOF
📚 SessionStart 导航层（2.0）—— **这不是全部状态，是一张地图。**
1.x 在这里塞 180 KB 全文；2.0 只给导航，**要什么按会话类型自己去取**。

${NAV}

${REMIND_BLOCK}
${MAIL_FLAG}
${CHECK_BLOCK}

────────────────────────────────────────────────────────
${GUIDE}
EOF
)

# JSON 用 python3 拼（python3 本来就是必需依赖）—— 原来用 jq，新机器没装 jq 时整道门静默失效，什么都注入不进去
BODY="$BODY" python3 -c 'import json,os; print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":os.environ["BODY"]}}, ensure_ascii=False))'
