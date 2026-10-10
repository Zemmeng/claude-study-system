#!/usr/bin/env python3
"""study —— claude-study-system 的自带运行时：接任意 OpenAI 兼容模型，不需要 Claude Code。

    ./study                     交互模式
    ./study -p "跑一下体检"      单次模式：跑完一个回合就退出（脚本 / 测试用）
    ./study --yes               所有工具调用都不问
    ./study --provider kimi --model kimi-k3

它和 Claude Code 共用同一套东西：CLAUDE.md（规则）· .claude/commands/（/学 /作业 /总控 …）·
.claude/settings.json 里的三道门 hook。改规则只改一处，两条路都生效。
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from runtime import config as cfgmod  # noqa: E402
from runtime.hooks import Hooks  # noqa: E402
from runtime.llm import Client, LLMError  # noqa: E402
from runtime.tools import Tools, openai_tools  # noqa: E402

ROOT = cfgmod.ROOT
STATE = os.path.join(ROOT, ".study")
MAX_STEPS = 40
MAX_CHARS = int(os.environ.get("STUDY_MAX_CHARS", "250000"))

RUNTIME_PROMPT = """你在 study 运行时里工作 —— 它是 claude-study-system 的自带运行时，接的是用户自己配置的模型（{name} · {model}）。
项目根目录：{root}。现在：{now}。

## 工具对照（项目规则是按 Claude Code 写的，这里的对应关系）

| 规则里写的 | 在这里用 |
|---|---|
| AskUserQuestion | ask_user |
| SendUserFile（把 PDF 发到侧边面板）| open_file（用系统程序打开）|
| Read · Write · Edit · Bash · Grep | read_file · write_file · edit_file · bash · grep |
| Agent / 子 agent · MCP · WebSearch / WebFetch · list_sessions · ToolSearch · Artifact | **本运行时没有** —— 跳过那一步，在回复里用一句话说明，不要假装做了 |

## 工作方式

- 规则要求「先查再说」的地方，一律先用工具查（读文件、跑 shared/工具/ 下的脚本），不凭印象。
- 改文件优先用 edit_file 精确替换；新文件用 write_file。路径相对项目根。
- 可以在一次回复里连续调用多个工具；查完再给结论。
- 输出是终端里的 Markdown。表格窄一点，公式用反引号。
- 回合结束时会自动跑收尾门（shared/工具/检查.py）。被拦回来时，照它说的修，修不了就在回复里明说。

---

# 项目规则（CLAUDE.md —— 必须遵守）

