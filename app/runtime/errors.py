"""User-facing error text.

The runtime must not hand an end user a developer instruction such as
"未配置IMA凭证，请在服务端环境变量或本机 ~/.config/ima 中配置". That message is
useless to a store clerk, and it names internal configuration surfaces.

So: the operator-facing ``stop_reason`` keeps a short machine-ish marker, and anything
shown to the user goes through :func:`public_error`, which never contains a credential
name, an environment variable, or a filesystem path.
"""
from __future__ import annotations

import re

# Knowledge-base surfaces.
_KB_TOKENS = re.compile(
    r"IMA|知识库|图片索引|official-kb|LOCAL_KB|KB_SOURCE|KB_ZIP", re.IGNORECASE)
# Model / gateway surfaces.
_MODEL_TOKENS = re.compile(
    r"模型|LLM|超时|网关|HTTP|连接|api[_-]?key|endpoint", re.IGNORECASE)
# Anything else that must never be echoed verbatim.
_SENSITIVE = re.compile(
    r"IMA|\.config|环境变量|Secrets|secret"
    r"|LLM_[A-Z_]+|DATABASE_URL|AGENT_DB_PATH|LG_[A-Z_]+|MODELSCOPE"
    r"|official-kb|LOCAL_KB|KB_SOURCE|XHS_MCP"
    r"|/app/|/Users/|/tmp/|~/"
    r"|凭证|密钥|api[_-]?key|ZIP",
    re.IGNORECASE)

KNOWLEDGE_UNAVAILABLE = ("知识库当前不可用，我没法核对产品事实。"
                         "可以先给我一个不依赖参数的创意方向，或者稍后重试。")
MODEL_UNAVAILABLE = "主模型当前不可用，本轮没有改用固定模板冒充成功，可以稍后重试。"
GENERIC_FAILURE = "本轮执行没有完成，已保留已有材料，可以稍后重试。"


def public_error(text: str) -> str:
    """Return a user-safe sentence for an internal error."""
    raw = str(text or "").strip()
    if not raw:
        return GENERIC_FAILURE
    knowledge = bool(_KB_TOKENS.search(raw))
    model = bool(_MODEL_TOKENS.search(raw))
    if knowledge and not model:
        return KNOWLEDGE_UNAVAILABLE
    if model:
        return MODEL_UNAVAILABLE
    if _SENSITIVE.search(raw):
        return GENERIC_FAILURE
    return GENERIC_FAILURE


def error_marker(text: str) -> str:
    """Short, non-revealing marker for the operator-facing stop reason."""
    raw = str(text or "").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
        return raw[:60]
    knowledge = bool(_KB_TOKENS.search(raw))
    model = bool(_MODEL_TOKENS.search(raw))
    if knowledge and not model:
        return "knowledge_unavailable"
    if model:
        return "model_unavailable"
    return "internal_error"


def sanitize_observation(outcome: dict) -> dict:
    """Make a tool outcome safe to show the model and, ultimately, the user."""
    result = dict(outcome or {})
    message = result.get("message")
    if isinstance(message, str) and message.strip():
        safe = public_error(message)
        if safe != message:
            # Keep the machine-readable code; replace only the human sentence.
            result["message"] = safe
            result["internal_detail_redacted"] = True
    return result