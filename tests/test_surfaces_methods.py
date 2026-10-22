"""Surfaces stay separate from platforms; methods are gated by evidence, not by preference."""
import pytest

from app import methods, surfaces
from app.contracts import Platform


# ------------------------------------------------------------------ surfaces

def test_the_existing_platform_enum_is_untouched():
    """The user's decision was to add a surface layer, not to rewrite platform values."""
    assert [item.value for item in Platform] == ["xiaohongshu", "wechat_article", "moments",
                                                 "poster"]


def test_moments_is_ambiguous_so_a_surface_must_be_named():
    """This project's `moments` means store Moments; personal Moments is a new face."""
    info = surfaces.resolve("moments")
    assert info["surface"] == ""
    # 三个交付面共用 moments 平台：个人朋友圈、门店朋友圈、企业微信朋友圈
    assert sorted(info["ambiguous"]) == ["personal_moments", "store_moments", "work_account"]
    assert "必须显式指定交付面" in info["note"]


def test_every_moments_surface_maps_back_to_the_moments_platform():
    """确认 work_account = 企业微信朋友圈后，moments 平台下有三个交付面。

    "多个交付面共享一个 platform"是本项目既有做法（个人/门店朋友圈本来就这样），
    所以新增第三个不需要动 Platform 枚举。
    """
    for sid in ("personal_moments", "store_moments", "work_account"):
        assert surfaces.platform_for(sid) == Platform.MOMENTS, sid
    assert sorted(surfaces.surfaces_for("moments")) == [
        "personal_moments", "store_moments", "work_account"]


def test_an_unknown_value_is_an_error_not_a_default():
    info = surfaces.resolve("douyin")
    assert info["error"].startswith("未知交付面或平台")
    assert surfaces.platform_for("douyin") is None
    assert surfaces.surfaces_for("douyin") == []


def test_spec_conflicts_are_recorded_rather_than_silently_applied():
    """Existing output must not change length behind the user's back."""
    fields = {(row["surface"], row["field"]) for row in surfaces.SPEC_CONFLICTS}
    assert ("xiaohongshu", "body") in fields
    assert ("store_poster", "headline") in fields
    xhs = [row for row in surfaces.SPEC_CONFLICTS if row["surface"] == "xiaohongshu"][0]
    assert xhs["legacy"] == "300-600" and xhs["package3"] == "350-700"


def test_soft_limits_are_notes_never_blocking_failures():
    result = surfaces.limits_check("xiaohongshu", body_chars=900, headline_chars=30)
    assert result["ok"] is True, "建议值不得当成硬门槛"
    assert result["limits_are"] == "editorial_recommendations_not_platform_rules"
    assert len(result["notes"]) == 2
    assert all("非平台规则" in note for note in result["notes"])
    clean = surfaces.limits_check("xiaohongshu", body_chars=500, headline_chars=18)
    assert clean["notes"] == []


def test_limits_check_surfaces_the_conflict_for_that_surface():
    result = surfaces.limits_check("store_poster", headline_chars=13)
    assert any(row["field"] == "headline" for row in result["conflicts"])


def test_describe_lists_every_surface_with_its_open_question():
    data = surfaces.describe()
    listed = {row["surface"] for row in data["surfaces"]}
    assert listed == set(surfaces.surface_ids())
    assert len(data["hard_gates"]) == 5
    assert "no_unresolved_placeholders" in data["hard_gates"]
    work = next(row for row in data["surfaces"] if row["surface"] == "work_account")
    # 2026-10-22 已确认，所以不再挂待确认问题；但 describe 仍必须列出这个交付面。
    assert work["unconfirmed"] is False and work["open_question"] == ""
    assert work["platform"] == "moments"


# ------------------------------------------------------------------ methods

def test_the_catalogue_holds_all_twenty_methods():
    assert len(methods.METHODS) == 20
    assert len(methods.methods_for("xiaohongshu")) == 5
    assert len(methods.methods_for("personal_moments")) == 5
    assert len(methods.methods_for("wechat_article")) == 5
    assert len(methods.methods_for("store_poster")) == 5
    assert methods.methods_for("store_moments") == methods.methods_for("store_poster")


def test_a_case_study_is_excluded_without_an_authorised_case():
    """The whole point of the column: no case means no case study, with the reason shown."""
    result = methods.route("xiaohongshu", available_evidence=["verified_relevant_facts"])
    assert "X01" in result["allowed"]
    assert "X04" not in result["allowed"]
    blocked = next(row for row in result["excluded"] if row["method_id"] == "X04")
    assert blocked["blocked_by"] == "real_case"
    assert blocked["verified_available"] is False
    assert "无法自造" in blocked["reason"]
    assert "没有真实授权案例" in blocked["do_not_use_when"]


def test_business_only_evidence_is_marked_unverifiable():
    for kind in ("price_terms", "campaign_terms", "real_case", "own_event", "service_policy"):
        assert methods.EVIDENCE_VERIFIABLE[kind] is False
    for kind in ("verified_relevant_facts", "comparable_data", "scene_fit"):
        assert methods.EVIDENCE_VERIFIABLE[kind] is True


def test_a_promotion_method_opens_only_when_price_evidence_exists():
    closed = methods.route("store_poster", available_evidence=["product_capability"])
    assert "S01" not in closed["allowed"]
    assert "S02" in closed["allowed"]
    opened = methods.route("store_poster",
                           available_evidence=["product_capability", "price_terms"])
    assert "S01" in opened["allowed"]


def test_every_method_carries_a_do_not_use_condition():
    for method_id, row in methods.METHODS.items():
        assert row["do_not_use_when"], method_id
        assert row["required_evidence"], method_id
        assert row["structure"], method_id


