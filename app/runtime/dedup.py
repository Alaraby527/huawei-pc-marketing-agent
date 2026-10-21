"""Content fingerprints and near-duplicate detection — shadow mode only.

The business problem this addresses is not "can one person write a post" but "will ten
thousand store staff produce the same post". The plan is explicit about the sequencing:

* generating few, genuinely different directions matters more than wording tweaks;
* similarity scores are *evidence*, not an "originality score";
* thresholds must be calibrated on labelled pairs before they may block anything, so the
  first phase runs in **shadow mode** and only records.

What this module deliberately does NOT do yet, because doing it without calibration would
be a false claim:

* no semantic/embedding view (that needs an embedding model and a separate index version);
* no blocked deliveries, no `duplicate` status change;
* no cross-store scope authorisation — comparison is scoped to one workspace, which is
  the honest extent of what is implemented.
"""
from __future__ import annotations

import hashlib
import os
import unicodedata

# 64 permutations is the usual precision/space trade-off: the estimate's standard error is
# about 1/sqrt(64) ≈ 12%, which is fine for shadow-mode evidence and cheap to store.
MINHASH_SIZE = 64
SHINGLE_SIZE = 4
MAX_SHINGLES = 4000
ALGORITHM = "minhash-64-v1"

_MERSENNE = (1 << 61) - 1
# Fixed coefficients: a stable fingerprint must not change between runs or processes,
# otherwise historical rows become incomparable.
_COEFFICIENTS = [
    (1 + 2 * index, 1 + 4 * index) for index in range(MINHASH_SIZE)
]

_PUNCTUATION = "「」『』（）()【】[]{}<>《》〈〉“”\"'‘’·、，。；：！？,.;:!?—-…～~*#@$%^&+=|\\/`"


def mode() -> str:
    """``shadow`` records only; ``off`` disables the pass. Never blocks in either mode."""
    value = str(os.getenv("LG_DEDUP_MODE", "shadow")).strip().lower()
    return value if value in ("shadow", "off") else "shadow"


def normalize(text: str) -> str:
    """A comparison view: full-width to half-width, no layout, no punctuation.

    This is the *full* view (facts included). The plan's "creative view", which down-weights
    fixed facts so a necessary clause does not look like copying, is not implemented — so a
    similarity here is evidence, never a verdict.
    """
    body = unicodedata.normalize("NFKC", str(text or "")).casefold()
    kept = []
    for char in body:
        if char.isspace() or char in _PUNCTUATION:
            continue
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            kept.append(char)
    return "".join(kept)


def _base_hash(shingle: str) -> int:
    digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % _MERSENNE


def fingerprint(text: str) -> dict:
    """A comparable fingerprint of a delivered text.

    Stores a digest for exact repeats plus a MinHash sketch for near repeats, so history
    can be compared without keeping a second copy of the copy.
    """
    normalized = normalize(text)
    shingles = [normalized[index:index + SHINGLE_SIZE]
                for index in range(max(0, len(normalized) - SHINGLE_SIZE + 1))]
    if len(shingles) > MAX_SHINGLES:
        step = len(shingles) // MAX_SHINGLES + 1
        shingles = shingles[::step]
    sketch = [_MERSENNE] * MINHASH_SIZE
    for shingle in shingles:
        base = _base_hash(shingle)
        for index, (a, b) in enumerate(_COEFFICIENTS):
            value = (a * base + b) % _MERSENNE
            if value < sketch[index]:
                sketch[index] = value
    return {
        "algorithm": ALGORITHM,
        "normalized_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "length": len(normalized),
        "shingles": len(shingles),
        "minhash": sketch,
    }


def estimated_similarity(left: dict, right: dict) -> float:
    """Estimated Jaccard similarity from two sketches of the same algorithm."""
    if not left or not right:
        return 0.0
    if left.get("algorithm") != right.get("algorithm"):
        # Different algorithms are different spaces; comparing them would be meaningless.
        return 0.0
    a = left.get("minhash") or []
    b = right.get("minhash") or []
    if len(a) != len(b) or not a:
        return 0.0
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


def compare(candidate: dict, history: list[dict]) -> dict:
    """Compare one fingerprint against history. Returns evidence; it never blocks."""
    result = {"mode": mode(), "algorithm": ALGORITHM, "kind": "none", "similarity": 0.0,
              "exact_match": None, "nearest": None, "compared": len(history),
              "calibrated": False, "blocks": False}
    if result["mode"] == "off":
        result["kind"] = "not_run"
        return result
    best = 0.0
    best_row = None
    for row in history:
        if row.get("normalized_sha256") and \
                row["normalized_sha256"] == candidate.get("normalized_sha256"):
            result["kind"] = "exact"
            result["similarity"] = 1.0
            result["exact_match"] = row.get("run_id") or row.get("id")
            return result
        score = estimated_similarity(candidate, row)
        if score > best:
            best, best_row = score, row
    result["similarity"] = round(best, 4)
    if best_row is not None:
        result["nearest"] = {"run_id": best_row.get("run_id") or "",
                             "conversation_id": best_row.get("conversation_id") or "",
                             "content_hash": (best_row.get("normalized_sha256") or "")[:16]}
    # No threshold is applied: an uncalibrated threshold must not decide anything yet.
    result["kind"] = "near" if best > 0 else "none"
    return result