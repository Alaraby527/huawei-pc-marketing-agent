"""Semantic layer: versioned vector spaces, exact search, no threshold and no verdict."""
import pytest

from app.runtime import semantic


def _vec(*values):
    return list(values)


def test_index_version_identifies_the_space():
    left = semantic.index_version("text-embedding-v3", 1024)
    right = semantic.index_version("other-model", 1024)
    wider = semantic.index_version("text-embedding-v3", 2048)
    assert left != right, "a different model is a different space"
    assert left != wider, "a different dimension is a different space"


def test_normalize_produces_a_unit_vector():
    vector = semantic.normalize([3.0, 4.0])
    assert semantic.cosine(vector, vector) == pytest.approx(1.0)
    assert semantic.normalize([0.0, 0.0]) == [0.0, 0.0]


def test_cosine_needs_matching_widths():
    assert semantic.cosine([1.0, 0.0], [1.0]) == 0.0
    assert semantic.cosine([], [1.0]) == 0.0


def test_off_mode_reports_not_run(monkeypatch):
    monkeypatch.setenv("LG_EMBEDDING_MODE", "off")
    result = semantic.compare({"index_version": "x", "dimension": 2, "vector": [1.0, 0.0]}, [])
    assert result["kind"] == "not_run"
    assert result["blocks"] is False


def test_vectors_from_another_index_version_are_never_compared(monkeypatch):
    """A distance across two embedding models is meaningless, so it is not computed."""
    monkeypatch.setenv("LG_EMBEDDING_MODE", "shadow")
    candidate = {"index_version": semantic.index_version("model-a", 3),
                 "dimension": 3, "vector": semantic.normalize(_vec(1.0, 0.0, 0.0))}
    history = [{"run_id": "r1", "index_version": semantic.index_version("model-b", 3),
                "dimension": 3, "vector": semantic.normalize(_vec(1.0, 0.0, 0.0))},
               {"run_id": "r2", "index_version": semantic.index_version("model-a", 4),
                "dimension": 4, "vector": semantic.normalize(_vec(1.0, 0.0, 0.0, 0.0))}]
    result = semantic.compare(candidate, history)
    assert result["compared"] == 0
    assert result["kind"] == "none"
    assert result["index_version"] == candidate["index_version"]


def test_a_paraphrase_is_found_by_the_semantic_layer(monkeypatch):
    """The literal layer misses a paraphrase; this is what the layer exists for."""
    monkeypatch.setenv("LG_EMBEDDING_MODE", "shadow")
    version = semantic.index_version("model-a", 3)
    candidate = {"index_version": version, "dimension": 3,
                 "vector": semantic.normalize(_vec(0.9, 0.1, 0.0))}
    history = [{"run_id": "r1", "conversation_id": "c1", "index_version": version,
                "dimension": 3, "vector": semantic.normalize(_vec(1.0, 0.0, 0.0))},
               {"run_id": "r2", "conversation_id": "c2", "index_version": version,
                "dimension": 3, "vector": semantic.normalize(_vec(0.0, 1.0, 0.0))}]
    result = semantic.compare(candidate, history)
    assert result["compared"] == 2, "exact scan over the same space"
    assert result["nearest"]["run_id"] == "r1"
    assert result["similarity"] > 0.9
    assert result["blocks"] is False
    assert result["calibrated"] is False


def test_searches_are_exact_not_approximate(monkeypatch):
    """At this scale every same-space vector is compared: there is no recall to lose."""
    monkeypatch.setenv("LG_EMBEDDING_MODE", "shadow")
    version = semantic.index_version("model-a", 2)
    history = [{"run_id": f"r{index}", "index_version": version, "dimension": 2,
                "vector": semantic.normalize(_vec(1.0, float(index) / 100))}
               for index in range(50)]
    candidate = {"index_version": version, "dimension": 2,
                 "vector": semantic.normalize(_vec(1.0, 0.0))}
    assert semantic.compare(candidate, history)["compared"] == 50


def test_a_missing_embedding_is_reported_not_assumed_clean(monkeypatch):
    monkeypatch.setenv("LG_EMBEDDING_MODE", "shadow")
    result = semantic.compare(None, [])
    assert result["kind"] == "not_run"
    assert result["reason"] == "no_embedding"


def test_combined_report_keeps_the_layers_separate():
    literal = {"mode": "shadow", "kind": "near", "similarity": 0.4}
    sem = {"mode": "shadow", "kind": "semantic", "similarity": 0.88}
    report = semantic.combined(literal, sem)
    assert report["literal"] is literal and report["semantic"] is sem
    assert report["blocks"] is False
    assert report["calibrated"] is False
    assert "未应用阈值" in report["note"]


def test_an_exact_match_is_stated_plainly():
    report = semantic.combined({"mode": "shadow", "kind": "exact"}, {"kind": "none"})
    assert report["kind"] == "exact"
    assert "精确重复" in report["note"]
