#!/usr/bin/env python3
"""自带运行时（./study）的协议测试 —— 用一个按剧本回话的假模型服务器，不花钱、CI 里也能跑。

测的是：三道门是不是真的生效（开场注入 · 每条消息注入 · 收尾拦回后同一回合修好）、
slash 命令展开、工具调用往返、流式 / 非流式两种模式。

    python3 tests/runtime_test.py
"""
import json
import os
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


def tool_call(name, args, cid):
    return {"tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}


class Mock:
    """按剧本依次回话；把收到的每个请求体记下来给断言用。"""

    def __init__(self, script):
        self.script, self.requests = list(script), []
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                mock.requests.append(body)
                reply = mock.script.pop(0) if mock.script else {"content": "（剧本用完了）"}
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    chunks = []
                    if reply.get("content"):
                        text = reply["content"]
                        for i in range(0, len(text), 3):
                            chunks.append({"choices": [{"index": 0, "delta": {"content": text[i:i + 3]}}]})
                    for i, tc in enumerate(reply.get("tool_calls", [])):
                        args = tc["function"]["arguments"]
                        chunks.append({"choices": [{"index": 0, "delta": {"tool_calls": [
                            {"index": i, "id": tc["id"], "type": "function", "function": {"name": tc["function"]["name"], "arguments": ""}}]}}]})
                        for j in range(0, len(args), 7):
                            chunks.append({"choices": [{"index": 0, "delta": {"tool_calls": [
                                {"index": i, "function": {"arguments": args[j:j + 7]}}]}}]})
                    chunks.append({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 10}})
                    for c in chunks:
                        self.wfile.write(f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    msg = {"role": "assistant", "content": reply.get("content", "")}
                    if reply.get("tool_calls"):
                        msg["tool_calls"] = reply["tool_calls"]
                    data = json.dumps({"choices": [{"index": 0, "message": msg}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}},
                                      ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def fresh_repo(port, stream):
    d = os.path.join(tempfile.mkdtemp(), "仓库 副本")
    # 测的是工作区里的最新代码（可能还没提交）：整份复制，再 git init 当基线
    shutil.copytree(SRC, d, ignore=shutil.ignore_patterns(".git", ".env", ".study", "__pycache__"))
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    with open(os.path.join(d, ".env"), "w", encoding="utf-8") as f:
        f.write(f"STUDY_BASE_URL=http://127.0.0.1:{port}/v1\nSTUDY_API_KEY=test\nSTUDY_MODEL=mock\nSTUDY_STREAM={'1' if stream else '0'}\n")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-qm", "test base", "--allow-empty"], cwd=d, check=True, env=env)
    return d


def run_study(d, prompt):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("STUDY_", "CLAUDE_PROJECT_DIR"))}
    return subprocess.run([os.path.join(d, "study"), "-p", prompt, "--yes"], cwd=d, capture_output=True, text=True, timeout=180, env=env)


def scenario_basic(stream):
    print(f"【基本回合 · {'流式' if stream else '非流式'}】")
    m = Mock([tool_call("bash", {"command": "python3 shared/工具/导航.py"}, "c1"), {"content": "好的，接着讲 w02-L1 的 p.7。"}])
    d = fresh_repo(m.port, stream)
    r = run_study(d, "/学 algorithms w02-L1")
    m.close()
    check(r.returncode == 0, f"退出码 0（实际 {r.returncode}）{r.stderr[-300:] if r.returncode else ''}")
    check(len(m.requests) == 2, f"模型被调了 2 次（实际 {len(m.requests)}）")
    if len(m.requests) < 2:
        return
    sysmsg = m.requests[0]["messages"][0]["content"]
    user = m.requests[0]["messages"][1]["content"]
    check("项目规则（CLAUDE.md" in sysmsg and "一个对话只做一件事" in sysmsg, "系统提示里有 CLAUDE.md 全文")
    check("开场门注入的导航层" in sysmsg and "📅" in sysmsg, "开场门（SessionStart）注入了导航层")
    check("<command-name>/学</command-name>" in user and "algorithms w02-L1" in user, "/学 展开成命令文件内容并带上参数")
    check("⏰ 当前时间" in user, "消息门（UserPromptSubmit）注入了时间和必做清单")
    check(any(t["function"]["name"] == "ask_user" for t in m.requests[0].get("tools", [])), "工具表里有 ask_user（对应 AskUserQuestion）")
    tool_msgs = [x for x in m.requests[1]["messages"] if x["role"] == "tool"]
    check(tool_msgs and "📅" in tool_msgs[0]["content"], "bash 工具真的跑了脚本，结果回给了模型")
    check("接着讲 w02-L1" in r.stdout, "最终回复打印到了终端")
    shutil.rmtree(os.path.dirname(d), ignore_errors=True)


def scenario_stop_gate():
    print("【收尾门拦回 → 同一回合修好】")
    bad = {"path": "shared/lecture台账.md", "old_string": "| W3 | `w03-L1` | 8 | ❌未开始 |", "new_string": "| W3 | `w03-L1` | 8 | ✅全 |"}
    fix = {"path": "shared/lecture台账.md", "old_string": "| W3 | `w03-L1` | 8 | ✅全 |", "new_string": "| W3 | `w03-L1` | 8 | ❌未开始 |"}
    m = Mock([tool_call("edit_file", bad, "c1"), {"content": "w03-L1 讲完了，已记账。"},
              tool_call("edit_file", fix, "c2"), {"content": "收尾门说得对：他还没答题，台账撤回成 ❌未开始。"}])
    d = fresh_repo(m.port, True)
    r = run_study(d, "把 w03-L1 记成讲完")
    m.close()
    check(len(m.requests) == 4, f"拦回后模型又被调了两次（共 {len(m.requests)} 次，应为 4）")
    if len(m.requests) >= 3:
        reminder = m.requests[2]["messages"][-1]["content"]
        check("收尾门" in reminder and "[G1]" in reminder, "收尾门的拦截原因（[G1] 凭证锁）回给了模型")
    with open(os.path.join(d, "shared", "lecture台账.md"), encoding="utf-8") as f:
        check("| W3 | `w03-L1` | 8 | ❌未开始 |" in f.read(), "最终台账是对的（被撤回）")
    check("收尾门拦回" in r.stdout, "终端上能看到「收尾门拦回」")
    shutil.rmtree(os.path.dirname(d), ignore_errors=True)


def scenario_permission():
    print("【权限：非交互且没给 --yes 时，危险命令不执行】")
    m = Mock([tool_call("bash", {"command": "rm -rf shared"}, "c1"), {"content": "好"}])
    d = fresh_repo(m.port, True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("STUDY_")}
    subprocess.run([os.path.join(d, "study"), "-p", "清理一下"], cwd=d, capture_output=True, text=True, timeout=120, env=env)
    m.close()
    check(os.path.isdir(os.path.join(d, "shared")), "rm -rf 没有被执行")
    tool_msgs = [x for x in m.requests[1]["messages"] if x["role"] == "tool"] if len(m.requests) > 1 else []
    check(tool_msgs and "需要 --yes" in tool_msgs[0]["content"], "模型收到了「被拒绝」的说明")
    shutil.rmtree(os.path.dirname(d), ignore_errors=True)


if __name__ == "__main__":
    scenario_basic(stream=True)
    scenario_basic(stream=False)
    scenario_stop_gate()
    scenario_permission()
    print(f"== 失败 {len(FAILS)} 项")
    sys.exit(1 if FAILS else 0)
