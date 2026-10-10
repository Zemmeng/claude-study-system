#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""到点提醒 —— 主 CLAUDE.md「到点必须主动提醒我」那张表的机械化（2026-08-14）。

数据在 shared/提醒.tsv，一行一条：类型<TAB>规则<TAB>提醒文字
    RANGE   2026-08-15..2026-08-23                     日期区间内每天响
    WEEKLY  MON;from=2026-08-17;until=2026-10-25       每周某天响（from/until 可选）
    EVERY   14;from=2026-08-16;days=2                  从 from 起每 14 天响 days 天

到期的条目打到 stdout（SessionStart hook 注入新会话开头）；没到期不输出任何东西。
改提醒改 tsv，不用动这份代码。过期的条目定期删掉。

用法：python3 提醒.py [YYYY-MM-DD]   （日期参数只给测试用）
"""
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WD = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}


def d(s):
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def main():
    today = d(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    tsv = ROOT / "shared/提醒.tsv"
    if not tsv.exists():
        return
    for raw in tsv.read_text(encoding="utf-8").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            print(f"⚠️ 提醒.tsv 有一行格式不对（要两个 TAB 三列）：{line[:50]}")
            continue
        typ, spec, msg = parts[0].strip().upper(), parts[1], "\t".join(parts[2:])
        fire = False
        try:
            if typ == "RANGE":
                a, b = spec.split("..")
                fire = d(a) <= today <= d(b)
            elif typ == "WEEKLY":
                head, *rest = spec.split(";")
                opt = dict(p.split("=", 1) for p in rest)
                fire = today.weekday() == WD[head.strip().upper()]
                if fire and "from" in opt:
                    fire = today >= d(opt["from"])
                if fire and "until" in opt:
                    fire = today <= d(opt["until"])
            elif typ == "EVERY":
                head, *rest = spec.split(";")
                opt = dict(p.split("=", 1) for p in rest)
                delta = (today - d(opt["from"])).days
                fire = delta >= 0 and delta % int(head) < int(opt.get("days", 1))
            else:
                print(f"⚠️ 提醒.tsv 未知类型 {typ}：{line[:50]}")
                continue
        except Exception:
            print(f"⚠️ 提醒.tsv 解析失败：{line[:50]}")
            continue
        if fire:
            print(f"⏰ {msg}")


if __name__ == "__main__":
    main()
