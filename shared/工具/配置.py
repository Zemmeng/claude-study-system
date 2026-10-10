"""读 shared/配置.json —— 所有脚本的课程列表都从这里来，不再写死。

用法（其他脚本里）：
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import 配置
    配置.COURSES        # ('algorithms', 'networks')
    配置.ALIAS          # {'algorithms': 'algorithms', '算法': 'algorithms', ...}
    配置.course('networks')['tute']

单独跑：python3 shared/工具/配置.py  → 打印解析结果，配置写错时报出哪一行。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(ROOT, "shared", "配置.json")


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"❌ 找不到 {PATH} —— 先照 README 写一份配置")
    except json.JSONDecodeError as e:
        sys.exit(f"❌ shared/配置.json 第 {e.lineno} 行 JSON 写错了：{e.msg}")


CFG = load()
YEAR = int(CFG.get("semester", {}).get("year", 0)) or None
COURSE_LIST = CFG.get("courses", [])
COURSES = tuple(c["key"] for c in COURSE_LIST)

ALIAS = {}
for _c in COURSE_LIST:
    ALIAS[_c["key"]] = _c["key"]
    if _c.get("name"):
        ALIAS[_c["name"]] = _c["key"]
    for _a in _c.get("aliases", []):
        ALIAS[_a] = _c["key"]

EXTRA_CORE = tuple(CFG.get("extra_core_files", []))


def course(key):
    for c in COURSE_LIST:
        if c["key"] == key:
            return c
    raise KeyError(f"配置里没有课程 {key!r}（有的是 {', '.join(COURSES)}）")


if __name__ == "__main__":
    print(f"学期：{CFG.get('semester', {}).get('name')}（{YEAR}）")
    for c in COURSE_LIST:
        print(f"  · {c['key']:<14} {c.get('code', ''):<10} {c.get('name', '')}  "
              f"lecture {c.get('lecture', '—')} · tute {c.get('tute', '—')}")
    print(f"别名 → key：{ALIAS}")
