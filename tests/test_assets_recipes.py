"""Asset roles and rights from real provenance, and the recipe release gate."""
import json

import pytest

from app import assets, recipes


def _image(image_id="I-1", caption="原野绿多彩配色", kind="local_official_export",
           title="hwpc_matebook-14-harmonyos_原野绿多彩配色_04.png", verified=True):
    return {"image_id": image_id, "caption": caption, "model_verified": verified,
            "access_url": "/api/v1/assets/tok-" + image_id,
            "source": {"kind": kind, "title": title},
            "verification": {"method": "local_official_export_index", "sha256": "a" * 64}}


# ------------------------------------------------------------------ assets

def test_provenance_is_not_reported_as_an_authorisation():
    """An official export says where the file came from, not that we may use it."""
    rights = assets.rights_of(_image())
    assert rights["status"] == assets.RIGHTS_PROVENANCE_ONLY
    assert rights["status"] != assets.RIGHTS_APPROVED
    assert "未经复核" in rights["note"]
    assert "不得视为已授权" in rights["note"]


def test_the_real_sha256_is_carried_through():
    """Package 1's resolver must expose a content hash, and the KB already has one."""
    described = assets.describe(_image())
    assert described["sha256"] == "a" * 64
    assert described["rights"]["sha256"] == "a" * 64


def test_a_product_display_becomes_product_identity_and_says_it_was_derived():
    described = assets.describe(_image())
    assert described["role"] == assets.ROLE_PRODUCT_IDENTITY
    assert described["role_source"] == "derived_from_kb_metadata"
    assert "非人工标注" in described["note"]


def test_a_scene_shot_is_an_official_reference_not_the_product():
    scene = assets.describe(_image(caption="手写笔创作场景",
                                   title="hwpc_matebook-14-harmonyos_手写笔创作场景_07.jpg"))
    assert scene["role"] == assets.ROLE_OFFICIAL_REFERENCE
    assert "非产品本体" in scene["reason"]


def test_an_unverified_image_has_no_confident_role():
    described = assets.describe(_image(verified=False))
    assert described["role"] == assets.ROLE_UNKNOWN


def test_an_unknown_source_is_reported_as_unknown_rights():
    described = assets.describe(_image(kind=""))
    assert described["rights_status"] == assets.RIGHTS_UNKNOWN


def test_rights_enforcement_is_off_by_default_but_always_reported():
    resolved = assets.resolve([_image()])
    assert resolved["rights_enforced"] is False
    assert resolved["by_rights"][assets.RIGHTS_PROVENANCE_ONLY] == 1
    assert "未强制授权" in resolved["note"]
    assert "IMAGE_REQUIRE_ASSET_RIGHTS=1" in resolved["note"]


def test_enforcement_refuses_every_unreviewed_asset(monkeypatch):
    monkeypatch.setenv("IMAGE_REQUIRE_ASSET_RIGHTS", "1")
    resolved = assets.resolve([_image()])
    assert resolved["rights_enforced"] is True
    assert resolved["assets"][0]["allowed_as_reference"] is False
    assert assets.pick_reference([_image()]) is None, "开启后未复核素材不得用于生成"


def test_pick_reference_prefers_product_identity(monkeypatch):
    scene = _image(image_id="I-2", caption="手写笔创作场景",
                   title="x_手写笔创作场景_07.jpg")
    product = _image(image_id="I-3")
    chosen = assets.pick_reference([scene, product])
    assert chosen["image_id"] == "I-3"
    assert chosen["role"] == assets.ROLE_PRODUCT_IDENTITY


def test_pick_reference_honours_an_explicit_id():
    chosen = assets.pick_reference([_image("I-1"), _image("I-2")], image_id="I-2")
    assert chosen["image_id"] == "I-2"


def test_view_ids_tell_two_angles_apart():
    front = assets.describe(_image(caption="原野绿正面产品展示",
                                   title="vmall_原野绿正面产品展示_09.png"))
    keys = assets.describe(_image(caption="经典开合外观", title="vmall_经典开合外观_18.png"))
    assert front["view_id"] and keys["view_id"]
    assert front["view_id"] != keys["view_id"]


# ------------------------------------------------------------------ recipes

def test_all_twenty_four_recipes_load():
    assert len(recipes.all_ids()) == 24
    assert recipes.all_ids()[0] == "R01"


def test_no_recipe_is_approved_for_production():
    """Every recipe ships unverified_real_images, so the usable list must be empty."""
    assert recipes.usable() == []
    assert recipes.status()["usable_for_production"] == 0
    assert recipes.status()["by_release_status"] == {"unverified_real_images": 24}


