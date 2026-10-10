"""OpenAI 兼容的 Chat Completions 客户端（只用标准库）。

几乎所有厂商都提供这套接口：OpenAI · DeepSeek · Kimi · 智谱 · 通义 · OpenRouter · Anthropic（兼容层）· Ollama · vLLM …
支持流式（SSE）和工具调用；429 / 5xx 自动重试。
"""
import json
import socket
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


class Client:
    def __init__(self, base_url, api_key, model, stream=True, timeout=300):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.stream = stream
        self.timeout = timeout

    # ------------------------------------------------------------------ HTTP
    def _open(self, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "claude-study-system/runtime",
        })
        last = None
        for attempt in range(4):
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:800]
                if e.code in (429, 500, 502, 503, 504, 529) and attempt < 3:
                    last = f"HTTP {e.code}: {detail}"
                    time.sleep(2 ** attempt * 2)
                    continue
                raise LLMError(f"HTTP {e.code}（{self.url}）：{detail}") from None
            except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
                if attempt < 3:
                    last = str(e)
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"连不上 {self.url}：{e}") from None
        raise LLMError(f"重试 4 次仍失败：{last}")

    # ------------------------------------------------------------------ chat
    def chat(self, messages, tools=None, on_text=None, on_reasoning=None, model=None, max_tokens=None):
        """发一轮对话，返回 (assistant_message_dict, usage_dict)。

        on_text / on_reasoning：流式时每来一段文字就回调一次（用来实时打印）。
        """
        body = {"model": model or self.model, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if max_tokens:
            body["max_tokens"] = max_tokens
        if self.stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
            try:
                return self._chat_stream(body, on_text, on_reasoning)
            except LLMError as e:
                # 个别服务不认 stream_options —— 去掉再试一次
                if "stream_options" in str(e) or "include_usage" in str(e):
                    body.pop("stream_options", None)
                    return self._chat_stream(body, on_text, on_reasoning)
                raise
        with self._open(body) as r:
            resp = json.loads(r.read().decode("utf-8"))
        if "choices" not in resp:
            raise LLMError(f"返回里没有 choices：{json.dumps(resp, ensure_ascii=False)[:500]}")
        msg = resp["choices"][0].get("message") or {}
        out = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("reasoning_content"):
            out["reasoning_content"] = msg["reasoning_content"]
            if on_reasoning:
                on_reasoning(msg["reasoning_content"])
        if msg.get("tool_calls"):
            out["tool_calls"] = [
                {"id": tc.get("id") or f"call_{i}", "type": "function",
                 "function": {"name": tc["function"]["name"], "arguments": tc["function"].get("arguments") or "{}"}}
                for i, tc in enumerate(msg["tool_calls"])]
        if out["content"] and on_text:
            on_text(out["content"])
        return out, resp.get("usage") or {}

    def _chat_stream(self, body, on_text, on_reasoning):
        text, reasoning, calls, usage = [], [], {}, {}
        with self._open(body) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if ev.get("error"):
                    raise LLMError(f"流式返回错误：{json.dumps(ev['error'], ensure_ascii=False)[:500]}")
                if ev.get("usage"):
                    usage = ev["usage"]
                for ch in ev.get("choices") or []:
                    d = ch.get("delta") or {}
                    if d.get("reasoning_content"):
                        reasoning.append(d["reasoning_content"])
                        if on_reasoning:
                            on_reasoning(d["reasoning_content"])
                    if d.get("content"):
                        text.append(d["content"])
                        if on_text:
                            on_text(d["content"])
                    for tc in d.get("tool_calls") or []:
                        i = tc.get("index", len(calls))
                        slot = calls.setdefault(i, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
        out = {"role": "assistant", "content": "".join(text)}
        if reasoning:
            out["reasoning_content"] = "".join(reasoning)
        if calls:
            out["tool_calls"] = [
                {"id": c["id"] or f"call_{i}", "type": "function",
                 "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                for i, c in sorted(calls.items())]
        return out, usage
