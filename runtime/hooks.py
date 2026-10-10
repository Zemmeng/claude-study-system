"""按 Claude Code 的 hook 协议调 .claude/settings.json 里登记的脚本 —— 两条路共用同一套三道门。

协议（只实现本项目用到的子集）：
  · stdin  = 事件 JSON（hook_event_name · session_id · cwd · transcript_path · prompt / stop_hook_active）
  · stdout = JSON {"hookSpecificOutput": {"additionalContext": "…"}}，或纯文本（当作上下文）
  · 退出码 2 = 拦截（Stop 门把 stderr 打回给模型），其他非 0 = 脚本自己出错（不拦，只提示）
  · 环境变量 CLAUDE_PROJECT_DIR = 项目根；另加 STUDY_RUNTIME=1 让脚本知道是自带运行时在调
"""
import json
import os
import subprocess


class Hooks:
    def __init__(self, root):
        self.root = root
        self.table = {}
        try:
            with open(os.path.join(root, ".claude", "settings.json"), encoding="utf-8") as f:
                self.table = (json.load(f) or {}).get("hooks", {})
        except (FileNotFoundError, json.JSONDecodeError):
            self.table = {}

    def _commands(self, event):
        out = []
        for group in self.table.get(event, []):
            for h in group.get("hooks", []):
                if h.get("type") == "command" and h.get("command"):
                    out.append((h["command"], int(h.get("timeout", 60))))
        return out

    def run(self, event, payload):
        """跑某个事件的全部 hook。返回 (context_text, blocked, block_reason, notes)。"""
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.root, STUDY_RUNTIME="1")
        data = dict(payload, hook_event_name=event, cwd=self.root)
        ctx, blocked, reasons, notes = [], False, [], []
        for cmd, timeout in self._commands(event):
            try:
                r = subprocess.run(["bash", "-c", cmd], cwd=self.root, env=env, input=json.dumps(data, ensure_ascii=False),
                                   capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                notes.append(f"{event} hook 超时（{timeout}s）：{cmd}")
                continue
            out = (r.stdout or "").strip()
            if r.returncode == 2:
                blocked = True
                reasons.append((r.stderr or out).strip())
                continue
            if r.returncode != 0:
                notes.append(f"{event} hook 退出码 {r.returncode}：{(r.stderr or '').strip()[:300]}")
            if r.stderr and r.returncode == 0 and event == "Stop":
                notes.append(r.stderr.strip())          # 放行但带提示（例：别的会话的半成品）
            if not out:
                continue
            try:
                obj = json.loads(out)
            except json.JSONDecodeError:
                ctx.append(out)
                continue
            if obj.get("decision") == "block":
                blocked = True
                reasons.append(obj.get("reason", ""))
            add = (obj.get("hookSpecificOutput") or {}).get("additionalContext")
            if add:
                ctx.append(add)
        return "\n\n".join(ctx), blocked, "\n".join(r for r in reasons if r), notes
