#!/usr/bin/env python3
"""作业交前审查 —— 和 /审查（Claude Code workflow）同一套设计，见 docs/作业审查.md。

    prep       <课程> <代号> --files 作答… [--spec …] [--rubric …] [--faq …] [--slides …] [--starter …]
               [--reference …] [--raw-data …] [--round N] [--no-spec-feed | --allow-spec-feed] [--render-pages]
               冻结副本 · SHA-256 · 文字层 · README · 机械检查 · 本轮材料快照（下一轮的 diff 和「是否没改」都和它比）。不调模型。
    lenses     <课程> <代号> --type … --tier … [--hint none|question] [--round N] [--xmodel]
               [--lenses a,b | --add x | --drop y] [--shard key=a,b] [--force]
               按类型、档位、材料选镜头，输出 /审查 要塞进 workflow args 的 JSON，并把这一轮的档位记进 rN/tier.json。不调模型。
    checklist  <课程> <代号> --type …  [--spec …]
               forbidden 档（spec 禁止任何 AI 使用）的自查清单。🔒 零模型、零网络，不读你的作答。
    scrub      <课程> <代号> --round N
               guided 档收口：rN/ 和 rN.stale-*/ 里的 JSON 按白名单清洗、逐字段扫描、核引文；
               再扫一遍报告 —— 有疑似答案的行就列出来，退出码 1。
    scan       <报告路径> [--course … --code …]
               发报告之前最后一道检查：疑似答案的行逐条列出，有就退出码 1。
    fix-ids    <课程> <代号> --round N
               workflow 返回 renumbered: true 时用：把盘上 merged.json 的编号按 workflow 的规则重编（scrub / assemble 也会顺手做）。
    assemble   <课程> <代号> --round N [--tier …]
               从盘上的 JSON 拼一份原始清单（判官挂了时用）。档位按 rN/tier.json，不知道就按 guided。不调模型。
    run        <课程> <代号> --type … --tier allowed|guided --confirm-tier <同一个档> [--hint question]
               [--yes] [--force] [--refresh find|verify|judge|all]
               简化版审查：用 .env 里配置的 OpenAI 兼容模型（不需要 Claude Code）。不运行代码、不看图片。

档位（由你确认，脚本不会自动放宽）：
    allowed   作业允许 AI：可以给改法
    guided    不允许 AI 产出最终件：只指出位置、违反的规则、为什么扣分，不给改好的答案
    forbidden spec 禁止任何 AI 使用：只能用 checklist，run 会拒绝
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import difflib
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB_PATH = os.path.join(ROOT, "shared", "工具", "审查镜头库.json")
TYPES = ["report", "sql", "code", "calc", "short", "model"]
ROLES = ["work", "spec", "faq", "rubric", "slides", "starter", "reference", "raw-data"]
AUTH_ROLES = ("spec", "faq", "rubric", "slides", "reference", "starter")
RULE_ROLES = ("spec", "faq", "rubric")              # guided 档只回显这几类的原句：slides / reference / starter 里可能就有答案
RULE_AUTHORITY = ("spec", "official-faq", "rubric")
TEXT_EXT = (".md", ".txt", ".tex", ".sql", ".java", ".py", ".c", ".cpp", ".h", ".js", ".ts", ".csv", ".json", ".r", ".m", ".html", ".ipynb")
RESERVED = {"readme.txt", "manifest.json"}           # 材料/ 里脚本自己要用的文件名
SEV = ("zero", "deduct", "risk", "polish")
CLASSES = ("must_fix", "should_fix", "leave", "rejected", "unverified", "incomplete")
FIX_COST = ("low", "medium", "high")
BASE_KEYS = ("title", "severity", "marks_at_risk", "location", "work_quote", "rule_quote", "rule_source",
             "what_is_wrong", "why_it_costs", "evidence", "needs_execution", "confidence")
FIX_KEYS = ("suggested_fix", "old_text", "new_text", "adds_lines")
META_KEYS = ("id", "lens", "source", "raised_by", "merged_from", "fix_note")
LOC_KEYS = ("file", "line_start", "line_end", "page", "question")
RS_KEYS = ("doc", "locator", "authority")
VERDICT_KEYS = ("finding_id", "keep", "confidence", "reason", "evidence_checked", "severity_adjusted", "marks_at_risk_adjusted", "p_deduct")
VERDICT_FIX_KEYS = ("fix_is_safe", "better_fix")
JUDGE_ITEM_KEYS = ("id", "class", "expected_loss", "fix_cost", "fix_cost_reason", "judge_reproduced", "note")
# 🔒 LEAK / NUM / STRUCT 三个正则和 .claude/workflows/assignment-review.js 保持一字不差（tests 会比对）
LEAK = re.compile(r"```|~~~|应该?(是|为|改)|应该(用|写)|应写|应当|必须(写成|是)|(应(?!用)|请|建议|需要?|可以?)\S{0,4}(改写|改用|换成|写成|替换)(?!了)|把[^。；;\n]{1,30}?(换成|写成|替换为|替换成|改写为|改写成)(?!了)|改(成|为)(?!了)|(正确|答案)(答案|的?(值|写法|表达式|结果|公式))?(是|为)|答案\s*[:：]|结果(应|是|为)\s*-?\d|should\s+(be|read|say|use|have|equal)|should\s+([A-Za-z]+\s+){1,3}be(?![A-Za-z])|ought\s+to|instead\s+of|in\s+place\s+of|replace\s[^\n]{1,80}?\swith|change\s[^\n]{1,80}?\sto(?![a-z])|did\s+you\s+mean|(correct|right)\s+(answer|value|formula|result)\s+is|the\s+answer\s+is(?!\s+(incomplete|missing|wrong|incorrect|unclear|not))|(?<![A-Za-z])(answer|result)\s*[:：=]|(->|→)\s*(-?\d|[\"「`])|`[^`\n]{0,80}=[^`\n]{0,80}`", re.I | re.A)
NUM = re.compile(r"\d[\d,]{0,24}\.\d+|\d{1,3}(?:,\d{3})+|\d{3,}", re.A)
# 结构化记号（哈希、行号、页码、字数、期望损失……）不是答案：查「新数字」之前先去掉
STRUCT = re.compile(r"sha-?256(\s*[0-9a-f]{6,64})?|(?<![0-9a-z])(?=[0-9a-f]{0,63}[a-f])[0-9a-f]{12,64}(?![0-9a-z])|(期望损失|暂定分区间|暂定分)\s*≈?\s*[\d.]{1,12}(\s*[–-]\s*[\d.]{1,12})?|第\s*\d{1,6}\s*(行|页|题)|(?<![a-z])(pp?|lines?|pages?)\.?\s*\d{1,6}(\s*[–-]\s*\d{1,6})?|\d{1,9}(\s*[–-]\s*\d{1,9})?\s*(words?(?![a-z])|marks?(?![a-z])|chars?(?![a-z])|个?字符|词|字(?!节)|页|分(?!之|钟))|\.[a-z0-9]{1,5}:\d{1,7}(-\d{1,7})?(?![0-9])|(?<![0-9a-z])F\d{2,5}(?![0-9])|(?<![a-z])(http(\s+error)?|status(\s+code)?)\s*[:：]?\s*\d{3}(?![0-9])|(?<![0-9])\d{1,7},\d{1,7}p(?![a-z])", re.I | re.A)
MASK = "（已隐去：疑似包含答案）"
MASK_LINE = "- （此行疑似包含答案，已隐去）"
QUOTE = re.compile(r"「([^」\n]{2,})」|“([^”\n]{2,})”|\"([^\"\n]{2,})\"")
FIXED_LINES = {"⚠️ 按这份作业的 AI 政策，本报告只指出位置和原因，不含改法。",
               "⚠️ 按这份作业的 AI 政策，本清单只列位置和原因，不含改法，也不含核实员的说明。",
               "- ⚠️ 需要你自己跑一下才能确认"}
HEADER = re.compile(r"^(档位|模型)\s*[：:]")


# ====================================================================== 公共
def d_of(course, code):
    return os.path.join(ROOT, course, "作业", "_审查", code)


def load_lib():
    with open(LIB_PATH, encoding="utf-8") as f:
        return json.load(f)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def file_sha(path):
    return sha256(path) if os.path.isfile(path) else ""


def fp(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]


def rjson(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def rdict(path):
    obj = rjson(path)
    return obj if isinstance(obj, dict) else {}


def wjson(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def wtext(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def rtext(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def norm(s):
    return re.sub(r"\s+", " ", s if isinstance(s, str) else "").strip().lower()


def loose(s):
    """核对引文用：去掉 Markdown 强调 / 代码记号，统一引号和破折号，再压空白。模型引用时常常不带这些记号。"""
    t = s if isinstance(s, str) else ""
    t = re.sub(r"[*_`#>]", "", t)
    t = t.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "×": "x"}))
    return norm(t)


def as_int(x):
    m = re.search(r"\d+", str(x)) if x is not None else None
    return int(m.group(0)) if m else None


def as_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        m = re.search(r"\d+(\.\d+)?", str(x or ""))
        return float(m.group(0)) if m else 0.0


def as_num(x):
    """严格版：只认真正的数字，认不出返回 None（判官的期望损失、分数区间用这个，不猜）。"""
    if isinstance(x, bool) or x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def strs(x):
    return [str(y) for y in x if isinstance(y, (str, int, float)) and not isinstance(y, bool)] if isinstance(x, list) else []


def abspath(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def tier_of(R):
    """这一轮确认过的档位（lenses / run 写的 rN/tier.json）。"""
    return rdict(os.path.join(R, "tier.json"))


def stale_stamp(D, rn):
    """作废副本的后缀：同一秒里挪两次也不会撞名。"""
    base = dt.datetime.now().strftime("%m%d-%H%M%S")
    stamp, n = base, 1
    while glob.glob(os.path.join(D, f"r{rn}.stale-{stamp}")) or glob.glob(os.path.join(D, f"报告-r{rn}*.stale-{stamp}.md")):
        n += 1
        stamp = f"{base}-{n}"
    return stamp


def round_dirs(D, rn):
    """rN 和它的所有作废副本 rN.stale-*。"""
    out = [os.path.join(D, f"r{rn}")] + sorted(glob.glob(os.path.join(D, f"r{rn}.stale-*")))
    return [p for p in out if os.path.isdir(p)]


def read_settled(D):
    p = os.path.join(D, "settled.json")
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        sys.exit(f"❌ settled.json 格式不对（{e}）：应为 {{\"rejected\": […], \"leave\": […], \"fixed\": […]}}")
    if isinstance(obj, list):
        print("⚠️ settled.json 是一个数组，按 {\"rejected\": […]} 理解", file=sys.stderr)
        obj = {"rejected": obj}
    if not isinstance(obj, dict):
        sys.exit("❌ settled.json 格式不对：应为 {\"rejected\": […], \"leave\": […], \"fixed\": […]}")
    return {k: [s for s in obj[k] if isinstance(s, dict)] for k in ("rejected", "leave", "fixed") if isinstance(obj.get(k), list)}


# ====================================================================== 泄漏检查
class LeakCtx:
    """guided 档泄漏检查的上下文：已知数字 + 可以逐字核实的作答 / 规则原文（都已 loose 过）。"""

    def __init__(self, known=None, rule=(), work=(), names=(), auth=()):
        self.known = known
        self.rule = [t for t in rule if t]
        self.work = [t for t in work if t]
        self.auth = [t for t in auth if t]      # 全部权威文件（含 slides / reference / starter）：只用来判断引文是不是编的
        self.names = set(names)                 # 材料文件名：位置字段里出现它们不算


def known_numbers(docs, D=None):
    """作答、spec / faq / rubric、README、机械检查里出现过的数。slides / reference / starter 的不算 —— 那里面可能就是答案。"""
    texts = [d["text"] for d in docs.values() if d.get("text") and d["role"] in ("work",) + RULE_ROLES]
    texts.append(" ".join(docs.keys()))              # 文件名里的数字（例：A2-2024.pdf）
    if D:
        texts.append(os.path.relpath(D, ROOT))         # 课程名、作业代号里的数字
        p = os.path.join(D, "材料", "README.txt")
        if os.path.isfile(p):
            texts.append(rtext(p))
        for m in glob.glob(os.path.join(D, "r*", "mechanical.json")):
            texts.append(json.dumps(rdict(m)))
    s = set()
    for t in texts:
        for n in NUM.findall(t):
            s.add(n)
            s.add(n.replace(",", ""))
    return s


def leak_ctx(docs, D=None):
    docs = docs or {}
    return LeakCtx(known_numbers(docs, D),
                   [loose(d["text"]) for d in docs.values() if d["role"] in RULE_ROLES and d.get("text")],
                   [loose(d["text"]) for d in docs.values() if d["role"] == "work" and d.get("text")],
                   docs.keys(),
                   [loose(d["text"]) for d in docs.values() if d["role"] in AUTH_ROLES and d.get("text")])


def guided_ctx(D):
    _, docs = load_docs(D)
    return leak_ctx(docs, D)


CLAUSE_END = re.compile(r"[。；;!?！？\n，,]|\.(\s|$)")


def _rule_window(t, s, e, rule):
    """命中位置向两边扩：扩出来的整段若是 spec / faq / rubric 的逐字子串（≥ 20 字符），而且一直扩到这个分句的末尾，
    说明是在复述规则，不是给答案。分句末尾还剩别的字（例：「Replace … with datagram」只有前半句是 spec）→ 不豁免。"""
    def ok(i, j):
        w = loose(t[i:j])
        return bool(w) and any(w in r for r in rule)
    if not rule or not ok(s, e):
        return None
    m = CLAUSE_END.search(t, e)
    end = m.start() if m else len(t)
    lo, hi = 0, s                          # 包含关系是单调的：二分找最左、最右
    while lo < hi:
        mid = (lo + hi) // 2
        if ok(mid, e):
            hi = mid
        else:
            lo = mid + 1
    i = lo
    lo, hi = e, end
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if ok(i, mid):
            lo = mid
        else:
            hi = mid - 1
    j = lo
    if t[j:end].strip(" \t\"'」”*`)）") or len(loose(t[i:j])) < 20:
        return None
    return i, j


def _exempt(t, ctx):
    if ctx is None:
        return t
    # ① 引号里的文字能在作答或规则原文里逐字找到 → 是引用，不算
    def q(m):
        g = loose(next(x for x in m.groups() if x is not None))
        return " " if len(g) >= 2 and any(g in x for x in ctx.rule + ctx.work) else m.group(0)
    t = QUOTE.sub(q, t)
    # ② 命中所在的整段是规则原文 → 不算
    spans = sorted(w for w in (_rule_window(t, m.start(), m.end(), ctx.rule) for m in LEAK.finditer(t)) if w)
    merged = []
    for i, j in spans:
        if merged and i <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(j, merged[-1][1]))
        else:
            merged.append((i, j))
    for i, j in reversed(merged):
        t = t[:i] + " " + t[j:]
    return t


def leaky(text, ctx=None):
    """guided 档：这段文字像不像在给答案？
    ① LEAK 正则（逐字复述作答或 spec / faq / rubric 原文不算）
    ② 新颖数字：作答、规则和机械检查里都没出现过的数（例：算出来的 24,000）。行号、页码、字数、哈希这类结构化记号先去掉"""
    t = text if isinstance(text, str) else " ".join(map(str, text or []))
    t = unicodedata.normalize("NFKC", t)               # 全角数字、不换行空格先统一（和 workflow 一致）
    if ctx is not None and t.strip() in ctx.names:
        return False
    t = _exempt(t, ctx)
    if LEAK.search(t):
        return True
    if ctx is not None and ctx.known is not None:
        for n in NUM.findall(STRUCT.sub(" ", t)):
            if n not in ctx.known and n.replace(",", "") not in ctx.known:
                return True
    return False


def scan_lines(text, ctx=None):
    """逐行找疑似答案：围栏 / 缩进代码块整段算命中；只有几条固定说明行豁免；抬头行只查措辞，不查标识符里的数字。"""
    hits, fence, prev_blank, in_code = [], None, True, False
    for i, l in enumerate(text.split("\n"), 1):
        s = l.lstrip()
        if fence:
            hits.append((i, l))
            if s.startswith(fence):
                fence = None
            continue
        if s.startswith(("```", "~~~")):
            fence = s[:3]
            hits.append((i, l))
            continue
        if l.strip() and l.startswith(("    ", "\t")) and (prev_blank or in_code):
            in_code = True
            hits.append((i, l))
            prev_blank = False
            continue
        if l.strip():
            in_code = False
        prev_blank = not l.strip()
        if not l.strip() or l in FIXED_LINES:
            continue
        if HEADER.match(l) or (i == 1 and l.startswith("# ")):
            # 抬头行：文件名、模型名这类标识符里的数字不算（带字母又带 - _ . / 的词）；其余照查
            ident = re.sub(r"\S+", lambda m: " " if re.search(r"[A-Za-z]", m.group()) and re.search(r"[-_./]", m.group()) else m.group(), l)
            bad = LEAK.search(_exempt(l, ctx)) or leaky(ident, ctx)
        else:
            bad = leaky(l, ctx)
        if bad:
            hits.append((i, l))
    return hits


def mask_lines(text, ctx=None):
    bad = {i for i, _ in scan_lines(text, ctx)}
    return "\n".join(MASK_LINE if i in bad else l for i, l in enumerate(text.split("\n"), 1))


def scrub(f, tier, hint, ctx=None):
    """白名单：只留认识的字段（location / rule_source 也只留认识的键）。guided 档再逐字段标疑似泄漏。"""
    if not isinstance(f, dict):
        return None
    keep = BASE_KEYS + META_KEYS + (() if tier == "guided" else FIX_KEYS) + (("guiding_question",) if hint == "question" else ())
    out = {k: f[k] for k in keep if k in f}
    if isinstance(out.get("location"), dict):
        out["location"] = {k: out["location"][k] for k in LOC_KEYS if k in out["location"]}
    if isinstance(out.get("rule_source"), dict):
        out["rule_source"] = {k: out["rule_source"][k] for k in RS_KEYS if k in out["rule_source"]}
    if "raised_by" in out:
        out["raised_by"] = as_int(out["raised_by"]) or 1
    if tier != "guided":
        return out
    loc = out.get("location") if isinstance(out.get("location"), dict) else {}
    rs = out.get("rule_source") if isinstance(out.get("rule_source"), dict) else {}
    ev = out.get("evidence") if isinstance(out.get("evidence"), list) else [out.get("evidence") or ""]
    mf = out.get("merged_from") if isinstance(out.get("merged_from"), list) else [out.get("merged_from") or ""]
    fields = [out.get(k) for k in ("title", "what_is_wrong", "why_it_costs", "guiding_question", "lens", "source", "fix_note")] + ev + mf + \
             [loc.get("question"), loc.get("file"), rs.get("doc"), rs.get("locator")]
    rq = loose(out.get("rule_quote"))
    if rq and ctx is not None and not any(rq in r for r in ctx.rule) and any(rq in a for a in ctx.auth):
        out["rule_quote"] = ""            # 真引自课件 / 参考答案 / 起始代码：可能就是答案，guided 不留原句，只留出处
    if any(leaky(str(x), ctx) for x in fields if x):
        out["leak_suspect"] = True
    return out


def mask_verdict(v, tier, ctx=None):
    """核实员的票：白名单 + 类型校正；guided 档把带答案的 reason / evidence 隐去。"""
    if not isinstance(v, dict) or not isinstance(v.get("finding_id"), str):
        return None
    out = {k: v[k] for k in VERDICT_KEYS + (() if tier == "guided" else VERDICT_FIX_KEYS) if k in v}
    out["keep"] = out.get("keep") is True
    out["reason"] = out.get("reason") if isinstance(out.get("reason"), str) else str(out.get("reason") or "")
    out["evidence_checked"] = strs(out.get("evidence_checked"))
    for k in ("confidence", "marks_at_risk_adjusted", "p_deduct"):
        if k in out:
            x = as_num(out[k])
            if x is None:
                out.pop(k)
            else:
                out[k] = x
    if out.get("severity_adjusted") not in SEV + ("none",):
        out.pop("severity_adjusted", None)
    if tier == "guided":
        if leaky(out["reason"], ctx):
            out["reason"] = MASK
        out["evidence_checked"] = [MASK if leaky(e, ctx) else e for e in out["evidence_checked"]]
    return out


def clean_judge(j, tier, ctx=None):
    """判官的 JSON：白名单 + 枚举 / 数字校验；guided 档隐去带答案的说明，不留 mark_range.caveat。"""
    items = []
    for x in j.get("items") if isinstance(j.get("items"), list) else []:
        if not isinstance(x, dict) or x.get("class") not in CLASSES or not isinstance(x.get("id"), str):
            continue
        c = {k: x[k] for k in JUDGE_ITEM_KEYS if k in x}
        c["fix_cost"] = c.get("fix_cost") if c.get("fix_cost") in FIX_COST else None
        el = as_num(c.get("expected_loss"))
        if el is None:
            c.pop("expected_loss", None)
        else:
            c["expected_loss"] = el
        for k in ("note", "fix_cost_reason"):
            if k in c:
                c[k] = c[k] if isinstance(c[k], str) else str(c[k])
                if tier == "guided" and leaky(c[k], ctx):
                    c[k] = MASK
        items.append(c)
    mr = j.get("mark_range") if isinstance(j.get("mark_range"), dict) else {}
    mark = {k: as_num(mr.get(k)) for k in ("low", "high") if as_num(mr.get(k)) is not None}
    if tier != "guided" and isinstance(mr.get("caveat"), str):
        mark["caveat"] = mr["caveat"]
    out = {"items": items, "mark_range": mark}
    for k in ("not_covered", "operations_checklist"):
        out[k] = [MASK if tier == "guided" and leaky(s, ctx) else s for s in strs(j.get(k))]
    if "open_must_fix" in j:
        out["open_must_fix"] = as_int(j["open_must_fix"])
    for k in ("report_path", "input_key", "_fp"):
        if isinstance(j.get(k), str):
            out[k] = MASK if k == "report_path" and tier == "guided" and leaky(j[k], ctx) else j[k]
    return out


# ====================================================================== 材料
def pdf_text_pages(path):
    """返回 (带页码标记的文字层, 有字的页数)；没装 poppler 返回 (None, 0)。"""
    if not shutil.which("pdftotext"):
        return None, 0
    r = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True)
    if r.returncode != 0:
        return "", 0
    pages = r.stdout.split("\f")
    kept = [(i, p) for i, p in enumerate(pages, 1) if p.strip()]
    return "\n".join(f"=== p.{i} ===\n{p}" for i, p in kept), len(kept)


def pdf_text(path):
    return pdf_text_pages(path)[0]


def pdf_pages(path):
    if not shutil.which("pdfinfo"):
        return None
    r = subprocess.run(["pdfinfo", path], capture_output=True, text=True)
    m = re.search(r"^Pages:\s+(\d+)", r.stdout, re.M)
    return int(m.group(1)) if m else None


def load_docs(D):
    manifest = rjson(os.path.join(D, "材料", "MANIFEST.json"))
    if not isinstance(manifest, dict) or not manifest:
        return None, None
    docs = {}
    for name, m in manifest.items():
        p = os.path.join(D, "材料", name)
        tp = p + ".txt" if name.lower().endswith(".pdf") else p
        text = None
        if os.path.isfile(tp) and (tp.endswith(".txt") or name.lower().endswith(TEXT_EXT)):
            text = rtext(tp)
        docs[name] = {"role": m["role"], "text": text, "sha256": m["sha256"]}
    return manifest, docs


def material_map(manifest):
    return {n: {"sha256": m.get("sha256"), "role": m.get("role")} for n, m in (manifest or {}).items() if isinstance(m, dict)}


def inputs_digest(D, manifest):
    """缓存键的「输入」部分：材料（含角色）+ README（学生在 prep 之后手填的两节）+ settled + 自定义镜头。
    任何一个变了，所有阶段都重做。必须在取镜头 / 开跑那一刻现算，不能用 prep 时存的旧值。"""
    return fp(material_map(manifest), file_sha(os.path.join(D, "材料", "README.txt")),
              file_sha(os.path.join(D, "settled.json")), file_sha(os.path.join(D, "镜头.json")))


# ====================================================================== prep
def cmd_prep(a):
    D = d_of(a.course, a.code)
    M = os.path.join(D, "材料")
    rn = a.round
    meta = rdict(os.path.join(D, "审查.json"))
    settled = read_settled(D)                          # 先校验，格式不对就在动任何东西之前退出
    no_feed = a.no_spec_feed or (meta.get("spec_feed_allowed") is False and not a.allow_spec_feed)
    if no_feed and not a.no_spec_feed:
        print("🔒 之前 prep 标过 --no-spec-feed，这次沿用（这份作业的政策真的变了才用 --allow-spec-feed）")
    pairs = [(role, f) for role in ROLES for f in (getattr(a, role.replace("-", "_")) or [])]
    if not any(r == "work" for r, _ in pairs):
        sys.exit("❌ 至少要给一个 --files 作答文件")
    # 先全部校验，再动手
    seen_name, seen_path = {}, {}
    for role, src in pairs:
        sp = os.path.realpath(abspath(src))
        if not os.path.isfile(sp):
            sys.exit(f"❌ 找不到 {src}")
        if os.path.dirname(sp) == os.path.realpath(M):
            sys.exit(f"❌ {src} 是冻结副本本身 —— 给原文件（你在改的那份），不是 材料/ 里的")
        if sp in seen_path:
            sys.exit(f"❌ 同一个文件 {src} 同时给了 --{seen_path[sp]} 和 --{role}，一个文件只能有一个角色")
        name = os.path.basename(sp)
        if name.casefold() in RESERVED:
            sys.exit(f"❌ {src} 的文件名和脚本自己要写的 {name} 冲突 —— 先改名再 prep")
        if name.casefold() in seen_name:
            sys.exit(f"❌ 两个文件同名（不分大小写）：{seen_name[name.casefold()]} 和 {src} —— 冻结副本按文件名存，会互相覆盖。先改名再 prep")
        seen_path[sp], seen_name[name.casefold()] = role, src
        if role == "spec" and no_feed and not name.endswith("-要点.md"):
            sys.exit(f"❌ 这份作业不许把整份 spec 喂给 AI（--no-spec-feed）：--spec 只能给 <代号>-要点.md，不能给 {name}")
    for k in seen_name:                                 # ans.pdf 的文字层就叫 ans.pdf.txt
        if k.endswith(".pdf.txt") and k[:-4] in seen_name:
            sys.exit(f"❌ {seen_name[k]} 会和 {seen_name[k[:-4]]} 的文字层同名 —— 先改名再 prep")

    new_mat = {os.path.basename(os.path.realpath(abspath(s))): {"sha256": sha256(os.path.realpath(abspath(s))), "role": role}
               for role, s in pairs}
    prev = material_map(rdict(os.path.join(M, "MANIFEST.json")))
    R = os.path.join(D, f"r{rn}")
    r_digest = rdict(os.path.join(R, "manifest.json")).get("digest") or fp(prev)     # 这一轮自己记的材料；老目录没有就看上次 prep
    if os.path.isdir(R) and fp(new_mat) != r_digest:
        stamp = stale_stamp(D, rn)
        if (tier_of(R).get("tier") or "guided") == "guided":       # 作废的结果也可能带着答案：挪走之前先清洗
            scrub_jsons(D, R, guided_ctx(D), tier_of(R).get("hint") or "none")
        os.rename(R, R + f".stale-{stamp}")
        for suffix in ("", "-原始"):
            p = os.path.join(D, f"报告-r{rn}{suffix}.md")
            if os.path.isfile(p):
                os.rename(p, os.path.join(D, f"报告-r{rn}{suffix}.stale-{stamp}.md"))
        print(f"⚠️ 材料和上次 prep 不一样，r{rn}/ 和第 {rn} 轮的报告作废，挪到 r{rn}.stale-{stamp}/")

    os.makedirs(M, exist_ok=True)
    shutil.rmtree(os.path.join(M, "页面"), ignore_errors=True)       # 页面图每次重渲，不留旧页
    P = os.path.join(D, f"r{rn - 1}")
    prev_round = rdict(os.path.join(P, "manifest.json")) if rn > 1 else {}
    prev_files = prev_round.get("files") if isinstance(prev_round.get("files"), dict) else {}
    manifest, files, diffs = {}, {}, []
    for role, src in pairs:
        sp = os.path.realpath(abspath(src))
        name = os.path.basename(sp)
        dst = os.path.join(M, name)
        shutil.copy2(sp, dst)
        h = new_mat[name]["sha256"]
        manifest[name] = {"sha256": h, "role": role, "source": os.path.relpath(sp, ROOT)}
        info = {"role": role, "bytes": os.path.getsize(dst)}
        text = None
        low = name.lower()
        if low.endswith(".pdf"):
            info["pages"] = pdf_pages(dst)
            text, k = pdf_text_pages(dst)
            if text is None:
                info["note"] = "没装 poppler，PDF 没抽文字层"
            elif not text.strip():
                info["note"] = "无文字层（扫描件？）—— 这个文件的内容审不了"
                text = None
            elif info["pages"] and k < info["pages"]:
                info["note"] = f"{info['pages'] - k} 页没有文字层（只审得到有字的 {k} 页）"
            if text is None:
                if os.path.isfile(dst + ".txt"):
                    os.remove(dst + ".txt")            # 旧的文字层不能留着冒充新文件
            else:
                wtext(dst + ".txt", text)
            if a.render_pages and role == "work" and shutil.which("pdftoppm"):
                os.makedirs(os.path.join(M, "页面"), exist_ok=True)
                subprocess.run(["pdftoppm", "-png", "-r", "80", dst, os.path.join(M, "页面", os.path.splitext(name)[0])], capture_output=True)
                info["rendered_pages"] = "材料/页面/"
        elif low.endswith(TEXT_EXT):
            text = rtext(dst)
        else:
            info["note"] = f"格式不支持抽取文字（{os.path.splitext(name)[1] or '无后缀'}）—— 这个文件的内容审不了，转成 PDF 或文本再 prep"
        if text is not None:
            info["words"] = len(re.findall(r"[A-Za-z0-9_']+", text)) + len(re.findall(r"[一-鿿]", text))
            info["lines"] = text.count("\n") + 1
            if role == "work" and not text.strip():
                info["note"] = "空文件"
        if role == "work":
            if text is not None:
                wtext(os.path.join(R, "snap", name + ".txt"), text)          # 本轮快照：下一轮的 diff 和它比
            old = os.path.join(P, "snap", name + ".txt")
            if text is not None and os.path.isfile(old):
                old_text = rtext(old)
                if old_text != text:
                    diffs.append("".join(difflib.unified_diff(old_text.splitlines(True), text.splitlines(True), f"{name}（第 {rn - 1} 轮）", name)))
        if name.endswith(".tex"):
            log = os.path.splitext(sp)[0] + ".log"
            if os.path.isfile(log):
                lg = rtext(log)
                info["latex_errors"] = len(re.findall(r"^!", lg, re.M))
                info["overfull_hbox"] = lg.count("Overfull \\hbox")
            else:
                info["latex_log"] = "没找到 .log —— 编译一次再 prep，才能查 ^! 和 Overfull"
        if name in prev_files and prev_files[name].get("sha256") != h:
            info["changed_since_last_round"] = True
        files[name] = info
    kept_names = {n.casefold() for n in manifest}
    for gone in set(prev) - set(manifest):          # 上次有、这次没给的材料（只改了大小写的不算 —— 不分大小写的文件系统上它就是刚拷进来的那份）
        if gone.casefold() in kept_names:
            continue
        for suffix in ("", ".txt"):
            p = os.path.join(M, gone + suffix)
            if os.path.isfile(p):
                os.remove(p)
    digest = fp(new_mat)
    same = bool(prev_files) and prev_round.get("digest") == digest
    work_changed = [n for n in files if files[n]["role"] == "work" and files[n].get("changed_since_last_round")]
    wjson(os.path.join(M, "MANIFEST.json"), manifest)
    wjson(os.path.join(R, "mechanical.json"), {"round": rn, "files": files, "same_material_as_prev_round": same})
    wjson(os.path.join(R, "manifest.json"), {"files": new_mat, "digest": digest})

    readme = os.path.join(M, "README.txt")
    keep = {}
    if os.path.isfile(readme):          # 保留学生手写的两节
        old = rtext(readme)
        for sec in ("已知且刻意", "已修"):
            m = re.search(rf"## {sec}[^\n]*\n(.*?)(?=\n## |\Z)", old, re.S)
            if m:
                keep[sec] = m.group(1).strip("\n")
    lines = [f"# 审查材料说明 —— {a.course} {a.code} · 第 {rn} 轮", "",
             "## 权威文件（冲突时按这个顺序：spec > 官方答疑 faq > rubric > 课件 slides > 参考资料 reference）"]
    for role in ("spec", "faq", "rubric", "slides", "reference", "starter", "raw-data"):
        for n, m in manifest.items():
            if m["role"] == role:
                extra = "（文字层：" + n + ".txt）" if n.lower().endswith(".pdf") and os.path.isfile(os.path.join(M, n + ".txt")) else ""
                lines.append(f"- [{role}] {n}{extra}" + (f" ⚠️ {files[n]['note']}" if files[n].get("note") else ""))
    absent = [r for r in ("slides", "starter", "reference", "raw-data", "faq", "rubric") if not any(m["role"] == r for m in manifest.values())]
    if absent:
        lines.append(f"- 没有提供：{', '.join(absent)} —— 依赖它们的判断写进 not_checked，不凭常识下结论")
    if no_feed:
        lines.append("- 🔒 这份作业不许把整份 spec 喂给 AI：这里只有要点文件")
    lines += ["", "## 被审文件（只审这些冻结副本，别处的草稿一律忽略）"]
    lines += [f"- {n}  sha256 {m['sha256'][:12]}" + ("（页面图在 材料/页面/）" if files[n].get("rendered_pages") else "")
              + (f" ⚠️ {files[n]['note']}" if files[n].get("note") else "") for n, m in manifest.items() if m["role"] == "work"]
    lines += ["", "## 已知且刻意（不许报）—— ✏️ 每行一条，写清是什么、为什么故意这样", keep.get("已知且刻意", "（暂无）"),
              "", "## 已修（不许重报，除非修复本身错了）", keep.get("已修", "（暂无）"),
              "", "## 已裁定 settled（没有新证据不许重开）"]
    for k in ("rejected", "leave", "fixed"):
        for s in settled.get(k, []):
            lines.append(f"- [{k}] {s.get('title', '')} —— {s.get('reason', '')}")
    if diffs or work_changed:
        lines += ["", f"## 自第 {rn - 1} 轮以来的改动（regression 镜头只看这些）"]
        lines += [f"- {n}（没有文字层，改动见文件本身）" for n in work_changed
                  if not os.path.isfile(os.path.join(R, "snap", n + ".txt"))]
        if diffs:
            lines += ["```diff"] + diffs + ["```"]
    wtext(readme, "\n".join(lines) + "\n")
    meta.update({"course": a.course, "code": a.code, "last_prep_round": rn, "spec_feed_allowed": not no_feed,
                 "manifest_digest": digest, "last_prep_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M")})
    wjson(os.path.join(D, "审查.json"), meta)
    print(f"✅ 材料已冻结：{os.path.relpath(M, ROOT)}（{len(manifest)} 个文件 · 材料指纹 {digest}）")
    for n, info in files.items():
        bits = [f"{k}={v}" for k, v in info.items() if k not in ("role", "bytes")]
        print(f"   · {n} [{info['role']}] " + " · ".join(bits))
    if same:
        print(f"   ⚠️ 和第 {rn - 1} 轮的材料完全一样 —— 档位也一样的话不用再审，直接看第 {rn - 1} 轮的报告")
    print(f"   README 里「已知且刻意」「已修」两节请补上：{os.path.relpath(readme, ROOT)}")


# ====================================================================== 镜头选择
def load_custom(D, lib):
    p = os.path.join(D, "镜头.json")
    if not os.path.isfile(p):
        return []
    rel = os.path.relpath(p, ROOT)
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        sys.exit(f"❌ {rel} 第 {e.lineno} 行 JSON 语法错：{e.msg}")
    if isinstance(raw, dict):
        raw = raw.get("lenses")
    if not isinstance(raw, list):
        sys.exit(f"❌ {rel} 格式不对：应该是 {{\"lenses\": [ … ]}} 或一个数组")
    by_key = {l["key"]: l for l in lib["lenses"]}
    out = []
    for n, l in enumerate(raw, 1):
        if not isinstance(l, dict) or not l.get("key"):
            sys.exit(f"❌ {rel} 第 {n} 个镜头没有 key")
        if l["key"] in by_key:
            l = dict(by_key[l["key"]], **l)            # 覆盖库里的：没写的字段沿用库里的
        miss = [k for k in ("title", "types", "tiers", "prompt") if not l.get(k)]
        if miss:
            sys.exit(f"❌ {rel} 的镜头 {l['key']} 缺字段：{', '.join(miss)}")
        out.append(l)
    return out


def lens_id(l):
    return l["key"] + (f"__{l['shard']}" if l.get("shard") else "")


def rounds_ok(l, round_no):
    r = l.get("rounds", "all")
    return not ((r == "followup" and round_no <= 1) or (r == "first" and round_no != 1))


def parse_shards(spec):
    out = {}
    for part in spec or []:
        k, _, v = part.partition("=")
        parts = [x.strip() for x in v.split(",") if x.strip()]
        if not k or len(parts) < 2:
            sys.exit(f"❌ --shard 的格式是 <镜头>=分片1,分片2（至少两片），收到 {part}")
        out[k.strip()] = parts
    return out


def pick_lenses(lib, typ, tier, round_no, only=None, add=(), drop=(), extra=(), roles=(), spec_text="", max_finders=6, shards=None):
    """返回 (镜头列表, 跳过说明列表)。默认 = 这类作业的 core + 满足条件的 auto 镜头（预算内）；第 2 轮起加 regression；
    extra 同 key 覆盖库里的。rounds 对 core 和 auto 都生效，--add / --lenses 显式点名的不受限。
    max_finders = 10 个 agent 减去 merge / facts / marker / judge（和 xmodel）。"""
    by_key = {l["key"]: l for l in lib["lenses"]}
    for l in extra:
        by_key[l["key"]] = l                      # 同 key = 覆盖
    notes = []
    for k in list(only or []) + list(add) + list(drop) + list((shards or {}).keys()):
        if k not in by_key:
            notes.append(f"未知镜头 {k}（忽略）")
    if only:
        chosen = [by_key[k] for k in only if k in by_key]
    else:
        chosen = [l for l in by_key.values() if typ in l.get("core", []) and rounds_ok(l, round_no)]
        if round_no > 1 and "regression" in by_key and by_key["regression"] not in chosen:
            chosen.append(by_key["regression"])
        chosen += [l for l in by_key.values() if l.get("auto") and l not in chosen and rounds_ok(l, round_no)]
    chosen += [by_key[k] for k in add if k in by_key and by_key[k] not in chosen]
    chosen = [l for l in chosen if l["key"] not in drop]
    out = []
    for l in chosen:
        types = l.get("types", ["*"])
        if "*" not in types and typ not in types:
            notes.append(f"{l['key']} 不适用于 {typ}（跳过）")
            continue
        if tier not in l.get("tiers", []):
            notes.append(f"{l['key']} 在 {tier} 档不能跑（跳过）")
            continue
        req = l.get("requires") or {}
        if req.get("roles") and not set(req["roles"]) & set(roles):
            notes.append(f"{l['key']} 需要材料 {req['roles']}，没有提供（跳过）")
            continue
        if req.get("spec_regex") and not re.search(req["spec_regex"], spec_text or "", re.I):
            notes.append(f"{l['key']} 的触发条件在 spec 里没找到（跳过）")
            continue
        out.append(l)
    for k, parts in (shards or {}).items():          # 一个镜头切成几片并行跑（例：代码按模块切）
        for l in [x for x in out if x["key"] == k]:
            i = out.index(l)
            out[i:i + 1] = [dict(l, shard=s, prompt=l["prompt"] + f"\n【分片】你只负责「{s}」这一片（共 {len(parts)} 片：{'、'.join(parts)}），别的分片有人审。")
                            for s in parts]
    seen, uniq = set(), []
    for l in out:
        if lens_id(l) not in seen:
            seen.add(lens_id(l))
            uniq.append(l)
    out = uniq
    if not only:
        autos = [l for l in out if l.get("auto") and l["key"] not in add]
        while len(out) > max_finders and autos:
            l = autos.pop()
            out.remove(l)
            notes.append(f"{l['key']} 条件满足，但默认预算只够 {max_finders} 个镜头（跳过；要开就用 --add，并 --drop 一个核心镜头）")
    return out, notes


def lens_context(a):
    D = d_of(a.course, a.code)
    manifest, docs = load_docs(D)
    roles = sorted({m["role"] for m in (manifest or {}).values()})
    spec_text = "\n".join(d["text"] or "" for d in (docs or {}).values() if d["role"] == "spec")
    return D, manifest, docs, roles, spec_text


def same_as_prev(D, rn, tier, hint, typ):
    """材料和上一轮一样、档位 / hint / 类型也一样、上一轮有报告 → 不用再审（终止条件 T3）。"""
    if rn <= 1:
        return False
    mech = rdict(os.path.join(D, f"r{rn}", "mechanical.json"))
    t = tier_of(os.path.join(D, f"r{rn - 1}"))
    return bool(mech.get("same_material_as_prev_round")) and (t.get("tier"), t.get("hint") or "none", t.get("type")) == (tier, hint, typ) and \
        any(os.path.isfile(os.path.join(D, f"报告-r{rn - 1}{s}.md")) for s in ("", "-原始"))


RESULT_PREFIX = ("find-", "merged", "verify-", "judge")


def retire_if_changed(D, R, tier, hint, typ):
    """同一轮换了档位 / hint / 类型：上一次的结果整批挪进 rN.stale-*（guided 的 scrub 也会清那里）。
    🔒 已经按 guided 审过的一轮不能放宽成 allowed —— 那要开新一轮。"""
    old = tier_of(R)
    results = [f for f in os.listdir(R) if f.endswith(".json") and f.startswith(RESULT_PREFIX)] if os.path.isdir(R) else []
    if not old or not results or (old.get("tier"), old.get("hint") or "none", old.get("type")) == (tier, hint, typ):
        return
    rn = os.path.basename(R)[1:]
    if old.get("tier") == "guided" and tier == "allowed":
        sys.exit(f"❌ 第 {rn} 轮已经按 guided 审过，不能在同一轮放宽成 allowed。档位真的放宽了（分级表改过），就 prep --round {int(rn) + 1} 开新一轮")
    stamp = stale_stamp(D, rn)
    dst = f"{R}.stale-{stamp}"
    os.makedirs(dst, exist_ok=True)
    for f in results:
        os.rename(os.path.join(R, f), os.path.join(dst, f))
    for suffix in ("", "-原始"):
        p = os.path.join(D, f"报告-r{rn}{suffix}.md")
        if os.path.isfile(p):
            os.rename(p, os.path.join(D, f"报告-r{rn}{suffix}.stale-{stamp}.md"))
    print(f"⚠️ 第 {rn} 轮换了档位 / hint / 类型：上一次的结果挪到 {os.path.relpath(dst, ROOT)}/", file=sys.stderr)


def cmd_lenses(a):
    D, manifest, docs, roles, spec_text = lens_context(a)
    if not manifest:
        sys.exit("❌ 先 prep")
    R = os.path.join(D, f"r{a.round}")
    if not os.path.isfile(os.path.join(R, "mechanical.json")):
        sys.exit(f"❌ 第 {a.round} 轮还没 prep：先 prep --round {a.round}")
    hint = a.hint if a.tier == "guided" else "none"
    if same_as_prev(D, a.round, a.tier, hint, a.type) and not a.force:
        sys.exit(f"❌ 材料和第 {a.round - 1} 轮完全一样、档位也一样 —— 直接看第 {a.round - 1} 轮的报告（真要重跑加 --force）")
    lib = load_lib()
    ls, notes = pick_lenses(lib, a.type, a.tier, a.round, a.lenses.split(",") if a.lenses else None,
                            a.add.split(",") if a.add else (), a.drop.split(",") if a.drop else (), load_custom(D, lib), roles, spec_text,
                            max_finders=6 - (1 if a.xmodel else 0), shards=parse_shards(a.shard))
    planned = len(ls) + (1 if a.xmodel else 0) + 4
    meta = rdict(os.path.join(D, "审查.json"))
    retire_if_changed(D, R, a.tier, hint, a.type)
    wjson(os.path.join(R, "tier.json"), {"tier": a.tier, "hint": hint, "type": a.type,
                                          "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "by": "lenses"})
    out = {"lenses": ls, "plannedAgents": planned, "manifestDigest": inputs_digest(D, manifest),
           "specFeedAllowed": meta.get("spec_feed_allowed", True), "notes": notes}
    if a.tier == "guided":
        out["knownNumbers"] = sorted(known_numbers(docs or {}, D))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if planned > 10:
        print(f"⚠️ 计划 {planned} 个 agent，超过 10 —— 要学生单独同意大规模运行", file=sys.stderr)


# ====================================================================== checklist（零模型、零网络）
def extract_constraints(text):
    found = []
    pats = [
        (r"[^\n]{0,60}\b\d{1,3}\s*(?:pages?|页)\b[^\n]{0,60}", "页数"),
        (r"[^\n]{0,60}\b\d[\d,]{1,6}\s*(?:words?|字)\b[^\n]{0,60}", "字数"),
        (r"[^\n]{0,40}\b[\w\-<>]+\.(?:pdf|zip|java|sql|py|docx|tex|ipynb)\b[^\n]{0,40}", "文件名 / 格式"),
        (r"[^\n]{0,30}\b(?:due|deadline|截止)\b[^\n]{0,80}", "截止"),
        (r"[^\n]{0,30}\b(?:late|penalt|迟交|扣分)\w*[^\n]{0,100}", "迟交 / 罚则"),
        (r"[^\n]{0,30}\b(?:declar|acknowledg|声明)\w*[^\n]{0,100}", "声明"),
        (r"[^\n]{0,30}\b(?:not (?:allowed|permitted)|must not|forbidden|禁止|不许|不得)\b[^\n]{0,100}", "禁止事项"),
    ]
    for pat, kind in pats:
        for m in re.finditer(pat, text, re.I):
            s = re.sub(r"\s+", " ", m.group(0)).strip()
            if s and (kind, s) not in found:
                found.append((kind, s))
    return found[:40]


def cmd_checklist(a):
    lib = load_lib()
    D = d_of(a.course, a.code)
    types = a.type
    rel = [l for l in lib["lenses"] if l.get("self_check") and ("*" in l.get("types", []) or set(types) & set(l.get("types", [])))]
    rel.sort(key=lambda l: (0 if set(types) & set(l.get("core", [])) else 1, l["key"]))
    out = [f"# {a.course} {a.code} · 交前自查清单", "",
           "> 这份作业的 spec 禁止任何 AI 使用，所以这里没有任何 AI 审查：清单由脚本按模板生成，",
           "> 没有读你的作答内容，也没有调用任何模型。逐条自己核，打勾。", ""]
    spec_text = ""
    for s in a.spec:
        p = abspath(s)
        if os.path.isfile(p):
            spec_text += (pdf_text(p) or "") if p.lower().endswith(".pdf") else rtext(p)
    out.append("## 1. spec 里的硬性条款（脚本按关键词抽的，可能不全 —— 对着 spec 原文补齐）")
    cons = extract_constraints(spec_text) if spec_text else []
    if cons:
        out += [f"- [ ] **{k}**：「{s}」" for k, s in cons]
    else:
        out += ["- [ ] 页数 / 字数上限：________", "- [ ] 文件名和格式：________", "- [ ] 必交的附件和声明：________",
                "- [ ] 截止时间（几点、哪个时区）：________", "- [ ] 迟交规则：________"]
    out += ["", f"## 2. 这类作业（{' + '.join(types)}）的自查项"]
    seen = set()
    for l in rel:
        if l.get("rounds") == "followup":
            continue
        for q in l.get("self_check", []):
            if q not in seen:
                seen.add(q)
                out.append(f"- [ ] {q}  <sub>（{l['title']}）</sub>")
    later = [(q, l) for l in rel if l.get("rounds") == "followup" for q in l.get("self_check", []) if q not in seen]
    if later:
        out += ["", "### 改过之后再核一遍"]
        for q, l in later:
            seen.add(q)
            out.append(f"- [ ] {q}  <sub>（{l['title']}）</sub>")
    mech = None
    for rn in range(9, 0, -1):
        mech = rjson(os.path.join(D, f"r{rn}", "mechanical.json"))
        if isinstance(mech, dict):
            break
    out += ["", "## 3. 机械检查（脚本跑的，不是 AI）"]
    if isinstance(mech, dict):
        for n, info in (mech.get("files") or {}).items():
            bits = [f"{k} {v}" for k, v in info.items() if k in ("pages", "words", "latex_errors", "overfull_hbox", "note")]
            out.append(f"- {n}：" + (" · ".join(map(str, bits)) or "—"))
    else:
        out.append(f"- 还没跑。先 `python3 shared/工具/作业审查.py prep {a.course} {a.code} --files <作答文件>`（只做冻结和计数，不调模型）")
    out += ["", "## 4. 交前只有你能做的",
            "- [ ] 在提交平台上确认上传的是最终版（下载回来打开看一遍）",
            "- [ ] 提交按钮的语义：是「保存草稿」还是「最终提交」？",
            "- [ ] 提交后别再动的东西（服务器上的数据、共享文档的权限）",
            "- [ ] 截图或保存提交回执"]
    path = os.path.join(D, "自查清单.md")
    wtext(path, "\n".join(out) + "\n")
    print(f"✅ 自查清单：{os.path.relpath(path, ROOT)}（零模型、零网络）")


# ====================================================================== scrub / scan（guided 档收口）
def fix_merged_ids(R):
    """merged.json 的编号不合格（重复 / 不是 F01 这种）时，按顺序重编 —— 和 workflow 在内存里的重编规则一致，
    这样 verify-*.json 和 judge.json 里的编号才对得上。返回是否改过。"""
    p = os.path.join(R, "merged.json")
    m = rjson(p)
    if not isinstance(m, dict) or not isinstance(m.get("items"), list):
        return False
    items = [x for x in m["items"] if isinstance(x, dict)]
    ids = [x.get("id") for x in items]
    if all(isinstance(i, str) and re.fullmatch(r"F\d{2,}", i, re.A) for i in ids) and len(set(ids)) == len(ids) and len(items) == len(m["items"]):
        return False
    for n, x in enumerate(items, 1):
        x["id"] = f"F{n:02d}"
    m["items"], m["ids_renumbered"] = items, True
    wjson(p, m)
    return True


def quotes_ok(f, work, auth):
    """引文必须能在作答里逐字找到，规则原句必须能在权威文件里逐字找到 —— 找不到的可能是编的，也可能夹带了答案。"""
    q = loose(f.get("work_quote"))
    if q and not any(q in t for t in work):
        return False
    rq = loose(f.get("rule_quote"))
    return not rq or any(rq in t for t in auth)


def scrub_jsons(D, R, ctx, hint):
    """清洗一个轮次目录里的 JSON。返回 (丢弃条数, 隐去处数)。"""
    _, docs = load_docs(D)
    docs = docs or {}
    work = [loose(d["text"]) for d in docs.values() if d["role"] == "work" and d.get("text")]
    auth = [loose(d["text"]) for d in docs.values() if d["role"] in AUTH_ROLES and d.get("text")]
    fix_merged_ids(R)
    n_drop = n_mask = 0

    def masked(xs):
        nonlocal n_mask
        out = []
        for s in strs(xs):
            if leaky(s, ctx):
                n_mask += 1
                out.append(MASK)
            else:
                out.append(s)
        return out

    def walk(x):                       # 不认识的 JSON：每个字符串叶子都扫
        nonlocal n_mask
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, str) and leaky(x, ctx):
            n_mask += 1
            return MASK
        return x

    for fn in sorted(os.listdir(R)):
        p = os.path.join(R, fn)
        if not fn.endswith(".json") or fn in ("mechanical.json", "manifest.json", "tier.json"):
            continue
        obj = rjson(p)
        if not isinstance(obj, dict):
            continue
        if fn.startswith("find-") or fn == "merged.json":
            key = "findings" if fn.startswith("find-") else "items"
            kept = []
            for f in obj.get(key) if isinstance(obj.get(key), list) else []:
                c = scrub(f, "guided", hint, ctx)
                if not c or c.get("leak_suspect") or not quotes_ok(c, work, auth):
                    n_drop += 1
                    continue
                kept.append(c)
            out = {k: obj[k] for k in ("_fp", "input_key", "lens", "round", "aborted", "dropped_pre", "ids_renumbered") if k in obj}
            out[key] = kept
            for k in ("checked_ok", "not_checked", "coverage", "missing_finders"):
                if k in obj:
                    out[k] = masked(obj[k])
            if "dropped" in obj:
                out["dropped"] = [{"source": str(d.get("source", "")), "reason": str(d.get("reason", ""))}
                                  for d in obj["dropped"] if isinstance(d, dict)] if isinstance(obj["dropped"], list) else []
        elif fn.startswith("verify-"):
            vs = []
            for v in obj.get("verdicts") if isinstance(obj.get("verdicts"), list) else []:
                m = mask_verdict(v, "guided", ctx)
                if m:
                    n_mask += (m["reason"] == MASK) + m["evidence_checked"].count(MASK)
                    vs.append(m)
            out = {k: obj[k] for k in ("_fp", "input_key", "partial") if k in obj}
            out["verdicts"] = vs
        elif fn == "judge.json":
            out = clean_judge(obj, "guided", ctx)
        else:
            out = walk(obj)
        wjson(p, out)
    return n_drop, n_mask


def report_paths(D, rn):
    return [p for p in (os.path.join(D, f"报告-r{rn}.md"), os.path.join(D, f"报告-r{rn}-原始.md")) if os.path.isfile(p)]


def print_hits(path, hits):
    print(f"❌ {os.path.relpath(path, ROOT) if path.startswith(ROOT) else path}：{len(hits)} 行疑似包含答案（guided 档发出去之前必须删掉或改写）：")
    for i, l in hits[:50]:
        print(f"   {i}: {l[:160]}")


def cmd_scrub(a):
    D = d_of(a.course, a.code)
    dirs = round_dirs(D, a.round)
    if not dirs:
        sys.exit(f"❌ 没有 {os.path.relpath(os.path.join(D, f'r{a.round}'), ROOT)}")
    t = tier_of(dirs[0])
    if t.get("tier") == "allowed":
        sys.exit(f"❌ 第 {a.round} 轮记的是 allowed 档（{os.path.relpath(os.path.join(dirs[0], 'tier.json'), ROOT)}），不用 scrub —— scrub 会删掉改法")
    ctx = guided_ctx(D)
    n_drop = n_mask = 0
    for R in dirs:
        d, m = scrub_jsons(D, R, ctx, t.get("hint") or "none")
        n_drop, n_mask = n_drop + d, n_mask + m
    print(f"✅ guided 收口：{len(dirs)} 个目录，丢弃 {n_drop} 条疑似答案或引文对不上的发现，隐去 {n_mask} 处说明")
    bad = 0
    for p in report_paths(D, a.round):
        hits = scan_lines(rtext(p), ctx)
        if hits:
            bad += 1
            print_hits(p, hits)
    if bad:
        sys.exit(1)
    print("✅ 报告里没有发现疑似答案的行")


def cmd_scan(a):
    if not os.path.isfile(a.report):
        sys.exit(f"❌ 找不到 {a.report}")
    if (a.course or a.code) and not os.path.isdir(d_of(a.course or "", a.code or "")):
        sys.exit(f"❌ 没有 {a.course} {a.code} 的审查目录 —— 课程和代号写对了吗？")
    ctx = guided_ctx(d_of(a.course, a.code)) if a.course and a.code else None
    hits = scan_lines(rtext(a.report), ctx)
    if not hits:
        print("✅ 没有发现疑似答案的行")
        return
    print_hits(a.report, hits)
    sys.exit(1)


def cmd_fix_ids(a):
    R = os.path.join(d_of(a.course, a.code), f"r{a.round}")
    print("✅ merged.json 的编号已按顺序重编" if fix_merged_ids(R) else "✅ merged.json 的编号本来就合格，没动")


# ====================================================================== assemble
def cmd_assemble(a):
    D = d_of(a.course, a.code)
    R = os.path.join(D, f"r{a.round}")
    if not os.path.isdir(R):
        sys.exit(f"❌ 没有 {os.path.relpath(R, ROOT)}")
    t = tier_of(R)
    if a.tier == "allowed" and t.get("tier") == "guided":
        sys.exit(f"❌ 第 {a.round} 轮记的是 guided 档，assemble 不能把它放宽成 allowed")
    tier = a.tier or t.get("tier")
    if tier not in ("allowed", "guided"):
        tier = "guided"                               # 不知道档位时按严的来
    hint = (t.get("hint") or "none") if tier == "guided" else "none"
    finds = sorted(f for f in os.listdir(R) if f.startswith("find-") and f.endswith(".json"))
    if not os.path.isfile(os.path.join(R, "merged.json")) and not finds:
        sys.exit(f"❌ 第 {a.round} 轮还没审过（没有 merged.json，也没有任何 find-*.json）—— 没有东西可拼")
    renumbered = fix_merged_ids(R)
    merged = rjson(os.path.join(R, "merged.json"))
    if merged is not None and not isinstance(merged, dict):
        sys.exit("❌ merged.json 格式不对（应该是一个对象）")
    _, docs = load_docs(D)
    ctx = leak_ctx(docs, D) if tier == "guided" else None
    verdicts = {}
    for f in sorted(os.listdir(R)):
        if f.startswith("verify-") and f.endswith(".json"):
            obj = rdict(os.path.join(R, f))
            for v in obj.get("verdicts") if isinstance(obj.get("verdicts"), list) else []:
                if isinstance(v, dict) and isinstance(v.get("finding_id"), str):
                    verdicts.setdefault(v["finding_id"], []).append((f[7:-5].split("-b")[0], v))
    if merged is not None:
        items = [x for x in merged.get("items") or [] if isinstance(x, dict)] if isinstance(merged.get("items"), list) else []
        missing = strs(merged.get("missing_finders"))
        dropped = [d for d in merged.get("dropped") or [] if isinstance(d, dict)] if isinstance(merged.get("dropped"), list) else []
    else:                                           # 只有 merge 没跑成时才退回去读 finder 原始结果
        raw = []
        for f in finds:
            obj = rdict(os.path.join(R, f))
            for i, x in enumerate(obj.get("findings") if isinstance(obj.get("findings"), list) else []):
                x = sanitize(x)
                if x:
                    raw.append(dict(x, lens=f[5:-5], source=f"{f[5:-5]}#{i}"))
        raw = [c for c in (scrub(x, tier, hint, ctx) for x in raw) if c]
        items, dropped = local_merge(raw, docs or {}, tier, read_settled(D))
        missing = []
    judge_ok = isinstance(rjson(os.path.join(R, "judge.json")), dict)
    out = [f"# {a.course} {a.code} · 第 {a.round} 轮 · 原始清单（机械拼出，未经判官整理）", ""]
    if missing or not judge_ok:
        out.append("⚠️ 结论不完整：" + "；".join(([f"{len(missing)} 个镜头没跑成（{'、'.join(missing)}）"] if missing else [])
                                         + ([] if judge_ok else ["判官没跑成"])))
    if tier == "guided":
        out.append("⚠️ 按这份作业的 AI 政策，本清单只列位置和原因，不含改法，也不含核实员的说明。")
    out += [f"merge：{'有' if merged is not None else '没有（退回 finder 原始结果，已过滤）'} · 判官：{'有' if judge_ok else '没有'}"
            + (" · merged.json 的编号不合格，已按顺序重编" if renumbered else "")]
    if dropped:
        by = {}
        for d in dropped:
            by[str(d.get("reason", "?"))] = by.get(str(d.get("reason", "?")), 0) + 1
        out.append("合并时剔除：" + " · ".join(f"{k} {v} 条" for k, v in sorted(by.items())))
    n = 0
    for it in items:
        it = scrub(it, tier, hint, ctx)
        if not it or it.get("leak_suspect"):
            continue
        n += 1
        out += ["", f"## {it.get('id', '')} · {it.get('title', '')} · {it.get('severity', '')} · {it.get('marks_at_risk', '')} 分",
                f"- 位置：{where_of(it)}",
                f"- 作答原文：「{it.get('work_quote', '')}」", rule_line(it, tier, ctx),
                f"- 问题：{it.get('what_is_wrong', '')}", f"- 为什么扣分：{it.get('why_it_costs', '')}"]
        for stage, v in verdicts.get(it.get("id"), []):
            line = f"- {stage}：{'成立' if v.get('keep') is True else '不成立'}"
            if tier != "guided":
                line += f" —— {v.get('reason', '')}"
            out.append(line)
    text = "\n".join(out) + "\n"
    if tier == "guided":
        text = mask_lines(text, ctx)
    path = os.path.join(D, f"报告-r{a.round}-原始.md")
    wtext(path, text)
    print(f"✅ {os.path.relpath(path, ROOT)}（{n} 条，档位 {tier}）")


def where_of(it):
    loc = it.get("location") if isinstance(it.get("location"), dict) else {}
    return (f"{loc.get('file', '')}" + (f":{loc.get('line_start')}" if loc.get("line_start") else "")
            + (f" p.{loc.get('page')}" if loc.get("page") else "") + (f" {loc.get('question')}" if loc.get("question") else ""))


def rule_line(it, tier, ctx):
    """guided 档只回显 spec / faq / rubric 里能逐字找到的原句；其他来源（课件、参考答案、起始代码）只给出处。"""
    rs = it.get("rule_source") if isinstance(it.get("rule_source"), dict) else {}
    rq = it.get("rule_quote") or ""
    where = f"{rs.get('doc', '')} {rs.get('locator', '')}".strip()
    if tier == "guided" and not (rq and rs.get("authority") in RULE_AUTHORITY and ctx and any(loose(rq) in t for t in ctx.rule)):
        return f"- 依据：{where}（原句见材料）"
    return f"- 依据：{where}：「{rq}」"


# ====================================================================== run（任意 OpenAI 兼容模型）
FORMAT = """只回一个 JSON 对象，不要任何其他文字，不要代码块。格式：
{"lens": "<镜头 key>", "findings": [ {
   "title": "≤ 120 字",
   "severity": "zero | deduct | risk | polish",
   "marks_at_risk": 数字,
   "location": {"file": "文件名", "line_start": 行号, "line_end": 行号, "page": 页码, "question": "题号"},
   "work_quote": "作答里的逐字原文（≤ 200 字符，必须能在作答里原样找到）",
   "rule_quote": "权威文件里的逐字原句（≤ 300 字符，必须能在权威文件里原样找到）",
   "rule_source": {"doc": "文件名", "locator": "页 / 节", "authority": "spec | official-faq | rubric | slides | reference-only"},
   "what_is_wrong": "…", "why_it_costs": "…", "evidence": ["你依据的是哪几行 / 哪几页"],
   "needs_execution": 这个判断是否需要真的运行代码才能确定（true / false），
   "confidence": 0 到 1%s
 } ],
 "checked_ok": ["查过且没问题的"], "not_checked": ["没查到的"] }"""
FIX_FMT = """,
   "suggested_fix": "改法说明", "old_text": "源文件里逐字且只出现一次的原文", "new_text": "替换成什么（同长或更短）", "adds_lines": false"""
Q_FMT = """,
   "guiding_question": "一个引导性的问题（只许是问题，不许含答案）\""""


