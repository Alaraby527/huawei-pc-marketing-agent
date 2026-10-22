"""Probe-backed image capability registry.

The plan's rule is blunt: "未知或未测试一律不写 true". A capability claim that was never
probed is exactly how a system ends up promising reference-image editing and returning a
gateway error to a store clerk.

So capability is recorded here as evidence, per (model, input shape), with three states:

* ``True``  — a real request returned a real image;
* ``False`` — a real request failed in a way that identifies the capability as absent;
* ``None``  — not probed. Callers must treat this as unknown, not as working.

The defaults below are the probes actually run against the configured endpoint on
2026-10-22; see ``docs/20_图像能力探针记录_2026-10-22.md`` for the request/response evidence.
A deployment can extend or override them with ``IMAGE_CAPABILITY_FILE`` (JSON), which is how
a different provider gets its own evidence instead of inheriting someone else's.
"""
from __future__ import annotations

import json
import os

# (model, has_reference_image) -> verdict
PROBED: dict[tuple[str, bool], dict] = {
    ("gpt-image-2.5", True): {
        "supports": True, "probed_at": "2026-10-22",
        "evidence": "POST /images/generations with an `image` data URL returned HTTP 200 "
                    "and a real PNG (1086x1448) in 24-102 s",
        "input_shape": "generations+image(data-url)"},
    ("gpt-image-2.5", False): {
        "supports": True, "probed_at": "2026-10-22",
        "evidence": "text-to-image returned HTTP 200 for 1024x1024, 1024x1536, 1536x1024, "
                    "1080x1440 and auto",
        "input_shape": "generations"},
    ("gpt-image-2-w", False): {
        "supports": True, "probed_at": "2026-10-22",
        "evidence": "text-to-image returned HTTP 200 (b64 PNG, 24.4 s) and accepted "
                    "1024x1536, 1536x1024, 1080x1440 and auto",
        "input_shape": "generations"},
    ("gpt-image-2-w", True): {
        "supports": False, "probed_at": "2026-10-22",
        "evidence": "three requests carrying an image returned HTTP 503 "
                    "(图片服务暂时繁忙或不可用) while the same model without an image "
                    "returned 200; /images/edits also returned 503",
        "input_shape": "generations+image or edits"},
}

UNKNOWN = "unverified"


def _load_overrides() -> dict:
    path = (os.getenv("IMAGE_CAPABILITY_FILE") or "").strip()
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    out = {}
    for key, value in (data or {}).items():
        model, _, tail = str(key).partition("|")
        has_reference = tail.strip().lower() in ("reference", "true", "1", "yes")
        out[(model.strip(), has_reference)] = value if isinstance(value, dict) else {}
    return out


def probe_for(model: str, has_reference: bool) -> dict | None:
    entry = _load_overrides().get((model, has_reference))
    if entry is None:
        entry = PROBED.get((model, has_reference))
    if entry is None:
        # Same model, other input shape: the fact that text-to-image works says nothing
        # about reference input, which is the whole point of this registry.
        return None
    return dict(entry)


def supports_reference(model: str) -> bool | None:
    """True / False / None (unknown). Callers must not read None as "works"."""
    entry = probe_for(model, True)
    if entry is None:
        return None
    return bool(entry.get("supports"))


def supports_generation(model: str) -> bool | None:
    entry = probe_for(model, False)
    return None if entry is None else bool(entry.get("supports"))


def describe(model: str, has_reference: bool) -> dict:
    entry = probe_for(model, has_reference)
    if entry is None:
        return {"model": model, "has_reference": has_reference, "state": UNKNOWN,
                "supports": None,
                "note": "该组合没有探针证据；不得当作可用能力"}
    return {"model": model, "has_reference": has_reference,
            "state": "verified_yes" if entry.get("supports") else "verified_no",
            "supports": bool(entry.get("supports")),
            "probed_at": entry.get("probed_at", ""), "evidence": entry.get("evidence", ""),
            "input_shape": entry.get("input_shape", "")}


def reference_model() -> str:
    """The model used when a reference image is supplied."""
    return (os.getenv("IMAGE_REFERENCE_MODEL") or "gpt-image-2.5").strip()