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
