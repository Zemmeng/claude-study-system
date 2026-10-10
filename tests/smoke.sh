#!/bin/bash
# 冒烟测试：模拟一个新用户从 clone 到部署的全过程（CI 每次推送都跑）。
#   用法：bash tests/smoke.sh [源仓库，默认当前仓库] [测试目录，默认临时目录]
#   测：示例原样全绿 → 改配置 + 放课件 + 初始化 → 幂等 / 增量 → 无 git 身份时的快照 → 凭证锁
set -u
SRC="${1:-$(cd "$(dirname "$0")/.." && pwd)}"; T="${2:-$(mktemp -d)/新用户 的仓库}"; rm -rf "$T"; mkdir -p "$(dirname "$T")"; git clone -q "$SRC" "$T" && cd "$T" || exit 1
export CLAUDE_PROJECT_DIR="$T"
ok(){ echo "  ✅ $*"; }; bad(){ echo "  ❌ $*"; FAILN=$((FAILN+1)); }; FAILN=0
echo "【0】clone 下来原样跑"
python3 shared/工具/检查.py | grep -q "✅ 通过 14 项" && ok "示例 14 项全绿" || bad "示例没全绿"
python3 shared/工具/导航.py >/dev/null 2>&1 && ok "导航" || bad "导航崩"
bash .claude/hooks/inject-context.sh | python3 -c 'import json,sys; json.load(sys.stdin)' && ok "开场门 JSON" || bad "开场门"
echo '{}' | bash .claude/hooks/inject-time.sh | python3 -c 'import json,sys; json.load(sys.stdin)' && ok "消息门 JSON" || bad "消息门"
echo "【1】改配置 + 放一份课件 + 初始化"
python3 - <<'PY'
import json
c=json.load(open('shared/配置.json',encoding='utf-8'))
import datetime as dt
s=dt.date.today()+dt.timedelta(days=60); s-=dt.timedelta(days=s.weekday())          # 两个月后的周一 = 开学
b=s+dt.timedelta(weeks=6)
c['semester']={"name":"测试学期","year":s.year,"start":str(s),"teaching_weeks":12,"breaks":[{"from":str(b),"to":str(b+dt.timedelta(days=6))}]}
c['courses']=[{"key":"cs101","code":"CS101","name":"计算机导论","aliases":["导论"],"lecture":"周一 10:00","tute":"周三 14:00"},
              {"key":"数据结构","code":"DS200","name":"数据结构","aliases":["DS"],"lecture":"周二 13:00","tute":"周四 16:00"}]
json.dump(c,open('shared/配置.json','w',encoding='utf-8'),ensure_ascii=False,indent=2)
PY
mkdir -p cs101/课件 && cp algorithms/课件/w02-L1.pdf cs101/课件/w01-L1.pdf
python3 shared/工具/初始化.py --apply > "$T/.init1.log" 2>&1
grep -q "✅ 通过 14 项" "$T/.init1.log" && ok "初始化后 14 项全绿" || { bad "初始化后没全绿"; sed -n '/汇总/,/下一步/p' "$T/.init1.log"; }
[ ! -d algorithms ] && [ ! -d networks ] && ok "示例课已删" || bad "示例课还在"
grep -q '`w01-L1` | 9 |' shared/lecture台账.md && ok "课件自动登记（9 页）" || bad "课件没登记"
[ -f cs101/课件/_预扫/w01-L1.md ] && ok "自动预扫" || bad "没预扫"
python3 shared/工具/导航.py | grep -q "开学前" && ok "导航：开学前" || bad "导航没认出开学前"
echo "【2】幂等 + 增量"
python3 shared/工具/初始化.py --apply | grep -q "已经是最新的" && ok "重跑无副作用" || bad "重跑有动作"
sed -i.bak 's/^===== 历史流水 =====$/===== 历史流水 =====\n\n### MY-OWN-ENTRY/' shared/学习日志.md 2>/dev/null; python3 - <<'PY'
p='shared/学习日志.md'; s=open(p,encoding='utf-8').read()
if 'MY-OWN-ENTRY' not in s: s=s.rstrip()+'\n\n### MY-OWN-ENTRY\n'
open(p,'w',encoding='utf-8').write(s)
import json
c=json.load(open('shared/配置.json',encoding='utf-8'))
c['courses'].append({"key":"stats","code":"ST300","name":"统计","aliases":[],"lecture":"周五 09:00","tute":"周五 14:00"})
json.dump(c,open('shared/配置.json','w',encoding='utf-8'),ensure_ascii=False,indent=2)
PY
rm -f shared/学习日志.md.bak; cp cs101/课件/w01-L1.pdf cs101/课件/w01-L2.pdf
python3 shared/工具/初始化.py --apply > "$T/.init2.log" 2>&1
grep -q "✅ 通过 14 项" "$T/.init2.log" && ok "加课 + 加课件后 14 项全绿" || { bad "增量后没全绿"; sed -n '/汇总/,/下一步/p' "$T/.init2.log"; }
grep -q "MY-OWN-ENTRY" shared/学习日志.md && ok "自己写的内容没被覆盖" || bad "自己的内容被覆盖了"
grep -q '`w01-L2`' shared/lecture台账.md && ok "新课件补登" || bad "新课件没补登"
echo "【3】收尾门：没配 git 身份也能快照"
EH="$(mktemp -d)"; echo '{}' | HOME="$EH" GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 bash .claude/hooks/stop-check.sh >/dev/null 2>&1; rc=$?
[ $rc -eq 0 ] && [ -z "$(git status --porcelain)" ] && ok "放行并已提交快照" || bad "收尾门 rc=$rc / 工作区没提交干净"
echo "【4】凭证锁：推进台账不记学时 → 必须被拦"
python3 - <<'PY'
p='shared/lecture台账.md'; s=open(p,encoding='utf-8').read(); s=s.replace('| `w01-L1` | 9 | ❌未开始 |','| `w01-L1` | 9 | ✅全 |',1); open(p,'w',encoding='utf-8').write(s)
PY
echo '{}' | bash .claude/hooks/stop-check.sh >/dev/null 2>&1; [ $? -eq 2 ] && ok "被 [G1] 拦回" || bad "没拦住"
rm -f "$T"/.init*.log
echo "== 失败 $FAILN 项"
[ "$FAILN" -eq 0 ]
