#!/bin/bash
# UserPromptSubmit hook —— 每条消息都注入，所以「沉底失效」在这里不会发生
#
# 注入三样：
#   ① 当前时间 —— 治 CLAUDE.md「时间必须说准」（犯过 3 次）
#   ② 开讲前必读清单 —— 治「读了索引没读内容」（08-10 漏了 Ed 那次）
#      🔒 2026-09-01 改：② 原本指某门课的「学习顺序.md」，但那份 08-10 之后一次没改过，
#         已经落后真实进度两份 deck —— 每条消息 1/3 的注入预算在指一份死索引。
#         改指对照表顶部的「📗 其他资料对应段落」表（每讲都更新，且有硬门禁）。
#   ②b 批改前必贴题干 —— 治「批改时只写 Q30(d) 就开讲」（08-16 23:15 定的规矩，
#      08-29 一整晚反复违反，他说「好多次了」。CLAUDE.md 里已经写着，
#      再加一句没用 —— 按第 16 条的教训，只有每条消息都响的 hook 才不失效）
#   ③ 一致性体检（检查.py）—— 只在有 ❌ 时出声，全绿不占上下文（08-13 加）
#      → 对话中途文件被改出矛盾，下一条消息就会被抓，不用等到换对话
#
# 为什么放在 UserPromptSubmit 而不是 SessionStart：
#   SessionStart 只响一次。08-10 那次 18:10 读到「第 9 步要带 Ed」，
#   20:45 才开讲，中间隔了 2.5 小时十几轮，那条信息早沉底了。
#   每条消息都响的 hook 是唯一被验证过「隔多久都不失效」的机制。
#
# ⚠️ 改这个文件之后必须验 JSON：
#   bash .claude/hooks/inject-time.sh | python3 -c 'import json,sys; json.load(sys.stdin)'
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"

NOW="$(date '+%Y-%m-%d %H:%M %A')"

READ_BEFORE='📌 讲课时每一块都查一遍，不许靠记：① <课程>/字幕/<deck>-对照.md —— 没有就先建再讲，没校对的页不许开讲 ② 打开该 deck 对照表顶部的「📗 其他资料对应段落」表，按行号把点名的文件（tute 讲义 · 教材章节 · 练习平台存档）全文读掉 —— 读索引不算，索引只告诉你要读什么'

# 🔒 批改前必贴题干（08-16 23:15 他定，08-29 他说「好多次了」）
GRADE_RULE='🔒 批改任何一题之前，先把那道题的题干原样贴一遍（含全部选项和小问），再写判对判错和讲解 —— ❌ 不许只写「Q30(d) 正确答案是 True」就开讲，他得往回翻才知道 (d) 问的是什么。催他答题时同理：把没答的题【全文】重贴，不许写「还欠 Q2、Q3」。'

# 🔒 题目一律全英文（主 CLAUDE.md 出题节早就写着；09-03 23:58 algorithms w05-L1 那个会话还是用中文出了 Q1，
#    他问「咋还变中文回答问题了？」—— 同第 16 条教训：写在 CLAUDE.md 里会沉底，只有每条都响的 hook 不失效）
QUIZ_EN='🔒 出题一律全英文，写成考卷的样子 —— 题干、选项、指令都是英文，不夹中文；选项带 (a)(b)(c)(d)。他用英文答，批改时才可以用中文解释。'

# 体检：只报 ❌（硬伤）。⚠️ 留给 SessionStart 的完整汇总，这里不重复。
FAILS="$(cd "$ROOT" && python3 shared/工具/检查.py 2>/dev/null | grep '❌' || true)"

BODY="⏰ 当前时间：${NOW}（这是系统自动注入的，说时间直接用它，不要再猜）
${READ_BEFORE}
${GRADE_RULE}
${QUIZ_EN}"
if [[ -n "$FAILS" ]]; then
  BODY="${BODY}
🔴 一致性体检刚抓到硬伤（shared/工具/检查.py），先修再继续：
${FAILS}"
fi

# JSON 用 python3 拼（python3 本来就是必需依赖）—— 原来用 jq，新机器没装 jq 时整道门静默失效，什么都注入不进去
BODY="$BODY" python3 -c 'import json,os; print(json.dumps({"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":os.environ["BODY"]}}, ensure_ascii=False))'
