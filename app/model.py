"""Thin adapter to the user-configured OpenAI-Next-compatible endpoint.

Text completion drives platform copy; image generation is optional and only
ever produces atmosphere/background art, never the product itself. Product
photography always comes from ima (see app/evidence.py, app/retrieval.py).
Credentials are read from environment variables only and never logged.
"""
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import __version__


class ModelError(RuntimeError):
    pass


def _env_int(name, default, low, high):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


def _settings():
    base = os.getenv("LLM_BASE_URL", "").rstrip("/")
    return {
        "base_url": base,
        "api_key": os.getenv("LLM_API_KEY", ""),
        "model": os.getenv("LLM_MODEL", "deepseek-v4.1-flash"),
        "timeout": _env_int("LLM_TIMEOUT", 90, 10, 170),
        "max_tokens": _env_int("LLM_MAX_TOKENS", 4000, 500, 8000),
    }


def _image_settings():
    base = os.getenv("LLM_BASE_URL", "").rstrip("/")
    return {
        "base_url": base,
        "api_key": os.getenv("LLM_API_KEY", ""),
        "model": os.getenv("LLM_IMAGE_MODEL", "gpt-image-2"),
        "timeout": _env_int("LLM_IMAGE_TIMEOUT", 150, 30, 170),
        "enabled": os.getenv("ENABLE_AI_COVER", "0") == "1",
    }


def _check_https(base_url):
    parts = urlsplit(base_url)
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ModelError("模型服务地址需为 HTTPS；仅本机地址允许 HTTP")


def complete_json(system, payload, *, schema_hint=""):
    """Call chat/completions and parse a JSON object from the response content.

    The payload is untrusted task data from the caller's perspective of the
    model, not additional instructions; callers must still validate the
    returned structure against their own contract before trusting it.
    """
    settings = _settings()
    if not all([settings["base_url"], settings["api_key"], settings["model"]]):
        raise ModelError("请先配置 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL")
    _check_https(settings["base_url"])
    user_content = json.dumps(payload, ensure_ascii=False)
    if schema_hint:
        user_content += "\n\n输出必须是严格 JSON，不要代码围栏。" + schema_hint
    body = json.dumps({
        "model": settings["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
        "stream": False,
        "temperature": 0.7,
        "max_tokens": settings["max_tokens"],
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        settings["base_url"] + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + settings["api_key"],
                 "User-Agent": "huawei-pc-marketing-agent/" + __version__},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=settings["timeout"]) as response:
            raw = response.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        raise ModelError("模型服务 HTTP " + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ModelError("模型连接或读取超时，请检查服务地址和网络") from None
    try:
        result = json.loads(raw)
        content = result["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("content 不是字符串")
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        parsed = json.loads(content)
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelError("模型没有返回有效的 JSON 结构") from None
    return parsed, {"provider": "openai-next-compatible", "model": settings["model"],
                    "usage": result.get("usage", {})}


def image_configured():
    settings = _image_settings()
    return settings["enabled"] and all([settings["base_url"], settings["api_key"], settings["model"]])


def generate_cover_image(prompt):
    """Generate an atmosphere/background illustration only. Never the product itself.

    Returns (bytes, mime) or raises ModelError. Callers must keep this
    strictly decorative — product photography must come from ima.
    """
    settings = _image_settings()
    if not settings["enabled"]:
        raise ModelError("AI 封面氛围图功能未开启")
    if not all([settings["base_url"], settings["api_key"], settings["model"]]):
        raise ModelError("请先配置 LLM_BASE_URL、LLM_API_KEY 和 LLM_IMAGE_MODEL")
    _check_https(settings["base_url"])
    guarded_prompt = (prompt + "。严格要求：不出现任何笔记本电脑、平板、手机或其他数码产品实体；"
                       "只画抽象背景、光影、桌面场景道具（杯子、绿植、灯具等），不画产品主体；无文字。")
    body = json.dumps({"model": settings["model"], "prompt": guarded_prompt[:1800], "size": "1024x1024", "n": 1}).encode()
    req = urllib.request.Request(
        settings["base_url"] + "/images/generations", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + settings["api_key"],
                 "User-Agent": "huawei-pc-marketing-agent/" + __version__}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=settings["timeout"]) as response:
            raw = response.read(12 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        raise ModelError("生图服务 HTTP " + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ModelError("生图连接或读取超时") from None
    try:
        result = json.loads(raw)
        item = result["data"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelError("生图服务没有返回有效结果") from None
    import base64
    b64 = item.get("b64_json")
    if not b64:
        raise ModelError("生图服务未返回图片数据")
    try:
        data = base64.b64decode(b64, validate=True)
    except (ValueError, TypeError):
        raise ModelError("生图返回的数据无法解码") from None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data, "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return data, "image/jpeg"
    raise ModelError("生图返回的数据不是受支持的图片格式")
