#!/bin/bash
# Stop hook —— 收尾门禁（2026-08-14 加）
#
# 每个回合结束时跑 shared/工具/检查.py：
#   · 全绿 → 静默放行（exit 0）
#   · 有 ❌ → exit 2，把失败项打回给模型，本回合不许结束，先修
#
# 为什么要它（和另外两个 hook 的分工）：
#   SessionStart      开场体检 —— 抓「上个会话留下的」矛盾
#   UserPromptSubmit  下一条消息体检 —— 抓中途改出的矛盾，但要等用户再说话才响
#   Stop              收尾体检 —— 回合内改出的矛盾当场拦住，不让带着矛盾把回合交出去
#   → 这是「仄梦大王 = 检查过了」这句承诺的机械化：没过就说不出口
#
# 防死循环：stop_hook_active=true 表示上一次 Stop 已经因此拦过一回，
# 这次放行 —— 修不掉的问题（如缺文件要用户补）交给模型在回复里明说，不无限拦。
# 递归防护（2026-08-15）：行动项审计会在 /tmp 里再起一个 claude -p 当审计员，
# 万一它意外带上了本项目 hooks，见到这个环境变量直接放行，不会套娃。
[ -n "$CLAUDE_AUDIT" ] && exit 0

INPUT=$(cat)
ACTIVE=$(printf '%s' "$INPUT" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("stop_hook_active",False))' 2>/dev/null)
[ "$ACTIVE" = "True" ] && exit 0

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
OUT=$(cd "$ROOT" && python3 shared/工具/检查.py 2>&1)
RC=$?
if [ $RC -ne 0 ]; then
  # 🔴 2026-09-03 加：多个会话共用同一个工作树，别的会话改到一半的账本会让**这个**会话被拦，
  #    而按写文件分区，学习/作业会话根本无权去修那些账本 —— 拦了也只能拦死。
  #    判据：❌ 全都出在 shared/ 的账本上（[G]/[8]/[9]/[1·语义]/[4]），
  #    而本回合（HEAD..工作树）**没碰过** shared/ 里的账本 → 那就不是这个会话的锅，放行。
  BAD=$(printf '%s\n' "$OUT" | grep '❌')
  # 🔒 只看**汇总节**判类别 —— 逐条行不带 [N] 标签（`❌ algorithms w05-L2：台账写 54 页…`），
  #    拿全文计数永远 ≥1，放行分支就成了死代码（09-03 23:0x 实测抓到）。
  SUM_BAD=$(printf '%s\n' "$OUT" | sed -n '/======== 汇总/,$p' | grep '❌')
  MINE=$(cd "$ROOT" && git diff --name-only HEAD -- \
           shared/lecture台账.md shared/学习日志.md shared/deadlines.md \
           shared/学时记录.md shared/公告.md 2>/dev/null)
  NON_LEDGER=$(printf '%s\n' "$SUM_BAD" | grep -cv '\[G\]\|\[3\]\|\[8\]\|\[9\]\|\[1·语义\]\|\[4\]')
  if [ -z "$MINE" ] && [ "$NON_LEDGER" = "0" ] && [ -n "$SUM_BAD" ]; then
    {
      echo "⚠️ 收尾门禁：检查.py 有 ❌，但**全部出在 shared/ 的账本上，而本回合没碰过那些文件** —— 判定是别的会话的半成品，放行。"
      echo "   🔒 别去改账本（写文件分区）。在回复里写一句「门禁红的是总控账本，本会话没动过」即可。"
      printf '%s\n' "$BAD"
    } >&2
    exit 0
  fi
  {
    echo "🔒 收尾门禁（stop-check.sh）：检查.py 有 ❌，先修复再结束回合。修不了的（缺文件/要问用户）在回复里明说，下一次放行。"
    printf '%s\n' "$BAD"
  } >&2
  exit 2
fi

# 检查.py 全绿之后：行动项审计（2026-08-15 03:10 加，治「把已完成的事当新待办」）。
# 只在简报/看板回合触发（脚本自己判断），退出码 3 = 审计不过。fail-open：其他情况一律放行。
AUD=$(printf '%s' "$INPUT" | python3 "$ROOT/shared/工具/行动项审计.py" 2>/dev/null)
if [ $? -eq 3 ]; then
  {
    echo "🔒 行动项审计不过：下面这些事档案里已标完成，不许再当待办报给他 —— 修掉再收尾。"
    printf '%s\n' "$AUD"
  } >&2
  exit 2
fi

# 全绿 → 自动提交本回合快照（2026-08-15 git 化）。
# 这一步是凭证锁的地基：HEAD 永远指向「上一个通过检查的回合末尾」，
# 检查.py 的 [G] 锁拿 HEAD vs 工作区 精确判定「本回合改了什么」。
# 没改动就不提交；git 出任何岔子都静默放行（提交失败不该拦学习）。
#
# 🔴🔴 2026-08-17 修的一个 bug，代价很大，别改回去：
#   这里原来写的是 [ -d "$ROOT/.git" ]。**在 git worktree 里 `.git` 是个「文件」不是目录**
#   （内容是 `gitdir: /path/to/main/.git/worktrees/<name>`），所以那个条件恒假 →
#   **自动提交整段在所有 worktree 里被静默跳过**，而会话基本都跑在 worktree 里。
#   后果：HEAD 一直停在旧快照，别的 worktree/会话读到的是过期的 .md
#   → 08-17 真出事了：新对话读到「从 p.1 开始」，把已经学完的 p.1–19 重讲了一遍。
#   用 -e（存在即可，文件或目录都算）。
if command -v git >/dev/null && [ -e "$ROOT/.git" ]; then
  (cd "$ROOT" && git add -A >/dev/null 2>&1
   git diff --cached --quiet 2>/dev/null || git commit -q -m "turn: $(date '+%F %H:%M')" >/dev/null 2>&1)
fi
exit 0
