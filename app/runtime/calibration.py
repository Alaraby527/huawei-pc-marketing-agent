"""Threshold calibration for duplicate detection (plan Phase D).

The shadow pass records similarity evidence and deliberately applies no threshold. Turning
that evidence into a blocking rule requires labelled pairs, and the plan is blunt about the
order: "语义阈值必须人工标注校准", "不采用通用 0.8", "上线配置校验不允许以空阈值冒充语义去重
已完成".

So this module does the arithmetic and nothing else:

* it evaluates a threshold against labelled pairs and reports precision, recall and both
  error rates — because a false positive (blocking a genuinely different post) and a false
  negative (letting a repeat through) are different failures with different costs;
* it recommends a threshold only in the direction the plan allows: pick the lowest
  threshold whose **false-positive rate** stays within an explicitly supplied budget;
* it refuses to recommend anything from an empty or unlabelled set. Returning a number there
  would be exactly the "empty threshold pretending calibration is done" the plan forbids.

Labels are ``duplicate`` (a human decided these two should not both be delivered) or
``distinct``. Everything else — including "maybe" — is treated as unlabelled and excluded.
"""
from __future__ import annotations

import json
from pathlib import Path

DUPLICATE = "duplicate"
DISTINCT = "distinct"
_LABELS = {DUPLICATE, DISTINCT}


def load_pairs(path: str | Path) -> list[dict]:
    """Read a labelling sheet. Unlabelled rows are kept but reported as unusable."""
    rows: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        label = str(item.get("label") or "").strip().lower()
        item["label"] = label if label in _LABELS else ""
        item["similarity"] = float(item.get("similarity") or 0.0)
        rows.append(item)
    return rows


def is_labelled(pairs: list[dict]) -> bool:
    return any(pair.get("label") in _LABELS for pair in pairs)


def evaluate(pairs: list[dict], threshold: float) -> dict:
    """Confusion matrix and rates for one threshold, over labelled pairs only."""
    labelled = [pair for pair in pairs if pair.get("label") in _LABELS]
    tp = fp = tn = fn = 0
    for pair in labelled:
        predicted = pair["similarity"] >= threshold
        if pair["label"] == DUPLICATE:
            tp += predicted
            fn += not predicted
        else:
            fp += predicted
            tn += not predicted
    positives = tp + fn
    negatives = fp + tn
    return {
        "threshold": round(float(threshold), 4),
        "labelled": len(labelled),
        "duplicates": positives,
        "distinct": negatives,
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        # Deliberately None rather than 0.0 when the denominator is empty: an undefined rate
        # must not be printed as a measured zero.
        "precision": (tp / (tp + fp)) if (tp + fp) else None,
        "recall": (tp / positives) if positives else None,
        "false_positive_rate": (fp / negatives) if negatives else None,
        "false_negative_rate": (fn / positives) if positives else None,
    }


def separation(pairs: list[dict]) -> dict:
    """How far apart the two classes actually are.

    A clean separation does **not** identify a threshold: if every duplicate scores 1.0 and
    every distinct pair scores 0.0, any value in between "works" on this sample and the data
    says nothing about which. Reporting the gap is what stops a grid floor from being
    presented as a calibrated number.
    """
    duplicates = [pair["similarity"] for pair in pairs if pair.get("label") == DUPLICATE]
    distinct = [pair["similarity"] for pair in pairs if pair.get("label") == DISTINCT]
    if not duplicates or not distinct:
        return {"separable": None, "note": "缺少某一类样本，无法判断可分性"}
    lowest_duplicate = min(duplicates)
    highest_distinct = max(distinct)
    return {
        "min_duplicate": round(lowest_duplicate, 4),
        "max_distinct": round(highest_distinct, 4),
        "gap": round(lowest_duplicate - highest_distinct, 4),
        "separable": lowest_duplicate > highest_distinct,
        "note": ("样本可分离，但边界只能落在这个区间内，样本本身不能确定具体值；"
                 "需要加入更难的负例（同型号不同角度、共用必要条款、长文截断）"
                 if lowest_duplicate > highest_distinct else
                 "两类样本有重叠，任何阈值都会产生误杀或漏检"),
    }


