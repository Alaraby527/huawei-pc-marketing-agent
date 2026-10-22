"""Make the suite hermetic.

Running the app requires sourcing the private environment (``KB_SOURCE=local``,
``LOCAL_KB_ZIP``, the model and image credentials). Those same variables change what the tests
do: with a real knowledge base configured, a test that simulates a **knowledge-base failure**
no longer fails, so its assertion flips. Observed directly - the same file passed in 56 s with a
clean environment and failed in 103 s with the private environment sourced.

A suite whose result depends on the shell it runs in is worse than a slow one, because the
failure looks like a flake. This fixture removes the ambient settings for every test, so a test
that wants the real knowledge base or an image channel has to ask for it explicitly with
``monkeypatch.setenv`` - which the tests that need them already do.
"""
from __future__ import annotations

import pytest

# Ambient settings that change test behaviour. Cleared for every test unless a test sets them.
AMBIENT = (
    "KB_SOURCE", "LOCAL_KB_ZIP", "IMA_BASE_URL", "IMA_TOKEN", "KB_CACHE_DIR",
    "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "LLM_TIMEOUT",
    "IMAGE_API_BASE_URL", "IMAGE_API_KEY", "IMAGE_MODEL", "IMAGE_REFERENCE_MODEL",
    "IMAGE_BASE_URL", "IMAGE_FALLBACK_BASE_URL", "IMAGE_FALLBACK_API_KEY",
    "IMAGE_FALLBACK_MODEL", "IMAGE_BILLING_ENFORCE", "IMAGE_REQUIRE_ASSET_RIGHTS",
    "IMAGE_CURRENCY", "IMAGE_BUDGET_PER_REQUEST", "IMAGE_BUDGET_PER_DAY",
    "HOT_API_BASE", "NEWSNOW_INTERNAL_BASE_URL", "NEWSNOW_INTERNAL_TOKEN",
    "HOTSPOTS_ALLOW_SYNTHETIC", "HOTSPOTS_ENABLED", "HOTSPOTS_DATA_MODE",
    "LG_EMBEDDING_MODE", "LG_DEDUP_MODE", "DATABASE_URL",
)


@pytest.fixture(autouse=True)
def _hermetic_environment(monkeypatch):
    for name in AMBIENT:
        monkeypatch.delenv(name, raising=False)
    yield