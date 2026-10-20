"""Thin adapter to the user-configured OpenAI-Next-compatible endpoint.

Text completion drives platform copy; image generation is optional and only
ever produces atmosphere/background art, never the product itself. Product
photography always comes from ima (see app/evidence.py, app/retrieval.py).
Credentials are read from environment variables only and never logged.
"""
import base64
import json
import os
import re
import time
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


def model_registry():
    """Return configured text providers.

    LLM_MODELS_JSON (server-side only) supports multiple providers, e.g.
    [{"id":"deepseek-v4.1-flash","label":"DeepSeek v4.1 flash","base_url":"…",
      "api_key":"…","model":"deepseek-v4.1-flash","context":"1M","default":true}]
    Without it we fall back to the original single-provider LLM_* variables.
    """
    raw = (os.getenv("LLM_MODELS_JSON") or "").strip()
    if raw:
        try:
            items = json.loads(raw)
        except ValueError:
            items = []
        registry = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or item.get("model") or "").strip()
            base = str(item.get("base_url") or "").strip().rstrip("/")
            key = str(item.get("api_key") or "").strip()
            if not model_id or not base or not key:
                continue
            registry.append({
                "id": model_id,
                "label": str(item.get("label") or model_id),
                "base_url": base,
                "api_key": key,
                "model": str(item.get("model") or model_id),
                "context": str(item.get("context") or ""),
                "default": bool(item.get("default")),
                "max_tokens": item.get("max_tokens"),
                "extra_body": item.get("extra_body") if isinstance(item.get("extra_body"), dict) else {},
            })
        if registry:
            return registry
    base = os.getenv("LLM_BASE_URL", "").rstrip("/")
    key = os.getenv("LLM_API_KEY", "")
    default = os.getenv("LLM_MODEL", "deepseek-v4.1-flash")
    ids = [default] + [item.strip() for item in os.getenv("LLM_ALLOWED_MODELS", "").split(",") if item.strip()]
    registry = []
    for model_id in dict.fromkeys(ids):
        registry.append({"id": model_id, "label": model_id, "base_url": base, "api_key": key,
                         "model": model_id, "context": "", "default": model_id == default,
                         "max_tokens": None})
    return registry


def default_model():
    registry = model_registry()
    entry = next((item for item in registry if item.get("default")), registry[0])
    return entry["id"]


def allowed_models():
    return [item["id"] for item in model_registry()]


def model_catalog():
    """Safe model list for the browser: no keys or URLs."""
    return [{"id": item["id"], "label": item["label"], "context": item.get("context", ""),
             "default": item["id"] == default_model()}
            for item in model_registry()]


def _settings(model=None):
    registry = model_registry()
    entry = next((item for item in registry if item["id"] == (model or default_model())), None)
    if entry is None:
        raise ModelError("该模型未在服务端配置")
    max_tokens = entry.get("max_tokens")
    return {
        "base_url": entry["base_url"],
        "api_key": entry["api_key"],
        "model": entry["model"],
        "timeout": _env_int("LLM_TIMEOUT", 120, 10, 170),
        "max_tokens": int(max_tokens) if max_tokens else _env_int("LLM_MAX_TOKENS", 6000, 500, 8000),
        "extra_body": entry.get("extra_body") or {},
    }


def _image_settings():
    base = os.getenv("LLM_BASE_URL", "").rstrip("/")
    native = os.getenv("LLM_IMAGE_NATIVE_BASE", "").rstrip("/")
    if not native and "dashscope.aliyuncs.com" in base:
        native = "https://dashscope.aliyuncs.com/api/v1"
    return {
        "base_url": base,
        "api_key": os.getenv("LLM_API_KEY", ""),
        "model": os.getenv("LLM_IMAGE_MODEL", "wanx2.1-t2i-turbo"),
        "size": os.getenv("LLM_IMAGE_SIZE", "768*1024"),
        "native_base": native,
        "timeout": _env_int("LLM_IMAGE_TIMEOUT", 150, 30, 170),
        "enabled": os.getenv("ENABLE_AI_COVER", "0") == "1",
    }


def _check_https(base_url):
    parts = urlsplit(base_url)
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ModelError("模型服务地址需为 HTTPS；仅本机地址允许 HTTP")