{claude_md}
"""


# ====================================================================== 终端 UI
class UI:
    def __init__(self):
        self.color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    def c(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.color else s

    def dim(self, s):
        return self.c("2", s)

    def out(self, s="", end="\n"):
        sys.stdout.write(s + end)
        sys.stdout.flush()

    def ask(self, prompt):
        try:
            return input(prompt)
        except EOFError:
            return ""

    def choose(self, question, options):
        self.out("\n" + self.c("1;36", "? " + question))
        for i, o in enumerate(options, 1):
            desc = f" —— {o.get('description')}" if o.get("description") else ""
            self.out(f"  {self.c('1', str(i))}. {o.get('label', '')}{self.dim(desc)}")
        self.out(f"  {self.c('1', str(len(options) + 1))}. 其他（自己输入）")
        while True:
            a = self.ask(self.c("36", "选一个编号 › ")).strip()
            if a.isdigit() and 1 <= int(a) <= len(options):
                o = options[int(a) - 1]
                return f"用户选了：{o.get('label')}"
            if a.isdigit() and int(a) == len(options) + 1 or (a and not a.isdigit()):
                text = a if not a.isdigit() else self.ask("输入你的回答 › ")
                return f"用户的回答：{text}"


# ====================================================================== 会话
class Session:
    def __init__(self, cfg, ui, auto_yes=False, interactive=True):
        self.cfg, self.ui = cfg, ui
        self.client = Client(cfg["base_url"], cfg["api_key"], cfg["model"], stream=cfg["stream"])
        self.hooks = Hooks(ROOT)
        self.tools = Tools(ROOT, ui, auto_yes=auto_yes, interactive=interactive)
        self.interactive = interactive
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.start()

    def start(self):
        self.id = str(uuid.uuid4())
        os.makedirs(os.path.join(STATE, "sessions"), exist_ok=True)
        self.transcript = os.path.join(STATE, "sessions", dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{self.id[:8]}.jsonl")
        try:
            with open(os.path.join(ROOT, "CLAUDE.md"), encoding="utf-8") as f:
                claude_md = f.read()
        except FileNotFoundError:
            claude_md = "（项目根没有 CLAUDE.md）"
        system = RUNTIME_PROMPT.format(name=self.cfg["name"], model=self.cfg["model"], root=ROOT,
                                       now=dt.datetime.now().strftime("%Y-%m-%d %H:%M %A"), claude_md=claude_md)
        ctx, _, _, notes = self.hooks.run("SessionStart", {"session_id": self.id, "source": "startup",
                                                            "transcript_path": self.transcript})
        for n in notes:
            self.ui.out(self.ui.dim("⚠️ " + n))
        if ctx:
            system += "\n---\n\n# 开场门注入的导航层（SessionStart hook）\n\n" + ctx
        self.messages = [{"role": "system", "content": system}]

    def log(self, kind, text):
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": kind, "message": {"role": kind, "content": [{"type": "text", "text": text}]},
                                "timestamp": dt.datetime.now().isoformat()}, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ 一个回合
    def turn(self, user_text):
        ctx, blocked, reason, notes = self.hooks.run("UserPromptSubmit", {"session_id": self.id, "prompt": user_text,
                                                                          "transcript_path": self.transcript})
        if blocked:
            self.ui.out(self.ui.c("31", f"⛔ 消息门拦下了这条消息：{reason}"))
            return ""
        content = user_text + (f"\n\n<system-reminder>\n{ctx}\n</system-reminder>" if ctx else "")
        self.messages.append({"role": "user", "content": content})
        self.log("user", user_text)

        stop_active, final = False, ""
        for _ in range(MAX_STEPS):
            self.trim()
            msg = self.ask_model()
            self.messages.append(msg)
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    name, args = tc["function"]["name"], tc["function"]["arguments"]
                    self.ui.out(self.ui.c("33", f"⏺ {name}") + self.ui.dim(f"  {self.tools.summary(name, args)}"))
                    result = self.tools.call(name, args)
                    first = result.strip().split("\n", 1)[0][:140]
                    self.ui.out(self.ui.dim(f"  ⎿ {first}"))
                    self.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                continue
            final = msg.get("content") or ""
            self.log("assistant", final)
            # ---- 收尾门
            _, blocked, reason, notes = self.hooks.run("Stop", {"session_id": self.id, "stop_hook_active": stop_active,
                                                                "transcript_path": self.transcript})
            for n in notes:
                self.ui.out(self.ui.dim(n))
            if blocked and not stop_active:
                stop_active = True
                self.ui.out(self.ui.c("31", "\n🔒 收尾门拦回：") + self.ui.dim(reason[:600]))
                self.messages.append({"role": "user", "content": f"<system-reminder>\n收尾门（Stop hook）拦下了这个回合：\n{reason}\n"
                                                                 f"照它说的修好再结束；修不了的在回复里明说。\n</system-reminder>"})
                continue
            break
        else:
            self.ui.out(self.ui.c("31", f"\n⚠️ 这个回合连续调用工具超过 {MAX_STEPS} 步，先停下。"))
        u = self.usage
        self.ui.out(self.ui.dim(f"\n↳ 累计 tokens：输入 {u['prompt_tokens']:,} · 输出 {u['completion_tokens']:,}"))
        return final

    def ask_model(self):
        printed = {"text": False, "reason": False}

        def on_text(t):
            if printed["reason"] and not printed["text"]:
                self.ui.out("")
            printed["text"] = True
            self.ui.out(t, end="")

        def on_reason(t):
            if not printed["reason"]:
                self.ui.out(self.ui.dim("（思考）"), end="")
            printed["reason"] = True
            self.ui.out(self.ui.dim(t), end="")

        msg, usage = self.client.chat(self.messages, tools=openai_tools(), on_text=on_text, on_reasoning=on_reason)
        if printed["text"] or printed["reason"]:
            self.ui.out("")
        for k in self.usage:
            self.usage[k] += int(usage.get(k) or 0)
        return msg

    def trim(self):
        """上下文太长时，从最早的工具输出开始省略（规则和最近的对话保留）。"""
        total = sum(len(str(m.get("content") or "")) for m in self.messages)
        for m in self.messages:
            if total <= MAX_CHARS:
                break
            if m["role"] == "tool" and len(m["content"]) > 200:
                total -= len(m["content"]) - 40
                m["content"] = "（早先的工具输出，已省略以节省上下文）"


# ====================================================================== slash 命令
def expand_command(text):
    """/学 algorithms w02-L1 → .claude/commands/学.md 的内容（$ARGUMENTS 换成参数）。不是项目命令就原样返回 None。"""
    m = re.match(r"^/(\S+)\s*(.*)$", text.strip(), re.S)
    if not m:
        return None
    name, args = m.group(1), m.group(2).strip()
    path = os.path.join(ROOT, ".claude", "commands", name + ".md")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        body = f.read()
    body = re.sub(r"^---\n.*?\n---\n", "", body, count=1, flags=re.S)
    if "$ARGUMENTS" in body:
        body = body.replace("$ARGUMENTS", args)
    elif args:
        body += f"\n\nARGUMENTS: {args}"
    return f"<command-name>/{name}</command-name>\n{body}"


def list_commands():
    d = os.path.join(ROOT, ".claude", "commands")
    out = []
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if fn.endswith(".md"):
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                m = re.search(r"^description:\s*(.+)$", f.read(), re.M)
            out.append((fn[:-3], m.group(1).strip() if m else ""))
    return out


HELP = """内置命令：
  /help        这份帮助          /commands   项目命令（/学 /作业 /总控 …）
  /model       当前模型          /clear      开新会话（重新过开场门）
  /exit        退出
