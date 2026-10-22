"""generate_scene_image: verified reference, honest labelling, budget and capability gates."""
import asyncio
import json
import os
import time

import pytest

from app import agent_runtime as ar
from app import image_capabilities as caps


class _Ctx:
    def __init__(self, deadline_in=600):
        self.catalog = object()
        self.deadline = time.monotonic() + deadline_in


def _run(coro):
    return asyncio.run(coro)


def test_it_refuses_without_a_prompt():
    result = _run(ar._generate_scene(_Ctx(), {"product_id": "P-1"}))
    assert result["ok"] is False and result["error"] == "PROMPT_REQUIRED"


def test_it_refuses_when_the_model_lacks_reference_support(monkeypatch):
    """The refusal cites probe evidence instead of forwarding a call that returns 503."""
    monkeypatch.setenv("IMAGE_REFERENCE_MODEL", "gpt-image-2-w")
    monkeypatch.setattr(ar, "_resolve_product", lambda *a, **k: object())
    result = _run(ar._generate_scene(_Ctx(), {"product_id": "P-1", "prompt": "x"}))
    assert result["ok"] is False
    assert result["error"] == "CAPABILITY_UNAVAILABLE"
    assert result["capability"]["supports"] is False
    assert "503" in result["capability"]["evidence"]


def test_it_refuses_when_the_run_budget_cannot_fit_an_image(monkeypatch):
    """Starting a 100 s call inside a 30 s budget would strand the delivery."""
    monkeypatch.setenv("IMAGE_MIN_SECONDS", "120")
    monkeypatch.setattr(ar, "_resolve_product", lambda *a, **k: object())
    result = _run(ar._generate_scene(_Ctx(deadline_in=30),
                                     {"product_id": "P-1", "prompt": "x"}))
    assert result["ok"] is False
    assert result["error"] == "INSUFFICIENT_TIME"
    assert result["remaining_seconds"] <= 30
    assert result["required_seconds"] == 120
    assert "AGENT_RUN_BUDGET_SECONDS" in result["message"]


def test_it_refuses_without_a_verified_reference_image(monkeypatch):
    """No verified product image means no reference: the model must not invent the look."""
    monkeypatch.setattr(ar, "_resolve_product", lambda *a, **k: object())

    async def no_images(*a, **k):
        return [], None

    monkeypatch.setattr(ar, "verified_images", no_images)
    result = _run(ar._generate_scene(_Ctx(), {"product_id": "P-1", "prompt": "x"}))
    assert result["ok"] is False
    assert result["error"] == "NO_REFERENCE_IMAGE"
    assert "不编造产品外观" in result["message"]


def test_a_generated_image_is_labelled_as_generated(monkeypatch):
    """The pipeline must never hand a generated scene over as a product photo."""
    monkeypatch.setattr(ar, "_resolve_product", lambda *a, **k: object())

    async def one_image(*a, **k):
        return [{"image_id": "I-1", "caption": "原野绿正面产品展示", "model_verified": True,
                 "access_url": "/api/v1/assets/tok123"}], None

    monkeypatch.setattr(ar, "verified_images", one_image)
    monkeypatch.setattr(ar, "get_asset", lambda token: (b"png-bytes", "image/png"))
    monkeypatch.setattr(ar, "store_asset", lambda body, mime: "/api/v1/assets/out123")

    async def fake_to_thread(fn, *args):
        return [b"generated"], "primary"

    monkeypatch.setattr(ar.asyncio, "to_thread", fake_to_thread)
    result = _run(ar._generate_scene(_Ctx(), {"product_id": "P-1", "prompt": "宿舍书桌"}))
    assert result["ok"] is True
    assert result["images"][0]["kind"] == "generated_scene"
    assert result["reference"]["image_id"] == "I-1"
    assert "不是产品实物照" in result["notice"]
    assert "不得作为产品外观" in result["notice"]
    assert result["capability"]["model"] == caps.reference_model()


def test_a_channel_failure_is_reported_never_disguised(monkeypatch):
    monkeypatch.setattr(ar, "_resolve_product", lambda *a, **k: object())

    async def one_image(*a, **k):
        return [{"image_id": "I-1", "caption": "正面产品", "model_verified": True,
                 "access_url": "/api/v1/assets/tok"}], None

    monkeypatch.setattr(ar, "verified_images", one_image)
    monkeypatch.setattr(ar, "get_asset", lambda token: (b"png", "image/png"))

    async def boom(fn, *args):
        from app.image_channel import ImageChannelError
        raise ImageChannelError("图像通道未配置 IMAGE_API_KEY", 503)

    monkeypatch.setattr(ar.asyncio, "to_thread", boom)
    result = _run(ar._generate_scene(_Ctx(), {"product_id": "P-1", "prompt": "x"}))
    assert result["ok"] is False and result["error"] == "IMAGE_CHANNEL_ERROR"
    assert "IMAGE_API_KEY" in result["message"]


def test_tool_is_in_the_whitelist_and_has_a_spec():
    names = [spec["name"] for spec in ar.TOOL_SPECS]
    assert "generate_scene_image" in names
    spec = next(s for s in ar.TOOL_SPECS if s["name"] == "generate_scene_image")
    assert spec["parameters"]["required"] == ["product_id", "prompt"]
    assert "场景图" in spec["description"]


def test_an_expired_reference_is_a_clear_refusal(monkeypatch):
    """get_asset returns None after the TTL; that must not raise inside the tool."""
    monkeypatch.setattr(ar, "_resolve_product", lambda *a, **k: object())

    async def one_image(*a, **k):
        return [{"image_id": "I-1", "caption": "正面产品", "model_verified": True,
                 "access_url": "/api/v1/assets/gone"}], None

    monkeypatch.setattr(ar, "verified_images", one_image)
    monkeypatch.setattr(ar, "get_asset", lambda token: None)
    result = _run(ar._generate_scene(_Ctx(), {"product_id": "P-1", "prompt": "x"}))
    assert result["ok"] is False
    assert result["error"] == "REFERENCE_EXPIRED"
    assert result["reference"]["image_id"] == "I-1"