def complete_json(system, payload, *, schema_hint="", model=None):
    """Call chat/completions and parse a JSON object from the response content.

    The payload is untrusted task data from the caller's perspective of the
    model, not additional instructions; callers must still validate the
    returned structure against their own contract before trusting it.
    """
    if model and model not in allowed_models():
        raise ModelError("该模型未在服务端允许列表；请在 LLM_ALLOWED_MODELS 中配置")
    settings = _settings(model)
    if not all([settings["base_url"], settings["api_key"], settings["model"]]):
        raise ModelError("请先配置 LLM_BASE_URL、LLM_API_KEY 和 LLM_MODEL")
    _check_https(settings["base_url"])
    user_content = json.dumps(payload, ensure_ascii=False)
    if schema_hint:
        user_content += "\n\n输出必须是严格 JSON，不要代码围栏。" + schema_hint
    body_data = {
        "model": settings["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_content}],
        "stream": False,
        "temperature": 0.7,
        "max_tokens": settings["max_tokens"],
        "response_format": {"type": "json_object"},
    }
    body_data.update(settings.get("extra_body") or {})
    body = json.dumps(body_data).encode()
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


def _post_json(url, body, settings, extra_headers=None):
    request = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + settings["api_key"],
                 "User-Agent": "huawei-pc-marketing-agent/" + __version__, **(extra_headers or {})},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=settings["timeout"]) as response:
            return json.loads(response.read(2 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        raise ModelError("生图服务 HTTP " + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ModelError("生图服务连接、读取或 JSON 解析失败") from None


def _image_mime(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def _download_generated(url, allow_any_https=False):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except (TypeError, ValueError):
        raise ModelError("生图结果地址无效") from None
    if parts.scheme != "https" or parts.username or parts.password:
        raise ModelError("生图结果必须使用 HTTPS 地址")
    if not allow_any_https and not (host == "dashscope.aliyuncs.com" or host.endswith(".aliyuncs.com")):
        raise ModelError("生图结果域名不在允许范围内")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "huawei-pc-marketing-agent/" + __version__}),
                                    timeout=60) as response:
            data = response.read(12 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ModelError("生图结果下载失败") from None
    if len(data) > 12 * 1024 * 1024:
        raise ModelError("生图结果超过单次读取上限")
    mime = _image_mime(data)
    if not mime:
        raise ModelError("生图结果不是受支持的图片格式")
    return data, mime


def _dashscope_native(prompt, settings):
    """DashScope text-to-image is asynchronous: submit, poll, then download."""
    base = settings["native_base"].rstrip("/")
    submit = _post_json(
        base + "/services/aigc/text2image/image-synthesis",
        {"model": settings["model"], "input": {"prompt": prompt[:1800]},
         "parameters": {"size": settings["size"], "n": 1}},
        settings, {"X-DashScope-Async": "enable"})
    output = submit.get("output") or {}
    task_id = output.get("task_id")
    if not task_id:
        raise ModelError("生图服务没有返回任务 ID")
    deadline = time.monotonic() + settings["timeout"]
    while time.monotonic() < deadline:
        time.sleep(2)
        request = urllib.request.Request(
            base + "/tasks/" + task_id,
            headers={"Authorization": "Bearer " + settings["api_key"],
                     "User-Agent": "huawei-pc-marketing-agent/" + __version__})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read(2 * 1024 * 1024))
        except urllib.error.HTTPError as exc:
            raise ModelError("生图任务查询 HTTP " + str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise ModelError("生图任务查询失败或超时") from None
        output = result.get("output") or {}
        status = output.get("task_status")
        if status == "SUCCEEDED":
            results = output.get("results") or []
            url = results[0].get("url") if results and isinstance(results[0], dict) else None
            if not url:
                raise ModelError("生图任务成功但没有图片地址")
            return _download_generated(url)
        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            raise ModelError("生图任务失败：" + str(status))
    raise ModelError("生图任务超时，请稍后重试")


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
    if settings["native_base"]:
        return _dashscope_native(guarded_prompt, settings)
    result = _post_json(
        settings["base_url"] + "/images/generations",
        {"model": settings["model"], "prompt": guarded_prompt[:1800],
         "size": settings["size"].replace("*", "x"), "n": 1},
        settings)
    try:
        item = result["data"][0]
    except (KeyError, IndexError, TypeError):
        raise ModelError("生图服务没有返回有效结果") from None
    if item.get("b64_json"):
        try:
            data = base64.b64decode(item["b64_json"], validate=True)
        except (ValueError, TypeError):
            raise ModelError("生图返回的数据无法解码") from None
        mime = _image_mime(data)
        if not mime:
            raise ModelError("生图返回的数据不是受支持的图片格式")
        return data, mime
    if item.get("url"):
        return _download_generated(item["url"], allow_any_https=True)
    raise ModelError("生图服务未返回图片数据")
