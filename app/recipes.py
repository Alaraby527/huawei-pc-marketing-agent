"""Creative recipes: 24 authored style rules behind a release gate.

Copied verbatim from package 1's ``recipes/`` as its NOTICE intends ("本包由本次任务原创
编写，用于交付方内部实施与修改"). Nothing else from that package is copied - its examples and
fixtures are declared fictional placeholders that must not reach a production store.

**The gate is the point of this module.** All 24 recipes ship with
``release_status = "unverified_real_images"``, so *none* of them is approved for production,
and the package's "首批建议 R01、R02、R04、R06、R09、R16、R22、R23" is a suggestion for the
first batch of testing rather than an approval. Package 1 also warns that editing a JSON file
to say approved is not a production approval; approval has to come from a recorded decision.
Accordingly ``select`` returns nothing usable while every recipe is unverified, and says why -
a silent fallback to an unverified recipe is how an unreviewed style reaches a customer.

Channel naming differs across the packages, so the mapping is explicit below rather than
assumed: package 1's ``moments`` is 朋友圈 without distinguishing personal from store, and its
``work_account`` still has no confirmed platform.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data" / "recipes"

APPROVED_STATUSES = ("approved", "released")

# 用户逐张验收时要求写进提示词的那一句（原话，不改写）。
PRODUCT_CLAUSE = "产品要求和原图中的产品一致，大小比例logo外观保持不变。"

# 风格化类配方：用户明确"换个画风是允许的，合适的融合到对应场景里面去"。
# 这些风格本身就是重绘（黏土/像素/纸艺/波普…），要求外观像素级不变会和风格互相打架——
# 实测里 R06 几何构成把屏幕改成了同色系、R13 像素也做了风格化，那都是对的。
# 所以这类配方换成 STYLE_CLAUSE：仍要求产品正确且可识别，但允许按风格重绘。
STYLE_TRANSFORM_FAMILIES = ("clay", "pixel", "paper", "pop", "miniature", "collage",
                            "riso", "surreal", "geometric")

STYLE_CLAUSE = ("产品必须来自参考图中的同一款产品：型号特征、机身比例关系与 logo 位置保持一致，"
                "不得出现替代商品或其他品牌。本配方属于风格化呈现，允许按该风格重绘产品外观，"
                "但要自然地融合进场景，让人一眼仍能认出是这台机器。")

# 配方原文里有一句与上面直接冲突：它写着"本图不出现产品、替代商品、品牌标识"，
# 因为包 1 的设计前提是"只出背景，产品后期合成"。我们要的是产品在图里，所以构建提示词时
# 把这一句替换掉——这不是我自造的约束，是用户那句要求能成立的必要条件。
# 实测依据：R04 水墨空间、R22 文字主视觉 因为这句话产品整体消失；R06/R07 丢了机身 Logo。
CONFLICTING_CLAUSE = "本图不出现产品、替代商品、品牌标识、正文、二维码。"
REPLACEMENT_CLAUSE = "画面中保留参考图中的产品本体与其品牌标识。"

RELEASES_PATH = DATA_DIR / "releases.json"

# package 1 channel -> this project's surfaces. `moments` maps to both faces because that
# package never splits them, and picking one silently would be a guess.
CHANNEL_TO_SURFACES = {
    "xhs": ("xiaohongshu",),
    "moments": ("personal_moments", "store_moments"),
    "work_account": ("work_account",),
    "store_poster": ("store_poster",),
}

# Package 1's own first-batch suggestion. Deliberately exposed as a suggestion, not a licence.
SUGGESTED_FIRST_BATCH = ("R01", "R02", "R04", "R06", "R09", "R16", "R22", "R23")


@lru_cache(maxsize=1)
@lru_cache(maxsize=1)
def releases() -> dict:
    """The recorded approval decision.

    Package 1 is explicit that editing a JSON file to say approved is not a production approval -
    approval has to come from a recorded decision. This reads that record.
    """
    if not RELEASES_PATH.exists():
        return {}
    try:
        return json.loads(RELEASES_PATH.read_text())
    except (OSError, ValueError):
        return {}


def reset_release_cache() -> None:
    releases.cache_clear()


def load() -> dict[str, dict]:
    recipes: dict[str, dict] = {}
    if not DATA_DIR.exists():
        return recipes
    for path in sorted(DATA_DIR.glob("R*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        recipe_id = str(data.get("recipe_id") or "").strip()
        if not recipe_id:
            continue
        data["surfaces"] = sorted({surface for channel in (data.get("channels") or [])
                                   for surface in CHANNEL_TO_SURFACES.get(channel, ())})
        data["approved"] = str(data.get("release_status") or "") in APPROVED_STATUSES
        recipes[recipe_id] = data
    return recipes


def reset_cache() -> None:
    load.cache_clear()


def all_ids() -> list[str]:
    return sorted(load())


def is_approved(recipe_id: str) -> bool:
    """Approved only if a *record* says so. The per-file release_status is not enough."""
    record = releases()
    approved = record.get("approved") or []
    rejected = record.get("rejected") or {}
    if recipe_id in rejected:
        return False
    if recipe_id in approved:
        return True
    row = load().get(str(recipe_id or "").strip())
    return bool(row and row.get("approved"))


def usable() -> list[str]:
    """Recipes approved for production, per the *record* - not the per-file release_status.

    Until 2026-10-22 this was empty and correctly so: nothing had been reviewed. The project owner
    then reviewed one generated image per recipe and passed 22 of 24 (R04 水墨空间 and R22 文字主
    视觉 dropped the product entirely), which is the recorded decision ``releases.json`` holds.
    """
    return [key for key in sorted(load()) if is_approved(key)]


def describe(recipe_id: str) -> dict | None:
    row = load().get(str(recipe_id or "").strip())
    if row is None:
        return None
    return {"recipe_id": row["recipe_id"], "version": row.get("version"),
            "name": row.get("name"), "family": row.get("family"),
            "mechanism": row.get("mechanism"), "layout": row.get("layout"),
            "palette": row.get("palette"), "information_density": row.get("information_density"),
            "surfaces": row.get("surfaces"), "channels": row.get("channels"),
            "allowed_routes": row.get("allowed_routes"), "product_lock": row.get("product_lock"),
            "avoid": row.get("avoid"), "variation_axes": row.get("variation_axes"),
            "background_prompt": row.get("background_prompt"),
            "release_status": row.get("release_status"), "approved": row.get("approved"),
            "rights_note": row.get("rights_note")}


def for_surface(surface_id: str, *, approved_only: bool = False) -> list[str]:
    rows = [key for key, row in sorted(load().items())
            if surface_id in (row.get("surfaces") or [])]
    if approved_only:
        rows = [key for key in rows if is_approved(key)]
    return rows


def select(surface_id: str, *, route: str = "", require_approved: bool = True) -> dict:
    """Pick a recipe for a surface, or explain why none is available.

    ``require_approved`` defaults to True. Passing False is for internal previews only and the
    result still carries ``approved: False`` so a caller cannot present it as production.
    """
    candidates = for_surface(surface_id)
    if not candidates:
        return {"ok": False, "error": "NO_RECIPE_FOR_SURFACE", "surface": surface_id,
                "available_surfaces": sorted({s for row in load().values()
                                              for s in (row.get("surfaces") or [])})}
    if route:
        candidates = [key for key in candidates
                      if route in (load()[key].get("allowed_routes") or [])]
    approved = [key for key in candidates if is_approved(key)]
    if require_approved and not approved:
        return {"ok": False, "error": "NO_APPROVED_RECIPE",
                "surface": surface_id, "route": route or None,
                "candidates": candidates,
                "blocked_by": sorted({str(load()[key].get("release_status"))
                                      for key in candidates}),
                "suggested_first_batch": [key for key in SUGGESTED_FIRST_BATCH
                                          if key in candidates],
                "message": "该交付面没有已批准配方；全部配方仍为 unverified_real_images，"
                           "需真实图片验证与审批记录后才能用于生产",
                "note": "方案明确：手改 JSON 的 release_status 不等于生产批准。"}
    chosen = (approved or candidates)[0]
    return {"ok": True, "recipe": describe(chosen), "approved": is_approved(chosen),
            "preview_only": not is_approved(chosen),
            "note": ("" if is_approved(chosen) else
                     "未批准配方：仅可用于内部预览，不得作为生产输出。")}


def prompt_for(recipe_id: str, *, product_note: str = "") -> dict:
    """The background prompt plus the protections that must travel with it.

    ``product_lock`` and ``avoid`` are returned alongside rather than merged into the prompt:
    the package is explicit that prompt wording is not a pixel-fidelity guarantee, so these
    are review criteria, not incantations.
    """
    row = load().get(str(recipe_id or "").strip())
    if row is None:
        return {"ok": False, "error": "RECIPE_NOT_FOUND", "recipe_id": recipe_id}
    raw = str(row.get("background_prompt") or "")
    # 用户那句要求在构建时注入；同时把与之冲突的"本图不出现产品…"替换掉。
    if CONFLICTING_CLAUSE in raw:
        raw = raw.replace(CONFLICTING_CLAUSE, REPLACEMENT_CLAUSE)
    # 按族选用不同的产品条款：风格化类允许换画风，写实类严格要求外观不变。
    family = str(row.get("family") or "")
    style_transform = family in STYLE_TRANSFORM_FAMILIES
    clause = STYLE_CLAUSE if style_transform else PRODUCT_CLAUSE
    return {"ok": True, "recipe_id": row["recipe_id"], "approved": is_approved(row["recipe_id"]),
            "background_prompt": raw, "family": family,
            "style_transform": style_transform,
            "product_clause": clause,
            "composed_prompt": (raw + "\n" + clause).strip(),
            "product_lock": row.get("product_lock"),
            "avoid": row.get("avoid") or [],
            "variation_axes": row.get("variation_axes") or [],
            "extra": product_note,
            "note": "提示词约束不构成像素保真保证；product_lock 与 avoid 是审稿判据。"}


def approve(recipe_id: str, *, approved_by: str, evidence: str) -> dict:
    """Record an approval decision. Writes the record; it does not edit the recipe file.

    Replaces the earlier ``RELEASE_RECORD_NOT_IMPLEMENTED`` stub: the record table now exists as
    ``app/data/recipes/releases.json``, so a human decision can actually be recorded.
    """
    rid = str(recipe_id or "").strip()
    if rid not in load():
        return {"ok": False, "error": "RECIPE_NOT_FOUND", "recipe_id": rid}
    if not str(approved_by or "").strip() or not str(evidence or "").strip():
        return {"ok": False, "error": "APPROVAL_NEEDS_WHO_AND_EVIDENCE",
                "message": "批准必须记录批准人与证据；改 JSON 里的 release_status 不算批准"}
    record = releases() or {"record_version": 1, "approved": [], "rejected": {}, "evidence": {}}
    approved = list(record.get("approved") or [])
    if rid not in approved:
        approved.append(rid)
    record["approved"] = approved
    record.setdefault("rejected", {}).pop(rid, None)
    record.setdefault("evidence", {})[rid] = {"note": str(evidence)[:300],
                                             "approved_by": str(approved_by)[:80]}
    RELEASES_PATH.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    reset_release_cache()
    return {"ok": True, "recipe_id": rid, "approved_by": approved_by,
            "approved_count": len(approved),
            "note": "已写入批准记录（releases.json）；配方文件本身未被修改。"}


def status() -> dict:
    rows = load()
    by_status: dict[str, int] = {}
    for row in rows.values():
        key = str(row.get("release_status") or "unknown")
        by_status[key] = by_status.get(key, 0) + 1
    return {"total": len(rows), "by_release_status": by_status,
            "approved": usable(), "suggested_first_batch": list(SUGGESTED_FIRST_BATCH),
            "usable_for_production": len(usable()),
            "notice": ("全部配方仍为待真实验证状态，生产可用数为 0；"
                       "首批建议是测试批次建议，不是批准。")}