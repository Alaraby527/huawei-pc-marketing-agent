#!/usr/bin/env python3
"""First-pass labels for duplicate-detection pairs — AI-proposed, human-confirmable.

Why a first pass at all: thresholds can only be calibrated on labelled pairs, and the plan
asks for ~200 of them. Why it is marked so loudly: labels produced by the same system being
calibrated are **not** independent evidence. Every row carries
``label_source = "ai_first_pass"`` and a confidence, and the calibration report refuses to
call itself finished while any label is unconfirmed — enabling a blocking threshold on
labels authored by the model under test would be grading its own exam.

Only the *informative* pairs are labelled. A pair whose two texts share no shingles and sit
far apart in embedding space tells a threshold nothing; the pairs that matter are the ones
near the boundary, so those are selected first.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.model import ModelError, chat_json_messages  # noqa: E402
from app.runtime import semantic  # noqa: E402

SYSTEM = """你在为"内容查重"做标注。给你同一工作区内两篇已交付的营销文案片段。

判断它们是否**实质可替换**，也就是：同事拿其中一篇去发，另一篇再发一次，读者会觉得是同一篇内容。

- duplicate：核心问题、核心判断和展开方式实质相同；或只是换词、换标点、换标题、调换句子顺序。
- distinct：核心问题或核心判断不同；同一机型但讲的是不同的点；只是共用必要的免责条款或活动条款；篇幅差异导致内容量不同。
- uncertain：片段太短或信息不足，无法判断。

注意：语气不同、emoji 不同、标签不同都不算区分；而"同一型号、不同卖点"必须算 distinct。
不要因为两篇都提到同一个产品就判 duplicate。

只输出 JSON：{"verdict":"duplicate|distinct|uncertain","confidence":"high|medium|low","reason":"一句话依据"}"""


def _load_pairs(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _score_pair(row: dict) -> float:
    return float(row.get("similarity") or 0.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sheet", type=Path, help="JSONL sheet from calibrate_dedup.py --sheet")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--top", type=int, default=30,
                        help="how many most-similar pairs to label")
    parser.add_argument("--add-semantic", action="store_true",
                        help="also record embedding similarity for each selected pair")
    args = parser.parse_args()

    rows = _load_pairs(args.sheet)
    if not rows:
        print("清单为空")
        return 2
    rows.sort(key=_score_pair, reverse=True)
    selected = rows[:max(1, args.top)]

    labelled = []
    for index, row in enumerate(selected, 1):
        left = (row.get("left") or {}).get("excerpt") or ""
        right = (row.get("right") or {}).get("excerpt") or ""
        payload = "【A】\n" + left + "\n\n【B】\n" + right
        try:
            result, _meta = chat_json_messages(SYSTEM, [{"role": "user", "content": payload}],
                                               schema_hint="只输出 JSON", model=None)
            verdict = str((result or {}).get("verdict") or "").strip().lower()
            confidence = str((result or {}).get("confidence") or "").strip().lower()
            reason = str((result or {}).get("reason") or "")[:200]
        except ModelError as error:
            verdict, confidence, reason = "", "", "调用失败：" + type(error).__name__
        item = {**row,
                "label": verdict if verdict in ("duplicate", "distinct") else "",
                "raw_verdict": verdict or "uncertain",
                "confidence": confidence,
                "label_source": "ai_first_pass",
                "confirmed_by_human": False,
                "reason": reason}
        if args.add_semantic:
            try:
                a = semantic.embed(left)
                b = semantic.embed(right)
                item["semantic_similarity"] = (
                    round(semantic.cosine(a["vector"], b["vector"]), 4)
                    if a and b else None)
            except Exception:  # noqa: BLE001 - semantic scoring is optional here
                item["semantic_similarity"] = None
        labelled.append(item)
        print(f"  [{index}/{len(selected)}] {item['raw_verdict']:9s} "
              f"({item['confidence'] or '-'}) literal={row.get('similarity')} {reason[:48]}")

    # Everything below the cut stays explicitly unlabelled rather than assumed distinct.
    for row in rows[len(selected):]:
        labelled.append({**row, "label": "", "raw_verdict": "not_selected",
                         "label_source": "unlabelled", "confirmed_by_human": False,
                         "reason": "相似度过低，对阈值无信息量，未标注"})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in labelled),
                        encoding="utf-8")
    counts = {}
    for item in labelled:
        counts[item["raw_verdict"]] = counts.get(item["raw_verdict"], 0) + 1
    print(f"\n已写出 {args.out}（{len(labelled)} 组）")
    print("首轮判定分布:", counts)
    print("注意：这些标签由被测系统自身模型给出，属 ai_first_pass，需人工确认后才能用于启用阈值。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())