"""Semantic similarity layer — shadow mode, exact search, versioned vector spaces.

The literal MinHash layer cannot see a paraphrase: it compares character shingles, so a
rewritten post that makes the same points scores near zero. That is the gap this layer
covers, and the plan is specific about how it must be built:

* the embedding model id, dimension and normalisation are recorded with every vector;
* a different embedding model is a **different index version**, and vectors from two
  versions are never compared — mixing spaces produces meaningless distances;
* the index is built from delivered content, with the same authorisation and retention as
  the text itself;
* thresholds are calibrated on labelled pairs before they may block, so this runs in
  **shadow mode** and applies no threshold at all;
* at this scale the search is **exact** (a full cosine scan over the stored vectors), which
  the plan prefers as the baseline; no approximate index is introduced, so there is no
  recall to monitor yet.

Enabled explicitly with ``LG_EMBEDDING_MODE=shadow``. It is off by default because it is a
paid network call on every delivery, and because a test suite must not reach the network.
"""
from __future__ import annotations

import math
import os

from . import dedup

MODE_OFF = "off"
MODE_SHADOW = "shadow"
VIEW_FULL = "full"


def mode() -> str:
    value = str(os.getenv("LG_EMBEDDING_MODE", MODE_OFF)).strip().lower()
    return MODE_SHADOW if value == MODE_SHADOW else MODE_OFF


def index_version(model_id: str, dimension: int) -> str:
    """Identifies a vector space. Two different versions must never be compared."""
    return "emb-v1:" + str(model_id or "unknown") + ":" + str(int(dimension or 0))


def normalize(vector: list[float]) -> list[float]:
    """Store unit vectors, so cosine similarity is a plain dot product."""
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return [0.0 for _ in vector]
    return [value / norm for value in vector]


def embed(text: str, *, view: str = VIEW_FULL, model: str | None = None) -> dict | None:
    """One embedding, with everything needed to compare it safely later."""
    from ..model import embed_texts

    vectors, meta = embed_texts([text], model=model)
    if not vectors:
        return None
    vector = normalize([float(value) for value in vectors[0]])
    return {"model_id": str(meta.get("model") or ""), "dimension": len(vector),
            "view": view, "index_version": index_version(meta.get("model") or "", len(vector)),
            "vector": vector, "usage": meta.get("usage") or {}}


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def compare(candidate: dict | None, history: list[dict]) -> dict:
    """Nearest neighbour among vectors from the *same* index version. Never blocks."""
    result = {"mode": mode(), "layer": "semantic", "kind": "none", "similarity": 0.0,
              "nearest": None, "compared": 0, "index_version": None,
              "calibrated": False, "blocks": False}
    if result["mode"] == MODE_OFF:
        result["kind"] = "not_run"
        return result
    if not candidate:
        result["kind"] = "not_run"
        result["reason"] = "no_embedding"
        return result
    result["index_version"] = candidate["index_version"]
    version = candidate["index_version"]
    dimension = candidate["dimension"]
    best, nearest = 0.0, None
    compared = 0
    for row in history:
        # Only the same space, and only the same width. Anything else is not comparable.
        if row.get("index_version") != version or int(row.get("dimension") or 0) != dimension:
            continue
        compared += 1
        score = cosine(candidate["vector"], row.get("vector") or [])
        if score > best:
            best, nearest = score, row
    result["compared"] = compared
    result["similarity"] = round(best, 4)
    if nearest is not None:
        result["nearest"] = {"run_id": nearest.get("run_id") or "",
                             "conversation_id": nearest.get("conversation_id") or "",
                             "index_version": nearest.get("index_version") or ""}
    result["kind"] = "semantic" if best > 0 else "none"
    return result


def combined(literal: dict, semantic: dict) -> dict:
    """One evidence record from both layers, keeping the literal view authoritative.

    Reported side by side rather than merged into a single "originality" number: the plan
    is explicit that these are evidence, not a verdict, and that a score must not silently
    replace a judgement.
    """
    exact = str(literal.get("kind")) == "exact"
    return {"mode": literal.get("mode"), "blocks": False, "calibrated": False,
            "literal": literal, "semantic": semantic,
            "kind": "exact" if exact else str(literal.get("kind") or "none"),
            "note": ("任一层都未应用阈值；未校准的分数不做决定"
                     if not exact else "精确重复（规范化摘要一致）")}


def literal_algorithm() -> str:
    return dedup.ALGORITHM