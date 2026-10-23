"""Reference-image generation: capability is evidence, not an assumption."""
import json

import pytest


# ---------------------------------------------------------------- capability registry

def test_reference_and_text_are_separate_capabilities(monkeypatch):
    """Text-to-image working says nothing about reference input."""
    from app import image_capabilities as caps
    assert caps.supports_reference("gpt-image-2.5") is True
    assert caps.supports_generation("gpt-image-2.5") is True
    # The same model refuses image input: the two probed shapes differ.
    assert caps.supports_reference("gpt-image-2-w") is False
    assert caps.supports_generation("gpt-image-2-w") is True


def test_an_unprobed_combination_is_unknown_not_true():
    from app import image_capabilities as caps
    assert caps.supports_reference("some-model-nobody-probed") is None
    info = caps.describe("some-model-nobody-probed", True)
    assert info["state"] == "unverified"
    assert "不得当作可用能力" in info["note"]


def test_a_model_without_reference_support_is_refused_before_calling(monkeypatch):
    """Refuse from evidence instead of forwarding a request that returns 503."""
    monkeypatch.setenv("IMAGE_API_KEY", "test-key")
    from app import image_channel as ic
    with pytest.raises(ic.ImageChannelError) as error:
        ic.generate_from_product(b"fake", "prompt", model="gpt-image-2-w")
    assert "不支持参考图输入" in str(error.value)
    assert "503" in str(error.value)


def test_the_reference_model_can_be_configured(monkeypatch):
    from app import image_capabilities as caps
    monkeypatch.setenv("IMAGE_REFERENCE_MODEL", "my-model")
    assert caps.reference_model() == "my-model"


def test_registry_can_be_overridden_by_evidence_file(tmp_path, monkeypatch):
    from app import image_capabilities as caps
    path = tmp_path / "caps.json"
    path.write_text(json.dumps({
        "other-model|reference": {"supports": True, "probed_at": "2026-11-01",
                                  "evidence": "own probe", "input_shape": "generations+image"}
    }), encoding="utf-8")
    monkeypatch.setenv("IMAGE_CAPABILITY_FILE", str(path))
    assert caps.supports_reference("other-model") is True
    assert caps.probe_for("other-model", True)["evidence"] == "own probe"


# ---------------------------------------------------------------- timeouts

def test_an_upstream_timeout_says_it_is_an_upstream_timeout(monkeypatch):
    """上游卡死时，用户看到的必须明确是"上游超时"，不是笼统的"生成失败"。

    实测（2026-10-23）：上游 api 直接探测 TimeoutError @ 91s。这里模拟同样的情况，
    断言错误里带上"上游生图服务超时"和实际等待的秒数——超时预算是配置值，不是猜的。
    """
    from app import image_channel as ic

    def boom(request, timeout=None):
        raise TimeoutError("_ssl.c:3111: The handshake operation timed out")

    monkeypatch.setattr(ic.urllib.request, "urlopen", boom)
    with pytest.raises(ic.ImageChannelError) as error:
        ic._request("/images/generations", b"{}", "application/json", 120)
    message = str(error.value)
    assert "上游生图服务超时" in message
    assert "120" in message, "要写清等了多少秒，配置改了提示也要跟着改"
    assert "请稍后重试" in message
    assert error.value.status == 504


def test_a_plain_connection_error_is_not_reported_as_a_timeout(monkeypatch):
    """不能把"连不上"也说成"超时"——两种失败的处置不一样。"""
    from app import image_channel as ic

    def refused(request, timeout=None):
        raise ic.urllib.error.URLError("Connection refused")

    monkeypatch.setattr(ic.urllib.request, "urlopen", refused)
    with pytest.raises(ic.ImageChannelError) as error:
        ic._request("/images/generations", b"{}", "application/json", 120)
    assert "连接失败" in str(error.value)
    assert "超时" not in str(error.value)
