#!/usr/bin/env python3
"""Build a labelling sheet from real deliveries, and calibrate the lexical layer.

Two clearly separated jobs, because they have different evidential weight:

``--sheet``   Extract the delivered texts, score every pair, and write a JSONL labelling
              sheet with an empty ``label`` column, most-similar pairs first. This produces
              the *work*, not the answer: only a human decides whether two posts should both
              ship. Output goes outside the repository (it contains real copy).

``--constructed``
              Score pairs whose ground truth follows from how they were built, using the case
              list the plan itself defines: punctuation-only change, title change and
              synonym rewrite are near-duplicates (Q10-Q12); the same model answering a
              different question is distinct (Q13); a shared mandatory clause is not a
              duplicate (Q14). This is legitimate evidence for the **lexical layer only**,
              and it is labelled as constructed everywhere it is reported.

It never enables blocking: it prints what the numbers support, and says plainly when they
support nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db as db_layer  # noqa: E402
from app import models  # noqa: E402
from app.runtime import calibration, dedup  # noqa: E402


def _delivered_texts(limit: int = 200, only_artifacts: bool = True) -> list[dict]:
    """Final assistant replies, marked by whether the run produced a deliverable.

    A conversational reply ("你好，我可以帮你…") is never published, yet it was being
    registered in the content memory like any finished draft. Those rows dominate the
    highest-similarity pairs and would poison a threshold calibrated on them, so the sheet
    looks at artifact-backed deliveries by default.
    """
    db = db_layer.session_factory()()
    try:
        rows = db.query(models.AgentMessage).filter(
            models.AgentMessage.role == "assistant").order_by(
            models.AgentMessage.created_at.desc()).limit(limit).all()
        conversations = {row.conversation_id for row in rows}
        with_artifact = {
            row[0] for row in db.query(models.AgentArtifact.conversation_id).filter(
                models.AgentArtifact.conversation_id.in_(conversations or {""})).distinct().all()
        }
        items = []
        for row in rows:
            if len(row.content or "") <= 40:
                continue
            kind = "artifact" if row.conversation_id in with_artifact else "chat"
            if only_artifacts and kind != "artifact":
                continue
            items.append({"id": row.id, "conversation_id": row.conversation_id,
                          "seq": row.seq, "kind": kind, "text": row.content or ""})
        return items
    finally:
        db.close()


def _excerpt(text: str, size: int = 160) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    return flat[:size] + ("…" if len(flat) > size else "")


def build_sheet(out_path: Path, limit: int = 200, only_artifacts: bool = True) -> dict:
    texts = _delivered_texts(limit, only_artifacts=only_artifacts)
    sketches = [{"item": item, "sketch": dedup.fingerprint(item["text"])} for item in texts]
    pairs = []
    for left in range(len(sketches)):
        for right in range(left + 1, len(sketches)):
            a, b = sketches[left], sketches[right]
            score = dedup.estimated_similarity(a["sketch"], b["sketch"])
            pairs.append({
                "pair_id": a["item"]["id"][:8] + "-" + b["item"]["id"][:8],
                "left": {"id": a["item"]["id"], "conversation_id": a["item"]["conversation_id"],
                         "excerpt": _excerpt(a["item"]["text"])},
                "right": {"id": b["item"]["id"], "conversation_id": b["item"]["conversation_id"],
                          "excerpt": _excerpt(b["item"]["text"])},
                "similarity": round(score, 4),
                "algorithm": dedup.ALGORITHM,
                "source": "real",
                "delivery_kind": a["item"].get("kind", ""),
                "label": "",
            })
    pairs.sort(key=lambda row: row["similarity"], reverse=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in pairs),
                        encoding="utf-8")
    return {"deliveries": len(texts), "pairs": len(pairs), "path": str(out_path),
            "only_artifact_backed": only_artifacts,
            "top_similarity": pairs[0]["similarity"] if pairs else None}


# ------------------------------------------------------------------ constructed pairs
_BASE = ("背了三年游戏本上班，今年终于换回轻薄本了。每天挤地铁那一小时，"
         "肩膀先扛不住。14 英寸这个尺寸塞包里不占地，摊在高铁小桌板上刚好。"
         "开合、打字、触控板的手感是每天都要打交道的部分。")
_SAME_MODEL_OTHER_QUESTION = ("写论文的时候，我更在意屏幕能不能一屏多看几行。"
                              "纵向空间多一点，滚动就少一点，读文献的节奏完全不一样。"
                              "键盘回弹和触控板精度也直接影响连续写作的耐心。")


def _constructed_pairs() -> list[dict]:
    import random

    def pair(text_a, text_b, label, case, note):
        return {"pair_id": case, "label": label, "case": case, "note": note,
                "similarity": round(dedup.estimated_similarity(
                    dedup.fingerprint(text_a), dedup.fingerprint(text_b)), 4),
                "source": "constructed"}

    swap = {"轻薄本": "轻便本", "游戏本": "游戏笔记本", "肩膀": "肩", "尺寸": "规格"}
    synonym = _BASE
    for source, target in swap.items():
        synonym = synonym.replace(source, target)
    random.seed(7)
    unrelated = ("门店这个月的活动是到店体验真机，售货员会按你的专业和预算给建议，"
                 "不合适也可以只看不买。想先了解配置的，可以加门店微信拿资料。")
    clause_only = ("下单前请确认销售版本，配置与随机附件以实际销售为准。")
    return [
        pair(_BASE, _BASE.replace("，", " ").replace("。", "！"), "duplicate",
             "Q10_标点与空白", "只改标点与空白，属于重复"),
        pair(_BASE, "【标题】换了个标题\n" + _BASE[8:], "duplicate",
             "Q11_换标题不改正文", "标题不同但正文相同，不能被标题掩盖"),
        pair(_BASE, synonym, "duplicate", "Q12_同义改写", "近义替换不改变判断"),
        pair(_BASE, _SAME_MODEL_OTHER_QUESTION, "distinct",
             "Q13_同型号不同问题", "同一机型但核心问题不同，不得误杀"),
        pair(_BASE, clause_only, "distinct", "Q14_必要条款复用",
             "共用必要条款不构成整篇重复"),
        pair(_BASE, unrelated, "distinct", "不同门店与用途", "内容无可替换性"),
        pair(_SAME_MODEL_OTHER_QUESTION, unrelated, "distinct", "无关联", "主题不同"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path,
                        help="write a labelling sheet for real deliveries to this path")
    parser.add_argument("--constructed", action="store_true",
                        help="calibrate the lexical layer on by-construction pairs")
    parser.add_argument("--max-false-positive-rate", type=float, default=0.01)
    parser.add_argument("--from-labelled", type=Path,
                        help="a labelled JSONL sheet: run the sweep on it")
    parser.add_argument("--metric", default="semantic_similarity",
                        choices=("similarity", "semantic_similarity"),
                        help="which layer's score to calibrate")
    parser.add_argument("--include-chat", action="store_true",
                        help="also include replies that produced no deliverable")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.sheet:
        if not db_layer.configured() and not db_layer.ensure_configured():
            print("数据库未配置，无法抽取真实交付")
            return 2
        summary = build_sheet(args.sheet, only_artifacts=not args.include_chat)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if summary["pairs"] < 200:
            print(f"注意：只有 {summary['pairs']} 组候选，方案建议 200 组人工标注文本对；"
                  "样本不足时不要据此外推阈值。")

    if args.from_labelled:
        rows = calibration.load_pairs(args.from_labelled)
        labelled = [row for row in rows if row.get("label") in ("duplicate", "distinct")
                    and row.get(args.metric) is not None]
        if not labelled:
            print(f"没有可用于 {args.metric} 的标注样本")
            return 2
        confirmed = all(bool(row.get("confirmed_by_human")) for row in labelled)
        pairs = [{"pair_id": row.get("pair_id", ""), "label": row["label"],
                  "similarity": float(row[args.metric])} for row in labelled]
        result = calibration.recommend(pairs, args.max_false_positive_rate,
                                       labels_confirmed=confirmed)
        result["metric"] = args.metric
        result["label_source"] = sorted({str(row.get("label_source") or "") for row in labelled})
        result["human_confirmed"] = confirmed
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json
              else calibration.format_report(result))
        print(f"（指标：{args.metric}｜标签来源：{result['label_source']}｜"
              f"人工确认：{'是' if confirmed else '否'}）")

    if args.constructed:
        pairs = _constructed_pairs()
        result = calibration.recommend(pairs, args.max_false_positive_rate)
        result["source"] = "constructed"
        result["disclaimer"] = ("这是按构造方式确定真值的字面层样本，不是方案要求的人工标注集；"
                                "结论只对字面层有效，不得据此声称语义去重已完成")
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json
              else calibration.format_report(result) + "\n" + result["disclaimer"])
    if not args.sheet and not args.constructed and not args.from_labelled:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())