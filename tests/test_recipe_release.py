"""配方批准记录：批准必须来自有记录的决定，不能靠改 JSON。

2026-10-22 项目负责人逐张验收了 24 个配方各一张生成图，通过 22 个；R04 水墨空间与
R22 文字主视觉因产品整体消失被剔除。那份验收就是模块要求的"有记录的决定"。
"""
import json

from app import recipes


def test_only_recorded_approvals_count():
    approved = recipes.usable()
    assert len(approved) == 22, f"应为 22 个，实际 {len(approved)}"
    for rid in ("R01", "R02", "R24"):
        assert rid in approved
    for rid in ("R04", "R22"):
        assert rid not in approved, f"{rid} 已被剔除，不应可用"


def test_the_recipe_files_still_say_unverified():
    """批准写在记录里，配方文件保持原样——模块明确禁止"改 JSON 说 approved"。"""
    rows = recipes.load()
    for rid in recipes.usable():
        assert rows[rid].get("release_status") == "unverified_real_images", rid


def test_the_users_sentence_is_baked_into_the_prompt():
    spec = recipes.prompt_for("R01")
    assert recipes.PRODUCT_CLAUSE in spec["composed_prompt"]
    assert "产品要求和原图中的产品一致" in recipes.PRODUCT_CLAUSE
    assert "大小比例logo外观保持不变" in recipes.PRODUCT_CLAUSE


def test_the_conflicting_clause_is_replaced():
    """配方原文写着"本图不出现产品、品牌标识"，与用户那句直接冲突。

    实测依据：R04/R22 因此产品整体消失，R06/R07 丢了机身 Logo。
    """
    spec = recipes.prompt_for("R01")
    assert "本图不出现产品" not in spec["composed_prompt"]
    assert "画面中保留参考图中的产品本体" in spec["composed_prompt"]


def test_approval_requires_who_and_evidence():
    bad = recipes.approve("R05", approved_by="", evidence="")
    assert bad["ok"] is False and bad["error"] == "APPROVAL_NEEDS_WHO_AND_EVIDENCE"
    missing = recipes.approve("R99", approved_by="someone", evidence="something")
    assert missing["ok"] is False and missing["error"] == "RECIPE_NOT_FOUND"


def test_select_returns_an_approved_recipe_per_surface():
    for surface in ("xiaohongshu", "store_poster", "personal_moments", "store_moments"):
        picked = recipes.select(surface)
        assert picked["ok"] is True, (surface, picked.get("error"))
        assert picked["approved"] is True, surface
        assert picked["preview_only"] is False


def test_the_release_record_names_the_rejected_ones():
    record = json.loads(recipes.RELEASES_PATH.read_text())
    assert set(record["rejected"]) == {"R04", "R22"}
    assert len(record["approved"]) == 22
    assert record["evidence"], "批准必须带证据"
    assert record["decided_by"] and record["decided_at"]


def test_studio_uses_an_approved_recipe_and_reports_it():
    """配方接进生图链路：没给提示词时按交付面自动挑已批准配方，并把用了哪个带出来。"""
    import inspect
    from app import studio
    source = inspect.getsource(studio.generate_image)
    assert "recipe_lib.select(surface)" in source
    assert "recipe_lib.prompt_for(recipe_id)" in source
    assert "RECIPE_NOT_APPROVED" in source, "未批准的配方必须被拒绝"
    assert '"recipe": recipe_used' in source, "必须带出配方溯源"


def test_studio_refuses_an_unapproved_recipe_instead_of_downgrading():
    """静默降级到未批准配方 = 未评审风格流到客户手里。必须是显式拒绝。"""
    import inspect
    from app import studio
    source = inspect.getsource(studio.generate_image)
    assert "不得用于生产输出" in source
    assert "why_no_recipe" in source, "没用配方时要如实说明原因"


def test_stylised_recipes_may_change_the_art_style():
    """用户明确：漫画这类"换个画风是允许的，合适的融合到对应场景里面去"。

    风格化配方本身就是重绘（黏土/像素/纸艺/波普…），硬要求外观像素级不变会和风格互相打架。
    """
    stylised = {"R03": "miniature", "R11": "paper", "R12": "clay", "R13": "pixel",
                "R24": "pop", "R02": "collage", "R07": "riso", "R08": "surreal",
                "R06": "geometric"}
    for rid, family in stylised.items():
        spec = recipes.prompt_for(rid)
        assert spec["style_transform"] is True, rid
        assert spec["product_clause"] == recipes.STYLE_CLAUSE, rid
        assert "允许按该风格重绘产品外观" in spec["composed_prompt"]
        assert "不得出现替代商品" in spec["composed_prompt"], "换画风不等于可以不认产品"
        assert recipes.load()[rid]["family"] == family


def test_realistic_recipes_keep_the_users_sentence_verbatim():
    """写实类照用户原话执行，一个字不改。"""
    for rid in ("R01", "R05", "R09", "R10", "R15", "R23"):
        spec = recipes.prompt_for(rid)
        assert spec["style_transform"] is False, rid
        assert spec["product_clause"] == recipes.PRODUCT_CLAUSE, rid
        assert "大小比例logo外观保持不变" in spec["composed_prompt"]


def test_every_recipe_gets_exactly_one_of_the_two_clauses():
    for rid in sorted(recipes.load()):
        spec = recipes.prompt_for(rid)
        assert spec["product_clause"] in (recipes.PRODUCT_CLAUSE, recipes.STYLE_CLAUSE), rid
        assert spec["product_clause"] in spec["composed_prompt"], rid
