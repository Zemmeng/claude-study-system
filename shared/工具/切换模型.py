#!/usr/bin/env python3
"""让 Claude Code 在本项目里改用别家模型（路线 A：换模型不换框架）。

    python3 shared/工具/切换模型.py              看现在用的是哪个后端
    python3 shared/工具/切换模型.py deepseek     切到 DeepSeek（key 从 .env 的 STUDY_API_KEY 读）
    python3 shared/工具/切换模型.py kimi         切到 Kimi
    python3 shared/工具/切换模型.py custom http://localhost:4000    任意 Anthropic 兼容地址（例：LiteLLM 代理）
    python3 shared/工具/切换模型.py claude       切回 Anthropic 官方

原理：Claude Code 读项目里的 .claude/settings.local.json 的 env 块（这个文件在 .gitignore 里，只在你本机生效）。
本脚本只改其中 ANTHROPIC_* 等几个变量，别的设置原样保留。改完要重启 Claude Code 才生效。

只有提供 **Anthropic 兼容接口** 的厂商能直连（预设里有 anthropic_base_url 的那几家）。
只有 OpenAI 格式接口的（OpenAI · Ollama · 通义 …）两个办法：用自带运行时 ./study，或者起一个 LiteLLM 代理再用 custom。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from runtime import config as rc  # noqa: E402

ROOT = rc.ROOT
LOCAL = os.path.join(ROOT, ".claude", "settings.local.json")
KEYS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")


def load_local():
    try:
        with open(LOCAL, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        sys.exit(f"❌ {LOCAL} 不是合法 JSON（第 {e.lineno} 行）—— 先修好它")


def save_local(data):
    os.makedirs(os.path.dirname(LOCAL), exist_ok=True)
    with open(LOCAL, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def mask(k):
    return k[:6] + "…" + k[-4:] if len(k) > 12 else "…"


def status():
    env = load_local().get("env", {})
    url = env.get("ANTHROPIC_BASE_URL")
    if not url:
        print("现在：Anthropic 官方（Claude Code 默认后端）")
    else:
        print(f"现在：{url}")
        print(f"  模型：{env.get('ANTHROPIC_MODEL', '（默认）')} · 轻量档：{env.get('ANTHROPIC_DEFAULT_HAIKU_MODEL', '—')}")
        print(f"  key：{mask(env.get('ANTHROPIC_AUTH_TOKEN', ''))}")
    print("\n可切换：" + " · ".join(k for k, v in rc.load_presets().items() if v.get("anthropic_base_url")) + " · custom <地址> · claude")


def switch(name, custom_url=None):
    dotenv = rc.load_dotenv()
    presets = rc.load_presets()
    data = load_local()
    env = data.setdefault("env", {})

    if name in ("claude", "官方", "anthropic-official"):
        for k in KEYS:
            env.pop(k, None)
        if not env:
            data.pop("env")
        save_local(data)
        print("✅ 已切回 Anthropic 官方。重启 Claude Code 生效。")
        return

    if name == "custom":
        if not custom_url:
            sys.exit("用法：切换模型.py custom <Anthropic 兼容地址>   例：http://localhost:4000")
        p = {"name": custom_url, "anthropic_base_url": custom_url}
    else:
        if name not in presets:
            sys.exit(f"❌ 没有预设 {name}。可选：{' · '.join(presets)}")
        p = presets[name]
        if not p.get("anthropic_base_url"):
            sys.exit(f"❌ {p['name']} 没有 Anthropic 兼容接口，Claude Code 不能直连。两个办法：\n"
                     f"   ① 用自带运行时：./study（.env 里 STUDY_PROVIDER={name}）\n"
                     f"   ② 起 LiteLLM 代理把它转成 Anthropic 格式，再 `切换模型.py custom http://localhost:4000`"
                     f"（见 docs/接入其他模型.md）")

    key = rc.get("STUDY_API_KEY", dotenv)
    if not key:
        sys.exit("❌ .env 里的 STUDY_API_KEY 是空的。先 `cp .env.example .env` 再填 key" +
                 (f"（在这里拿：{p['key_url']}）" if p.get("key_url") else ""))
    model = rc.get("STUDY_MODEL", dotenv) or p.get("anthropic_model") or p.get("model", "")
    small = rc.get("STUDY_SMALL_MODEL", dotenv) or p.get("small_model") or model
    if not model:
        sys.exit("❌ 这个后端没有默认模型名 —— 在 .env 里填 STUDY_MODEL")

    env.update({
        "ANTHROPIC_BASE_URL": p["anthropic_base_url"],
        "ANTHROPIC_AUTH_TOKEN": key,
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": small,
        "CLAUDE_CODE_SUBAGENT_MODEL": model,
    })
    save_local(data)
    print(f"✅ Claude Code 在本项目里改用 {p['name']}：{model}（轻量档 {small}）")
    print(f"   写进了 .claude/settings.local.json（只在本机生效，不会被提交）。重启 Claude Code 生效，进去后 /status 可以核对。")
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("⚠️ 你的 shell 里设了 ANTHROPIC_API_KEY —— 它可能盖掉这里的配置、仍然走 Anthropic 计费。先 `unset ANTHROPIC_API_KEY`。")
    if p.get("docs"):
        print(f"   模型名以厂商文档为准：{p['docs']}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        status()
    else:
        switch(args[0], args[1] if len(args) > 1 else None)
