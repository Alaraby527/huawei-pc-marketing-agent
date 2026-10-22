"""Calibration arithmetic, and its refusals.

The refusals matter as much as the arithmetic: the plan forbids presenting an empty or
uninformative threshold as "calibration done", so those paths are asserted explicitly.
"""
import json

import pytest

from app.runtime import calibration


def _pairs(*rows):
    return [{"pair_id": f"p{index}", "similarity": similarity, "label": label}
            for index, (similarity, label) in enumerate(rows)]


def test_confusion_matrix_and_rates():
    # Three duplicates above the threshold, one distinct pair just above it too — which is
    # the whole point: that pair is a false positive, and it must be counted as one.
    pairs = _pairs((0.95, "duplicate"), (0.80, "duplicate"), (0.60, "duplicate"),
                   (0.55, "distinct"), (0.10, "distinct"), (0.05, "distinct"))
    result = calibration.evaluate(pairs, 0.50)
    assert result["true_positive"] == 3 and result["false_negative"] == 0
    assert result["false_positive"] == 1 and result["true_negative"] == 2
    assert result["recall"] == 1.0
    assert result["precision"] == pytest.approx(0.75)
    # Three distinct pairs, one of them above the threshold: 1/3, not 1/2.
    assert result["false_positive_rate"] == pytest.approx(1 / 3)


def test_a_threshold_that_blocks_everything_shows_its_cost():
    pairs = _pairs((0.90, "duplicate"), (0.20, "distinct"), (0.30, "distinct"))
    result = calibration.evaluate(pairs, 0.10)
    assert result["false_positive"] == 2
    assert result["false_positive_rate"] == 1.0
    assert result["recall"] == 1.0


def test_undefined_rates_are_none_not_zero():
    """A rate with no denominator must not print as a measured 0.0."""
    result = calibration.evaluate(_pairs((0.9, "duplicate")), 0.5)
    assert result["false_positive_rate"] is None
    assert result["false_negative_rate"] == 0.0


def test_unlabelled_pairs_are_excluded_from_the_maths():
    pairs = _pairs((0.90, "duplicate"), (0.20, "distinct"))
    pairs.append({"pair_id": "x", "similarity": 0.99, "label": ""})
    pairs.append({"pair_id": "y", "similarity": 0.99, "label": "maybe"})
    result = calibration.evaluate(pairs, 0.5)
    assert result["labelled"] == 2, "only human-labelled rows count"
    assert result["false_positive"] == 0


def test_no_labels_means_no_recommendation():
    result = calibration.recommend(_pairs((0.9, ""), (0.2, "")))
    assert result["calibrated"] is False
    assert result["reason"] == "no_labelled_pairs"
    assert "不得冒充" in result["note"]


def test_clean_separation_is_reported_as_provisional_not_calibrated():
    """Every duplicate high, every distinct low: the data cannot pick a value."""
    pairs = _pairs((0.95, "duplicate"), (0.90, "duplicate"),
                   (0.0, "distinct"), (0.0, "distinct"))
    result = calibration.recommend(pairs)
    assert result["calibrated"] is True
    assert result["provisional"] is True, "a grid floor is not a finding"
    gap = result["separation"]
    assert gap["separable"] is True
    assert gap["min_duplicate"] == 0.90 and gap["max_distinct"] == 0.0
    assert "暂定值" in result["note"]


def test_overlapping_classes_are_reported_as_inseparable():
    pairs = _pairs((0.90, "duplicate"), (0.95, "distinct"), (0.4, "distinct"))
    result = calibration.recommend(pairs)
    gap = result["separation"]
    assert gap["separable"] is False
    assert "误杀或漏检" in gap["note"]


def test_no_threshold_within_the_false_positive_budget_refuses():
    # A distinct pair scores higher than every duplicate, so no threshold avoids both errors.
    pairs = _pairs((0.30, "duplicate"), (0.95, "distinct"))
    result = calibration.recommend(pairs, max_false_positive_rate=0.0)
    assert result["calibrated"] is False
    assert result["reason"] == "no_threshold_within_budget"
    assert "不得据此启用拦截" in result["note"]


def test_sweep_is_ascending_and_covers_the_grid():
    results = calibration.sweep(_pairs((0.9, "duplicate"), (0.1, "distinct")))
    values = [row["threshold"] for row in results]
    assert values == sorted(values)
    assert values[0] == 0.50 and values[-1] == 0.95


def test_load_pairs_tolerates_unlabelled_and_malformed_rows(tmp_path):
    path = tmp_path / "pairs.jsonl"
    path.write_text("\n".join([
        json.dumps({"pair_id": "a", "similarity": 0.9, "label": "duplicate"}),
        "not json at all",
        json.dumps({"pair_id": "b", "similarity": 0.2}),
        json.dumps({"pair_id": "c", "similarity": 0.3, "label": "MAYBE"}),
        "# a comment",
    ]), encoding="utf-8")
    rows = calibration.load_pairs(path)
    assert len(rows) == 3
    assert rows[0]["label"] == "duplicate"
    assert rows[1]["label"] == "" and rows[2]["label"] == ""
    assert calibration.is_labelled(rows) is True


def test_report_never_contains_source_text():
    pairs = _pairs((0.9, "duplicate"), (0.1, "distinct"))
    pairs[0]["left"] = {"excerpt": "真实门店文案内容不应出现在报告里"}
    report = calibration.format_report(calibration.recommend(pairs))
    assert "真实门店文案内容" not in report
    assert "可分性" in report


def test_report_states_when_calibration_is_incomplete():
    report = calibration.format_report(calibration.recommend(_pairs((0.9, ""))))
    assert report.startswith("校准未完成")
    assert "不得冒充" in report