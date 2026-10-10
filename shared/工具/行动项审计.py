#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行动项审计 —— 抓「把已完成的事当新待办报给他」（2026-08-15 03:10 建，当天凌晨刚犯这错）。

由 stop-check.sh 在 检查.py 全绿之后调用。stdin = Stop hook 的 JSON。
退出码：0 = 通过或跳过 · 3 = 审计不过（原因打到 stdout）。内部任何异常 → 0（fail-open，不拦学习）。

触发条件（低频高危，不是每回合都跑）：
  本回合最后一条助手消息含 📬 / 简报，或 shared/dashboard.tex 有未提交改动。
  —— 这两个正是「行动项」的两个出口：邮箱简报和看板。

两个脑，顺序跑：
  脑 A（确定性，永远在）：候选行动句 × `已完成事实.tsv` 的正则。
      行动句判据：含「必须/发给/排进/第一格/去做/尽快/记得/别忘」之一；
      含「已/已经/早就/✅」的行视为陈述完成，不算行动句（防止把汇报当待办误咬）。
  脑 B（LLM 审计，claude CLI 有登录态才生效）：把档案 + 待审文本交给 claude CLI，
      第一行 PASS/FAIL。没登录 / 超时 / 输出不可解析 → 静默跳过。
      模型默认用 CLI 自己的默认；想指定就设环境变量 CLAUDE_AUDIT_MODEL（例：CLAUDE_AUDIT_MODEL=opus）。
      递归防护：cwd=系统临时目录（那里没有项目 hooks）+ CLAUDE_AUDIT=1（stop-check.sh 开头见到直接放行）。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ACTION_WORDS = re.compile(r"必须|发给|排进|第一格|去做|尽快|记得|别忘|得跟|要核|要发")
RECAP_WORDS = re.compile(r"已(?!读)|已经|早就|✅|完成了|做完了|交掉了|发过了")


def last_assistant_text(transcript_path: str) -> str:
    text = ""
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") != "assistant":
                continue
            content = (obj.get("message") or {}).get("content")
            parts = []
            if isinstance(content, str):
                parts = [content]
            elif isinstance(content, list):
                parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            t = "\n".join(p for p in parts if p)
            if t.strip():
                text = t
    return text


def dashboard_dirty() -> bool:
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "shared/dashboard.tex"],
                           capture_output=True, text=True, timeout=10)
        return bool(r.stdout.strip())
    except Exception:
        return False


def load_facts():
    facts = []
    p = ROOT / "shared/工具/已完成事实.tsv"
    if not p.exists():
        return facts
    for line in p.read_text(encoding="utf-8").split("\n"):
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) >= 2:
            try:
                facts.append((re.compile(cols[0]), cols[1]))
            except re.error:
                continue
    return facts


def brain_a(text: str):
    """确定性脑：行动句 × 已完成事实。返回 [(行, 依据), ...]"""
    hits = []
    facts = load_facts()
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or not ACTION_WORDS.search(line) or RECAP_WORDS.search(line):
            continue
        for rx, note in facts:
            if rx.search(line):
                hits.append((line[:80], note))
                break
    return hits


def brain_b(text: str, authority: str):
    """LLM 脑：有登录态的 claude CLI 才生效。返回 None（跳过/通过）或 FAIL 原因字符串。"""
    if not shutil.which("claude"):
        return None
    prompt = (
        "你是审计员，只做一件事：检查下面「待审文本」里有没有把**已经完成的事**当成**新待办**报给用户。\n"
        "「档案」是唯一事实源 —— 里面标了完成的事，待审文本不许再让用户去做。\n"
        "只审「让用户去做某事」的句子；对新任务、真待办、单纯的陈述一律放行。\n"
        "输出格式：第一行只写 PASS 或 FAIL；若 FAIL，第二行起用一两句中文指出哪一条、档案哪里说明它已完成。\n\n"
        f"【档案】\n{authority[:12000]}\n\n【待审文本】\n{text[-4000:]}\n"
    )
    try:
        env = dict(os.environ, CLAUDE_AUDIT="1")
        # 🔒 模型：注释说用哪个、代码就得用哪个。2026-09-01 体检发现过一次不一致
        #    （注释和日志写一个模型，代码里却是另一个）—— 「双脑」到底用的什么脑，从来没人核过。
        #    现在只有一个来源：环境变量 CLAUDE_AUDIT_MODEL；没设就不传 --model，用 CLI 默认。
        cmd = ["claude", "-p", prompt]
        model = os.environ.get("CLAUDE_AUDIT_MODEL", "").strip()
        if model:
            cmd += ["--model", model]
        r = subprocess.run(cmd, cwd=tempfile.gettempdir(), env=env,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout or "").strip()
        if not out:
            return None
        first = out.split("\n", 1)[0].strip().upper()
        # 🔒 2026-09-15（总控4 收尾撞上）：claude CLI 登录态过期时 stdout 是
        #    "Failed to authenticate: OAuth session expired…"，大写后以 FAIL 开头，
        #    被当成审计 FAIL 拦住收尾。基础设施错误不是审计结论 —— 只认裸的 FAIL 判决。
        if re.match(r"FAIL\b", first) and not first.startswith("FAILED"):
            return out
    except Exception:
        pass
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("stop_hook_active"):
        return 0
    tp = data.get("transcript_path", "")
    text = ""
    if tp and os.path.exists(tp):
        try:
            text = last_assistant_text(tp)
        except Exception:
            text = ""
    triggered = ("📬" in text or "简报" in text) or dashboard_dirty()
    if not triggered or not text:
        return 0

    fails = []
    for line, note in brain_a(text):
        fails.append(f"[规则脑] 把已完成的事当待办：「{line}」\n         依据：{note}")
    # ⚠️ 分隔线必须行首锚定 —— 文件开头的说明文字里就含这串字（检查.py 踩过同一个坑）
    try:
        log_all = (ROOT / "shared/学习日志.md").read_text(encoding="utf-8")
    except OSError:
        log_all = ""                     # 学习日志还没建：只靠规则脑和事实清单审计
    authority = re.split(r"^#\s*=====\s*历史流水", log_all, flags=re.M)[0]
    facts_txt = (ROOT / "shared/工具/已完成事实.tsv").read_text(encoding="utf-8") if (ROOT / "shared/工具/已完成事实.tsv").exists() else ""
    llm = brain_b(text, authority + "\n\n【已确认完成的事实清单】\n" + facts_txt)
    if llm:
        fails.append(f"[LLM 脑] {llm[:400]}")

    if fails:
        print("\n".join(fails))
        return 3
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
