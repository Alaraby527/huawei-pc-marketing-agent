"""热点中心验收测试：纯规则、服务层与 /api/hotspots HTTP 契约。

对应 03_验收测试与上线手册 的 A04 A07 A08 A15 A16 A22 A25 A27 A28 等场景，外加
04 里"只新增不改既有行为"的边界。所有来源都是测试桩：没有 NewsNow、没有真实已审批
来源，因此每个响应都必须如实说明 data_mode 与 production_ready，断言也按"没有假数据"
来写——空就是空，不能补一条合成热点让它看起来能用。

测试桩与合成数据的区别很重要：
* ``_approve_source`` 走"已审批来源 + 桩采集器"的集成路径，data_mode 保持上游语义
  （live/cache/stale_fallback），不需要 HOTSPOTS_ALLOW_SYNTHETIC。
* ``enable_synthetic`` 是模块自带的、被开关保护的合成通道，只在专门验证它的用例里打开。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text as sql_text

from app import db as db_layer
from app import hotspot_rules as rules
from app import hotspots, models
from app.main import app

client = TestClient(app)
STUB_EVIDENCE = "test-only: 集成测试桩，不是真实来源审批"


# ------------------------------------------------------------------ fixtures / helpers

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """每个用例一个独立 SQLite 文件；采集器注册表与合成开关都从干净状态开始。"""
    url = f"sqlite:///{tmp_path / 'hotspots.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.delenv("HOTSPOTS_ALLOW_SYNTHETIC", raising=False)
    monkeypatch.delenv("NEWSNOW_INTERNAL_BASE_URL", raising=False)
    hotspots.reset_providers()
    db_layer.reset_for_tests(url)
    db_layer.init_db()
    yield
    hotspots.reset_providers()
    db_layer.reset_for_tests(url)


def _db():
    return db_layer.session_factory()()


def _count(table: str) -> int:
    """直接数表：用于证明"读接口没有抓取""幂等没有多建行"这类负面事实。"""
    with db_layer.engine().connect() as connection:
        return int(connection.execute(sql_text("SELECT count(*) FROM " + table)).scalar() or 0)


def _auth(token: str = "user-a", tenant: str = "") -> dict:
    headers = {"Authorization": "Bearer " + token}
    if tenant:
        headers["X-Tenant-Id"] = tenant
    return headers


def _same_moment(left: str, right: str) -> bool:
    """比较两个 ISO 时间是否同一时刻；SQLite 读回来的时间没有偏移量，按 UTC 补。"""
    def _moment(text: str) -> datetime:
        moment = datetime.fromisoformat(text)
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return _moment(left) == _moment(right)


def _stub(entries, *, reached_origin=True, data_mode="live", origin_updated_at=None,
          http_status=200):
    """测试桩采集器：返回结构必须与方案 6.1 的 provider 协议一致。"""
    def fetch(_row, _timeout):
        return {"entries": entries, "reached_origin": reached_origin, "data_mode": data_mode,
                "http_status": http_status, "origin_updated_at": origin_updated_at}
    return fetch


def _approve_source(db, source_id, *, origin_platform="", entries=None, **stub_kwargs):
    """把来源改成"已审批且启用"并注册桩采集器，返回来源行。

    这不是合成数据通道：evidence 明说是测试桩，容器摘要留空表示不经 NewsNow 内网校验。
    """
    row = db.get(models.HotspotSource, source_id)
    if row is None:
        row = models.HotspotSource(source_id=source_id, display_name=source_id,
                                   origin_platform=origin_platform or source_id,
                                   provider="test-stub")
        db.add(row)
    row.enabled = True
    row.approval_status = "approved"
    row.approval_evidence = STUB_EVIDENCE
    row.approved_by = "test-harness"
    row.approved_at = datetime.now(timezone.utc)
    row.locked_commit = "test-pin"
    row.container_digest = ""
    db.flush()
    if entries is not None:
        hotspots.register_provider(source_id, _stub(entries, **stub_kwargs))
    return row


def _outcome(result: dict, source_id: str) -> dict:
    return next(item for item in result["sources"] if item["source_id"] == source_id)


def _publish_with(db, entries_by_source: dict) -> None:
    """按 {source_id: entries} 造数据并发布一版榜单，随后提交以便 HTTP 层可见。"""
    for source_id, entries in entries_by_source.items():
        _approve_source(db, source_id, entries=entries)
    hotspots.refresh(db)
    hotspots.publish(db)
    db.commit()


# ------------------------------------------------------------------ 1. 空态：没有已审批来源

def test_no_approved_sources_is_no_work_and_an_honest_empty_board():
    """方案的安全默认值：来源默认关闭未审批，刷新必须报 no_work，榜单必须解释空态。"""
    db = _db()
    try:
        result = hotspots.refresh(db)
        assert result["state"] == "no_work"
        assert result["sources"], "候选来源必须逐个列出，空态才解释得清原因"
        assert {item["outcome"] for item in result["sources"]} == {
            hotspots.OUTCOME_SKIPPED_DISABLED}
        assert all(item["verified_upstream_at"] is None for item in result["sources"])
        assert result["production_ready"] is False
        assert hotspots.NOTICE_NO_SOURCES in result["notice"]

        board = hotspots.board(db)
        assert board["data_mode"] == "no_approved_sources"
        assert board["items"] == []
        assert board["production_ready"] is False
        assert board["notice"]
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ 2. GET 不抓取

def test_reading_the_board_never_creates_a_source_check():
    """A04：浏览与分页读取固定发布版本，不创建抓取任务、不调用模型。"""
    db = _db()
    try:
        hotspots.board(db)
        hotspots.board(db, view_key="article")
        assert _count("hs_source_checks") == 0
        assert _count("hs_refresh_groups") == 0
    finally:
        db.close()
    # HTTP 入口同样只读：连续两次 GET 不产生任何检查行。
    for _ in range(2):
        assert client.get("/api/hotspots", headers=_auth()).status_code == 200
    assert _count("hs_source_checks") == 0
    assert _count("hs_refresh_groups") == 0


# ------------------------------------------------------------------ 3. 六时间语义

def test_a_cache_hit_is_never_a_verified_upstream_visit():
    """A07：HTTP 200 只证明响应成功。

    provider 说 reached_origin=False 时 outcome 必须是 cache_only，且
    verified_upstream_at 必须保持 None——它不是"刚更新"，也不能推进来源新鲜度。
    """
    db = _db()
    try:
        _approve_source(db, "weibo", entries=[{"title": "缓存命中条目", "rank": 1}],
                        reached_origin=False, data_mode="cache")
        result = hotspots.refresh(db)
        db.commit()

        outcome = _outcome(result, "weibo")
        assert outcome["outcome"] == hotspots.OUTCOME_CACHE_ONLY
        assert outcome["verified_upstream_at"] is None
        check = db.scalars(select(models.HotspotSourceCheck).where(
            models.HotspotSourceCheck.source_id == "weibo")).one()
        assert check.outcome == "cache_only"
        assert check.verified_upstream_at is None
        assert check.data_mode == "cache"
        assert check.http_status == 200  # 响应是成功的，只是没有证实访问原平台
        source = db.get(models.HotspotSource, "weibo")
        assert source.last_checked_at is None
        db.rollback()
    finally:
        db.close()


def test_refresh_endpoint_never_claims_a_cache_hit_as_reaching_the_platform():
    """A07 走 HTTP 入口再验一次：202 与 job_id 都不代表访问过原平台。"""
    db = _db()
    try:
        _approve_source(db, "weibo", entries=[{"title": "缓存命中条目", "rank": 1}],
                        reached_origin=False, data_mode="cache")
        db.commit()
    finally:
        db.close()

    response = client.post("/api/hotspots/refresh", json={"source_ids": ["weibo"]},
                           headers={**_auth(), "Idempotency-Key": "idem-key-0004"})
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "no_work"
    assert body["status"] == "no_work"
    assert "同步执行" in body["notice"], "试点是同步执行，notice 必须说明"
    outcome = _outcome(body, "weibo")
    assert outcome["outcome"] == hotspots.OUTCOME_CACHE_ONLY
    assert outcome["verified_upstream_at"] is None

    job = client.get(f"/api/hotspots/refresh-jobs/{body['job_id']}",
                     headers=_auth()).json()
    assert job["results"] == job["sources"]
    assert job["sources"][0]["outcome"] == "cache_only"
    assert job["sources"][0]["verified_upstream_at"] is None


# ------------------------------------------------------------------ 4. stale_fallback 记为失败

def test_a_stale_fallback_is_recorded_as_failure_not_success():
    """A08：上游失败后退旧缓存必须算失败，旧快照保留，不能计入 upstream 成功。"""
    db = _db()
    try:
        _approve_source(db, "weibo", entries=[{"title": "过期缓存条目", "rank": 1}],
                        reached_origin=False, data_mode="stale_fallback")
        result = hotspots.refresh(db)
        db.commit()

        outcome = _outcome(result, "weibo")
        assert outcome["outcome"] == hotspots.OUTCOME_FAILED
        assert outcome["verified_upstream_at"] is None
        assert result["state"] == "failed"
        assert _count("hs_snapshots") == 0, "旧缓存不得被当成新快照写进去"
        check = db.scalars(select(models.HotspotSourceCheck).where(
            models.HotspotSourceCheck.source_id == "weibo")).one()
        assert check.state == "failed" and check.verified_upstream_at is None
        source = db.get(models.HotspotSource, "weibo")
        assert source.consecutive_failures == 1
        assert source.cooldown_until is not None
        db.rollback()
    finally:
        db.close()


# ------------------------------------------------------------------ 5. 合成数据必须显式开启

def test_synthetic_data_needs_the_explicit_flag_and_is_labelled_end_to_end(monkeypatch):
    """合成通道默认关闭；打开后数据可以走完整链路，但必须标成 synthetic 与 unknown。"""
    db = _db()
    try:
        with pytest.raises(RuntimeError):
            hotspots.enable_synthetic(db)  # 未设开关：拒绝注入
        assert db.get(models.HotspotSource, "weibo") is None, "被拒绝时不得改动任何来源"

        monkeypatch.setenv("HOTSPOTS_ALLOW_SYNTHETIC", "1")
        hotspots.enable_synthetic(db, "weibo", entries=[
            {"title": "合成测试条目：城市读书活动", "rank": 1,
             "features": {"practical": 1.0, "visual": 0.8}},
            {"title": "合成测试条目：周末露营装备清单", "rank": 2,
             "features": {"conversational": 1.0}},
        ])
        refreshed = hotspots.refresh(db)
        db.commit()
        # 假采集器自称 reached_origin，但合成观察不是原平台访问：不能伪造可验证时间。
        synthetic_outcome = _outcome(refreshed, "weibo")
        assert synthetic_outcome["verified_upstream_at"] is None

        published = hotspots.publish(db)
        db.commit()
        assert published["data_mode"] == "synthetic"
        board = hotspots.board(db)
        assert board["data_mode"] == "synthetic"
        assert {item["title"] for item in board["items"]} == {
            "合成测试条目：城市读书活动", "合成测试条目：周末露营装备清单"}
        assert board["production_ready"] is False
        assert all(item["reason"]["freshness"] == "unknown" for item in board["items"])
        db.rollback()
    finally:
        db.close()


def test_synthetic_stays_refused_after_the_flag_is_removed(monkeypatch):
    """开关是每次读取环境变量，不是一次性状态：关掉后合成通道立即关闭。"""
    db = _db()
    try:
        monkeypatch.setenv("HOTSPOTS_ALLOW_SYNTHETIC", "1")
        assert hotspots.allow_synthetic() is True
        monkeypatch.delenv("HOTSPOTS_ALLOW_SYNTHETIC")
        assert hotspots.allow_synthetic() is False
        with pytest.raises(RuntimeError):
            hotspots.enable_synthetic(db)
    finally:
        db.close()


# ------------------------------------------------------------------ 6. 同源不重复计票

def test_two_providers_of_one_origin_platform_count_as_a_single_vote():
    """A15：两个 provider（不同 source_id、同一 origin_platform）报同一条标题时，

    事件成员的 distinct origin 只能算 1——来源原榜两条都保留，但覆盖票不重复。
    """
    db = _db()
    try:
        title = "同源计票测试：城市马拉松报名今日开启"
        _approve_source(db, "weibo", entries=[{"title": title, "rank": 1}])
        _approve_source(db, "weibo-mirror", origin_platform="weibo",
                        entries=[{"title": title, "rank": 3}])
        hotspots.refresh(db)
        hotspots.publish(db)
        db.commit()

        item = next(row for row in hotspots.board(db)["items"] if row["title"] == title)
        assert item["reason"]["distinct_origins"] == 1
        assert item["reason"]["coverage"] == 0.0
        assert item["sources"] == ["weibo", "weibo-mirror"], "来源记录两条都要保留"
        db.rollback()
    finally:
        db.close()


# ------------------------------------------------------------------ 7. 保守聚合

def test_conservative_clustering_keeps_different_stories_apart_and_merges_identical_ones():
    """A16：宁少合并不误合并；完全相同的标题才合并成一个事件。"""
    db = _db()
    try:
        same = "地铁新线路今日正式开通运营"
        _approve_source(db, "weibo", entries=[
            {"title": same, "rank": 1},
            {"title": "秋季新茶饮口味评测合集", "rank": 2}])
        _approve_source(db, "zhihu", origin_platform="zhihu", entries=[
            {"title": same, "rank": 1},
            {"title": "某地暴雨导致部分路段积水", "rank": 2}])
        hotspots.refresh(db)
        hotspots.publish(db)
        db.commit()

        events = list(db.scalars(select(models.HotspotEvent)).all())
        assert sorted(event.canonical_title for event in events) == sorted(
            [same, "秋季新茶饮口味评测合集", "某地暴雨导致部分路段积水"])
        merged = next(event for event in events if event.canonical_title == same)
        members = list(db.scalars(select(models.HotspotEventMember).where(
            models.HotspotEventMember.event_id == merged.id)).all())
        assert len(members) == 2
        db.rollback()
    finally:
        db.close()


# ------------------------------------------------------------------ 8. 场景榜不是同一份数据

def test_scenario_views_are_not_the_same_array_reshuffled():
    """A22：四榜允许重合，但同一批候选用同一套特征必须排出可解释的不同顺序。"""
    db = _db()
    try:
        explanatory = "深度解析：产业链协同的五个关键环节"
        experience = "周末打卡：这家店的体验感拉满"
        features_explanatory = {"experience": 0.0, "practical": 0.0, "conversational": 0.0,
                                "explanatory": 1.0, "visual": 0.0, "local_activity": 0.0}
        features_experience = {"experience": 1.0, "practical": 1.0, "conversational": 1.0,
                               "explanatory": 0.0, "visual": 1.0, "local_activity": 0.0}
        _approve_source(db, "weibo", entries=[
            {"title": explanatory, "rank": 1, "domain": "科技",
             "features": features_explanatory}])
        _approve_source(db, "zhihu", origin_platform="zhihu", entries=[
            {"title": experience, "rank": 1, "domain": "生活", "features": features_experience}])
        hotspots.refresh(db)
        hotspots.publish(db)
        db.commit()

        xiaohongshu = [row["event_id"] for row in hotspots.board(
            db, view_key="xiaohongshu")["items"]]
        article = [row["event_id"] for row in hotspots.board(db, view_key="article")["items"]]
        assert len(xiaohongshu) == len(article) == 2
        assert xiaohongshu != article, "两个场景的排序必须反映不同权重，而不是同一份数组"
        assert sorted(xiaohongshu) == sorted(article), "场景榜允许重合，不得为了差异删条目"
        db.rollback()
    finally:
        db.close()


# ------------------------------------------------------------------ 9. 风险内容不入场景榜

def test_risk_content_is_kept_out_of_every_scenario_board():
    """A32：灾难伤亡类高风险内容默认不进入商业推荐榜，但事件本身与来源记录不删除。"""
    db = _db()
    try:
        risky = "事故相关热点：某路段发生严重事故"
        _approve_source(db, "weibo", entries=[
            {"title": "普通文化热点：博物馆夜场开放", "rank": 1, "domain": "文化"},
            {"title": risky, "rank": 2, "domain": "社会", "risk_flags": ["tragedy"]}])
        hotspots.refresh(db)
        hotspots.publish(db)
        db.commit()

        for view_key in rules.VIEW_WEIGHTS:
            titles = [row["title"] for row in hotspots.board(db, view_key=view_key)["items"]]
            assert "普通文化热点：博物馆夜场开放" in titles
            assert risky not in titles, f"{view_key} 不得推荐商业借势高风险内容"
        assert db.scalars(select(models.HotspotEvent).where(
            models.HotspotEvent.canonical_title == risky)).first() is not None
        db.rollback()
    finally:
        db.close()


# ------------------------------------------------------------------ 10. HTTP 幂等

def test_write_endpoints_replay_the_same_key_and_reject_a_different_body():
    headers = {**_auth(), "Idempotency-Key": "idem-key-0001"}
    first = client.post("/api/hotspots/refresh", json={"source_ids": []}, headers=headers)
    assert first.status_code == 202
    assert first.json()["state"] == "no_work"
    assert first.json()["job_id"]
    job_id = first.json()["job_id"]

    replay = client.post("/api/hotspots/refresh", json={"source_ids": []}, headers=headers)
    assert replay.status_code == 202
    assert replay.headers["Idempotent-Replay"] == "true"
    assert replay.json()["job_id"] == job_id
    assert _count("hs_refresh_groups") == 1, "同键同 body 必须复用，不能再建任务组"

    conflict = client.post("/api/hotspots/refresh", json={"source_ids": ["weibo"]},
                           headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"

    too_short = client.post("/api/hotspots/refresh", json={"source_ids": []},
                            headers={**_auth(), "Idempotency-Key": "short"})
    assert too_short.status_code == 400
    assert too_short.json()["code"] == "IDEMPOTENCY_KEY_INVALID"

    missing = client.post("/api/hotspots/refresh", json={"source_ids": []}, headers=_auth())
    assert missing.status_code == 400
    assert missing.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_a_lost_idempotency_race_replays_instead_of_duplicating(monkeypatch):
    """并发窗口：等我们发现"键还不存在"时，另一个同键请求已经登记成功。

    输的一方必须回滚自己刚创建的资源、按复用返回赢家的响应，而不是留下两条材料或 500。
    """
    db = _db()
    try:
        _publish_with(db, {"weibo": [{"title": "幂等竞态测试条目", "rank": 1}]})
        release_id = hotspots.latest_release(db).id
        event_id = db.scalars(select(models.HotspotEvent)).first().id
    finally:
        db.close()

    headers = {**_auth(), "Idempotency-Key": "idem-key-race-1"}
    body = {"event_id": event_id, "release_id": release_id, "view": "xiaohongshu"}
    first = client.post("/api/hotspots/selections", json=body, headers=headers)
    assert first.status_code == 201

    real_replay = hotspots.idempotent_replay
    calls = {"seen": 0}

    def racing_replay(*args, **kwargs):
        calls["seen"] += 1
        if calls["seen"] == 1:
            return None, "new"  # 只骗过第一次检查：登记时才会撞上唯一约束
        return real_replay(*args, **kwargs)

    monkeypatch.setattr(hotspots, "idempotent_replay", racing_replay)
    second = client.post("/api/hotspots/selections", json=body, headers=headers)
    assert second.status_code == 201
    assert second.headers.get("Idempotent-Replay") == "true"
    assert second.json()["selection_id"] == first.json()["selection_id"]
    assert _count("hs_selections") == 1


# ------------------------------------------------------------------ 11. 鉴权与隔离

def test_authorization_is_the_only_identity_source():
    """A28：不带平台认证一律 401；X-Studio-Request 之类的开发头不是身份。"""
    dev_header = {"X-Studio-Request": "user-a", "Idempotency-Key": "idem-key-0002"}
    assert client.post("/api/hotspots/refresh", json={"source_ids": []},
                       headers=dev_header).status_code == 401
    assert client.get("/api/hotspots").status_code == 401
    assert client.get("/api/hotspots/sources").status_code == 401
    assert client.get("/api/hotspots/favorites").status_code == 401
    assert client.get("/api/hotspots", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/api/hotspots", headers={"Authorization": "Bearer "}).status_code == 401
    body = client.get("/api/hotspots").json()
    assert body["code"] == "UNAUTHENTICATED" and body["request_id"]


def test_refresh_jobs_are_visible_only_to_their_owner():
    headers = {**_auth("user-a"), "Idempotency-Key": "idem-key-0003"}
    submitted = client.post("/api/hotspots/refresh", json={"source_ids": []}, headers=headers)
    assert submitted.status_code == 202
    assert submitted.json()["sources"], "任务组要带每个来源的明细"
    assert {item["outcome"] for item in submitted.json()["sources"]} == {
        hotspots.OUTCOME_SKIPPED_DISABLED}
    job_id = submitted.json()["job_id"]

    mine = client.get(f"/api/hotspots/refresh-jobs/{job_id}", headers=_auth("user-a"))
    assert mine.status_code == 200
    assert mine.json()["job_id"] == job_id
    assert mine.json()["state"] == "no_work"
    assert mine.json()["production_ready"] is False
    assert mine.json()["notice"]

    other_user = client.get(f"/api/hotspots/refresh-jobs/{job_id}", headers=_auth("user-b"))
    assert other_user.status_code == 404
    assert other_user.json()["code"] == "REFRESH_JOB_NOT_FOUND"
    other_tenant = client.get(f"/api/hotspots/refresh-jobs/{job_id}",
                              headers=_auth("user-a", tenant="tenant-b"))
    assert other_tenant.status_code == 404
    assert client.get("/api/hotspots/refresh-jobs/no-such-job",
                      headers=_auth("user-a")).status_code == 404


def test_selections_are_isolated_immutable_and_do_not_generate():
    db = _db()
    try:
        _publish_with(db, {"weibo": [{"title": "选择材料测试条目", "rank": 1}]})
        release_id = hotspots.latest_release(db).id
        event_id = db.scalars(select(models.HotspotEvent)).first().id
    finally:
        db.close()

    headers = {**_auth("user-a"), "Idempotency-Key": "idem-key-0010"}
    body = {"event_id": event_id, "release_id": release_id, "view": "xiaohongshu"}
    created = client.post("/api/hotspots/selections", json=body, headers=headers)
    assert created.status_code == 201
    payload = created.json()
    assert payload["generation_triggered"] is False, "保存材料不触发生成"
    assert payload["production_ready"] is False and payload["notice"]
    assert payload["events"][0]["title"] == "选择材料测试条目"
    selection_id = payload["selection_id"]

    replay = client.post("/api/hotspots/selections", json=body, headers=headers)
    assert replay.status_code == 201
    assert replay.headers["Idempotent-Replay"] == "true"
    assert replay.json()["selection_id"] == selection_id
    assert _count("hs_selections") == 1

    conflict = client.post("/api/hotspots/selections", json={**body, "view": "article"},
                           headers=headers)
    assert conflict.status_code == 409

    mine = client.get(f"/api/hotspots/selections/{selection_id}", headers=_auth("user-a"))
    assert mine.status_code == 200
    assert mine.json()["release_id"] == release_id
    assert mine.json()["production_ready"] is False
    assert client.get(f"/api/hotspots/selections/{selection_id}",
                      headers=_auth("user-b")).status_code == 404
    assert client.get(f"/api/hotspots/selections/{selection_id}",
                      headers=_auth("user-a", tenant="tenant-b")).status_code == 404
    assert client.get("/api/hotspots/selections/no-such-selection",
                      headers=_auth("user-a")).status_code == 404


def test_selection_rejects_material_that_was_never_published():
    db = _db()
    try:
        _publish_with(db, {"weibo": [{"title": "已发布的选择依据", "rank": 1}]})
        release_id = hotspots.latest_release(db).id
        published_event = db.scalars(select(models.HotspotEvent)).first().id
        # 造一个存在但不在该 release 里的事件：不允许新交接，也不允许静默保存空材料。
        orphan = models.HotspotEvent(canonical_title="未进入任何发布版本的事件", domain="其他")
        db.add(orphan)
        db.commit()
        orphan_id = orphan.id
    finally:
        db.close()

    headers = {**_auth(), "Idempotency-Key": "idem-key-0011"}
    unpublished = client.post("/api/hotspots/selections",
                              json={"event_id": orphan_id, "release_id": release_id},
                              headers=headers)
    assert unpublished.status_code == 404
    assert unpublished.json()["code"] == "EVENT_NOT_IN_RELEASE"
    unknown = client.post("/api/hotspots/selections",
                          json={"event_id": "no-such-event", "release_id": release_id},
                          headers=headers)
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "EVENT_NOT_FOUND"
    empty = client.post("/api/hotspots/selections", json={"release_id": release_id},
                        headers=headers)
    assert empty.status_code == 400
    assert empty.json()["code"] == "SELECTION_EMPTY"
    bad_view = client.post("/api/hotspots/selections",
                           json={"event_id": published_event, "release_id": release_id,
                                 "view": "general"}, headers=headers)
    assert bad_view.status_code == 400
    assert bad_view.json()["code"] == "VIEW_UNSUPPORTED"
    bad_moments = client.post("/api/hotspots/selections",
                              json={"event_id": published_event, "release_id": release_id,
                                    "view": "article", "moments_mode": "store"}, headers=headers)
    assert bad_moments.status_code == 400
    assert bad_moments.json()["code"] == "MOMENTS_MODE_INVALID"
    assert _count("hs_selections") == 0
    assert _count("hs_idempotency") == 0, "被拒绝的请求不得留下幂等记录"


# ------------------------------------------------------------------ 12. 收藏

def test_favorites_are_idempotent_isolated_and_delete_is_204_even_when_absent():
    db = _db()
    try:
        _publish_with(db, {"weibo": [{"title": "收藏测试条目", "rank": 1}]})
        event_id = db.scalars(select(models.HotspotEvent)).first().id
    finally:
        db.close()

    headers = {**_auth("user-a"), "Idempotency-Key": "idem-key-0030"}
    first = client.post("/api/hotspots/favorites", json={"event_id": event_id}, headers=headers)
    assert first.status_code == 200
    assert first.json()["event_id"] == event_id
    assert first.json()["production_ready"] is False

    replay = client.post("/api/hotspots/favorites", json={"event_id": event_id}, headers=headers)
    assert replay.status_code == 200
    assert replay.headers["Idempotent-Replay"] == "true"
    assert _same_moment(replay.json()["created_at"], first.json()["created_at"])

    # 换一把幂等键重复收藏同一事件：仍然返回原记录，不新增行。
    again = client.post("/api/hotspots/favorites", json={"event_id": event_id},
                        headers={**_auth("user-a"), "Idempotency-Key": "idem-key-0031"})
    assert again.status_code == 200
    assert again.json()["replayed"] is True
    assert _same_moment(again.json()["created_at"], first.json()["created_at"])
    assert _count("hs_favorites") == 1

    assert client.get("/api/hotspots/favorites", headers=_auth("user-b")).json()["items"] == []
    mine = client.get("/api/hotspots/favorites", headers=_auth("user-a")).json()
    assert [item["event_id"] for item in mine["items"]] == [event_id]
    assert mine["items"][0]["title"] == "收藏测试条目"
    assert mine["production_ready"] is False and mine["next_cursor"] is None

    assert client.delete("/api/hotspots/favorites/no-such-event",
                         headers=_auth("user-a")).status_code == 204
    assert client.delete(f"/api/hotspots/favorites/{event_id}",
                         headers=_auth("user-a")).status_code == 204
    assert client.delete(f"/api/hotspots/favorites/{event_id}",
                         headers=_auth("user-a")).status_code == 204
    assert client.get("/api/hotspots/favorites", headers=_auth("user-a")).json()["items"] == []
    assert client.delete(f"/api/hotspots/favorites/{event_id}",
                         headers={**_auth("user-a"),
                                  "Idempotency-Key": "short"}).status_code == 400
    assert client.post("/api/hotspots/favorites", json={"event_id": "no-such-event"},
                       headers={**_auth("user-a"),
                                "Idempotency-Key": "idem-key-0032"}).status_code == 404


# ------------------------------------------------------------------ HTTP 读接口形状

def test_board_endpoint_returns_the_contract_fields_and_only_known_views():
    response = client.get("/api/hotspots", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert {"release_id", "rule_version", "computed_at", "data_mode", "items",
            "source_statuses", "next_cursor"} <= set(body)
    assert body["production_ready"] is False
    assert body["notice"]
    assert body["view"] == "xiaohongshu"
    assert body["taxonomy_version"]
    assert body["data_mode"] == "no_approved_sources" and body["items"] == []

    for view_key in rules.VIEW_WEIGHTS:
        assert client.get("/api/hotspots", params={"view": view_key},
                          headers=_auth()).status_code == 200
    unsupported = client.get("/api/hotspots", params={"view": "general"}, headers=_auth())
    assert unsupported.status_code == 400
    assert unsupported.json()["code"] == "VIEW_UNSUPPORTED"
    bad_cursor = client.get("/api/hotspots", params={"cursor": "not-a-number"}, headers=_auth())
    assert bad_cursor.status_code == 400
    assert bad_cursor.json()["code"] == "CURSOR_INVALID"


def test_sources_endpoint_lists_every_approval_blocker():
    body = client.get("/api/hotspots/sources", headers=_auth()).json()
    assert body["production_ready"] is False
    assert body["approved_count"] == 0 and body["enabled_count"] == 0
    assert body["items"] == body["sources"]
    assert len(body["sources"]) == len(hotspots.SOURCE_CANDIDATES)
    # 准入与健康状态：默认关闭、未审批、从未检查、没有任何可验证时间。
    assert all(row["approval_status"] == "pending" for row in body["sources"])
    assert all(row["enabled"] is False for row in body["sources"])
    assert all(row["last_outcome"] == "" for row in body["sources"])
    assert all(row["verified_upstream_at"] is None for row in body["sources"])
    assert all(row["last_checked_at"] is None for row in body["sources"])
    assert all(row["approved"] is False for row in body["sources"])
    assert all(row["blockers"] for row in body["sources"]), "必须说明还缺哪份准入证据"


def test_event_detail_exposes_evidence_and_only_a_real_verified_time():
    db = _db()
    try:
        _publish_with(db, {"weibo": [{"title": "事件依据测试条目", "rank": 1}]})
        release_id = hotspots.latest_release(db).id
        event_id = db.scalars(select(models.HotspotEvent)).first().id
    finally:
        db.close()

    missing = client.get("/api/hotspots/events/does-not-exist", headers=_auth())
    assert missing.status_code == 404
    assert missing.json()["code"] == "EVENT_NOT_FOUND"

    body = client.get(f"/api/hotspots/events/{event_id}", headers=_auth()).json()
    assert body["event_id"] == event_id
    assert body["release_id"] == release_id
    assert body["data_mode"] == "live"
    assert body["production_ready"] is False
    assert body["items"][0]["verified_upstream_at"] is not None
    assert body["items"][0]["title"] == "事件依据测试条目"
    assert "保守聚合" in body["notice"]


def test_board_pagination_is_stable_within_a_release():
    titles = ["城市公园开放夜游", "秋季节气美食推荐", "新能源车展本周开幕",
              "图书馆延长开放时间", "周末露营装备清单"]
    db = _db()
    try:
        _publish_with(db, {"weibo": [{"title": title, "rank": index + 1}
                                     for index, title in enumerate(titles)]})
        release_id = hotspots.latest_release(db).id
    finally:
        db.close()

    first = client.get("/api/hotspots", params={"limit": 2}, headers=_auth()).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"] == 2
    assert first["release_id"] == release_id
    second = client.get("/api/hotspots", params={"limit": 2, "cursor": first["next_cursor"]},
                        headers=_auth()).json()
    assert second["release_id"] == release_id
    assert {row["event_id"] for row in first["items"]} & {
        row["event_id"] for row in second["items"]} == set()
    third = client.get("/api/hotspots", params={"limit": 2, "cursor": second["next_cursor"]},
                       headers=_auth()).json()
    assert third["next_cursor"] is None and len(third["items"]) == 1

    filtered = client.get("/api/hotspots", params={"source_id": "zhihu"},
                          headers=_auth()).json()
    assert filtered["items"] == [], "单来源筛选后不得保留其他来源的条目"


# ------------------------------------------------------------------ 13. 纯规则边界

def test_freshness_state_is_unknown_without_a_verified_upstream_time():
    now = datetime.now(timezone.utc)
    assert rules.freshness_state(None, None, now) == "unknown"
    # 原平台自称的更新时间不能替代"我们真的访问到了"。
    assert rules.freshness_state(None, now - timedelta(minutes=1), now) == "unknown"
    assert rules.freshness_state(now - timedelta(minutes=5), None, now) == "fresh"
    assert rules.freshness_state(now - timedelta(minutes=60), None, now) == "aging"
    assert rules.freshness_state(now - timedelta(minutes=200), None, now) == "stale"
    # naive 时间按 UTC 处理，不能因为缺时区就当成过期。
    naive = (now - timedelta(minutes=5)).replace(tzinfo=None)
    assert rules.freshness_state(naive, None, now) == "fresh"


def test_feature_fit_treats_a_missing_feature_as_neutral():
    assert rules.feature_fit({}, "xiaohongshu") == pytest.approx(0.5)
    assert rules.feature_fit({"experience": 1.0}, "xiaohongshu") == pytest.approx(
        0.25 * 1.0 + 0.75 * 0.5)
    # 明确的 0 与"没有标签"不是一回事。
    assert rules.feature_fit({"experience": 0.0}, "xiaohongshu") == pytest.approx(
        0.25 * 0.0 + 0.75 * 0.5)
    assert rules.feature_fit({}, "no-such-view") == 0.0


def test_score_boundaries_at_board_size_one():
    assert rules.position_score(1, 1) == 1.0
    assert rules.position_score(7, 1) == 1.0
    assert rules.position_score(0, 1) == 1.0
    assert rules.coverage_score(-3) == 0.0
    assert rules.coverage_score(3) == 1.0
    assert rules.coverage_score(99) == 1.0

    assert rules.general_score(1, 1, 1, "fresh") == 70.0
    assert rules.general_score(1, 1, 4, "fresh") == 100.0
    assert rules.general_score(1, 1, 1, "unknown") == 35.0
    assert rules.general_score(1, 2, 1, "fresh") == 70.0
    assert rules.general_score(2, 2, 1, "fresh") == 0.0

    # S = 0.65G + 0.35*100*F；把三个权重特征都显式给值，才能验证公式本身，
    # 缺特征会走中性 0.5（那正是下一条断言要验证的行为）。
    assert rules.scenario_score(70.0, {"explanatory": 1.0, "practical": 0.0,
                                       "conversational": 0.0}, "article") == pytest.approx(
        0.65 * 70.0 + 0.35 * 100.0 * 0.6)
    assert rules.scenario_score(0.0, {}, "poster") == pytest.approx(0.35 * 100.0 * 0.5)


def test_commercial_blocked_matches_only_the_four_reviewed_flags():
    for flag in rules.COMMERCIAL_CAUTION_FLAGS:
        assert rules.commercial_blocked([flag]) is True
    assert rules.commercial_blocked(["热门", "数码"]) is False
    assert rules.commercial_blocked([]) is False
    assert rules.commercial_blocked(None) is False
    # 精确匹配：没列入评审的相近标记不会被误挡普通热点。
    assert rules.commercial_blocked(["tragedy_rumor", "rights"]) is False


def test_diverse_relaxes_when_candidates_are_insufficient_but_keeps_determinism():
    candidates = [{"event_id": f"e{index}", "primary_source": "weibo", "domain": "社会",
                   "scenario_score": 50.0 - index} for index in range(3)]
    picked = rules.diverse(candidates, top_n=20)
    assert [row["event_id"] for row in picked] == ["e0", "e1", "e2"]
    assert rules.diverse(candidates, top_n=20) == picked, "同一输入必须稳定同序"
    assert len(rules.diverse(candidates, top_n=1)) == 1

    mixed = [{"event_id": "best", "primary_source": "weibo", "domain": "社会",
              "scenario_score": 90.0},
             {"event_id": "second", "primary_source": "weibo", "domain": "社会",
              "scenario_score": 60.0},
             {"event_id": "third", "primary_source": "zhihu", "domain": "科技",
              "scenario_score": 55.0}]
    relaxed = rules.diverse(mixed, top_n=20)
    assert {row["event_id"] for row in relaxed} == {"best", "second", "third"}
    assert relaxed[0]["event_id"] == "best", "综合分最高的候选必须留在第一位"
    assert len({row["event_id"] for row in relaxed}) == len(relaxed)