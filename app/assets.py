"""Asset resolution: which image may be used as what, and on whose authority.

Package 1's AssetResolver, built on what the knowledge base actually provides rather than on
what would be convenient. Each image already carries real provenance - ``source.kind``,
``source.title`` and ``verification.sha256`` - and that is enough to answer "where did this
come from" without inventing an answer to "may we use it".

**Provenance is not a licence.** ``local_official_export`` records that the file came from an
official export; it says nothing about usage terms, and package 1 requires rights to be
verified before production ("授权未核实，阻断制作"). So ``rights_status`` is reported as
``provenance_recorded_terms_unreviewed`` and never as approved. Enforcement follows the same
pattern as the budget ledger: the honest status is always returned, and
``IMAGE_REQUIRE_ASSET_RIGHTS=1`` makes the missing decision blocking. Default permissive is a
deliberate, visible choice because the user is running acceptance right now, not a claim that
the rights question is settled.

Roles are **derived**, and labelled as derived. A caption suggesting a product display on a
model-verified image yields ``product_identity``; everything else is ``official_reference``.
An authored role would be better, and ``role_source`` says plainly that this is not one.
"""
from __future__ import annotations

import os

ROLE_PRODUCT_IDENTITY = "product_identity"
ROLE_OFFICIAL_REFERENCE = "official_reference"
ROLE_STYLE_REFERENCE = "style_reference"
ROLE_UNKNOWN = "unknown"

ROLES = (ROLE_PRODUCT_IDENTITY, ROLE_OFFICIAL_REFERENCE, ROLE_STYLE_REFERENCE)

# Captions/filenames that indicate the image shows the product itself rather than a scene.
_PRODUCT_HINTS = ("主视觉", "正面产品", "产品展示", "精致产品外观", "配色", "经典开合", "键盘屏幕")
_SCENE_HINTS = ("场景", "创作", "办公", "学习", "出行", "生活")

RIGHTS_PROVENANCE_ONLY = "provenance_recorded_terms_unreviewed"
RIGHTS_UNKNOWN = "unknown"
RIGHTS_APPROVED = "approved_for_use"


def _get(item, key, default=None):
    """Read a field from a mapping *or* an object.

    ``verified_images`` returns pydantic-ish objects in some paths and plain dicts in others -
    the existing tool code reads them through ``_image_field`` for exactly that reason. Calling
    ``.get()`` on an object raised AttributeError and took the whole tool down, so this helper
    keeps the tolerance the rest of the codebase already had.
    """
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def require_rights() -> bool:
    """Whether an unreviewed rights status blocks generation.

    Off by default and stated as such: the user is using image generation for acceptance, so
    silently blocking every image would break it. The status is always reported either way.
    """
    return str(os.getenv("IMAGE_REQUIRE_ASSET_RIGHTS", "")).strip().lower() in ("1", "true", "yes")


def derive_role(image) -> dict:
    """A role for an image, with the fact that it was derived kept attached."""
    caption = str(_get(image, "caption") or "")
    title = str(_field(image, "source", "title") or "")
    text = caption + " " + title
    verified = str(_get(image, "model_verified")).lower() in ("true", "1", "yes")
    if any(hint in text for hint in _SCENE_HINTS) and not any(h in text for h in _PRODUCT_HINTS):
        return {"role": ROLE_OFFICIAL_REFERENCE, "role_source": "derived_from_kb_metadata",
                "reason": "画面为使用场景，非产品本体"}
    if verified and any(hint in text for hint in _PRODUCT_HINTS):
        return {"role": ROLE_PRODUCT_IDENTITY, "role_source": "derived_from_kb_metadata",
                "reason": "型号已核验且标题指向产品外观"}
    if verified:
        return {"role": ROLE_OFFICIAL_REFERENCE, "role_source": "derived_from_kb_metadata",
                "reason": "型号已核验，但未识别为产品外观图"}
    return {"role": ROLE_UNKNOWN, "role_source": "derived_from_kb_metadata",
            "reason": "型号未核验，无法判断角色"}


def _field(image, outer: str, inner: str):
    value = _get(image, outer)
    if isinstance(value, dict):
        return value.get(inner)
    return getattr(value, inner, None) if value is not None else None