def sweep(pairs: list[dict], thresholds: list[float] | None = None) -> list[dict]:
    """Evaluate a threshold grid, ascending."""
    if thresholds is None:
        thresholds = [round(0.50 + 0.05 * index, 2) for index in range(10)]
    return [evaluate(pairs, value) for value in sorted(thresholds)]


def recommend(pairs: list[dict], max_false_positive_rate: float = 0.01,
              labels_confirmed: bool = True) -> dict:
    """The lowest threshold whose false-positive rate fits the budget.

    Lowest, not "best": the cheaper failure here is a missed duplicate (a human still sees
    the delivery), while a false positive silently discards legitimate work.
    """
    if not is_labelled(pairs):
        return {"calibrated": False, "reason": "no_labelled_pairs",
                "measured": len(pairs), "labelled": 0,
                "note": "没有标注样本就不能给出阈值；空阈值不得冒充已完成校准"}
    results = sweep(pairs)
    usable = [row for row in results
              if row["false_positive_rate"] is not None
              and row["false_positive_rate"] <= max_false_positive_rate
              and row["recall"]]
    if not usable:
        return {"calibrated": False, "reason": "no_threshold_within_budget",
                "max_false_positive_rate": max_false_positive_rate,
                "sweep": results, "separation": separation(pairs),
                "note": "没有任何阈值同时满足误杀预算与召回；不得据此启用拦截"}
    chosen = min(usable, key=lambda row: row["threshold"])
    gap = separation(pairs)
    if not labels_confirmed:
        # Labels authored by the system under test are not independent evidence. The numbers
        # are still reported, because they are informative, but they can never be presented
        # as a finished calibration or used to switch on a blocking threshold.
        chosen = {**chosen, "threshold": chosen["threshold"]}
        return {"calibrated": False, "provisional": True,
                "reason": "labels_not_human_confirmed",
                "threshold": chosen["threshold"], "metrics": chosen, "sweep": results,
                "separation": gap, "warning_threshold": chosen["threshold"],
                "note": "标签为 ai_first_pass、未经人工确认：数字仅供参考，"
                        "不得据此启用拦截阈值"}
    # When the sample separates cleanly the chosen value is the grid floor, not a finding.
    at_floor = bool(gap.get("separable")) and chosen["threshold"] <= min(
        row["threshold"] for row in results)
    return {"calibrated": True, "threshold": chosen["threshold"],
            "provisional": at_floor,
            "max_false_positive_rate": max_false_positive_rate,
            "metrics": chosen, "sweep": results, "separation": gap,
            "warning_threshold": chosen["threshold"],
            "note": ("这只是字面层、且样本可分离——阈值落在网格下限，属于暂定值，"
                     "不能当作校准结论" if at_floor else
                     "仅对字面层有效；语义层需要嵌入模型与独立索引版本，尚未实现")}


def format_report(result: dict) -> str:
    """A short operator-facing summary; no source text is included."""
    if not result.get("calibrated"):
        return "校准未完成：" + str(result.get("note") or result.get("reason"))
    metrics = result["metrics"]
    lines = [
        f"字面层阈值：{result['threshold']}（误杀预算 {result['max_false_positive_rate']}）",
        f"样本：{metrics['labelled']} 组（重复 {metrics['duplicates']} / 不同 {metrics['distinct']}）",
    ]
    for key, label in (("precision", "精确率"), ("recall", "召回率"),
                       ("false_positive_rate", "误杀率"), ("false_negative_rate", "漏检率")):
        value = metrics[key]
        lines.append(f"{label}：{'未定义' if value is None else format(value, '.2%')}")
    gap = result.get("separation") or {}
    if gap.get("separable") is not None:
        lines.append(f"可分性：重复最低 {gap.get('min_duplicate')} / 不同最高 "
                     f"{gap.get('max_distinct')}（间隔 {gap.get('gap')}）")
    lines.append(result["note"])
    return "\n".join(lines)