"""The export pack: approvals bound to bytes, and a gate that lists everything outstanding."""
import pytest

from app import export_bundle as pack


def _copy(text="这台机器内存 16 GB，重量约 1.31 kg。"):
    return {"title": "学生党怎么挑", "body": text, "pages": [{"title": "屏幕", "text": "14.2 英寸"}]}


def _facts():
    return [{"field": "内存", "value": "16 GB", "source": "KB-1"},
            {"field": "重量", "value": "1.31 kg", "source": "KB-2"}]


def _bundle(**overrides):
    params = {"copy": _copy(), "review": {"total_score": 90, "hard_failures": []},
              "facts": _facts(), "meta": {"recipe_id": "R01", "recipe_version": "1.0",
                                          "sizes": {"xiaohongshu": [1080, 1440]}}}
    params.update(overrides)
    return pack.build_bundle(**params)


def test_the_five_files_are_all_produced():
    bundle = _bundle()
    for name in pack.FILE_TYPES:
        assert name in bundle, name
    assert bundle["manifest"]["count"] == 4


def test_the_claim_map_maps_claims_to_sources():
    bundle = _bundle()
    mapped = bundle[pack.CLAIM_MAP]
    assert mapped["checked"] > 0
    supported = {row["claim"]: row["source_id"] for row in mapped["claims"]}
    assert supported.get("16 GB") == "KB-1"


def test_an_unsupported_claim_is_shown_not_dropped():
    """Silently omitting it is how an unevidenced number reaches a customer."""
    bundle = _bundle(copy=_copy("这台机器内存 64 GB。"))
    mapped = bundle[pack.CLAIM_MAP]
    assert mapped["unsupported"], "无来源主张必须出现在 unsupported 里"
    assert any(row["claim"] == "64 GB" for row in mapped["unsupported"])


def test_an_approval_without_an_approver_is_not_trusted():
    """A caller-supplied approved flag is not evidence."""
    bundle = _bundle(approved=True, approved_by="", approved_hash="")
    record = bundle[pack.APPROVAL]
    assert record["approved"] is False
    assert record["reason"] == "no_approver"
    assert "不信任客户端提交的批准标记" in record["note"]


def test_an_approval_bound_to_other_bytes_is_not_valid():
    bundle = _bundle(approved=True, approved_by="张三", approved_hash="deadbeef")
    assert bundle[pack.APPROVAL]["valid"] is False
    assert bundle[pack.APPROVAL]["reason"] == "hash_mismatch"


def test_an_approval_of_the_current_bytes_is_valid():
    base = _bundle()
    bundle = _bundle(approved=True, approved_by="张三",
                     approved_hash=pack.content_hash({**base, "approved": True,
                                                      "approved_by": "张三"}))
    assert bundle[pack.APPROVAL]["valid"] is True
    assert pack.approval_state(bundle)["valid"] is True


def test_changing_the_copy_invalidates_the_previous_approval():
    """Package 1: any change to source image, copy, recipe or size invalidates approval."""
    first = _bundle()
    approved = pack.build_bundle(copy=first["copy"], review=first[pack.REVIEW], facts=_facts(),
                                 meta={"recipe_id": "R01"},
                                 approved=False)
    record = {"approved": True, "approved_by": "张三", "bound_hash": pack.content_hash(approved),
              "bound_material": pack.binding_material(approved)}
    edited = {**approved, "copy_text": approved["copy_text"] + "\n新加一句。",
              pack.APPROVAL: record}
    state = pack.approval_state(edited)
    assert state["valid"] is False
    assert state["reason"] == "invalidated_by_change"
    assert "copy_text" in state["changed"]


def test_changing_the_recipe_version_invalidates_it_too():
    base = _bundle(meta={"recipe_id": "R01", "recipe_version": "1.0"})
    record = {"approved": True, "approved_by": "张三", "bound_hash": pack.content_hash(base),
              "bound_material": pack.binding_material(base)}
    switched = {**base, "recipe_version": "1.1", pack[0] if False else pack.APPROVAL: record}
    switched = {**base, "recipe_version": "1.1", pack.APPROVAL: record}
    state = pack.approval_state(switched)
    assert state["valid"] is False
    assert "recipe_version" in state["changed"]


def test_changing_the_size_invalidates_it():
    base = _bundle(meta={"recipe_id": "R01", "sizes": {"xiaohongshu": [1080, 1440]}})
    record = {"approved": True, "approved_by": "张三", "bound_hash": pack.content_hash(base),
              "bound_material": pack.binding_material(base)}
    resized = {**base, "sizes": {"xiaohongshu": [1086, 1448]}, pack.APPROVAL: record}
    assert "sizes" in pack.approval_state(resized)["changed"]


def test_a_placeholder_blocks_the_export():
    bundle = _bundle(copy=_copy("价格待定，欢迎到店。"))
    state = pack.export_ready(bundle)
    assert state["ready"] is False
    assert any(row["gate"] == "no_unresolved_placeholders" for row in state["blockers"])


def test_an_unapproved_bundle_reports_the_approval_gate():
    state = pack.export_ready(_bundle())
    assert state["ready"] is False
    assert any(row["gate"] == "human_approval_for_exact_content" for row in state["blockers"])
    assert "导出不等于发布" in state["note"]


def test_the_manifest_hashes_every_file():
    manifest = _bundle()["manifest"]
    for name, row in manifest["files"].items():
        assert len(row["sha256"]) == 64
        assert row["bytes"] > 0


def test_saving_requires_the_agents_own_saver():
    """No parallel, weaker write path is allowed."""
    result = pack.save(None, None, _bundle())
    assert result["ok"] is False
    assert result["error"] == "SAVER_REQUIRED"


def test_saving_writes_each_file_through_the_injected_saver():
    seen = []

    def saver(name, payload):
        seen.append(name)
        return {"ok": True, "revision": 1, "artifact_id": "A-" + name}

    result = pack.save(None, None, _bundle(), save_artifact=saver)
    assert result["ok"] is True
    assert set(seen) == set(pack.FILE_TYPES)


def test_the_binding_fields_cover_what_the_packages_require():
    for field in ("copy_hash", "source_asset_hashes", "recipe_id", "recipe_version", "sizes"):
        assert field in pack.BINDING_FIELDS
