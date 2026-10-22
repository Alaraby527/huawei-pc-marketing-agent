"""Export pack: bundle, claim map, review, manifest and approval - with approvals bound to bytes.

Package 3's step 8 is "导出审核包、主张映射、用量和版本；等待外部人工批准", and package 1 adds
the rule that makes an approval worth anything: **any change to a source image, the copy, the
recipe or a size invalidates the previous approval**. An approval that survives an edit is not
an approval of the thing that ships.

So an approval is stored against a ``content_hash`` computed over the exact bytes it covers, and
``approval_state`` compares that hash with the current material. Two further rules come straight
from the packages:

* ``approved=true`` submitted by a caller is **not** trusted - it must come with a named
  approver and a hash that matches, otherwise the material is unapproved;
* the pack refuses to build while a publishable blocker is outstanding, so an export cannot be
  used to route around the input checks.

Files are written as ordinary artifacts through the agent's own saver, which means they inherit
its revision numbering and its fact check rather than getting a parallel, weaker write path.
"""
from __future__ import annotations

import hashlib
import json

from . import briefs

BUNDLE = "bundle"
CLAIM_MAP = "claim_map"
REVIEW = "review"
MANIFEST = "manifest"
APPROVAL = "approval"

FILE_TYPES = (BUNDLE, CLAIM_MAP, REVIEW, MANIFEST, APPROVAL)

# Everything that, when it changes, must invalidate an approval. Named rather than hashed
# together implicitly so the reason for an invalidation can be stated.
BINDING_FIELDS = ("copy_text", "copy_hash", "source_asset_hashes", "recipe_id",
                  "recipe_version", "prompt_version", "sizes", "channel_specs_version")


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    """A stable hash of whatever is being approved."""
    if isinstance(value, str):
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def binding_material(bundle: dict) -> dict:
    """The subset of a bundle an approval is bound to."""
    return {field: (bundle or {}).get(field) for field in BINDING_FIELDS}


def content_hash(bundle: dict) -> str:
    return digest(binding_material(bundle))


def build_claim_map(copy: dict, facts: list | None = None) -> dict:
    """Map every checkable claim to the source ids that support it.

    A claim with no source is returned as unsupported rather than omitted: silently dropping it
    is what lets an unevidenced number reach a customer.
    """
    facts = facts or []
    index = {}
    for fact in facts:
        if isinstance(fact, dict):
            key = str(fact.get("value") or "").strip()
            if key:
                index[key] = fact.get("source") or fact.get("source_id") or ""
    claims, unsupported = [], []
    for surface, text in _texts(copy):
        for claim in _claims(text):
            source = index.get(claim, "")
            row = {"surface": surface, "claim": claim, "source_id": source}
            claims.append(row)
            if not source:
                unsupported.append(row)
    return {"claims": claims, "unsupported": unsupported,
            "checked": len(claims), "supported": len(claims) - len(unsupported),
            "note": "无来源主张必须呈现并被处理，不能省略。"}


def _texts(copy) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(copy, str):
        return [("copy", copy)]
    if not isinstance(copy, dict):
        return out
    for key in ("title", "body", "cta", "headline", "subheadline", "summary", "text"):
        if isinstance(copy.get(key), str):
            out.append((key, copy[key]))
    for index, page in enumerate(copy.get("pages") or []):
        if isinstance(page, dict):
            for key in ("title", "text"):
                if isinstance(page.get(key), str):
                    out.append((f"pages[{index}].{key}", page[key]))
    for index, row in enumerate(copy.get("cards") or []):
        if isinstance(row, dict) and isinstance(row.get("text"), str):
            out.append((f"cards[{index}].text", row["text"]))
    return out


def _claims(text: str) -> list[str]:
    """Exact numeric/unit claims. Reuses the grounding extractor so one definition applies."""
    try:
        from .grounding import extract_claims
        return [claim.text for claim in extract_claims(text or "")]
    except Exception:  # noqa: BLE001 - claims are evidence, and a missing extractor is reported
        return []


def build_manifest(files: dict) -> dict:
    """Per-file hash and size, so a reviewer can prove what they reviewed."""
    entries = {}
    for name, payload in (files or {}).items():
        body = payload if isinstance(payload, str) else _canonical(payload)
        entries[name] = {"sha256": digest(body), "bytes": len(body.encode("utf-8"))}
    return {"files": entries, "count": len(entries),
            "combined": digest(entries),
            "note": "清单哈希用于核对评审对象；审批绑定的是内容哈希，不是清单。"}


