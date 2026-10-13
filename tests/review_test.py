#!/usr/bin/env python3
"""shared/工具/作业审查.py 的测试 —— 用按剧本回话的假模型服务器，不花钱，CI 里也能跑。

    python3 tests/review_test.py
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(cond, what):
    print(("  ✅ " if cond else "  ❌ ") + what)
    if not cond:
        FAILS.append(what)


def finding(**kw):
    base = {"title": "", "severity": "deduct", "marks_at_risk": 1, "location": {"file": "A2-答案.md", "line_start": 3},
            "work_quote": "", "rule_quote": "", "rule_source": {"doc": "A2-题目.md", "locator": "Q1", "authority": "spec"},
            "what_is_wrong": "", "why_it_costs": "", "evidence": ["读了第 3 行"], "needs_execution": False, "confidence": 0.8}
    base.update(kw)
    return base


LEAKY = finding(title="Q1 网络层的数据单位写错", work_quote="At the network layer the unit is a frame",
                rule_quote="Name the unit of data at the transport layer and at the network layer",
                what_is_wrong="网络层的单位应该是 datagram", why_it_costs="Q1 的第二个得分点会丢",
                suggested_fix="改成 datagram", old_text="the unit is a frame", new_text="the unit is a datagram")
GOOD = finding(title="Q2 公式漏了一项", marks_at_risk=3, work_quote="2 × B × V = 2 × 4000 × 8", location={"file": "A2-答案.md", "line_start": 5, "question": "Q2"},
               rule_quote="Using the Nyquist theorem, compute the maximum data rate",
               what_is_wrong="用的不是课件里 Nyquist 定理的完整形式，信号电平数 V 的处理方式不对", why_it_costs="计算题结果错，3 分里大部分会丢",
               suggested_fix="照课件公式重算", old_text="2 × B × V = 2 × 4000 × 8 = 64,000 bit/s", new_text="2 × B × log2 V = 2 × 4000 × 3 = 24,000 bit/s")
BOGUS = finding(title="引文是编的", work_quote="this sentence does not exist in the answer", rule_quote="Answer all three questions",
                what_is_wrong="……", why_it_costs="……")
DEFAULT = {"per-question-marker": [LEAKY, GOOD, BOGUS]}


class Mock:
    def __init__(self):
        self.requests = []
        self.reset()
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                mock.requests.append(body)
                sysmsg, user = body["messages"][0]["content"], body["messages"][1]["content"]
                m = re.search(r"【本镜头：([\w-]+)", sysmsg)
                if m:
                    if mock.fail_all:
                        out = "我现在没法回答"
                    elif m.group(1) in mock.finder_raw:
                        out = mock.finder_raw[m.group(1)]
                    else:
                        fs = mock.findings.get(m.group(1), [])
                        out = {"lens": m.group(1), "findings": fs, "checked_ok": mock.checked_ok, "not_checked": []}
                elif "事实核实员" in sysmsg or "阅卷判断员" in sysmsg:
                    mock.verify_users.append(user)
                    ids = sorted(set(re.findall(r'"id": "(F\d+)"', user.split("作答上下文")[0])))
                    if mock.partial:
                        ids = ids[:1]
                    out = {"verdicts": [{"finding_id": i, "keep": True, "reason": mock.reason, "evidence_checked": ["第 5 行"],
                                         "marks_at_risk_adjusted": 2, "p_deduct": 0.9} for i in ids]}
                    if mock.marker_garbage and "阅卷判断员" in sysmsg:
                        out = "乱回一通"
                elif "你是判官" in sysmsg:
                    if mock.judge_fail:
                        self.send_response(500)
                        self.end_headers()
                        return
                    out = {"items": [{"id": "F01", "class": "must_fix", "expected_loss": 2.7, "fix_cost": mock.fix_cost, "note": mock.note}],
                           "mark_range": mock.mark_range, "not_covered": ["没法核对 PDF 排版"],
                           "operations_checklist": ["交前下载回来打开看一遍"]}
                    if mock.judge_raw is not None:
                        out = mock.judge_raw
                else:
                    out = {}
                content = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
                if mock.think:
                    content = '<think>先想想 {"draft": true} 这样的格式……</think>\n' + content
                data = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
                                   "usage": {"prompt_tokens": 10, "completion_tokens": 5}}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def reset(self):
        self.findings = json.loads(json.dumps(DEFAULT))
        self.reason, self.note, self.mark_range = "原文确实如此", "计算题主体错", {"low": 4, "high": 6, "caveat": "仅供排序"}
        self.judge_fail = self.think = self.fail_all = self.partial = self.marker_garbage = False
        self.verify_users, self.checked_ok, self.finder_raw, self.judge_raw, self.fix_cost = [], ["ok"], {}, None, "low"

    def close(self):
        self.server.shutdown()


def repo(port):
    d = os.path.join(tempfile.mkdtemp(), "仓库 副本")
    shutil.copytree(SRC, d, ignore=shutil.ignore_patterns(".git", ".env", ".study", "__pycache__", "_审查"))
    with open(os.path.join(d, ".env"), "w", encoding="utf-8") as f:
        f.write(f"STUDY_BASE_URL=http://127.0.0.1:{port}/v1\nSTUDY_API_KEY=test\nSTUDY_MODEL=mock\n")
    return d


def tool(d, *args, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("STUDY_", "REVIEW_"))}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, os.path.join(d, "shared", "工具", "作业审查.py"), *args], cwd=d,
                          capture_output=True, text=True, timeout=180, env=env)


def read(d, *p):
    with open(os.path.join(d, *p), encoding="utf-8") as f:
        return f.read()


def write(d, rel, text):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


D = ("networks", "作业", "_审查", "A2")
PREP = ("prep", "networks", "A2", "--files", "networks/作业/A2-答案.md", "--spec", "networks/作业/A2-题目.md")
RUN = lambda tier, *extra: ("run", "networks", "A2", "--type", "short", "--tier", tier, "--confirm-tier", tier, "--yes", *extra)


def fresh(m):
    m.reset()
    d = repo(m.port)
    tool(d, *PREP)
    return d


if __name__ == "__main__":
    m = Mock()
    d = repo(m.port)

    print("【prep：冻结 + 机械检查】")
    r = tool(d, *PREP)
    check(r.returncode == 0, f"prep 成功{'' if r.returncode == 0 else '：' + r.stderr[-300:]}")
    man = json.loads(read(d, *D, "材料", "MANIFEST.json"))
    check(set(man) == {"A2-答案.md", "A2-题目.md"} and all(len(v["sha256"]) == 64 for v in man.values()), "MANIFEST 记下了两个文件的 SHA-256")
    check("已知且刻意" in read(d, *D, "材料", "README.txt") and "没有提供：slides" in read(d, *D, "材料", "README.txt"), "README 带「已知且刻意」，并写明哪些材料没提供")
    check(json.loads(read(d, *D, "r1", "mechanical.json"))["files"]["A2-答案.md"].get("words", 0) > 50, "机械检查数了字数")

    print("【prep 的输入校验】")
    write(d, "x/ans.md", "a")
    write(d, "y/ans.md", "b")
    r = tool(d, "prep", "networks", "A3", "--files", "x/ans.md", "y/ans.md")
    check(r.returncode != 0 and "同名" in r.stdout + r.stderr, "两个同名文件 → 拒绝（不会互相覆盖）")
    r = tool(d, "prep", "networks", "A3", "--files", "x/ans.md", "--spec", "networks/作业/A2-题目.md", "--rubric", "networks/作业/A2-题目.md")
    check(r.returncode != 0 and "角色" in r.stdout + r.stderr, "同一个文件同时给 --spec 和 --rubric → 拒绝")
    r = tool(d, "prep", "networks", "A3", "--files", "x/ans.md", "--spec", "networks/作业/A2-题目.md", "--no-spec-feed")
    check(r.returncode != 0 and "要点" in r.stdout + r.stderr, "--no-spec-feed 时给 spec 全文 → 拒绝（只收 -要点.md）")

    print("【lenses：按类型 / 档位 / 材料选镜头】")
    r = tool(d, "lenses", "networks", "A2", "--type", "short", "--tier", "guided")
    out = json.loads(r.stdout) if r.returncode == 0 else {}
    keys = [l["key"] for l in out.get("lenses", [])]
    check(r.returncode == 0 and out["plannedAgents"] <= 10, f"short 类默认计划 {out.get('plannedAgents')} 个 agent ≤ 10")
    check("faq-compliance" not in keys and any("faq" in n for n in out.get("notes", [])), "没给官方答疑 → faq 镜头不开，并说明原因")
    r2 = tool(d, "lenses", "networks", "A2", "--type", "short", "--tier", "guided", "--add", "declaration,canon")
    out2 = json.loads(r2.stdout) if r2.returncode == 0 else {}
    check("declaration" in [l["key"] for l in out2.get("lenses", [])], "spec 里写了 declaration → declaration 镜头可以加")
    check(any("canon" in n for n in out2.get("notes", [])), "未知镜头名 → 提示，不静默丢掉")
    write(d, "networks/作业/_审查/A2/镜头.json", json.dumps({"lenses": [{"key": "minimal-form", "title": "我自己的", "types": ["*"], "tiers": ["guided"], "core": ["short"], "prompt": "自定义", "max_findings": 2}]}, ensure_ascii=False))
    r3 = tool(d, "lenses", "networks", "A2", "--type", "short", "--tier", "guided")
    mf = [l for l in json.loads(r3.stdout)["lenses"] if l["key"] == "minimal-form"] if r3.returncode == 0 else []
    check(len(mf) == 1 and mf[0]["title"] == "我自己的", "镜头.json 写成 {\"lenses\": […]} 能读到；同 key = 覆盖，不会出现两份")
    os.remove(os.path.join(d, *D, "镜头.json"))

    print("【checklist：forbidden 档，零模型零网络】")
    before = len(m.requests)
    r = tool(d, "checklist", "networks", "A2", "--type", "short", "--spec", "networks/作业/A2-题目.md")
    cl = read(d, *D, "自查清单.md") if r.returncode == 0 else ""
    check(r.returncode == 0 and len(m.requests) == before, "出了清单，而且模型服务器一次请求都没收到")
    check("120 words" in cl and "why" in cl, "清单里有从 spec 抽的硬性条款（字数）和这类作业的自查项（why / what）")
    r = tool(d, "checklist", "networks", "A2", "--type", "report")
    cl = read(d, *D, "自查清单.md")
    check(all(x in cl for x in ("坐标轴", "参考文献", "术语")), "report 类的清单带上图表、引用、语言这些可选镜头的自查项")

    print("【run：档位闸门】")
    r = tool(d, "run", "networks", "A2", "--type", "short", "--tier", "allowed", "--confirm-tier", "guided", "--yes")
    check(r.returncode != 0 and len(m.requests) == before, "--tier 和 --confirm-tier 不一致 → 拒跑，不调模型")
    r = tool(d, "run", "networks", "A2", "--type", "short", "--tier", "forbidden", "--confirm-tier", "forbidden", "--yes")
    check(r.returncode != 0 and "checklist" in r.stdout + r.stderr, "forbidden → 拒跑并指向 checklist")

    print("【run：guided 档】")
    r = tool(d, *RUN("guided"))
    check(r.returncode == 0, f"跑完{'' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-400:]}")
    merged = json.loads(read(d, *D, "r1", "merged.json"))
    reasons = {x["reason"] for x in merged["dropped"]}
    check("leak-suspect" in reasons and "quote-not-found" in reasons, "「应该是 datagram」判为泄漏丢弃；编造的引文被剔除")
    check(not any("does not exist" in json.dumps(x, ensure_ascii=False) for x in merged["dropped"]), "剔除说明里不回显引文内容")
    check(len(merged["items"]) == 1 and not any(k in merged["items"][0] for k in ("old_text", "new_text", "suggested_fix")), "留下的那条没有任何改法字段")
    find = json.loads(read(d, *D, "r1", "find-per-question-marker.json"))
    check(not re.search(r"suggested_fix|old_text|new_text|datagram", json.dumps(find, ensure_ascii=False)), "落盘的 find-*.json 已经是过滤后的版本（盘上不留答案）")
    rep = read(d, *D, "报告-r1.md")
    check("不含改法" in rep and "本实现做不到的" in rep, "报告抬头写明「不含改法」和简化版的局限")
    check(not re.search(r"24,000|24000|log2|datagram", rep), "报告里没有出现正确答案")
    check("## 1 必改" in rep and "F01" in rep and "交前下载回来打开看一遍" in rep, "必改项和交前清单都在")

    print("【换档 / 改作答：缓存不张冠李戴】")
    k = len(m.requests)
    r = tool(d, *RUN("allowed"))
    check(r.returncode != 0 and "开新一轮" in r.stdout + r.stderr and len(m.requests) == k, "同一轮已经按 guided 审过 → 不许放宽成 allowed（要开新一轮），不调模型")
    d = fresh(m)
    tool(d, *RUN("allowed"))
    rep = read(d, *D, "报告-r1.md")
    check("OLD「2 × B × V = 2 × 4000 × 8 = 64,000 bit/s」" in rep, "allowed 档给出了 OLD → NEW 改法（old_text 在原文里恰好一次）")
    k = len(m.requests)
    tool(d, *RUN("allowed"))
    check(len(m.requests) == k, "同一轮同一档再跑：每一步都读落盘结果，一次模型都不调")
    k = len(m.requests)
    r = tool(d, *RUN("guided"))
    rep = read(d, *D, "报告-r1.md")
    check(len(m.requests) > k and not re.search(r"24,000|datagram|计算题主体错", rep), "同一轮从 allowed 收紧到 guided → 重新调模型，报告里没有 allowed 档的改法和判官说明")
    check(any(n.startswith("r1.stale-") for n in os.listdir(os.path.join(d, *D))) and "suggested_fix" not in read(d, *D, "r1", "merged.json"),
          "↑ allowed 档的旧结果整批挪进 r1.stale-*（之后 scrub 会清），r1/ 里不留改法")
    a2 = read(d, "networks/作业/A2-答案.md").replace("2 × B × V = 2 × 4000 × 8 = 64,000 bit/s, so the channel can carry 64 kbps.", "I am not sure how to do this one.")
    write(d, "networks/作业/A2-答案.md", a2)
    r = tool(d, *PREP)
    check(r.returncode == 0 and "作废" in r.stdout and any(n.startswith("r1.stale-") for n in os.listdir(os.path.join(d, *D))), "改了作答、同一轮重新 prep → 旧结果挪到 r1.stale-*，不会被复用")
    k = len(m.requests)
    tool(d, *RUN("guided"))
    merged = json.loads(read(d, *D, "r1", "merged.json"))
    check(len(m.requests) > k and not merged["items"], "重跑时重新调模型；Q2 那条的引文在新作答里已经不存在 → 被剔除，不会张冠李戴")

    print("【guided：说明文字里的答案】")
    d = fresh(m)
    m.reason, m.note = "重算：2×4000×log2(8)=24,000 bit/s，应该换成 log2 V", "把 V 换成 log2 V 就是 24,000"
    tool(d, *RUN("guided"))
    rep = read(d, *D, "报告-r1.md")
    check(not re.search(r"24,000|log2", rep), "核实员 reason、判官 note 里带答案 → guided 报告里都不出现")
    v = read(d, *D, "r1", "verify-facts-b1.json")
    check("24,000" not in v and "已隐去" in v, "落盘的 verify 文件里，带答案的 reason 已被隐去")
    r = tool(d, "scan", os.path.join(d, *D, "报告-r1.md"), "--course", "networks", "--code", "A2")
    check(r.returncode == 0, "scan：报告干净 → 通过")
    with open(os.path.join(d, *D, "报告-r1.md"), "a", encoding="utf-8") as f:
        f.write("- 其实 the answer is 24 kbps\n")
    r = tool(d, "scan", os.path.join(d, *D, "报告-r1.md"), "--course", "networks", "--code", "A2")
    check(r.returncode == 1 and "24 kbps" in r.stdout, "scan：混进一行答案 → 退出码 1 并指出是哪一行")

    print("【guided：规则原句 / 复述规则 / 引文来源】")
    d = fresh(m)
    m.findings = {"per-question-marker": [
        finding(title="Q2 单位", work_quote="64,000 bit/s", rule_quote="the Nyquist maximum is 24,000 bit/s for 8 levels",
                rule_source={"doc": "slides", "locator": "p.9", "authority": "slides"}, what_is_wrong="单位没问题，但数值和课件不符", why_it_costs="会扣"),
        finding(title="Q3 字数", work_quote="Each layer adds its own header", rule_quote="Each answer must be at most 120 words",
                what_is_wrong="题目要求 Each answer must be at most 120 words，这一题接近上限", why_it_costs="超了会扣"),
    ]}
    tool(d, *RUN("guided"))
    merged = json.loads(read(d, *D, "r1", "merged.json"))
    check(any(x["reason"] == "rule-quote-not-found" for x in merged["dropped"]), "规则原句在权威文件里找不到（例：自称来自课件的「答案」）→ 剔除")
    check(any(i["title"] == "Q3 字数" for i in merged["items"]), "复述 spec 原句（含 must be）不算泄漏，照常保留")

    print("【guided：只有一条发现且被丢弃 → assemble】")
    d = fresh(m)
    m.findings = {"per-question-marker": [LEAKY]}
    tool(d, *RUN("guided"))
    r = tool(d, "assemble", "networks", "A2", "--round", "1")
    raw = read(d, *D, "报告-r1-原始.md") if r.returncode == 0 else ""
    check(r.returncode == 0 and "datagram" not in raw and "不含改法" in raw, "merged 为空时 assemble 不回头读未过滤的 finder 原文；带 guided 抬头")

    print("【scrub：清理盘上的中间文件】")
    write(d, "networks/作业/_审查/A2/r1/find-extra.json", json.dumps({"lens": "x", "findings": [LEAKY]}, ensure_ascii=False))
    r = tool(d, "scrub", "networks", "A2", "--round", "1")
    check(r.returncode == 0 and "datagram" not in read(d, *D, "r1", "find-extra.json"), "scrub 把盘上残留的答案性发现清掉")

    print("【模型乱回 / 全挂 / 判官挂】")
    d = fresh(m)
    m.findings = {"per-question-marker": [dict(GOOD, marks_at_risk="2-3分", location="A2-答案.md 第 5 行", rule_source="spec"),
                                          dict(GOOD, title="Q2b", location={"file": "A2-答案.md", "line_start": "5-7"}), "这不是对象", None]}
    m.mark_range = "4 到 6 分"
    r = tool(d, *RUN("allowed"))
    check(r.returncode == 0 and os.path.isfile(os.path.join(d, *D, "报告-r1.md")), "字段类型乱给（「2-3分」、location 是字符串、「5-7」、非对象）→ 不崩，照常出报告")
    d = fresh(m)
    m.think = True
    r = tool(d, *RUN("allowed"))
    check(r.returncode == 0 and "F01" in read(d, *D, "报告-r1.md"), "回复前面带 <think>{…}</think> → 照样解析出 JSON")
    d = fresh(m)
    m.fail_all = True
    r = tool(d, *RUN("allowed"))
    check(r.returncode != 0 and "所有镜头都没跑成" in r.stdout + r.stderr, "所有镜头都失败 → 退出码非 0，不出一份「必改 0 条」的假报告")
    d = fresh(m)
    m.judge_fail = True
    r = tool(d, *RUN("allowed"))
    rep = read(d, *D, "报告-r1.md")
    check("判官没跑成" in rep, "判官失败 → 报告 §0 写明「判官没跑成」")
    r2 = tool(d, "assemble", "networks", "A2", "--round", "1")
    check(r2.returncode == 0 and "facts" in read(d, *D, "报告-r1-原始.md"), "assemble 从盘上 JSON 拼出原始清单（含核实结论）")

    print("【材料：长作答 / 空作答 / 没改就再审 / 哈希闸门】")
    d = fresh(m)
    write(d, "networks/作业/A2-答案.md", read(d, "networks/作业/A2-答案.md") + "\n" + "filler line about layers.\n" * 4000)
    tool(d, *PREP)
    tool(d, *RUN("allowed"), env_extra={"REVIEW_MAX_CHARS": "60000"})
    check(m.verify_users and all("[spec]" in u for u in m.verify_users), "作答很长时，核实员拿到的上下文里仍然有题面（权威文件优先）")
    d = fresh(m)
    write(d, "networks/作业/空.md", "")
    tool(d, "prep", "networks", "A9", "--files", "networks/作业/空.md", "--spec", "networks/作业/A2-题目.md")
    r = tool(d, "run", "networks", "A9", "--type", "short", "--tier", "allowed", "--confirm-tier", "allowed", "--yes")
    check(r.returncode != 0 and "文字" in r.stdout + r.stderr, "空作答 → run 拒跑（不会让编造的引文混过去）")
    d = fresh(m)
    tool(d, *RUN("allowed"))
    tool(d, *PREP, "--round", "2")
    check(json.loads(read(d, *D, "r2", "mechanical.json"))["same_material_as_prev_round"] is True, "没改作答直接 prep r2 → 标记「和上一轮一样」")
    r = tool(d, "run", "networks", "A2", "--type", "short", "--tier", "allowed", "--confirm-tier", "allowed", "--yes", "--round", "2")
    check(r.returncode != 0 and "上一轮" in r.stdout + r.stderr, "材料没变 → run 拒跑，指向上一轮报告（--force 才重跑）")
    with open(os.path.join(d, *D, "材料", "A2-答案.md"), "a", encoding="utf-8") as f:
        f.write("\n多写一行\n")
    r = tool(d, *RUN("allowed"))
    check(r.returncode != 0 and "哈希" in r.stdout + r.stderr, "冻结副本被改过 → 哈希闸门拒跑")

    print("【第二轮复核补的：缓存键 / 上一轮 / 轮次】")
    d = fresh(m)
    tool(d, *RUN("guided"))
    rd = os.path.join(*D, "材料", "README.txt")
    write(d, rd, read(d, rd).replace("（暂无）", "- Q2 故意只写到这一步", 1))
    k = len(m.requests)
    tool(d, *RUN("guided"))
    check(len(m.requests) > k, "改了 README「已知且刻意」→ 重新调模型（缓存键里有 README）")
    k = len(m.requests)
    tool(d, *RUN("guided"), env_extra={"REVIEW_MODEL": "another"})
    check(len(m.requests) > k, "换了扇出模型 → 重新调模型（缓存键里有模型和字数上限）")
    d = fresh(m)
    tool(d, *RUN("allowed"))
    write(d, "networks/作业/A2-答案.md", read(d, "networks/作业/A2-答案.md") + "\nOne more line.\n")
    tool(d, *PREP, "--round", "2")
    tool(d, *PREP, "--round", "2")
    check(json.loads(read(d, *D, "r2", "mechanical.json"))["same_material_as_prev_round"] is False and "自第 1 轮以来的改动" in read(d, *D, "材料", "README.txt"),
          "同一轮连续 prep 两次：仍然和第 1 轮比，不会误判成「没改」，diff 一节还在")
    r = tool(d, *RUN("allowed", "--round", "2"))
    check(r.returncode == 0, "↑ 这时 run --round 2 照常跑")
    r = tool(d, *RUN("allowed"))
    check(r.returncode != 0 and "最近一次 prep 是第 2 轮" in r.stdout + r.stderr, "prep 了第 2 轮却不带 --round 跑 → 拒跑，不会写进 r1")
    d = fresh(m)
    tool(d, *RUN("allowed"))
    tool(d, *PREP, "--round", "2")
    r = tool(d, *RUN("guided", "--round", "2"))
    check(r.returncode == 0, "材料没变但换了档位（allowed → guided）→ 照样跑")
    r = tool(d, "lenses", "networks", "A2", "--type", "short", "--tier", "allowed", "--round", "2")
    check(r.returncode != 0 and "档位也一样" in r.stdout + r.stderr, "lenses 也认「材料和档位都和上一轮一样」（Claude Code 版不会白跑）")

    print("【第二轮复核补的：assemble 档位 / 兜底 / 编号】")
    d = fresh(m)
    r = tool(d, "assemble", "networks", "A2", "--round", "1")
    check(r.returncode != 0 and "还没审过" in r.stdout + r.stderr, "刚 prep 完就 assemble → 拒绝，不出一份「0 条」的清单")
    tool(d, *RUN("guided"))
    meta = json.loads(read(d, *D, "审查.json"))
    meta["last_run_tier"] = "allowed"
    write(d, os.path.join(*D, "审查.json"), json.dumps(meta))
    write(d, os.path.join(*D, "r1", "verify-facts-b9.json"), json.dumps({"verdicts": [{"finding_id": "F01", "keep": True, "reason": "重算得 24,000"}]}))
    r = tool(d, "assemble", "networks", "A2", "--round", "1")
    raw = read(d, *D, "报告-r1-原始.md")
    check("档位 guided" in r.stdout and "24,000" not in raw and "不含改法" in raw, "assemble 不带 --tier：按这一轮的 tier.json（guided），不看全局的 last_run_tier")
    r = tool(d, "assemble", "networks", "A2", "--round", "1", "--tier", "allowed")
    check(r.returncode != 0 and "不能把它放宽" in r.stdout + r.stderr, "assemble --tier allowed 不能放宽一轮记成 guided 的结果")
    m2 = json.loads(read(d, *D, "r1", "merged.json"))
    m2["items"] = [dict(m2["items"][0], id="F01"), dict(m2["items"][0], id="F01", title="另一条")]
    write(d, os.path.join(*D, "r1", "merged.json"), json.dumps(m2, ensure_ascii=False))
    r = tool(d, "assemble", "networks", "A2", "--round", "1")
    raw = read(d, *D, "报告-r1-原始.md")
    check("## F02 · 另一条" in raw and "重编" in raw, "merged.json 里编号重复 → 按顺序重编（和 workflow 的规则一致），并写明")
    write(d, os.path.join(*D, "r1", "merged.json"), "[]")
    r = tool(d, "assemble", "networks", "A2", "--round", "1")
    check(r.returncode != 0 and "格式不对" in r.stdout + r.stderr, "merged.json 不是对象 → 报错说清楚，不抛异常栈")

    print("【第二轮复核补的：guided 报告的过滤】")
    d = fresh(m)
    m.fix_cost = "use datagram here"
    m.findings = {"per-question-marker": [GOOD, dict(GOOD, title="Q1 单位", work_quote="At the network layer the unit is a frame",
                                                     location={"file": "A2-答案.md", "line_start": 3, "question": "Q2 应该是 datagram"})]}
    tool(d, *RUN("guided"))
    rep = read(d, *D, "报告-r1.md")
    check("sha256" in rep and "期望损失 ≈" in rep, "guided 报告：抬头的 sha256、期望损失这些机械行保留（不被误杀）")
    check("datagram" not in rep and "修法成本 ?" in rep, "location.question / 判官 fix_cost 里带答案 → 报告里不出现")
    r = tool(d, "scan", os.path.join(d, *D, "报告-r1.md"), "--course", "networks", "--code", "A2")
    check(r.returncode == 0, f"scan：干净的 guided 报告 → 通过{'' if r.returncode == 0 else '：' + r.stdout[-300:]}")
    with open(os.path.join(d, *D, "报告-r1.md"), "a", encoding="utf-8") as f:
        f.write("### F09 · Q2 · 公式应该改成 2×B×log2(V)\n⚠️ Q2 应写成 datagram\n```\nSELECT answer FROM t;\n```\n")
    r = tool(d, "scan", os.path.join(d, *D, "报告-r1.md"), "--course", "networks", "--code", "A2")
    check(r.returncode == 1 and all(x in r.stdout for x in ("应该改成", "应写成", "SELECT answer")), "scan：### 标题、⚠️ 行、代码块里的答案都抓得到")
    r = tool(d, "scrub", "networks", "A2", "--round", "1")
    check(r.returncode == 1 and "SELECT answer" in r.stdout and "SELECT answer" in read(d, *D, "报告-r1.md"), "scrub 不偷偷改报告：列出可疑行并退出码 1，让人删")

    print("【第二轮复核补的：scrub 白名单 / hint / stale】")
    d = fresh(m)
    tool(d, *RUN("guided", "--hint", "question"))
    q = dict(GOOD, rule_source={"doc": "A2-题目.md", "locator": "Q2", "authority": "spec", "answer": "24000"},
             guiding_question="V 和电平数是什么关系？", suggested_fix="改用 log2")
    write(d, os.path.join(*D, "r1", "find-js.json"), json.dumps({"lens": "js", "findings": [q]}, ensure_ascii=False))
    write(d, os.path.join(*D, "r1.stale-0101-000000", "verify-facts-b1.json"),
          json.dumps({"verdicts": [{"finding_id": "F01", "keep": True, "reason": "重算得 24,000", "suggested_fix": "x"}]}, ensure_ascii=False))
    tool(d, "scrub", "networks", "A2", "--round", "1")
    js = read(d, *D, "r1", "find-js.json")
    st = read(d, *D, "r1.stale-0101-000000", "verify-facts-b1.json")
    check("24000" not in js and "suggested_fix" not in js and "guiding_question" in js, "scrub：rule_source 的多余键、改法字段清掉；hint=question 时引导问题保留")
    check("24,000" not in st and "suggested_fix" not in st, "scrub 也清 rN.stale-* 里的旧结果")

    print("【第二轮复核补的：模型回复和核实账目】")
    d = fresh(m)
    m.finder_raw = {"per-question-marker": '{"lens": "per-question-marker", "findings": [' + json.dumps(GOOD, ensure_ascii=False)}
    tool(d, *RUN("allowed"))
    rep = read(d, *D, "报告-r1.md")
    check(re.search(r"## 9 没跑成的镜头\n- per-question-marker", rep) and not os.path.isfile(os.path.join(d, *D, "r1", "find-per-question-marker.json")),
          "finder 回了截断的 JSON → 记为没跑成（不落盘、不缓存），不会当成「零条发现」")
    rv_spec = importlib.util.spec_from_file_location("rv", os.path.join(SRC, "shared", "工具", "作业审查.py"))
    rv = importlib.util.module_from_spec(rv_spec)
    rv_spec.loader.exec_module(rv)
    ok = [len(rv.extract_json('{"lens": "x", "findings": [{"title": "a"},],}', ("findings",))["findings"]) == 1,
          len(rv.extract_json('{"lens": "x", // 注释\n "findings": [{"title": "a"}]}', ("findings",))["findings"]) == 1,
          len(rv.extract_json('[{"title": "a"}]', ("findings",))["findings"]) == 1]
    try:
        rv.extract_json('{"title": "只是一条 finding，没有外层"}', ("findings",))
        ok.append(False)
    except ValueError:
        ok.append(True)
    check(all(ok), "extract_json：结尾逗号 / 注释 / 顶层数组能修好；只有内层对象时报错，不当成零条")
    d = fresh(m)
    m.marker_garbage = True
    tool(d, *RUN("allowed"))
    rep = read(d, *D, "报告-r1.md")
    check(re.search(r"## 1 必改\n（无）", rep) and re.search(r"## 5b 票没收齐（不算驳回）\n- F01", rep), "marker 全挂、判官却说 must_fix → 记「票没收齐」，不采信判官")
    d = fresh(m)
    m.findings = {"per-question-marker": [GOOD, dict(GOOD, title="Q1 单位", work_quote="At the network layer the unit is a frame", location={"file": "A2-答案.md", "line_start": 3})]}
    m.partial = True
    tool(d, *RUN("allowed"))
    m.partial = False
    k = len(m.verify_users)
    tool(d, *RUN("allowed"))
    check(len(m.verify_users) > k and "## 5b 票没收齐（不算驳回）\n（无）" in read(d, *D, "报告-r1.md"), "核实员只回了一部分票 → 不当缓存，下次补票")
    d = fresh(m)
    m.judge_raw = '{"items": [{"id": "F01", "class": "must_fix", "expected_loss": 2, "fix_cost": "low", "note": "x"},], "not_covered": []}'
    tool(d, *RUN("allowed"))
    check("判官没跑成" not in read(d, *D, "报告-r1.md"), "判官回复结尾多逗号 → 修好照用，不丢分级")
    d = fresh(m)
    m.judge_raw = {"items": [], "not_covered": []}
    tool(d, *RUN("allowed"))
    check("判官没跑成" in read(d, *D, "报告-r1.md"), "有发现、判官却一条分级都没给 → 当作判官没跑成")
    d = fresh(m)
    m.findings, m.checked_ok = {"per-question-marker": []}, 5
    r = tool(d, *RUN("allowed"))
    rep = read(d, *D, "报告-r1.md") if r.returncode == 0 else ""
    check(r.returncode == 0 and "没有发现" in rep and "判官没跑成" not in rep, "零发现 + checked_ok 乱给 → 不崩；§0 写「没有发现」而不是「判官没跑成」")

    print("【第二轮复核补的：prep 的输入】")
    d = fresh(m)
    r = tool(d, "prep", "networks", "A5", "--files", "networks/作业/A2-答案.md", "--spec", "networks/作业/A2-要点.md", "--no-spec-feed")
    r = tool(d, "prep", "networks", "A5", "--files", "networks/作业/A2-答案.md", "--spec", "networks/作业/A2-题目.md", "--round", "2")
    check(r.returncode != 0 and "要点" in r.stdout + r.stderr, "--no-spec-feed 会沿用：下一次 prep 忘了加、给了 spec 全文 → 照样拒绝")
    write(d, "z/README.txt", "x")
    r = tool(d, "prep", "networks", "A6", "--files", "networks/作业/A2-答案.md", "--starter", "z/README.txt")
    check(r.returncode != 0 and "冲突" in r.stdout + r.stderr, "材料叫 README.txt → 拒绝（和脚本自己写的文件冲突）")
    write(d, "z/Ans.md", "a")
    write(d, "w/ans.md", "b")
    r = tool(d, "prep", "networks", "A7", "--files", "z/Ans.md", "w/ans.md")
    check(r.returncode != 0 and "同名" in r.stdout + r.stderr, "Ans.md 和 ans.md → 当成同名拒绝（大小写不敏感的文件系统会互相覆盖）")
    write(d, "z/a.docx", "PK\x03\x04")
    tool(d, "prep", "networks", "A8", "--files", "z/a.docx")
    r = tool(d, "run", "networks", "A8", "--type", "short", "--tier", "allowed", "--confirm-tier", "allowed", "--yes")
    check("格式不支持" in json.dumps(json.loads(read(d, "networks", "作业", "_审查", "A8", "r1", "mechanical.json")), ensure_ascii=False)
          and r.returncode != 0 and "格式不支持" in r.stdout + r.stderr, ".docx 作答 → prep 写明「格式不支持」，run 拒跑时说的是真实原因")
    write(d, os.path.join(*D, "settled.json"), json.dumps([{"title": "x", "reason": "y"}]))
    r = tool(d, *PREP)
    check(r.returncode == 0 and "数组" in r.stderr, "settled.json 写成数组 → 兼容并提示")
    write(d, os.path.join(*D, "settled.json"), "{坏的")
    r = tool(d, *PREP)
    check(r.returncode != 0 and "settled.json 格式不对" in r.stdout + r.stderr, "settled.json 损坏 → 明确报错，不静默当空")

    print("【第二轮复核补的：镜头选择】")
    d = fresh(m)
    tool(d, *PREP, "--round", "2")
    r = tool(d, "lenses", "networks", "A2", "--type", "short", "--tier", "guided", "--round", "2", "--force")
    keys = [l["key"] for l in json.loads(r.stdout)["lenses"]] if r.returncode == 0 else []
    check(r.returncode == 0 and "declaration" not in keys and "regression" in keys, "第 2 轮：rounds=first 的 auto 镜头（declaration）不再自动开，regression 自动加上")
    write(d, os.path.join(*D, "镜头.json"), '{"lenses": [{"key": "x", "title": "t", "types": ["*"], "tiers": ["guided"], "prompt": "p",}]}')
    r = tool(d, "lenses", "networks", "A2", "--type", "short", "--tier", "guided")
    check(r.returncode != 0 and "第 1 行" in r.stdout + r.stderr, "镜头.json 语法错 → 报错带行号，不静默忽略")
    write(d, os.path.join(*D, "镜头.json"), json.dumps({"lenses": [{"key": "minimal-form", "max_findings": 2}, {"key": "mine", "title": "t", "types": ["*"], "tiers": ["guided"], "prompt": "p", "optional_for": ["short"]}]}))
    r = tool(d, "lenses", "networks", "A2", "--type", "short", "--tier", "guided")
    keys = [l["key"] for l in json.loads(r.stdout)["lenses"]] if r.returncode == 0 else []
    check("minimal-form" in keys and "mine" not in keys, "覆盖库里的镜头只写一个字段 → 其余沿用库里的（仍是核心镜头）；只写 optional_for 的不会被默认选中")
    os.remove(os.path.join(d, *D, "镜头.json"))
    r = tool(d, "lenses", "networks", "A2", "--type", "short", "--tier", "guided", "--shard", "per-question-marker=Q1-Q2,Q3")
    ids = [l["key"] + "__" + l.get("shard", "") for l in json.loads(r.stdout)["lenses"]] if r.returncode == 0 else []
    check("per-question-marker__Q1-Q2" in ids and "per-question-marker__Q3" in ids, "--shard 把一个镜头切成几片")
    r = tool(d, "checklist", "networks", "A2", "--type", "code")
    check("改过之后再核" in read(d, *D, "自查清单.md") and "风格" in read(d, *D, "自查清单.md"), "forbidden 自查清单带上「改过之后再核」的项")

    print("【第三轮复核补的】")
    rv_spec = importlib.util.spec_from_file_location("rv3", os.path.join(SRC, "shared", "工具", "作业审查.py"))
    rv = importlib.util.module_from_spec(rv_spec)
    rv_spec.loader.exec_module(rv)
    ctx = rv.LeakCtx(known={"4000", "120"}, rule=[rv.loose("Replace any placeholder text with your own words. Each answer should be at most 120 words.")],
                     work=[rv.loose("the unit is a frame")], names=["A2-答案.md"])
    cases = {"Q2:24000 bit/s": True, "Replace any placeholder text with datagram": True, "用 `JOIN c ON a.id = c.id` 才对": True,
             "题目说 Each answer should be at most 120 words，这一题超了": False, "见 A2-答案.md:150 和第 150 行": False,
             "The answer is incomplete": False, "HTTP Error 429: Too Many Requests": False, "sed -n 1,400p A2-答案.md": False,
             "２４０００ bit/s": True}
    bad = [t for t, want in cases.items() if rv.leaky(t, ctx) != want]
    check(not bad, f"泄漏规则的边界（冒号后的数、规则只占半句、行内代码、全角数字；行号 / HTTP 码 / sed 范围不误杀）{bad or ''}")
    rep_txt = "# 答案 24,000 bit/s\n档位：guided ｜ 被审：A2-答案.md sha256 123456789 ｜ 24000bps\n模型：gpt-4o-2024-08-06"
    hits = [i for i, _ in rv.scan_lines(rep_txt, ctx)]
    check(hits == [1, 2], f"标题行和抬头行里的答案照样抓（模型名、全数字的 sha256 前缀不误杀）：命中 {hits}")
    import time
    t0 = time.time()
    rv.leaky("Each answer should be at most 120 words. " * 200, ctx)
    rv.leaky("replace" + " " * 20000, ctx)
    rv.leaky("change " * 10000, ctx)
    rv.leaky("1" * 30000, ctx)
    check(time.time() - t0 < 5, f"长输入不卡（规则复述 8 KB、两万个空格、三万位数字）：{time.time() - t0:.1f} s")
    ok = [len(rv.extract_json('{"lens": "x", "findings": [{"title": "a", "evidence": ["// 不是注释", "x"],},]}', ("findings",))["findings"][0]["evidence"]) == 2,
          "```" in rv.extract_json('{"lens": "x", "findings": [{"what_is_wrong": "看这段 ```python\\nx = 1\\n```"}]}', ("findings",))["findings"][0]["what_is_wrong"]]
    check(all(ok), "extract_json：字符串里的 // 不当注释删；字段值里的代码围栏原样保留（后面的泄漏检查才看得见）")
    check(len([q for q in rv.quoted_segments('mixes up "frame" with the unit named in the question and then "segment"') if len(q) >= 15]) == 0,
          "直引号两两配对：两段短引文中间的话不会被当成引文")
    d = fresh(m)
    m.findings = {"per-question-marker": [dict(GOOD, what_is_wrong="公式不对，看这段：```python\nrate = 2 * 4000 * math.log2(8)\n```"),
                                          dict(GOOD, title='Q1 mixes up "frame" with the unit named in the question and then "segment"', location={"file": "A2-答案.md", "line_start": 3},
                                               work_quote="At the network layer the unit is a frame", fix_note="datagram", id="F99", lens="x 24000")]}
    tool(d, *RUN("guided"))
    rep = read(d, *D, "报告-r1.md")
    disk = read(d, *D, "r1", "find-per-question-marker.json") + read(d, *D, "r1", "merged.json")
    check("math.log2" not in rep and "math.log2" not in disk, "what_is_wrong 里夹一段代码 → 报告和盘上都没有")
    check('mixes up' in rep, "标题里有两段短引文的正常发现不会被误删")
    check("datagram" not in rep and "datagram" not in disk and "24000" not in disk, "模型自己填的 fix_note / id / lens 不采信（这些只能由脚本写）")
    d = fresh(m)
    m.findings = {"per-question-marker": [dict(GOOD, rule_quote="Using the Nyquist theorem, compute the maximum data rate")]}
    write(d, "networks/作业/ref.md", "Sample solution: Using the Nyquist theorem, compute the maximum data rate = 24,000 bit/s.")
    tool(d, "prep", "networks", "A2", "--files", "networks/作业/A2-答案.md", "--reference", "networks/作业/ref.md")
    tool(d, *RUN("guided"))
    disk = read(d, *D, "r1", "find-per-question-marker.json")
    check("compute the maximum data rate" not in disk and "原句见材料" in read(d, *D, "报告-r1.md"), "guided：原句只在参考答案里能找到 → 盘上也不留原句，报告只给出处")
    d = fresh(m)
    tool(d, *RUN("guided"))
    r = tool(d, "lenses", "networks", "A2", "--type", "short", "--tier", "allowed", "--force")
    check(r.returncode != 0 and "开新一轮" in r.stdout + r.stderr and json.loads(read(d, *D, "r1", "tier.json"))["tier"] == "guided",
          "lenses 也不能把一轮 guided 放宽成 allowed（加 --force 也不行），tier.json 不被改")
    d = fresh(m)
    m.findings = {"per-question-marker": [GOOD, dict(GOOD, title="Q1 单位", work_quote="At the network layer the unit is a frame", location={"file": "A2-答案.md", "line_start": 3})]}
    tool(d, *RUN("allowed"), env_extra={"REVIEW_MAX_CHARS": "120000"})
    write(d, os.path.join(*D, "r1", "verify-marker-b7.json"), json.dumps({"verdicts": [{"finding_id": "F01", "keep": False, "reason": "旧的"}]}))
    m.findings = {"per-question-marker": [GOOD]}
    tool(d, *RUN("allowed", "--refresh", "find"))
    check(not os.path.isfile(os.path.join(d, *D, "r1", "verify-marker-b7.json")), "这次没产出的旧 verify 文件会被删掉（assemble 不会把旧票配到新编号上）")
    d = fresh(m)
    tool(d, *RUN("guided"))
    tool(d, *PREP, "--round", "2")
    r = tool(d, *RUN("guided", "--round", "2", "--hint", "question"))
    check(r.returncode == 0, "材料和档位没变、但 hint 变了 → 照样跑")
    d = fresh(m)
    r = tool(d, "prep", "networks", "A2", "--files", os.path.join(*D, "材料", "A2-答案.md"))
    check(r.returncode != 0 and "冻结副本" in r.stdout + r.stderr, "--files 给的是冻结副本本身 → 明确拒绝（不抛异常栈）")
    r = tool(d, "scan", "不存在.md")
    check(r.returncode != 0 and "找不到" in r.stdout + r.stderr and "Traceback" not in r.stderr, "scan 一个不存在的文件 → 说清楚，不抛异常栈")
    write(d, "z/Ans.md", "Hello there, this is my answer.")
    tool(d, "prep", "networks", "A4", "--files", "z/Ans.md")
    os.rename(os.path.join(d, "z", "Ans.md"), os.path.join(d, "z", "ans_tmp.md"))
    os.rename(os.path.join(d, "z", "ans_tmp.md"), os.path.join(d, "z", "ans.md"))
    r = tool(d, "prep", "networks", "A4", "--files", "z/ans.md")
    check(r.returncode == 0 and os.path.isfile(os.path.join(d, "networks", "作业", "_审查", "A4", "材料", "ans.md")), "只改了文件名大小写 → 冻结副本不会被当成「上次有、这次没给」删掉")
    lib = json.loads(read(SRC, "shared", "工具", "审查镜头库.json"))
    rx = [l for l in lib["lenses"] if l["key"] == "declaration"][0]["requires"]["spec_regex"]
    check(not any(re.search(rx, t, re.I) for t in ("Write a method declaration", "Add a variable declaration", "Complete the generic type declaration"))
          and re.search(rx, "you must include a declaration at the end", re.I), "declaration 镜头不再被代码题里的「method declaration」触发")

    print("【和 workflow 的 LEAK 正则一致】")
    js = read(SRC, ".claude", "workflows", "assignment-review.js")
    py = read(SRC, "shared", "工具", "作业审查.py")
    js_re = re.search(r"const LEAK = /(.*)/i\n", js).group(1)
    py_re = re.search(r'LEAK = re\.compile\(r"(.*)", re\.I \| re\.A\)\n', py).group(1).replace('\\"', '"')
    check(js_re == py_re, "两边的泄漏正则一字不差")

    m.close()
    print(f"== 失败 {len(FAILS)} 项")
    sys.exit(1 if FAILS else 0)