项目命令照 Claude Code 的写法用：/学 algorithms w02-L1 · /总控 · /next …
多行输入：行尾加 \\ 换行。Ctrl-C 打断当前回合。"""


# ====================================================================== main
def main():
    ap = argparse.ArgumentParser(prog="study", description="claude-study-system 的自带运行时（任意 OpenAI 兼容模型）")
    ap.add_argument("-p", "--print", dest="prompt", help="单次模式：跑完这一个回合就退出")
    ap.add_argument("--yes", action="store_true", help="所有工具调用都不问")
    ap.add_argument("--provider", help="覆盖 .env 的 STUDY_PROVIDER")
    ap.add_argument("--model", help="覆盖 .env 的 STUDY_MODEL")
    a = ap.parse_args()
    ui = UI()
    try:
        cfg = cfgmod.resolve(provider=a.provider, model=a.model)
    except cfgmod.ConfigError as e:
        ui.out(ui.c("31", "❌ " + str(e)))
        sys.exit(2)

    interactive = a.prompt is None
    s = Session(cfg, ui, auto_yes=a.yes, interactive=interactive)

    if not interactive:
        text = expand_command(a.prompt) or a.prompt
        try:
            s.turn(text)
        except LLMError as e:
            ui.out(ui.c("31", f"❌ 模型调用失败：{e}"))
            sys.exit(1)
        return

    try:
        import readline  # noqa: F401  行内编辑和历史
        hist = os.path.join(STATE, "history")
        try:
            readline.read_history_file(hist)
        except (FileNotFoundError, OSError):
            pass
    except ImportError:
        readline, hist = None, None

    ui.out(ui.c("1", "claude-study-system · study") + ui.dim(f"  模型 {cfg['name']} · {cfg['model']}  ·  /help 看帮助"))
    ui.out(ui.dim("直接说要干什么（例：学 algorithms w02-L1）；直接回车让它摆菜单。\n"))
    first = True
    while True:
        try:
            line = ui.ask(ui.c("1;32", "› "))
            while line.endswith("\\"):
                line = line[:-1] + "\n" + ui.ask("  ")
        except KeyboardInterrupt:
            ui.out("")
            continue
        text = line.strip()
        if not text:
            if not first:
                continue
            text = "我们开始吧。"
        first = False
        if text in ("/exit", "/quit"):
            break
        if text == "/help":
            ui.out(HELP)
            continue
        if text == "/commands":
            for n, d in list_commands():
                ui.out(f"  /{n}  " + ui.dim(d))
            continue
        if text == "/model":
            ui.out(f"  {cfg['name']} · {cfg['model']} · {cfg['base_url']}")
            continue
        if text == "/clear":
            s.start()
            ui.out(ui.dim("（新会话，开场门已重新注入）"))
            continue
        expanded = expand_command(text)
        if text.startswith("/") and expanded is None:
            ui.out(ui.dim(f"没有这个命令：{text.split()[0]}（/commands 看全部）"))
            continue
        try:
            s.turn(expanded or text)
        except KeyboardInterrupt:
            ui.out(ui.c("33", "\n（已打断这个回合）"))
            # 打断时最后一条可能是没收到结果的 tool_calls —— 删掉，免得下一轮请求格式不合法
            while s.messages and s.messages[-1]["role"] in ("assistant", "tool") and \
                    (s.messages[-1]["role"] == "tool" or s.messages[-1].get("tool_calls")):
                s.messages.pop()
        except LLMError as e:
            ui.out(ui.c("31", f"\n❌ 模型调用失败：{e}"))
        if readline and hist:
            try:
                readline.write_history_file(hist)
            except OSError:
                pass


if __name__ == "__main__":
    main()