def test_selection_refuses_and_says_why_while_nothing_is_approved():
    result = recipes.select("xiaohongshu")
    assert result["ok"] is False
    assert result["error"] == "NO_APPROVED_RECIPE"
    assert result["blocked_by"] == ["unverified_real_images"]
    assert "手改 JSON" in result["note"]
    assert result["suggested_first_batch"], "首批建议要一并给出，但它是建议不是批准"


def test_a_preview_selection_is_marked_preview_only():
    result = recipes.select("xiaohongshu", require_approved=False)
    assert result["ok"] is True
    assert result["approved"] is False
    assert result["preview_only"] is True
    assert "不得作为生产输出" in result["note"]


def test_package_one_channels_map_onto_this_projects_surfaces():
    """package 1 says xhs/moments/work_account/store_poster; the mapping is explicit."""
    xhs = recipes.for_surface("xiaohongshu")
    assert xhs, "小红书面必须有可用配方"
    assert all("xiaohongshu" in (recipes.describe(key)["surfaces"] or []) for key in xhs)
    assert len(xhs) == 18, "18 份配方声明了 xhs 渠道"
    # package 1 never splits 朋友圈, so a moments recipe serves both faces.
    personal = set(recipes.for_surface("personal_moments"))
    store = set(recipes.for_surface("store_moments"))
    assert personal and personal == store
    assert recipes.for_surface("work_account")


def test_the_prompt_carries_the_protections_alongside_it():
    """Prompt wording is not a fidelity guarantee, so the locks travel as review criteria."""
    result = recipes.prompt_for("R04")
    assert result["ok"] is True
    assert result["background_prompt"]
    assert result["product_lock"], "水墨配方必须带产品保护规则"
    assert result["avoid"]
    assert "不构成像素保真保证" in result["note"]


def test_approval_cannot_flip_the_status_by_editing_json():
    """The plan forbids treating an edited JSON as a production approval."""
    refused = recipes.approve("R01", approved_by="", evidence="")
    assert refused["error"] == "APPROVAL_INCOMPLETE"
    blocked = recipes.approve("R01", approved_by="张三", evidence="真实图片验证通过")
    assert blocked["ok"] is False
    assert blocked["error"] == "RELEASE_RECORD_NOT_IMPLEMENTED"
    assert "不会改写" in blocked["message"]
    assert recipes.is_approved("R01") is False


def test_an_unknown_recipe_is_an_error():
    assert recipes.describe("R99") is None
    assert recipes.prompt_for("R99")["error"] == "RECIPE_NOT_FOUND"
    assert recipes.select("douyin")["error"] == "NO_RECIPE_FOR_SURFACE"


def test_every_recipe_keeps_its_own_protection_rule():
    for recipe_id in recipes.all_ids():
        row = recipes.describe(recipe_id)
        assert row["product_lock"], recipe_id
        assert row["avoid"], recipe_id
        assert row["version"] == "1.0"


def test_the_resolver_tolerates_objects_as_well_as_dicts():
    """verified_images returns objects on some paths and dicts on others.

    Calling .get() on the object form raised AttributeError and took the whole image tool
    down, which a legacy test caught - the rest of the codebase already read both shapes.
    """
    class FakeImage:
        image_id = "IMG-1"
        caption = "HUAWEI MateBook Test 官方产品图"
        access_url = "/api/v1/assets/IMG-1"

    resolved = assets.resolve([FakeImage()], product_id="P-test")
    assert resolved["count"] == 1
    assert resolved["assets"][0]["image_id"] == "IMG-1"
    assert resolved["assets"][0]["access_url"] == "/api/v1/assets/IMG-1"
    # No source metadata at all, so the honest answer is unknown rather than an assumption.
    assert resolved["assets"][0]["rights_status"] == assets.RIGHTS_UNKNOWN
    assert resolved["assets"][0]["role"] == assets.ROLE_UNKNOWN
    assert assets.pick_reference([FakeImage()])["image_id"] == "IMG-1"


def test_mixed_shapes_in_one_batch_are_both_read():
    class FakeImage:
        image_id = "I-obj"
        caption = "手写笔创作场景"
        access_url = "/api/v1/assets/I-obj"

    resolved = assets.resolve([FakeImage(), _image("I-dict")])
    assert resolved["count"] == 2
    assert {row["image_id"] for row in resolved["assets"]} == {"I-obj", "I-dict"}
