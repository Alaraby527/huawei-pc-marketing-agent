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
    row = load().get(str(recipe_id or "").strip())
    return bool(row and row.get("approved"))


def usable() -> list[str]:
    """Recipes approved for production. Empty is the honest answer today."""
    return [key for key, row in sorted(load().items()) if row.get("approved")]


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
    return {"ok": True, "recipe_id": row["recipe_id"], "approved": row.get("approved"),
            "background_prompt": row.get("background_prompt"),
            "product_lock": row.get("product_lock"),
            "avoid": row.get("avoid") or [],
            "variation_axes": row.get("variation_axes") or [],
            "extra": product_note,
            "note": "提示词约束不构成像素保真保证；product_lock 与 avoid 是审稿判据。"}


def approve(recipe_id: str, *, approved_by: str, evidence: str) -> dict:
    """Record an approval decision. Refuses without a named approver and evidence."""
    row = load().get(str(recipe_id or "").strip())
    if row is None:
        return {"ok": False, "error": "RECIPE_NOT_FOUND", "recipe_id": recipe_id}
    if not approved_by or not evidence:
        return {"ok": False, "error": "APPROVAL_INCOMPLETE",
                "message": "配方批准必须有审批人与证据（真实图片验证结论），缺一不可"}
    # This module deliberately cannot flip release_status: that lives in the package's JSON,
    # and rewriting it here would be exactly the "手改 approved" the plan forbids. A real
    # approval belongs in a release record, which is not implemented yet.
    return {"ok": False, "error": "RELEASE_RECORD_NOT_IMPLEMENTED",
            "message": "批准记录表（recipe_releases）尚未实现；本函数只做入参校验，"
                       "不会改写 JSON 里的 release_status",
            "would_record": {"recipe_id": row["recipe_id"], "approved_by": approved_by,
                             "evidence": evidence[:200]}}


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