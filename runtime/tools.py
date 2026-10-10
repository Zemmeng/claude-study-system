"""运行时给模型的工具。名字和语义对齐 Claude Code，规则文件里提到的工具都有对应。

权限：
  · 只读（read_file · list_dir · grep）—— 直接放行
  · 写项目里的文件（write_file · edit_file）—— 直接放行（会显示出来）；写项目外 → 先问
  · bash —— 白名单里的只读命令直接放行；其他先问（y = 这一次 · a = 本会话都放行 · n = 拒绝）
  · --yes 启动时全部放行
"""
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

MAX_OUT = 30000

SAFE_PREFIXES = (
    "python3 shared/工具/", "ls", "cat ", "head ", "tail ", "grep ", "rg ", "find ", "wc ", "pdfinfo ", "pdftotext ",
    "pdftoppm ", "date", "pwd", "echo ", "git status", "git log", "git diff", "git show", "sed -n ", "file ", "stat ",
)
UNSAFE_CHARS = (";", "&&", "||", ">", "$(", "`", "\n")


PIPE_OK = SAFE_PREFIXES + ("sort", "uniq", "cut ", "tr ", "nl", "column")


def is_safe(cmd):
    """白名单里的只读命令 —— 不问直接跑。管道的每一段都得在白名单里；带重定向、命令替换、串联的一律要问。"""
    if any(c in cmd for c in UNSAFE_CHARS):
        return False
    segs = [x.strip() for x in cmd.split("|")]
    if not segs[0].startswith(SAFE_PREFIXES) or not all(x.startswith(PIPE_OK) for x in segs[1:]):
        return False
    if re.search(r"(^|\s)-(delete|exec|execdir|ok|fprint)\b", cmd):     # find 能删文件、能执行命令
        return False
    return True


SCHEMAS = [
    {"name": "read_file", "description": "读文本文件，返回带行号的内容。PDF 不能直接读 —— 用 bash 跑 pdftotext。",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "相对项目根或绝对路径"},
         "offset": {"type": "integer", "description": "从第几行开始（1 起）"},
         "limit": {"type": "integer", "description": "最多读几行，默认 2000"}}, "required": ["path"]}},
    {"name": "write_file", "description": "整份写入（新建或覆盖）。改已有文件的一部分请用 edit_file。",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "把文件里的 old_string 精确替换成 new_string。old_string 必须唯一（否则加更多上下文，或设 replace_all）。",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
         "replace_all": {"type": "boolean"}}, "required": ["path", "old_string", "new_string"]}},
    {"name": "list_dir", "description": "列目录（默认项目根）。",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}},
    {"name": "grep", "description": "在文件里按正则搜索，返回「文件:行号: 内容」。",
     "parameters": {"type": "object", "properties": {
         "pattern": {"type": "string"}, "path": {"type": "string", "description": "目录或文件，默认项目根"},
         "glob": {"type": "string", "description": "只搜匹配的文件名，例：*.md"}}, "required": ["pattern"]}},
    {"name": "bash", "description": "在项目根目录执行 shell 命令，返回 stdout+stderr。项目脚本都在 shared/工具/ 下（python3 运行）。",
     "parameters": {"type": "object", "properties": {
         "command": {"type": "string"}, "timeout": {"type": "integer", "description": "秒，默认 120"}}, "required": ["command"]}},
    {"name": "ask_user", "description": "给用户一个选择题让他点（对应规则里的 AskUserQuestion）。返回他选的内容。",
     "parameters": {"type": "object", "properties": {
         "question": {"type": "string"},
         "options": {"type": "array", "items": {"type": "object", "properties": {
             "label": {"type": "string"}, "description": {"type": "string"}}, "required": ["label"]}}},
         "required": ["question", "options"]}},
    {"name": "open_file", "description": "用系统默认程序打开文件给用户看（对应规则里的 SendUserFile，例：发课件 PDF）。",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
]


def openai_tools():
    return [{"type": "function", "function": s} for s in SCHEMAS]