def test_an_unknown_surface_routes_to_nothing_rather_than_a_generic_method():
    result = methods.route("douyin", available_evidence=["verified_relevant_facts"])
    assert result["allowed"] == [] and result["error"] == "UNKNOWN_SURFACE"
    assert "xiaohongshu" in result["known_surfaces"]


def test_objective_matching_orders_but_does_not_filter():
    result = methods.route("xiaohongshu", available_evidence=["verified_relevant_facts",
                                                              "comparable_data",
                                                              "usable_list"],
                           objective="选择")
    assert result["allowed"][0] == "X03", "与目标匹配的方法排前面"
    assert len(result["allowed"]) == 3, "不因目标不匹配而丢方法"


def test_evidence_kinds_are_a_closed_set():
    for method_id in methods.METHODS:
        kind = methods.evidence_kind(method_id)
        assert kind, method_id
        assert kind in methods.EVIDENCE_KINDS, method_id


# ------------------------------------------------------------------ confirmation gate

def test_a_new_brand_needs_strategy_confirmation_for_its_first_three_runs():
    assert methods.needs_strategy_confirmation(brand_runs=0)["required"] is True
    assert methods.needs_strategy_confirmation(brand_runs=2)["required"] is True
    assert methods.needs_strategy_confirmation(brand_runs=3)["required"] is False


def test_a_price_change_reopens_the_gate():
    result = methods.needs_strategy_confirmation(brand_runs=10, price_changed=True)
    assert result["required"] is True
    assert result["reason"] == "price_changed"
    promise = methods.needs_strategy_confirmation(brand_runs=10, major_promise_changed=True)
    assert promise["reason"] == "major_promise_changed"

def test_the_length_conflicts_are_decided_not_left_open():
    """4 处长度冲突已裁定采纳新方案值，不再有"并存待裁定"。"""
    from app import surfaces
    assert surfaces.UNRESOLVED_CONFLICTS == ()
    for row in surfaces.SPEC_CONFLICTS:
        assert row["resolution"] == "adopted_package3", row
    decision = surfaces.SPEC_DECISION
    assert decision["decided_by"] and decision["decided_at"]
    assert "采纳新方案" in decision["ruling"]


def test_the_specs_actually_carry_the_adopted_values():
    """裁定说的和实际生效的值必须一致——否则裁定只是文字。"""
    from app import surfaces
    assert surfaces.SURFACES["xiaohongshu"]["body_soft_range"] == (350, 700)
    assert surfaces.SURFACES["wechat_article"]["body_soft_range"] == (1000, 1800)
    assert surfaces.SURFACES["store_moments"]["chars_soft_range"] == (100, 200)
    assert surfaces.SURFACES["store_poster"]["headline_soft_max"] == 14


def test_a_user_stated_length_wins_over_the_spec():
    """用户原话："如果用户自己要求了字数则按用户要求为主"。"""
    from app import surfaces
    assert "优先于本规格" in surfaces.SPEC_DECISION["override_rule"]
    import inspect
    from app.runtime import context
    source = inspect.getsource(context.surface_directive)
    assert "明确要求的字数优先" in source, "提示词里必须写明这条，否则模型会拿规格压用户"
    assert "两者冲突时按用户要求" in source


def test_work_account_is_confirmed_as_wecom_moments():
    """2026-10-22 裁定：工作号 = 企业微信·朋友圈（客户可见的营销内容）。"""
    from app import surfaces
    row = surfaces.SURFACES["work_account"]
    assert row["label"] == "企业微信·朋友圈"
    assert row["alias"] == "工作号", "原名称要留作别名，否则历史引用会找不到"
    assert row["platform"] == surfaces.Platform.MOMENTS
    assert row.get("unconfirmed") is not True
    assert "open_question" not in row, "确认后不应再挂待确认问题"
    # 与既有模式一致：多个交付面可共享一个 platform
    assert surfaces.SURFACES["personal_moments"]["platform"] == surfaces.Platform.MOMENTS
    assert surfaces.SURFACES["store_moments"]["platform"] == surfaces.Platform.MOMENTS


def test_work_account_carries_compliance_not_invented_lengths():
    """客户可见的企业营销内容有合规约束；两个包都没给字数规格，就不编造区间。"""
    from app import surfaces
    row = surfaces.SURFACES["work_account"]
    assert row["max_text_lines"] == 8, "包 1 明确给的约束要保留"
    assert "没有" not in row["spec_gap"] and "未给出" in row["spec_gap"]
    assert "body_soft_range" not in row, "没有来源就不编造字数区间"
    assert "广告法" in row["compliance_note"]
    assert "冒充私人朋友圈" in row["compliance_note"]


def test_resolve_no_longer_reports_work_account_as_pending():
    from app import surfaces
    info = surfaces.resolve("work_account")
    assert info["unconfirmed"] is False
    assert info["open_question"] == ""
    assert info["platform"] == "moments"


def test_the_directive_carries_the_compliance_and_line_limit():
    import inspect
    from app.runtime import context
    source = inspect.getsource(context.surface_directive)
    assert "max_text_lines" in source
    assert "compliance_note" in source
    assert "spec_gap" in source


def test_work_account_was_pending_and_is_now_decided():
    """包 1 曾要求先由业务确认其真实含义，不得假定为公众号。

    2026-10-22 已确认：企业微信·朋友圈。这个测试保留原意（不得假定为公众号），
    断言换成确认后的状态。
    """
    from app import surfaces
    row = surfaces.SURFACES["work_account"]
    assert row["platform"] != surfaces.Platform.WECHAT_ARTICLE, "不得假定为公众号"
    assert row["platform"] == surfaces.Platform.MOMENTS
    info = surfaces.resolve("work_account")
    assert info["unconfirmed"] is False
    assert info["open_question"] == ""
    assert info["confirmed_at"] if "confirmed_at" in info else True
