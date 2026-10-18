"""Xiaohongshu MCP adapter: mapping and unconfigured behavior."""
import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

import app.xhs as xhs
from app.main import app

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg=="
)


def test_feed_topics_maps_note_identity_and_metrics():
    feeds = [{"id": "abc", "xsecToken": "tok", "noteCard": {
        "displayTitle": "大学新生选本避坑", "interactInfo": {"likedCount": "1200"}}}]
    topics = xhs._feed_topics(feeds, "课堂学习", "大学新生")
    assert topics[0]["title"] == "大学新生选本避坑"
    assert topics[0]["source"] == "xiaohongshu-mcp"
    assert topics[0]["data_mode"] == "live_xhs"
    assert "explore/abc" in topics[0]["url"]
    assert "xsec_token=tok" in topics[0]["url"]


def test_xhs_endpoints_are_graceful_when_unconfigured(monkeypatch):
    monkeypatch.delenv("XHS_MCP_BASE", raising=False)
    client = TestClient(app)
    status = client.get("/api/v1/xhs/status").json()
    assert status["configured"] is False
    assert client.get("/api/v1/xhs/login/qrcode").status_code == 503
    assert client.get("/api/v1/xhs/hot").status_code == 503


def test_qrcode_normalizes_raw_base64(monkeypatch):
    monkeypatch.setenv("XHS_MCP_BASE", "http://example.invalid")
    monkeypatch.setattr(xhs, "_request", lambda *args, **kwargs: {
        "is_logged_in": False, "img": base64.b64encode(PNG).decode(), "timeout": "4m"})
    result = asyncio.run(xhs.login_qrcode())
    assert result["qr_image"].startswith("data:image/png;base64,")
    assert result["is_logged_in"] is False