class Tools:
    def __init__(self, root, ui, auto_yes=False, interactive=True):
        self.root = os.path.realpath(root)
        self.ui = ui
        self.auto_yes = auto_yes
        self.interactive = interactive
        self.always_bash = False

    # ------------------------------------------------------------------ helpers
    def _path(self, p):
        p = os.path.expanduser(p or ".")
        return os.path.realpath(p if os.path.isabs(p) else os.path.join(self.root, p))

    def _inside(self, ap):
        return ap == self.root or ap.startswith(self.root + os.sep)

    def _rel(self, ap):
        return os.path.relpath(ap, self.root) if self._inside(ap) else ap

    def _confirm(self, what):
        if self.auto_yes:
            return True
        if not self.interactive:
            return False
        ans = self.ui.ask(f"允许 {what}？[y=这次 / a=本会话都允许 / n=拒绝] ").strip().lower()
        if ans == "a":
            self.always_bash = True
            return True
        return ans in ("y", "yes", "是")

    @staticmethod
    def _cap(s):
        return s if len(s) <= MAX_OUT else s[:MAX_OUT] + f"\n…（截断，共 {len(s)} 字符）"

    # ------------------------------------------------------------------ dispatch
    def call(self, name, raw_args):
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("参数必须是 JSON 对象")
        except (json.JSONDecodeError, ValueError) as e:
            return f"❌ 参数不是合法 JSON：{e}"
        fn = getattr(self, "t_" + name, None)
        if not fn:
            return f"❌ 没有这个工具：{name}（可用：{', '.join(s['name'] for s in SCHEMAS)}）"
        try:
            return fn(**args)
        except TypeError as e:
            return f"❌ 参数不对：{e}"
        except Exception as e:  # noqa: BLE001  工具出错要回给模型，不能让会话崩
            return f"❌ {type(e).__name__}: {e}"

    def summary(self, name, raw_args):
        try:
            a = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            return raw_args[:80]
        key = {"bash": "command", "grep": "pattern", "ask_user": "question"}.get(name, "path")
        return str(a.get(key, ""))[:120]

    # ------------------------------------------------------------------ tools
    def t_read_file(self, path, offset=1, limit=2000):
        ap = self._path(path)
        if not os.path.isfile(ap):
            return f"❌ 文件不存在：{path}"
        if ap.lower().endswith(".pdf"):
            return "❌ PDF 不能直接读。用 bash：pdftotext -layout -f <起页> -l <止页> <文件> - | head -200"
        with open(ap, "rb") as f:
            data = f.read()
        if b"\x00" in data[:4000]:
            return f"❌ 二进制文件（{len(data)} 字节），不能当文本读"
        lines = data.decode("utf-8", "replace").split("\n")
        offset = max(1, int(offset or 1))
        chunk = lines[offset - 1: offset - 1 + int(limit or 2000)]
        body = "\n".join(f"{i:>6}\t{l}" for i, l in enumerate(chunk, offset))
        more = f"\n…（共 {len(lines)} 行，用 offset 继续读）" if offset - 1 + len(chunk) < len(lines) else ""
        return self._cap(body + more)

    def t_write_file(self, path, content):
        ap = self._path(path)
        if not self._inside(ap) and not self._confirm(f"写项目外的文件 {ap}"):
            return "❌ 用户没有允许写项目外的文件"
        os.makedirs(os.path.dirname(ap), exist_ok=True)
        with open(ap, "w", encoding="utf-8") as f:
            f.write(content)
        return f"✅ 已写入 {self._rel(ap)}（{len(content)} 字符）"

    def t_edit_file(self, path, old_string, new_string, replace_all=False):
        ap = self._path(path)
        if not os.path.isfile(ap):
            return f"❌ 文件不存在：{path}"
        if not self._inside(ap) and not self._confirm(f"改项目外的文件 {ap}"):
            return "❌ 用户没有允许改项目外的文件"
        with open(ap, encoding="utf-8") as f:
            s = f.read()
        n = s.count(old_string)
        if n == 0:
            return "❌ old_string 在文件里找不到（注意空格、缩进、全角半角要完全一致；先 read_file 再改）"
        if n > 1 and not replace_all:
            return f"❌ old_string 出现了 {n} 次，不唯一 —— 多带几行上下文，或设 replace_all=true"
        s = s.replace(old_string, new_string) if replace_all else s.replace(old_string, new_string, 1)
        with open(ap, "w", encoding="utf-8") as f:
            f.write(s)
        return f"✅ 已修改 {self._rel(ap)}（替换 {n if replace_all else 1} 处）"

    def t_list_dir(self, path="."):
        ap = self._path(path)
        if not os.path.isdir(ap):
            return f"❌ 不是目录：{path}"
        items = []
        for n in sorted(os.listdir(ap)):
            if n in (".git", ".study", "__pycache__", ".DS_Store"):
                continue
            items.append(n + ("/" if os.path.isdir(os.path.join(ap, n)) else ""))
        return "\n".join(items) or "（空目录）"

    def t_grep(self, pattern, path=".", glob=None):
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"❌ 正则写错了：{e}"
        ap = self._path(path)
        files = [ap] if os.path.isfile(ap) else []
        if os.path.isdir(ap):
            for d, dirs, fs in os.walk(ap):
                dirs[:] = [x for x in dirs if x not in (".git", ".study", "__pycache__", "node_modules")]
                for fn in fs:
                    if glob and not fnmatch.fnmatch(fn, glob):
                        continue
                    if fn.lower().endswith((".pdf", ".png", ".jpg", ".mp3", ".m4a", ".wav", ".zip")):
                        continue
                    files.append(os.path.join(d, fn))
        hits = []
        for fp in files:
            try:
                with open(fp, encoding="utf-8") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            hits.append(f"{self._rel(fp)}:{i}: {line.rstrip()[:240]}")
                            if len(hits) >= 300:
                                return "\n".join(hits) + "\n…（只显示前 300 条）"
            except (UnicodeDecodeError, OSError):
                continue
        return "\n".join(hits) or "（没有匹配）"

    def t_bash(self, command, timeout=120):
        cmd = command.strip()
        safe = is_safe(cmd)
        if not (safe or self.always_bash or self._confirm(f"执行命令\n    {cmd}\n ")):
            return "❌ 用户没有允许执行这条命令" if self.interactive else "❌ 非交互模式下这条命令需要 --yes 才能执行"
        try:
            r = subprocess.run(["bash", "-c", cmd], cwd=self.root, capture_output=True, text=True,
                               timeout=int(timeout or 120), env=dict(os.environ, CLAUDE_PROJECT_DIR=self.root))
        except subprocess.TimeoutExpired:
            return f"❌ 超时（{timeout}s）"
        out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr.strip() else "")
        return self._cap(out.strip() or "（无输出）") + ("" if r.returncode == 0 else f"\n[退出码 {r.returncode}]")

    def t_ask_user(self, question, options):
        if not self.interactive:
            return "（非交互模式，用户没法回答。请自己选推荐项，并在回复里说明选了什么）"
        return self.ui.choose(question, options)

    def t_open_file(self, path):
        ap = self._path(path)
        if not os.path.exists(ap):
            return f"❌ 不存在：{path}"
        opener = "open" if sys.platform == "darwin" else ("xdg-open" if shutil.which("xdg-open") else None)
        if opener and self.interactive:
            subprocess.Popen([opener, ap], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"✅ 已用系统程序打开 {self._rel(ap)}"
        return f"（没法自动打开，请用户自己打开：{shlex.quote(ap)}）"