def tier_block(tier, hint):
    if tier == "allowed":
        return "【档位 allowed】这份作业允许 AI：可以给改法。old_text 必须在源文件里逐字出现且只出现一次；new_text 同长或更短。"
    return ("【档位 guided】这份作业不允许 AI 产出最终答案：只指出位置、违反的规则、为什么会扣分。绝对不许写出正确的值、表达式、句子、代码或推导，"
            "不许用「应该是 / 改成 / should be / replace with」这类说法；evidence 只写位置，不写算出来的值；checked_ok / not_checked 只写题号和检查项名称，不写值。"
            + ("可以给一个只含问题、不含答案的 guiding_question。" if hint == "question" else ""))


class LLMJSONError(Exception):
    def __init__(self, msg, usage):
        super().__init__(msg)
        self.usage = usage


def _repair(t):
    """常见的「差一点就是 JSON」：字符串外面的 // 行注释、结尾多逗号。字符串里面的内容一个字不动。"""
    out, i, n, in_str = [], 0, len(t), False
    while i < n:
        c = t[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(t[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif t.startswith("//", i):
            while i < n and t[i] != "\n":
                i += 1
            continue
        elif c == ",":
            k = i + 1
            while k < n and t[k] in " \t\r\n":
                k += 1
            if k < n and t[k] in "}]":
                i += 1
                continue
            out.append(c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def extract_json(text, want=("findings", "verdicts", "items")):
    """找出带 want 里某个键的 JSON 对象。找不到就抛错 —— 绝不把别的对象当成「零条发现」返回。"""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)     # 代码围栏不剥：从每个 { 开始试解析就够了，剥了会连字段值里的代码块一起剥掉
    dec = json.JSONDecoder()
    for cand in (text, _repair(text)):
        st = cand.strip()
        if st.startswith("[") and len(want) == 1:          # 顶层直接给了数组：包一层
            try:
                obj, _ = dec.raw_decode(st)
                if isinstance(obj, list) and all(isinstance(x, dict) for x in obj):
                    return {want[0]: obj}
            except json.JSONDecodeError:
                pass
        for i, ch in enumerate(cand):
            if ch != "{":
                continue
            try:
                obj, _ = dec.raw_decode(cand[i:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and any(k in obj for k in want):
                return obj
    raise ValueError(f"回复里没有带 {' / '.join(want)} 的 JSON 对象")


def ask_json(client, system, user, model, want, tries=2):
    last, usage = None, {"prompt_tokens": 0, "completion_tokens": 0}
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for _ in range(tries):
        msg, u = client.chat(msgs, model=model)
        for k in usage:
            usage[k] += int((u or {}).get(k) or 0)
        try:
            return extract_json(msg.get("content") or "", want), usage
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            msgs += [{"role": "assistant", "content": msg.get("content") or ""},
                     {"role": "user", "content": f"上面的回复不是合法 JSON（{e}）。只回 JSON 对象，不要任何其他文字。"}]
    raise LLMJSONError(f"两次都没拿到合法 JSON：{last}", usage)


def sanitize(f):
    """模型给的字段类型不可信：统一成脚本能处理的形状。"""
    if not isinstance(f, dict):
        return None
    out = {k: v for k, v in f.items() if k not in META_KEYS}      # id / lens / source / fix_note 这些只能由脚本写
    loc = out.get("location") if isinstance(out.get("location"), dict) else {}
    out["location"] = {"file": str(loc.get("file") or ""), "line_start": as_int(loc.get("line_start")),
                       "line_end": as_int(loc.get("line_end")), "page": as_int(loc.get("page")), "question": str(loc.get("question") or "")}
    out["rule_source"] = out["rule_source"] if isinstance(out.get("rule_source"), dict) else {}
    for k in ("title", "work_quote", "rule_quote", "what_is_wrong", "why_it_costs", "suggested_fix", "old_text", "new_text", "guiding_question"):
        if k in out and not isinstance(out[k], str):
            out[k] = "" if out[k] is None else str(out[k])
    out["marks_at_risk"] = as_float(out.get("marks_at_risk"))
    out["confidence"] = as_float(out.get("confidence"))
    ev = out.get("evidence")
    out["evidence"] = [str(e) for e in ev] if isinstance(ev, list) else ([str(ev)] if ev else [])
    if out.get("severity") not in SEV:
        out["severity"] = "risk"
    return out


def snippet(docs, it, radius=8):
    loc = it.get("location") or {}
    d = docs.get(loc.get("file") or "")
    if not d or not d.get("text"):
        return ""
    lines = d["text"].split("\n")
    s = max(1, (loc.get("line_start") or 1) - radius)
    e = min(len(lines), (loc.get("line_end") or loc.get("line_start") or s) + radius)
    return "\n".join(f"{i:>5}| {lines[i - 1]}" for i in range(s, e + 1))


def quoted_segments(text):
    """引号里的文字：「」和“”按配对取；直引号按出现顺序两两配对（不会把两段引文中间的话当成引文）。"""
    segs = [a or b for a, b in re.findall(r"「([^」\n]+)」|“([^”\n]+)”", text)]
    for line in text.split("\n"):
        pos = [i for i, c in enumerate(line) if c == '"']
        segs += [line[a + 1:b] for a, b in zip(pos[0::2], pos[1::2])]
    return segs


def local_merge(raw, docs, tier, settled):
    work_text = {n: loose(d["text"]) for n, d in docs.items() if d["role"] == "work" and d.get("text")}
    auth_text = {n: loose(d["text"]) for n, d in docs.items() if d["role"] in AUTH_ROLES and d.get("text")}
    settled_titles = [norm(s.get("title", "")) for k in ("rejected", "leave", "fixed") for s in settled.get(k, [])]
    items, dropped = [], []
    for f in raw:
        src = f.get("source")
        if f.get("leak_suspect"):
            dropped.append({"source": src, "reason": "leak-suspect", "note": "guided 档：疑似给了答案"})
            continue
        q = loose(f.get("work_quote"))
        if q and not work_text:
            dropped.append({"source": src, "reason": "no-work-text", "note": "作答没有文字层，引文核不了"})
            continue
        if q and not any(q in t for t in work_text.values()):
            dropped.append({"source": src, "reason": "quote-not-found", "note": "引文在作答里找不到"})
            continue
        rq = loose(f.get("rule_quote"))
        if rq and auth_text and not any(rq in t for t in auth_text.values()):
            dropped.append({"source": src, "reason": "rule-quote-not-found", "note": "规则原句在权威文件里找不到"})
            continue
        if rq and not auth_text and tier == "guided":
            dropped.append({"source": src, "reason": "rule-quote-unverifiable", "note": "没有权威文件可以核对规则原句"})
            continue
        quoted = [q for k in ("title", "what_is_wrong", "why_it_costs") for q in quoted_segments(f.get(k) or "")
                  if len(q) >= 15 and len(q.split()) > 3]
        if any(not any(loose(s) in t for t in list(work_text.values()) + list(auth_text.values())) for s in quoted):
            dropped.append({"source": src, "reason": "quote-not-verbatim", "note": "说明里引号中的文字既不是作答原文，也不是权威文件原文"})
            continue
        if tier == "allowed" and f.get("old_text"):
            file_text = (docs.get((f.get("location") or {}).get("file") or "") or {}).get("text") or ""
            if file_text.count(f["old_text"]) != 1:
                f = {k: v for k, v in f.items() if k not in ("old_text", "new_text")}
                f["fix_note"] = "改法的 old_text 在源文件里不是恰好一次，已去掉补丁，只保留问题"
        if any(difflib.SequenceMatcher(None, norm(f.get("title")), t).ratio() > 0.8 for t in settled_titles if t):
            dropped.append({"source": src, "reason": "settled", "note": "和已裁定的条目重复"})
            continue
        dup = None
        for it in items:
            la, lb = it.get("location") or {}, f.get("location") or {}
            same_file = la.get("file") == lb.get("file")
            near = abs((la.get("line_start") or 0) - (lb.get("line_start") or 0)) <= 3 or (la.get("question") and la.get("question") == lb.get("question"))
            similar = difflib.SequenceMatcher(None, norm(it.get("title")), norm(f.get("title"))).ratio() > 0.6
            if same_file and near and similar:
                dup = it
                break
        if dup:
            dup["raised_by"] += 1
            dup["merged_from"].append(src)
            dup["evidence"] = list(dict.fromkeys((dup.get("evidence") or []) + (f.get("evidence") or [])))
            continue
        items.append(dict(f, raised_by=1, merged_from=[src]))
    items.sort(key=lambda x: -as_float(x.get("marks_at_risk")))
    for i, it in enumerate(items, 1):
        it["id"] = f"F{i:02d}"
    return items, dropped


def cmd_run(a):
    if a.tier == "forbidden":
        sys.exit("❌ forbidden 档不跑 AI 审查（spec 禁止任何 AI 使用）。用：python3 shared/工具/作业审查.py checklist …")
    if a.tier != a.confirm_tier:
        sys.exit("❌ --tier 和 --confirm-tier 不一致。档位必须由你亲手确认两次，脚本不会替你选更宽的档。")
    D = d_of(a.course, a.code)
    R = os.path.join(D, f"r{a.round}")
    manifest, docs = load_docs(D)
    if not manifest:
        sys.exit(f"❌ 还没冻结材料。先：python3 shared/工具/作业审查.py prep {a.course} {a.code} --files <作答文件> --spec <题面> --round {a.round}")
    for name, d in docs.items():
        if sha256(os.path.join(D, "材料", name)) != d["sha256"]:
            sys.exit(f"❌ {name} 在 prep 之后被改过（哈希不一致）—— 重新 prep 再审")
    work_docs = {n: d for n, d in docs.items() if d["role"] == "work"}
    if not any((d.get("text") or "").strip() for d in work_docs.values()):
        mech_any = rdict(os.path.join(R, "mechanical.json")).get("files") or {}
        why = "；".join(f"{n}：{(mech_any.get(n) or {}).get('note', '没有文字')}" for n in work_docs)
        sys.exit(f"❌ 被审的作答没有可读的文字（{why}）—— 简化版审不了，转成文字或用 Claude Code 版")
    meta = rdict(os.path.join(D, "审查.json"))
    if meta.get("last_prep_round") not in (None, a.round) and not a.force:
        sys.exit(f"❌ 最近一次 prep 是第 {meta['last_prep_round']} 轮，这里是 --round {a.round} —— 加 --round {meta['last_prep_round']}（真要审第 {a.round} 轮加 --force）")
    if not os.path.isfile(os.path.join(R, "mechanical.json")):
        sys.exit(f"❌ 第 {a.round} 轮还没 prep：先 prep --round {a.round}")
    if meta.get("spec_feed_allowed") is False and any(d["role"] == "spec" and not n.endswith("-要点.md") for n, d in docs.items()):
        sys.exit("❌ 这份作业不许把整份 spec 喂给 AI，但材料里有 spec 全文 —— 重新 prep，--spec 只给要点文件")
    if same_as_prev(D, a.round, a.tier, a.hint if a.tier == "guided" else "none", a.type) and not a.force:
        sys.exit(f"❌ 材料和上一轮完全一样、档位也一样 —— 直接看第 {a.round - 1} 轮的报告（真要重跑加 --force）")

    sys.path.insert(0, ROOT)
    from runtime import config as rc      # 只有 run 才加载模型相关的模块
    from runtime.llm import Client
    try:
        cfg = rc.resolve()
    except rc.ConfigError as e:
        sys.exit(f"❌ {e}")
    env = rc.load_dotenv()
    fan_model = rc.get("REVIEW_MODEL", env) or cfg["model"]
    judge_model = rc.get("REVIEW_JUDGE_MODEL", env) or cfg["model"]
    conc = max(1, min(8, as_int(rc.get("REVIEW_CONCURRENCY", env, "4")) or 4))
    max_chars = as_int(rc.get("REVIEW_MAX_CHARS", env, "120000")) or 120000

    roles = sorted({d["role"] for d in docs.values()})
    spec_text = "\n".join(d["text"] or "" for d in docs.values() if d["role"] == "spec")
    lib = load_lib()
    lenses, notes = pick_lenses(lib, a.type, a.tier, a.round, a.lenses.split(",") if a.lenses else None,
                                a.add.split(",") if a.add else (), a.drop.split(",") if a.drop else (), load_custom(D, lib), roles, spec_text)
    if not lenses:
        sys.exit("❌ 这个类型 / 档位 / 材料下没有可用的镜头")
    static = [l["key"] for l in lenses if set(l.get("needs", [])) & {"run", "db", "images", "raw-data"}]
    guided = a.tier == "guided"
    settled = read_settled(D)                     # 格式不对就在调模型之前退出
    ctx = leak_ctx(docs, D) if guided else None
    run_fp = fp(inputs_digest(D, manifest), a.tier, a.hint, a.round, a.type, fan_model, judge_model, max_chars)

    # 材料：作答在前、权威文件在后 —— finder 看全部；verify 只看 README + 权威文件 + 本批相关片段
    readme = rtext(os.path.join(D, "材料", "README.txt"))
    truncated = []

    def numbered(n):
        return f"### [{docs[n]['role']}] {n}\n" + "\n".join(f"{i:>5}| {l}" for i, l in enumerate(docs[n]["text"].split("\n"), 1))

    def pack(names, budget):
        parts, used = [], 0
        for n in names:
            if not docs[n].get("text"):
                truncated.append(f"{n}：没有文字层，本实现看不到")
                continue
            piece = numbered(n)
            room = max(0, budget - used)
            if len(piece) > room:
                truncated.append(f"{n}：超出字数上限，只发了前 {room} 个字符")
                piece = piece[:room]
            used += len(piece)
            if piece:
                parts.append(piece)
        return parts, used

    order = ["work", "spec", "faq", "rubric", "slides", "reference", "starter", "raw-data"]
    names = sorted(docs, key=lambda n: order.index(docs[n]["role"]) if docs[n]["role"] in order else 99)
    work_names = [n for n in names if docs[n]["role"] == "work"]
    other = [n for n in names if docs[n]["role"] != "work"]
    budget = max_chars - len(readme)
    reserve = min(sum(len(numbered(n)) for n in other if docs[n].get("text")), budget // 2)   # 作答再长也给题面留一半
    wparts, wused = pack(work_names, budget - reserve)
    oparts, _ = pack(other, budget - wused)
    blob = "\n\n".join([f"### README.txt\n{readme}"] + wparts + oparts)
    auth_blob = "\n\n".join([f"### README.txt\n{readme}"] + pack([n for n in names if docs[n]["role"] in AUTH_ROLES], max_chars // 2)[0])
    truncated = list(dict.fromkeys(truncated))
    est = int(len(blob) / 3.5 * len(lenses) + len(auth_blob) / 3.5 * 3)

    print(f"将把这些文件发给 {cfg['name']}（扇出 {fan_model} · 判官 {judge_model}）：")
    for n, d in docs.items():
        print(f"   · [{d['role']}] {n}")
    print(f"档位 {a.tier}{'（hint=question）' if a.hint == 'question' else ''} · 类型 {a.type} · 镜头 {', '.join(lens_id(l) for l in lenses)}")
    for n in notes:
        print(f"   · {n}")
    if static:
        print(f"   ⚠️ 这些镜头需要运行代码 / 数据库 / 看图 / 原始数据，本实现只能静态推理：{', '.join(static)}")
    print(f"约 {len(lenses) + 3} 次模型调用，输入约 {est:,} token（粗估）。")
    if not a.yes and input("继续？[y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("已取消")
    retire_if_changed(D, R, a.tier, a.hint if guided else "none", a.type)
    wjson(os.path.join(R, "tier.json"), {"tier": a.tier, "hint": a.hint if guided else "none", "type": a.type,
                                          "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "by": "run"})
    for fn in os.listdir(R):                       # 这次不跑的镜头留下的旧结果：删掉，免得 assemble 拿错
        if fn.startswith("find-") and fn.endswith(".json") and fn[5:-5] not in {lens_id(l) for l in lenses}:
            os.remove(os.path.join(R, fn))
    refresh = {"find": ("find-", "merged"), "verify": ("verify-",), "judge": ("judge",), "all": ("find-", "merged", "verify-", "judge")}.get(a.refresh, ())
    for fn in os.listdir(R) if refresh else []:
        if fn.endswith(".json") and fn.startswith(refresh):
            os.remove(os.path.join(R, fn))

    client = Client(cfg["base_url"], cfg["api_key"], cfg["model"], stream=False, timeout=600)
    tb = tier_block(a.tier, a.hint)
    fmt = FORMAT % (FIX_FMT if a.tier == "allowed" else (Q_FMT if a.hint == "question" else ""))
    common = (f"你是严格但公平的阅卷人，在学生提交前预审「{a.course} {a.code}」（类型 {a.type}），第 {a.round} 轮。"
              "先读 README：权威文件和角色、「已知且刻意」（不许报）、「已修」、「已裁定」（没有新证据不许重开）；README 说没有提供的材料，相关判断写进 not_checked。"
              "每条 finding 必须带作答的逐字引文、精确位置（行号见每行开头）和权威规则原句；不许发明要求。"
              "返回前对每条自问：阅卷人真的会因此扣分吗？答不上来就别报。零条是合法答案。"
              "你不能运行代码、不能看图片：依赖运行结果或图片才能确定的判断，needs_execution 填 true。\n" + tb)
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

    def add_usage(u):
        for k in usage_total:
            usage_total[k] += int((u or {}).get(k) or 0)

    def cached(path, key):
        obj = rjson(path)
        return obj if isinstance(obj, dict) and obj.get("_fp") == key and not obj.get("partial") else None

    # ---- Find
    def run_lens(l):
        path = os.path.join(R, f"find-{lens_id(l)}.json")
        key = fp(run_fp, l)
        hit = cached(path, key)
        if hit:
            return lens_id(l), hit, None, None
        sysmsg = common + f"\n\n【本镜头：{lens_id(l)} · {l['title']}】{l['prompt']}\n最多 {l.get('max_findings', 6)} 条。\n\n" + fmt
        try:
            res, u = ask_json(client, sysmsg, blob, fan_model, ("findings",))
        except LLMJSONError as e:
            return lens_id(l), None, str(e), e.usage
        except Exception as e:  # noqa: BLE001
            return lens_id(l), None, str(e), None
        fs, pre = [], []             # 🔒 guided：疑似答案、或引文对不上（可能夹带答案）的发现不落盘，只记原因
        for f in res.get("findings") if isinstance(res.get("findings"), list) else []:
            c = scrub(sanitize(f), a.tier, a.hint, ctx)
            if not c:
                continue
            if c.get("leak_suspect"):
                pre.append("leak-suspect")
            elif guided and not quotes_ok(c, ctx.work, ctx.auth):
                q = loose(c.get("work_quote"))
                pre.append("quote-not-found" if q and not any(q in t for t in ctx.work)
                           else ("rule-quote-not-found" if ctx.auth else "rule-quote-unverifiable"))
            else:
                fs.append(c)
        co, nc = strs(res.get("checked_ok")), strs(res.get("not_checked"))
        if guided:
            co, nc = [MASK if leaky(c, ctx) else c for c in co], [MASK if leaky(c, ctx) else c for c in nc]
        out = {"_fp": key, "lens": lens_id(l), "findings": fs, "dropped_pre": pre, "checked_ok": co, "not_checked": nc}
        wjson(path, out)            # 🔒 落盘的已经是过滤后的版本
        return lens_id(l), out, None, u

    print("· Find …")
    raw, missing, coverage, not_checked, leak_drops = [], [], [], list(truncated), []
    with cf.ThreadPoolExecutor(conc) as ex:
        for key, res, err, u in ex.map(run_lens, lenses):
            add_usage(u)
            if not res:
                missing.append(f"{key}（{err}）")
                continue
            for i, f in enumerate(res.get("findings") or []):
                raw.append(dict(f, lens=key, source=f"{key}#{i}"))
            leak_drops += [{"source": f"{key}#pre{i}", "reason": r, "note": "guided 档：落盘前已拿掉"}
                           for i, r in enumerate(strs(res.get("dropped_pre")))]
            coverage += [f"{key}: {c}" for c in strs(res.get("checked_ok"))]
            not_checked += [f"{key}: {c}" for c in strs(res.get("not_checked"))]
    if len(missing) == len(lenses):
        print(f"❌ 所有镜头都没跑成（{'；'.join(missing)[:400]}）—— 不出报告")
        sys.exit(1)
    items, dropped = local_merge(raw, docs, a.tier, settled)
    dropped = leak_drops + dropped
    wjson(os.path.join(R, "merged.json"), {"_fp": fp(run_fp, raw), "round": a.round, "items": items, "dropped": dropped,
                                            "coverage": coverage, "missing_finders": missing})
    print(f"· Merge：原始 {len(raw)} 条 → {len(items)} 条（剔除 {len(dropped)} 条）")

    # ---- Verify（facts → 只把成立的给 marker；缺票记 incomplete，不算驳回）
    verdicts, incomplete = {}, set()
    to_verify = [it for it in items if it.get("severity") != "polish"]

    def strip_item(it):
        c = {k: v for k, v in it.items() if k not in ("confidence", "raised_by", "merged_from", "source")}
        if guided:
            c.pop("evidence", None)
        return c

    def verify(stage, batch, extra):
        ctx_s = "\n\n".join(f"[{it['id']}] 作答上下文：\n{snippet(docs, it)}" for it in batch)
        sysmsg = (f"你是{'事实核实员（facts）' if stage == 'facts' else '阅卷判断员（marker）'}。" + extra + "\n" + tb +
                  '\n只回 JSON：{"verdicts": [{"finding_id": "F01", "keep": true/false, "reason": "≤ 3 句", "evidence_checked": ["…"],'
                  ' "severity_adjusted": "zero|deduct|risk|polish|none", "marks_at_risk_adjusted": 数字, "p_deduct": 0~1'
                  + (', "fix_is_safe": true/false, "better_fix": "…"' if stage == "marker" and a.tier == "allowed" else "") + "}]}")
        user = (f"待判断：\n{json.dumps([strip_item(it) for it in batch], ensure_ascii=False)}\n\n{ctx_s}\n\n"
                f"README 和权威文件：\n{auth_blob}")
        res, u = ask_json(client, sysmsg, user, fan_model, ("verdicts",))
        add_usage(u)
        want = {it["id"] for it in batch}
        return [v for v in (res.get("verdicts") if isinstance(res.get("verdicts"), list) else []) if isinstance(v, dict) and v.get("finding_id") in want]

    produced = set()

    def staged(stage, batches, extra):
        for k, b in enumerate(batches, 1):
            path = os.path.join(R, f"verify-{stage}-b{k}.json")
            produced.add(os.path.basename(path))
            key = fp(run_fp, stage, b)
            hit = cached(path, key)
            vs = hit.get("verdicts") if hit else None
            if vs is None:
                try:
                    vs = [m for m in (mask_verdict(v, a.tier, ctx) for v in verify(stage, b, extra)) if m]
                    got = {v["finding_id"] for v in vs}
                    wjson(path, {"_fp": key, "verdicts": vs, "partial": not {it["id"] for it in b} <= got})   # 回票不全不当缓存
                except LLMJSONError as e:
                    add_usage(e.usage)
                    print(f"   ⚠️ {stage} 批次 {k} 失败：{e}（这几条记为票没收齐，不算驳回）")
                    vs = []
                except Exception as e:  # noqa: BLE001
                    print(f"   ⚠️ {stage} 批次 {k} 失败：{e}（这几条记为票没收齐，不算驳回）")
                    vs = []
            for v in vs if isinstance(vs, list) else []:
                if isinstance(v, dict) and isinstance(v.get("finding_id"), str):
                    verdicts.setdefault(v["finding_id"], {})[stage] = v
            for it in b:
                if stage not in verdicts.get(it["id"], {}):
                    incomplete.add(it["id"])

    if to_verify:
        print(f"· Verify：facts {len(to_verify)} 条 …")
        staged("facts", [to_verify[i:i + 8] for i in range(0, len(to_verify), 8)],
               "对每条发现，只根据给你的原文核实它描述的事实是否成立：引文是否在那个位置、规则原句是否真的这么写、推理是否成立。证据不足就判 keep=false（默认不成立）。")
        kept = [it for it in to_verify if (verdicts.get(it["id"], {}).get("facts") or {}).get("keep") is True]
        if kept:
            print(f"· Verify：marker {len(kept)} 条 …")
            staged("marker", [kept[i:i + 8] for i in range(0, len(kept), 8)],
                   "这些发现的事实已核实成立。照 rubric / spec / 官方答疑判断：阅卷人会不会因此扣分？扣多少？概率多大？没有规则支撑的推测一律 keep=false。"
                   + ("同时检查改法是否安全：改后是否仍然回答题目、结论是否仍然明确、是否没有变长、是否和别处不矛盾。" if a.tier == "allowed" else ""))

    for fn in os.listdir(R):                   # 上一次多出来的批次文件：删掉，免得 assemble 把旧票配到新编号上
        if fn.startswith("verify-") and fn.endswith(".json") and fn not in produced:
            os.remove(os.path.join(R, fn))

    # ---- Judge（只分级；报告由脚本渲染）
    print("· Judge …")
    judge_path = os.path.join(R, "judge.json")
    if not items and os.path.isfile(judge_path):
        os.remove(judge_path)
    v_for_judge = {i: ({k: {kk: vv for kk, vv in v.items() if kk in ("keep", "severity_adjusted", "marks_at_risk_adjusted", "p_deduct")}
                        for k, v in st.items()} if guided else st) for i, st in verdicts.items()}
    jkey = fp(run_fp, [strip_item(i) for i in items], v_for_judge, sorted(incomplete))
    judge = cached(judge_path, jkey)
    if judge is None and items:
        sysmsg = ("你是判官，给每条发现分级。规则：must_fix = facts 成立 + marker 会扣 + 级别 zero/deduct（或 risk 且期望损失高）；"
                  "should_fix = 成立且会扣但只是小分、修法便宜；leave = 成立但不会扣，或修法成本高于期望损失（理由必填）；"
                  "rejected = facts 不成立；没有 facts 或 marker 结论的 = incomplete（名单见输入）；polish 级 = unverified。"
                  "暂定分只给区间。不许写「可以交了 / 定稿」。\n" + tb +
                  '\n只回 JSON：{"items": [{"id": "F01", "class": "must_fix|should_fix|leave|rejected|unverified|incomplete", "expected_loss": 数字,'
                  ' "fix_cost": "low|medium|high", "note": "一句理由"}], "mark_range": {"low": 数字, "high": 数字, "caveat": "…"},'
                  ' "not_covered": ["本报告判断不了的事"], "operations_checklist": ["交前只有学生本人能做的动作"]}')
        try:
            j, u = ask_json(client, sysmsg, json.dumps({"items": [strip_item(i) for i in items], "verdicts": v_for_judge,
                                                        "incomplete": sorted(incomplete)}, ensure_ascii=False), judge_model, ("items",))
            add_usage(u)
            j = clean_judge(j, a.tier, ctx)
            if not j["items"]:
                raise ValueError("判官没有给出任何一条有效的分级")
            j["_fp"] = jkey
            wjson(judge_path, j)
            judge = j
        except LLMJSONError as e:
            add_usage(e.usage)
            print(f"   ⚠️ 判官失败：{e} —— 分级改按核实结果机械推；也可以用 assemble 出原始清单")
        except Exception as e:  # noqa: BLE001
            print(f"   ⚠️ 判官失败：{e} —— 分级改按核实结果机械推；也可以用 assemble 出原始清单")
    judge_failed = bool(items) and judge is None
    try:
        report = render_report(a, items, dropped, verdicts, judge or {}, judge_failed, incomplete, missing, coverage, not_checked,
                               static, cfg, fan_model, judge_model, docs, ctx)
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠️ 报告渲染失败：{e} —— 改出原始清单")
        cmd_assemble(argparse.Namespace(course=a.course, code=a.code, round=a.round, tier=a.tier))
        sys.exit(1)
    path = os.path.join(D, f"报告-r{a.round}.md")
    wtext(path, report)
    must = sum(1 for it in items if classify(it, verdicts, judge or {}, incomplete) == "must_fix")
    tail = f"报告：{os.path.relpath(path, ROOT)} · 必改 {must} 条 · tokens 输入 {usage_total['prompt_tokens']:,} / 输出 {usage_total['completion_tokens']:,}"
    if missing:
        print(f"⚠️ {len(missing)}/{len(lenses)} 个镜头没跑成，结论不完整。{tail}")
    else:
        print(f"✅ {tail}")


def classify(it, verdicts, judge, incomplete):
    """分级：核实账目是硬约束，判官只能在它允许的范围里分。
    polish → 未核实；没有 facts 票 → 票没收齐；facts 不成立 → 驳回；成立但没有 marker 票 → 票没收齐；
    其余用判官的分级；判官没给（或挂了）→ 按 marker 机械推。"""
    i = it["id"]
    v = verdicts.get(i, {})
    f, mk = v.get("facts"), v.get("marker")
    if it.get("severity") == "polish":
        return "unverified"
    if not f:
        return "incomplete"
    if f.get("keep") is not True:
        return "rejected"
    if not mk:
        return "incomplete"
    jc = next((x.get("class") for x in judge.get("items", []) if isinstance(x, dict) and x.get("id") == i), None)
    if jc in ("must_fix", "should_fix", "leave", "rejected"):
        return jc
    if mk.get("keep") is not True:
        return "leave"
    sev = mk.get("severity_adjusted") if mk.get("severity_adjusted") in SEV else it.get("severity")
    return "must_fix" if sev in ("zero", "deduct") else "should_fix"


def render_report(a, items, dropped, verdicts, judge, judge_failed, incomplete, missing, coverage, not_checked,
                  static, cfg, fan_model, judge_model, docs, ctx):
    guided = a.tier == "guided"

    def F(s):                                   # guided：模型写的每个字段先单独过一遍
        s = "" if s is None else str(s)
        return MASK if guided and s and leaky(s, ctx) else s
    cls = {x.get("id"): x for x in judge.get("items", []) if isinstance(x, dict)}
    work = [f"{n} sha256 {d['sha256'][:8]}" for n, d in docs.items() if d["role"] == "work"]
    L = [f"# {a.course} {a.code} · 交前审查 · 第 {a.round} 轮",
         f"档位：{a.tier}{'（hint=question）' if a.hint == 'question' else ''} ｜ 类型：{a.type} ｜ 被审：{'；'.join(work)}",
         f"模型：{cfg['name']} · 扇出 {fan_model} · 判官 {judge_model}（作业审查.py 简化版）"]
    if guided:
        L.append("⚠️ 按这份作业的 AI 政策，本报告只指出位置和原因，不含改法。")
    L += ["", "## 本实现做不到的（简化版的固定说明）",
          "- 不运行任何代码、SQL、编译或测试；依赖运行结果的判断标了「需要你跑一下」。",
          "- 不看图片、截图和渲染后的页面。",
          "- 核实只靠「引文是否逐字存在」的本地检查 + 模型推理，强度低于 Claude Code 版的「亲自复现」。",
          "- 你的作答已经发送给 .env 里配置的模型服务。"]
    if static:
        L.append(f"- 这几个镜头本该运行 / 看图 / 用原始数据，这里只做了静态推理：{', '.join(static)}")
    groups = {}
    for it in items:
        groups.setdefault(classify(it, verdicts, judge, incomplete), []).append(it)
    n = {k: len(v) for k, v in groups.items()}
    mr = judge.get("mark_range") or {}
    head = (f"必改 {n.get('must_fix', 0)} 条 · 值得改 {n.get('should_fix', 0)} 条 · 留着不改 {n.get('leave', 0)} 条 · 已驳回 {n.get('rejected', 0)} 条 · "
            f"未核实 {n.get('unverified', 0)} 条 · 票没收齐 {n.get('incomplete', 0)} 条"
            + (f" · 暂定分区间 {mr.get('low'):g}–{mr.get('high'):g}" if isinstance(mr.get("low"), float) and isinstance(mr.get("high"), float) else "")
            + (f"（仅供排序：{mr['caveat']}）" if not guided and mr.get("caveat") and mr.get("low") is not None else ""))
    L += ["", "## 0 现状"]
    if missing:
        L.append(f"⚠️ {len(missing)} 个镜头没跑成，结论不完整（见 §9）。")
    if judge_failed:
        L.append("⚠️ 判官没跑成，下面的分级是按核实结果机械推的。")
    if not items:
        L.append("没有发现：各镜头报出的问题要么为零，要么在合并时被剔除（见 §7）。查过的范围见 §6，判断不了的见 §8。")
    L.append(head)

    def block(it):
        loc = it.get("location") or {}
        where = F(loc.get("file", "")) + (f":{loc.get('line_start')}" if loc.get("line_start") else "") + \
            (f" p.{loc.get('page')}" if loc.get("page") else "") + (f" {F(loc.get('question'))}" if loc.get("question") else "")
        j = cls.get(it["id"]) or {}
        v = verdicts.get(it["id"], {})
        out = [f"### {it['id']} · {where} · {F(it.get('title', ''))}",
               f"- 问题：{F(it.get('what_is_wrong', ''))}",
               f"- 作答原文：「{it.get('work_quote', '')}」",
               rule_line(dict(it, rule_source={k: F(x) for k, x in (it.get("rule_source") or {}).items()}), a.tier, ctx),
               f"- 为什么扣分：{F(it.get('why_it_costs', ''))}"]
        if j.get("expected_loss") is not None:
            out.append(f"- 期望损失 ≈ {j['expected_loss']:g} 分 · 修法成本 {j.get('fix_cost') or '?'}" + ("" if guided else (f" · {j.get('note')}" if j.get("note") else "")))
        for stage in ("facts", "marker"):
            if v.get(stage):
                out.append(f"- {stage}：{'成立' if v[stage].get('keep') is True else '不成立'}" + ("" if guided else f" —— {v[stage].get('reason', '')}"))
        if it.get("needs_execution"):
            out.append("- ⚠️ 需要你自己跑一下才能确认")
        if not guided and it.get("old_text") and it.get("new_text"):
            out.append(f"- 改法：OLD「{it['old_text']}」→ NEW「{it['new_text']}」")
        elif not guided and it.get("suggested_fix"):
            out.append(f"- 改法：{it['suggested_fix']}")
        if it.get("fix_note"):
            out.append(f"- 注：{it['fix_note']}")
        if guided and a.hint == "question" and it.get("guiding_question"):
            out.append(f"- 想一想：{F(it['guiding_question'])}")
        return out
    titles = [("must_fix", "1 必改"), ("should_fix", "2 值得改"), ("leave", "3 留着不改"), ("rejected", "4 已驳回"),
              ("unverified", "5 未核实"), ("incomplete", "5b 票没收齐（不算驳回）")]
    for key, title in titles:
        L += ["", f"## {title}"]
        if not groups.get(key):
            L.append("（无）")
            continue
        for it in groups[key]:
            if key in ("must_fix", "should_fix", "leave"):
                L += block(it)
            else:
                note = "" if guided else (cls.get(it["id"]) or {}).get("note", "")
                L.append(f"- {it['id']} · {F(it.get('title', ''))}" + (f"（{note}）" if note else ""))
    L += ["", "## 6 覆盖（查过没问题的）"] + [f"- {F(c)}" for c in coverage[:60]]
    L += ["", "## 7 合并时剔除的"] + [f"- {d['source']}：{d['reason']}" + ("" if guided else f" —— {d.get('note', '')}") for d in dropped]
    L += ["", "## 8 本报告判断不了的事 + 交前只有你能做的"] + [f"- {F(x)}" for x in (judge.get("not_covered") or []) + not_checked]
    L += [f"- [ ] {F(x)}" for x in judge.get("operations_checklist") or []]
    if missing:
        L += ["", "## 9 没跑成的镜头"] + [f"- {m}" for m in missing]
    text = "\n".join(L) + "\n"
    if guided:                                    # 末道：渲染后的报告逐行再扫一遍
        text = mask_lines(text, ctx)
    return text


# ====================================================================== main
def main():
    ap = argparse.ArgumentParser(prog="作业审查.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep")
    p.add_argument("course"), p.add_argument("code")
    p.add_argument("--files", dest="work", nargs="+", required=True)
    for role in ROLES[1:]:
        p.add_argument(f"--{role}", dest=role.replace("-", "_"), nargs="*", default=[])
    p.add_argument("--round", type=int, default=1)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--no-spec-feed", action="store_true", help="这份作业不许把整份 spec 喂给 AI：--spec 只接受 <代号>-要点.md（之后每次 prep 都沿用）")
    g.add_argument("--allow-spec-feed", action="store_true", help="撤销之前的 --no-spec-feed（政策真的变了才用）")
    p.add_argument("--render-pages", action="store_true", help="把 PDF 作答渲染成 材料/页面/*.png（看图的镜头用）")
    ls = sub.add_parser("lenses")
    ls.add_argument("course"), ls.add_argument("code")
    ls.add_argument("--type", required=True, choices=TYPES)
    ls.add_argument("--tier", required=True, choices=["allowed", "guided"])
    ls.add_argument("--hint", choices=["none", "question"], default="none")
    ls.add_argument("--round", type=int, default=1)
    ls.add_argument("--xmodel", action="store_true")
    ls.add_argument("--lenses"), ls.add_argument("--add"), ls.add_argument("--drop")
    ls.add_argument("--shard", action="append", help="把一个镜头切成几片并行跑：--shard behaviour=parser,storage")
    ls.add_argument("--force", action="store_true", help="材料和档位都和上一轮一样也照样出计划")
    c = sub.add_parser("checklist")
    c.add_argument("course"), c.add_argument("code")
    c.add_argument("--type", required=True, nargs="+", choices=TYPES)
    c.add_argument("--spec", nargs="*", default=[])
    sc = sub.add_parser("scrub")
    sc.add_argument("course"), sc.add_argument("code")
    sc.add_argument("--round", type=int, default=1)
    sn = sub.add_parser("scan")
    sn.add_argument("report")
    sn.add_argument("--tier", default="guided", choices=["guided"])
    sn.add_argument("--course"), sn.add_argument("--code")
    fi = sub.add_parser("fix-ids")
    fi.add_argument("course"), fi.add_argument("code")
    fi.add_argument("--round", type=int, default=1)
    s = sub.add_parser("assemble")
    s.add_argument("course"), s.add_argument("code")
    s.add_argument("--round", type=int, default=1)
    s.add_argument("--tier", choices=["allowed", "guided"])
    r = sub.add_parser("run")
    r.add_argument("course"), r.add_argument("code")
    r.add_argument("--type", required=True, choices=TYPES)
    r.add_argument("--tier", required=True, choices=["allowed", "guided", "forbidden"])
    r.add_argument("--confirm-tier", required=True, dest="confirm_tier")
    r.add_argument("--hint", choices=["none", "question"], default="none")
    r.add_argument("--round", type=int, default=1)
    r.add_argument("--lenses"), r.add_argument("--add"), r.add_argument("--drop")
    r.add_argument("--yes", action="store_true")
    r.add_argument("--force", action="store_true", help="材料和档位都和上一轮一样 / 轮次和最近一次 prep 不一致，也照样跑")
    r.add_argument("--refresh", choices=["find", "verify", "judge", "all"], help="丢掉这一阶段的缓存重跑")
    a = ap.parse_args()
    {"prep": cmd_prep, "lenses": cmd_lenses, "checklist": cmd_checklist, "scrub": cmd_scrub, "scan": cmd_scan,
     "assemble": cmd_assemble, "run": cmd_run, "fix-ids": cmd_fix_ids}[a.cmd](a)


if __name__ == "__main__":
    main()