def rights_of(image) -> dict:
    """Report the rights state from real provenance, never as an approval we do not have."""
    kind = str(_field(image, "source", "kind") or "")
    sha = str(_field(image, "verification", "sha256") or "")
    if kind == "local_official_export":
        return {"status": RIGHTS_PROVENANCE_ONLY, "provenance": kind,
                "evidence": str(_field(image, "source", "title") or ""),
                "sha256": sha,
                "note": "已知来源为官方导出，但使用条款未经复核；不得视为已授权"}
    if kind:
        return {"status": RIGHTS_UNKNOWN, "provenance": kind, "sha256": sha,
                "note": "来源类型未定义，授权状态未知"}
    return {"status": RIGHTS_UNKNOWN, "provenance": "", "sha256": sha,
            "note": "没有来源信息，授权状态未知"}


def describe(image, *, product_id: str = "") -> dict:
    """One asset, fully described: role, hash, rights, and whether it may be generated from."""
    role = derive_role(image)
    rights = rights_of(image)
    url = str(_get(image, "access_url") or "")
    return {"image_id": str(_get(image, "image_id") or ""),
            "product_id": product_id or str(_get(image, "product_id") or ""),
            "caption": str(_get(image, "caption") or ""),
            "access_url": url,
            "sha256": rights.get("sha256", ""),
            "model_verified": str(_get(image, "model_verified")).lower() in ("true", "1", "yes"),
            "view_id": _view_id(image),
            **role, "rights": rights,
            "rights_status": rights["status"],
            "allowed_as_reference": not (require_rights()
                                         and rights["status"] != RIGHTS_APPROVED),
            "note": "角色由知识库元数据推导，非人工标注；授权状态如实返回。"}


def _view_id(image) -> str:
    """A stable view label so two angles of one product can be told apart."""
    title = str(_field(image, "source", "title") or "")
    for hint in _PRODUCT_HINTS + _SCENE_HINTS:
        if hint in title or hint in str(_get(image, "caption") or ""):
            return hint
    return ""


def resolve(images, *, product_id: str = "", role: str = "") -> dict:
    """Resolve a set of images into roles, optionally filtered to one role."""
    described = [describe(image, product_id=product_id) for image in (images or [])]
    if role:
        described = [row for row in described if row["role"] == role]
    counts: dict[str, int] = {}
    for row in described:
        counts[row["role"]] = counts.get(row["role"], 0) + 1
    by_rights: dict[str, int] = {}
    for row in described:
        by_rights[row["rights_status"]] = by_rights.get(row["rights_status"], 0) + 1
    return {"assets": described, "count": len(described), "by_role": counts,
            "by_rights": by_rights,
            "rights_enforced": require_rights(),
            "has_product_identity": any(row["role"] == ROLE_PRODUCT_IDENTITY
                                        for row in described),
            "note": ("角色为元数据推导；授权状态未经复核。"
                     + ("当前已开启授权强制，未授权素材不得用于生成。" if require_rights()
                        else "当前未强制授权（IMAGE_REQUIRE_ASSET_RIGHTS=1 可开启）。"))}


def pick_reference(images, *, image_id: str = "") -> dict | None:
    """Choose the reference image the way the studio and the tool both need.

    Prefers an explicit id, then a product-identity asset, then a product-display caption,
    then the first image - and never returns an asset the rights gate has refused.
    """
    resolved = resolve(images)["assets"]
    if require_rights():
        resolved = [row for row in resolved if row["allowed_as_reference"]]
    if not resolved:
        return None
    if image_id:
        for row in resolved:
            if row["image_id"] == image_id:
                return row
    for row in resolved:
        if row["role"] == ROLE_PRODUCT_IDENTITY:
            return row
    for row in resolved:
        if any(hint in row["caption"] for hint in ("主视觉", "正面产品", "精致产品")):
            return row
    return resolved[0]


def status() -> dict:
    return {"rights_enforced": require_rights(),
            "roles": list(ROLES),
            "note": "溯源不等于授权：知识库提供来源与 sha256，使用条款需人工复核后记录。",
            "env": "IMAGE_REQUIRE_ASSET_RIGHTS=1 可让未复核授权的素材阻断生成"}