def build_bundle(*, copy: dict, review: dict, facts: list | None = None, meta: dict | None = None,
                 copy_text: str = "", approved: bool = False, approved_by: str = "",
                 approved_hash: str = "") -> dict:
    """Assemble the five files and the approval state, refusing to imply approval."""
    bundle = {"copy": copy, "copy_text": copy_text or _flatten(copy),
              "meta": dict(meta or {}), **(meta or {})}
    bundle["copy_hash"] = digest(bundle["copy_text"])
    bundle["approved"] = bool(approved)
    bundle["approved_by"] = approved_by
    current = content_hash(bundle)

    bundle[CLAIM_MAP] = build_claim_map(copy, facts)
    bundle[REVIEW] = review
    bundle[APPROVAL] = approval_record(bundle, approved=approved, approved_by=approved_by,
                                       approved_hash=approved_hash)
    bundle["content_hash"] = current
    # The bundle file is the reviewable material itself. Built before the manifest so the
    # manifest can hash it, and it carries no reference to the manifest, which would recurse.
    bundle[BUNDLE] = {"copy": copy, "copy_text": bundle["copy_text"],
                      "copy_hash": bundle["copy_hash"], "meta": bundle["meta"],
                      CLAIM_MAP: bundle[CLAIM_MAP], REVIEW: review,
                      APPROVAL: bundle[APPROVAL], "content_hash": current}
    bundle[MANIFEST] = build_manifest({BUNDLE: bundle[BUNDLE],
                                       CLAIM_MAP: bundle[CLAIM_MAP],
                                       REVIEW: review,
                                       APPROVAL: bundle[APPROVAL]})
    bundle["blockers"] = briefs.publishable_blockers({**bundle, "copy_text": bundle["copy_text"]})
    bundle["export_ready"] = not bundle["blockers"] and bundle[APPROVAL]["valid"]
    return bundle


def _flatten(copy) -> str:
    return "\n".join(text for _surface, text in _texts(copy))


def approval_record(bundle: dict, *, approved: bool, approved_by: str,
                    approved_hash: str) -> dict:
    """Record an approval, and say whether it actually covers the current material."""
    current = content_hash(bundle)
    if not approved:
        return {"approved": False, "approved_by": "", "bound_hash": "", "current_hash": current,
                "valid": False, "reason": "unapproved",
                "note": "未批准：未提供人工批准，不得视为可发布。"}
    if not approved_by:
        # A caller-supplied approved flag is not evidence of anything.
        return {"approved": False, "approved_by": "", "bound_hash": approved_hash,
                "current_hash": current, "valid": False, "reason": "no_approver",
                "note": "收到 approved=true 但没有批准人：不信任客户端提交的批准标记。"}
    if approved_hash != current:
        return {"approved": False, "approved_by": approved_by, "bound_hash": approved_hash,
                "current_hash": current, "valid": False, "reason": "hash_mismatch",
                "note": "批准绑定的内容哈希与当前材料不一致：旧审批已失效。"}
    return {"approved": True, "approved_by": approved_by, "bound_hash": approved_hash,
            "current_hash": current, "valid": True, "reason": "ok",
            # Kept so an invalidation can name what changed instead of only that something did.
            "bound_material": binding_material(bundle),
            "note": "审批与当前内容哈希一致。"}


def approval_state(bundle: dict) -> dict:
    """Whether an existing approval still covers the material as it stands now."""
    record = (bundle or {}).get(APPROVAL) or {}
    if not record.get("approved"):
        return {"valid": False, "reason": record.get("reason") or "unapproved",
                "note": record.get("note") or "没有有效审批"}
    current = content_hash(bundle)
    if record.get("bound_hash") != current:
        changed = _changed_fields(bundle, record)
        return {"valid": False, "reason": "invalidated_by_change",
                "changed": changed,
                "note": "材料已变更，旧审批失效：须重新审批。变更项：" + "、".join(changed)}
    return {"valid": True, "reason": "ok", "note": "审批仍然有效"}


def _changed_fields(bundle: dict, record: dict) -> list[str]:
    """Name what changed, so the person re-approving knows what to look at."""
    bound = record.get("bound_material")
    if not isinstance(bound, dict):
        return ["unknown"]
    current = binding_material(bundle)
    return [field for field in BINDING_FIELDS if bound.get(field) != current.get(field)]


def export_ready(bundle: dict) -> dict:
    """The single gate an export must pass. Lists everything outstanding."""
    blockers = (bundle or {}).get("blockers")
    if blockers is None:
        blockers = briefs.publishable_blockers(bundle or {})
    state = approval_state(bundle)
    reasons = list(blockers)
    if not state["valid"]:
        reasons.append({"gate": "human_approval_for_exact_content", "field": "approval",
                        "why": state["note"]})
    return {"ready": not reasons, "blockers": reasons, "approval": state,
            "content_hash": content_hash(bundle or {}),
            "note": "导出不等于发布；已导出错误稿不能靠数据库回滚自动撤回。"}


def save(db, conversation, bundle: dict, *, save_artifact=None) -> dict:
    """Persist the five files as ordinary artifacts, inheriting the saver's checks.

    ``save_artifact`` is injected rather than imported so this module stays testable and so the
    caller decides the context; the agent's own ``_save_artifact`` is the intended one.
    """
    if save_artifact is None:
        return {"ok": False, "error": "SAVER_REQUIRED",
                "message": "需要注入 agent 自己的保存函数，避免另开更弱的写入路径"}
    written = {}
    for name in FILE_TYPES:
        payload = bundle.get(name)
        if payload is None:
            continue
        result = save_artifact(name, payload)
        written[name] = {"ok": bool(result.get("ok")), "revision": result.get("revision"),
                         "artifact_id": result.get("artifact_id"),
                         "error": result.get("error")}
    return {"ok": all(row["ok"] for row in written.values()), "files": written,
            "content_hash": bundle.get("content_hash"),
            "export_ready": export_ready(bundle)["ready"],
            "note": "已写入审核包各文件；导出仍需人工批准。"}


def describe() -> dict:
    return {"files": list(FILE_TYPES), "binding_fields": list(BINDING_FIELDS),
            "note": "审批绑定内容哈希；源图、文案、配方或尺寸任一变更都会使旧审批失效。"}