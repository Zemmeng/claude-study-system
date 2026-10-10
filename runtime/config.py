"""读 .env + shared/模型预设.json，得出「连哪个模型」。

优先级：命令行参数 > 环境变量 > .env > 预设默认值。
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESETS_PATH = os.path.join(ROOT, "shared", "模型预设.json")
ENV_PATH = os.path.join(ROOT, ".env")


class ConfigError(Exception):
    pass


def load_dotenv(path=ENV_PATH):
    vals = {}
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                vals[k] = v
    except FileNotFoundError:
        pass
    return vals


def load_presets():
    try:
        with open(PRESETS_PATH, encoding="utf-8") as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}


def get(key, dotenv, default=""):
    v = os.environ.get(key)
    if v is None or v == "":
        v = dotenv.get(key, "")
    return v if v != "" else default


def resolve(provider=None, model=None, base_url=None):
    """返回 dict(provider, name, base_url, api_key, model, small_model, stream)。缺东西就抛 ConfigError，信息直接给用户看。"""
    env = load_dotenv()
    presets = load_presets()
    provider = provider or get("STUDY_PROVIDER", env)
    preset = {}
    if provider:
        if provider not in presets:
            raise ConfigError(f"STUDY_PROVIDER={provider} 不在预设里。可选：{' · '.join(presets)}；"
                              f"或者不填 STUDY_PROVIDER，直接填 STUDY_BASE_URL")
        preset = presets[provider]
    base = base_url or get("STUDY_BASE_URL", env) or preset.get("openai_base_url", "")
    if not base:
        raise ConfigError("还没配置模型。先 `cp .env.example .env`，填 STUDY_PROVIDER 和 STUDY_API_KEY")
    key = get("STUDY_API_KEY", env)
    if not key and not preset.get("no_key"):
        hint = f"（在这里拿：{preset['key_url']}）" if preset.get("key_url") else ""
        raise ConfigError(f".env 里的 STUDY_API_KEY 是空的{hint}")
    mdl = model or get("STUDY_MODEL", env) or preset.get("model", "")
    if not mdl:
        raise ConfigError(f"{preset.get('name', provider or '这个接口')} 没有默认模型名 —— 在 .env 里填 STUDY_MODEL"
                          + (f"（模型列表见 {preset['docs']}）" if preset.get("docs") else ""))
    return {
        "provider": provider or "custom",
        "name": preset.get("name", provider or base),
        "base_url": base.rstrip("/"),
        "api_key": key or "ollama",
        "model": mdl,
        "small_model": get("STUDY_SMALL_MODEL", env) or preset.get("small_model", "") or mdl,
        "stream": get("STUDY_STREAM", env, "1") not in ("0", "false", "no"),
    }
