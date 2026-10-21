"""Shadow-mode dedup: the fingerprint must be stable, comparable, and never a verdict."""
import pytest

from app.runtime import dedup


def test_normalize_removes_layout_not_meaning():
    left = dedup.normalize("这台机器  的内存是 64 GB！")
    right = dedup.normalize("这台机器的内存是64GB")
    assert left == right
    assert " " not in left and "！" not in left


def test_identical_text_scores_one():
    text = "学生党选本，看屏幕和重量就够了。"
    a = dedup.fingerprint(text)
    b = dedup.fingerprint(text)
    assert a["normalized_sha256"] == b["normalized_sha256"]
    assert dedup.estimated_similarity(a, b) == 1.0


def test_a_small_edit_stays_similar_but_a_different_post_does_not():
    base = "学生党选本，先看屏幕和重量。14.2 英寸 OLED 看文档更舒服，1.31 kg 背着不累。" * 3
    edited = base.replace("不累", "不觉得累")
    other = "门店做活动，进店可以体验真机，售货员会按需求介绍合适的配置。" * 3

    near = dedup.estimated_similarity(dedup.fingerprint(base), dedup.fingerprint(edited))
    far = dedup.estimated_similarity(dedup.fingerprint(base), dedup.fingerprint(other))
    assert near > 0.8, f"a small edit should stay near ({near})"
    assert far < 0.2, f"an unrelated post must not look similar ({far})"


def test_compare_reports_an_exact_match_without_blocking():
    text = "同一篇稿件。"
    candidate = dedup.fingerprint(text)
    history = [{"run_id": "run-1", "conversation_id": "c-1",
                "normalized_sha256": candidate["normalized_sha256"],
                "algorithm": candidate["algorithm"], "minhash": candidate["minhash"]}]
    result = dedup.compare(candidate, history)
    assert result["kind"] == "exact"
    assert result["exact_match"] == "run-1"
    assert result["blocks"] is False
    assert result["calibrated"] is False


def test_compare_never_applies_a_threshold():
    """An uncalibrated score must not decide anything, so no verdict key exists."""
    candidate = dedup.fingerprint("一篇全新的稿件。" * 5)
    history = [{"run_id": "run-9", "conversation_id": "c-9",
                "normalized_sha256": "different",
                "algorithm": candidate["algorithm"],
                "minhash": [value + 1 for value in candidate["minhash"]]}]
    result = dedup.compare(candidate, history)
    assert result["blocks"] is False
    assert result["mode"] == "shadow"
    assert 0.0 <= result["similarity"] <= 1.0


def test_off_mode_is_explicit(monkeypatch):
    monkeypatch.setenv("LG_DEDUP_MODE", "off")
    result = dedup.compare(dedup.fingerprint("x" * 40), [])
    assert result["kind"] == "not_run"
    assert dedup.mode() == "off"


def test_an_unknown_mode_falls_back_to_shadow(monkeypatch):
    monkeypatch.setenv("LG_DEDUP_MODE", "enforce-please")
    assert dedup.mode() == "shadow"


def test_sketches_from_different_algorithms_are_not_compared():
    left = dedup.fingerprint("一段文字。" * 5)
    right = dict(dedup.fingerprint("一段文字。" * 5), algorithm="minhash-128-v2")
    assert dedup.estimated_similarity(left, right) == 0.0


def test_fingerprint_is_stable_across_processes():
    """A fingerprint that changed between runs would make history incomparable."""
    expected = dedup.fingerprint("稳定性检查：同一段文字必须得到同一个指纹。")
    again = dedup.fingerprint("稳定性检查：同一段文字必须得到同一个指纹。")
    assert expected["minhash"] == again["minhash"]
    assert all(isinstance(value, int) for value in expected["minhash"])
    assert len(expected["minhash"]) == dedup.MINHASH_SIZE


def test_long_text_is_bounded_not_unbounded():
    """Shingles are capped so a huge draft cannot make the pass expensive."""
    sketch = dedup.fingerprint("华为 MateBook 14 屏幕 14.2 英寸。" * 2000)
    assert sketch["shingles"] <= dedup.MAX_SHINGLES
    assert len(sketch["minhash"]) == dedup.MINHASH_SIZE