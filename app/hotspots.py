"""Hotspot centre service: sources, refresh tasks, releases, selections.

Implemented inside this application by the user's decision, following the plan's contract
and honesty rules, with SQLite instead of PostgreSQL. The difference is stated on every
response (``production_ready: false``) because it is real:

* SQLite is a **single-writer pilot store**. The plan's PostgreSQL-only guarantees -
  ``SKIP LOCKED`` claiming, advisory locks, publishing a release while readers are active -
  are *not* exercised here, so nothing in this module may be described as a concurrency
  result.
* There is **no NewsNow deployment and no approved source**. Every candidate ships
  ``enabled=false, approval_status="pending"`` exactly as the plan's templates do, so a
  refresh reports ``no_work`` and the board reports ``no_approved_sources``. That is the
  honest state, not a bug to paper over.
* Synthetic observations exist **only** for tests and are gated by
  ``HOTSPOTS_ALLOW_SYNTHETIC`` (default off). They are labelled ``synthetic`` end to end and
  must never be presented as live hotspots - the plan forbids exactly that.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import hotspot_rules as rules
from . import models

SOURCE_CANDIDATES = (
    ("weibo", "微博热搜", "weibo"),
    ("baidu", "百度热搜", "baidu"),
    ("zhihu", "知乎热榜", "zhihu"),
    ("toutiao", "今日头条热榜", "toutiao"),
    ("bilibili-hot-search", "B站热搜", "bilibili"),
    ("douyin", "抖音热点", "douyin"),
)

OUTCOME_UPDATED = "updated"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_CACHE_ONLY = "cache_only"
OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED_COOLDOWN = "skipped_cooldown"
OUTCOME_SKIPPED_DISABLED = "skipped_disabled"
OUTCOME_SKIPPED_SYNTHETIC_OFF = "skipped_synthetic_disabled"

DATA_MODE_LIVE = "live"
DATA_MODE_NO_SOURCES = "no_approved_sources"
DATA_MODE_SYNTHETIC = "synthetic"

NOTICE_NOT_PRODUCTION = ("本热点中心运行在现有应用内、使用 SQLite，属非生产形态："
                         "未部署 NewsNow、未接入任何已审批来源、未在 PostgreSQL 上验证并发与发布事务。")
NOTICE_NO_SOURCES = ("没有任何已审批来源被启用，因此没有实时热点可展示。"
                     "来源默认关闭是方案要求的安全设计，不是可以随手打开的开关。")
NOTICE_DISPLAY = "场景推荐榜由公共来源加权排序得到，不是任何平台的官方热榜。"

# source_id -> callable(source_row, timeout) -> list[dict]
PROVIDERS: dict[str, object] = {}


def register_provider(source_id: str, fetch) -> None:
    """Attach a real collector. Without one an enabled source fails loudly, not silently."""
    PROVIDERS[source_id] = fetch


def reset_providers() -> None:
    PROVIDERS.clear()


def allow_synthetic() -> bool:
    return str(os.getenv("HOTSPOTS_ALLOW_SYNTHETIC", "")).strip().lower() in ("1", "true", "yes")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def seed_sources(db: Session) -> int:
    """Create the candidate sources once, disabled and pending approval."""
    created = 0
    for source_id, display, origin in SOURCE_CANDIDATES:
        if db.get(models.HotspotSource, source_id) is None:
            db.add(models.HotspotSource(source_id=source_id, display_name=display,
                                        origin_platform=origin, provider="newsnow",
                                        enabled=False, approval_status="pending"))
            created += 1
    if created:
        db.flush()
    return created


def source_rows(db: Session) -> list[models.HotspotSource]:
    return list(db.scalars(select(models.HotspotSource).order_by(
        models.HotspotSource.source_id)).all())


def approval_blockers(db: Session) -> list[dict]:
    """What stops each source from being switched on. Empty evidence is the blocker."""
    out = []
    for row in source_rows(db):
        missing = []
        if not row.approval_evidence:
            missing.append("缺少可用性与数据使用评审证据")
        if not row.approved_by:
            missing.append("缺少审批人")
        if not row.locked_commit:
            missing.append("未锁定上游提交")
        if not row.container_digest:
            missing.append("未锁定镜像摘要")
        out.append({"source_id": row.source_id, "display_name": row.display_name,
                    "enabled": bool(row.enabled), "approval_status": row.approval_status,
                    "blockers": missing, "approved": not missing})
    return out


# ------------------------------------------------------------------ idempotency

def _digest(payload: dict) -> str:
    import json
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def idempotent_replay(db: Session, *, tenant_id: str, user_id: str, key: str,
                      payload: dict) -> tuple[dict | None, str]:
    """Return (stored_response, state). ``state`` is new / replay / conflict / expired.

    The plan requires a repeated write with the same key *and* the same body to return the
    original result, and the same key with a different body to be rejected rather than
    silently overwriting.
    """
    if not key:
        return None, "missing"
    if not 8 <= len(key) <= 128:
        return None, "invalid"
    digest = _digest(payload)
    row = db.scalars(select(models.HotspotIdempotency).where(
        models.HotspotIdempotency.tenant_id == tenant_id,
        models.HotspotIdempotency.user_id == user_id,
        models.HotspotIdempotency.key == key)).first()
    if row is None:
        return None, "new"
    if _aware(row.created_at) and _now() - _aware(row.created_at) > timedelta(hours=24):
        db.delete(row)
        db.flush()
        return None, "expired"
    if row.request_digest != digest:
        return None, "conflict"
    return dict(row.response or {}), "replay"


def remember(db: Session, *, tenant_id: str, user_id: str, key: str, payload: dict,
             response: dict) -> None:
    """Register the response for this key. The *caller* owns the transaction boundary.

    Concurrency, following ``runtime/lease.py``'s claim: only one request can register a
    given key, so the business row and this registration must sit in the caller's single
    savepoint (SQLAlchemy ``begin_nested``). On a lost race the IntegrityError is raised on
    purpose so the caller can roll the savepoint back - resource and key together - and then
    serve the winner's stored response. Calling ``db.rollback()`` here would throw away a
    business row the caller had already created, and swallowing the error would leave two
    resources for what the API reports as one request.
    """
    if not key:
        return
    db.add(models.HotspotIdempotency(tenant_id=tenant_id, user_id=user_id, key=key,
                                     request_digest=_digest(payload), response=response))
    db.flush()


# ------------------------------------------------------------------ refresh

def refresh(db: Session, *, tenant_id: str = "default", user_id: str = "",
            source_ids: list[str] | None = None, timeout: float = 20.0) -> dict:
    """Run or reuse a refresh. Never blocks on an upstream beyond ``timeout``.

    Every outcome distinguishes what actually happened, because the plan's core honesty rule
    is that "HTTP 200 only proves the response succeeded": a cache hit is ``cache_only`` and
    does **not** advance ``verified_upstream_at``, a stale fallback counts as ``failed``, and
    a run in which every source was skipped is ``no_work`` rather than ``succeeded``.
    """
    seed_sources(db)
    wanted = set(source_ids or [])
    rows = [row for row in source_rows(db) if not wanted or row.source_id in wanted]
    group = models.HotspotRefreshGroup(tenant_id=tenant_id, user_id=user_id, state="running")
    db.add(group)
    db.flush()

    outcomes: list[dict] = []
    any_verified = False
    for row in rows:
        outcome = _check_source(db, group, row, timeout=timeout)
        outcomes.append(outcome)
        any_verified = any_verified or bool(outcome.get("verified_upstream_at"))
    db.flush()

    kinds = {item["outcome"] for item in outcomes}
    if not any_verified and kinds <= {OUTCOME_SKIPPED_DISABLED, OUTCOME_SKIPPED_SYNTHETIC_OFF,
                                      OUTCOME_SKIPPED_COOLDOWN, OUTCOME_CACHE_ONLY, ""}:
        state = "no_work"
    elif any(item["outcome"] == OUTCOME_FAILED for item in outcomes):
        state = "partial_failed" if any_verified else "failed"
    elif any_verified:
        state = "succeeded"
    else:
        state = "no_work"
    group.state = state
    group.completed_at = _now()
    db.flush()
    return {"job_id": group.id, "state": state, "requested_at": group.requested_at,
            "completed_at": group.completed_at, "sources": outcomes,
            "production_ready": False,
            "notice": NOTICE_NOT_PRODUCTION + ("" if any_verified else " " + NOTICE_NO_SOURCES)}


def _check_source(db: Session, group: models.HotspotRefreshGroup,
                  row: models.HotspotSource, *, timeout: float) -> dict:
    check = models.HotspotSourceCheck(group_id=group.id, source_id=row.source_id,
                                      checked_at=_now(), state="running")
    db.add(check)
    db.flush()
    base = {"source_id": row.source_id, "display_name": row.display_name,
            "checked_at": check.checked_at, "verified_upstream_at": None,
            "origin_updated_at": None}

    if not row.enabled or row.approval_status != "approved":
        # The safety design, not a defect: an unapproved source is never fetched.
        return {**base, "state": "succeeded", "outcome": OUTCOME_SKIPPED_DISABLED,
                "error": "", "reason": "来源未启用或未审批通过"}
    cooldown = _aware(row.cooldown_until)
    if cooldown and cooldown > _now():
        return {**base, "state": "succeeded", "outcome": OUTCOME_SKIPPED_COOLDOWN,
                "error": "", "reason": "熔断冷却中"}
    if row.approval_status == "approved" and not row.approval_evidence:
        return {**base, "state": "failed", "outcome": OUTCOME_FAILED,
                "error": "已启用但缺少准入证据，拒绝采集"}
    if row.container_digest and row.provider == "newsnow" and not os.getenv("NEWSNOW_INTERNAL_BASE_URL"):
        return {**base, "state": "failed", "outcome": OUTCOME_FAILED,
                "error": "未配置 NEWSNOW_INTERNAL_BASE_URL，无法证实访问原平台"}

    fetch = PROVIDERS.get(row.source_id)
    if fetch is None:
        if not row.enabled:
            return {**base, "state": "succeeded", "outcome": OUTCOME_SKIPPED_DISABLED,
                    "error": "", "reason": "未启用"}
        return {**base, "state": "failed", "outcome": OUTCOME_FAILED,
                "error": "该来源没有采集器；已启用但没有采集器不等于有数据"}

    started = _now()
    try:
        result = fetch(row, timeout) or {}
    except Exception as error:  # noqa: BLE001 - a fetch failure is data, not an exception
        check.state, check.outcome, check.error = "failed", OUTCOME_FAILED, \
            type(error).__name__ + ": " + str(error)[:160]
        row.consecutive_failures += 1
        row.cooldown_until = _now() + timedelta(minutes=10)
        db.flush()
        return {**base, "state": "failed", "outcome": OUTCOME_FAILED,
                "error": check.error, "cooldown_until": row.cooldown_until}

    latency = int((_now() - started).total_seconds() * 1000)
    mode = str(result.get("data_mode") or "").strip()
    entries = list(result.get("entries") or [])
    origin_updated = _aware(result.get("origin_updated_at"))
    reached_origin = bool(result.get("reached_origin"))
    check.checked_at = _now()
    check.latency_ms = latency
    check.data_mode = mode
    check.entries_count = len(entries)
    check.http_status = int(result.get("http_status") or 0)
    check.origin_updated_at = origin_updated

    if mode == "stale_fallback":
        # The plan is explicit: a stale fallback must be recorded as failed, not as success.
        check.state, check.outcome = "failed", OUTCOME_FAILED
        check.error = "上游不可用，返回的是过期缓存"
        row.consecutive_failures += 1
        row.cooldown_until = _now() + timedelta(minutes=10)
        db.flush()
        return {**base, "state": "failed", "outcome": OUTCOME_FAILED, "error": check.error}

    if mode == DATA_MODE_SYNTHETIC:
        # A synthetic observation is *never* a verified upstream visit, whatever the fake
        # collector claims. Writing verified_upstream_at here would forge freshness - the
        # exact failure the six-times contract and the "HTTP 200 proves only a response"
        # rule exist to prevent - and it would also let a later release label synthetic
        # entries as ``live``. The entries are still stored (unverified) so an internal
        # test can exercise the pipeline, and the ``synthetic`` label travels with them.
        check.state, check.outcome = "succeeded", OUTCOME_CACHE_ONLY
        check.error = ""
        _store_snapshot(db, row, check, entries, verified=False, origin_updated=origin_updated)
        db.flush()
        return {**base, "state": "succeeded", "outcome": OUTCOME_CACHE_ONLY, "error": "",
                "reason": "合成数据，不作为可验证的原平台访问，不推进可验证时间"}

    if not reached_origin:
        # HTTP 200 with a cache hit proves a response, not that the origin was reached.
        check.state, check.outcome = "succeeded", OUTCOME_CACHE_ONLY
        check.error = ""
        _store_snapshot(db, row, check, entries, verified=False, origin_updated=origin_updated)
        db.flush()
        return {**base, "state": "succeeded", "outcome": OUTCOME_CACHE_ONLY,
                "error": "", "reason": "缓存命中，未证实访问原平台，不推进可验证时间"}

    check.verified_upstream_at = _now()
    row.last_checked_at = check.verified_upstream_at
    row.consecutive_failures = 0
    row.cooldown_until = None
    changed = _store_snapshot(db, row, check, entries, verified=True,
                              origin_updated=origin_updated)
    check.state = "succeeded"
    check.outcome = OUTCOME_UPDATED if changed else OUTCOME_UNCHANGED
    if changed:
        check.snapshot_changed_at = _now()
    db.flush()
    return {**base, "state": "succeeded", "outcome": check.outcome, "error": "",
            "verified_upstream_at": check.verified_upstream_at,
            "origin_updated_at": origin_updated, "entries_count": len(entries),
            "data_mode": mode, "latency_ms": latency}


def _store_snapshot(db: Session, row: models.HotspotSource, check: models.HotspotSourceCheck,
                    entries: list, *, verified: bool,
                    origin_updated: datetime | None) -> bool:
    import json
    body = json.dumps(entries, ensure_ascii=False, sort_keys=True)
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    previous = db.scalars(select(models.HotspotSnapshot).where(
        models.HotspotSnapshot.source_id == row.source_id).order_by(
        models.HotspotSnapshot.captured_at.desc()).limit(1)).first()
    db.add(models.HotspotSnapshot(source_id=row.source_id, check_id=check.id,
                                  verified=verified, origin_updated_at=origin_updated,
                                  entries=entries, content_hash=content_hash))
    _upsert_items(db, row, entries, origin_updated)
    return previous is None or previous.content_hash != content_hash


def _upsert_items(db: Session, row: models.HotspotSource, entries: list,
                  origin_updated: datetime | None) -> None:
    now = _now()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        external_key = str(entry.get("external_key") or "").strip() or \
            hashlib.sha256(title.encode("utf-8")).hexdigest()[:32]
        existing = db.scalars(select(models.HotspotItem).where(
            models.HotspotItem.source_id == row.source_id,
            models.HotspotItem.external_key == external_key)).first()
        annotated = str(entry.get("domain") or "") or None
        domain = rules.domain_of(title, annotated)
        if existing is None:
            db.add(models.HotspotItem(source_id=row.source_id, external_key=external_key,
                                      title=title[:300], url=str(entry.get("url") or "")[:600],
                                      rank=int(entry.get("rank") or 0), domain=domain,
                                      features=entry.get("features") or {},
                                      risk_flags=list(entry.get("risk_flags") or []),
                                      first_seen_at=now, last_seen_at=now,
                                      origin_updated_at=origin_updated))
        else:
            existing.title = title[:300]
            existing.rank = int(entry.get("rank") or 0)
            existing.last_seen_at = now
            existing.origin_updated_at = origin_updated or existing.origin_updated_at
            if annotated:
                existing.domain = domain
    db.flush()


# ------------------------------------------------------------------ clustering

def _normalize(title: str) -> str:
    import re
    return re.sub(r"[\s\W_]+", "", str(title or "")).casefold()


def _bigrams(text: str) -> set[str]:
    text = _normalize(text)
    return {text[index:index + 2] for index in range(max(1, len(text) - 1))}


def _same_story(left: str, right: str, threshold: float = 0.8) -> bool:
    """Conservative clustering: an over-merge is worse than a missed merge.

    The plan asks for a mechanism whose dangerous merges are verifiable against counter
    examples, so the rule is a high bigram-overlap bar plus exact-normalised equality. Two
    unrelated stories that share a few characters stay apart.
    """
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return False
    if a == b:
        return True
    ga, gb = _bigrams(left), _bigrams(right)
    if not ga or not gb:
        return False
    overlap = len(ga & gb) / min(len(ga), len(gb))
    return overlap >= threshold


def build_events(db: Session, *, limit: int = 400) -> int:
    """Cluster recent items into events, newest first. Returns the number of events."""
    items = list(db.scalars(select(models.HotspotItem).order_by(
        models.HotspotItem.last_seen_at.desc()).limit(limit)).all())
    events = list(db.scalars(select(models.HotspotEvent)).all())
    # Coverage is counted per *origin platform*, not per source_id: NewsNow and DailyHotApi
    # serving the same Weibo board are one origin, and a source without a row falls back to
    # its own id rather than to an empty string. Using source_id here (as an earlier version
    # did) let a second provider of the same platform buy an extra vote, which the plan
    # forbids: 换 provider 不增加 C.
    origins = {row.source_id: (row.origin_platform or row.source_id)
               for row in db.scalars(select(models.HotspotSource)).all()}
    created = 0
    for item in items:
        match = None
        for event in events:
            if event.domain != item.domain:
                continue
            if _same_story(item.title, event.canonical_title):
                match = event
                break
        if match is None:
            match = models.HotspotEvent(canonical_title=item.title[:300], domain=item.domain,
                                        features=dict(item.features or {}),
                                        risk_flags=list(item.risk_flags or []),
                                        first_seen_at=item.first_seen_at,
                                        last_seen_at=item.last_seen_at)
            db.add(match)
            db.flush()
            events.append(match)
            created += 1
        member = db.scalars(select(models.HotspotEventMember).where(
            models.HotspotEventMember.event_id == match.id,
            models.HotspotEventMember.item_id == item.id)).first()
        if member is None:
            db.add(models.HotspotEventMember(event_id=match.id, item_id=item.id,
                                             source_id=item.source_id,
                                             origin_platform=origins.get(item.source_id,
                                                                         item.source_id)))
        match.last_seen_at = max(_aware(match.last_seen_at), _aware(item.last_seen_at))
        match.risk_flags = sorted(set(list(match.risk_flags or [])
                                      + list(item.risk_flags or [])))
        for name, value in dict(item.features or {}).items():
            features = dict(match.features or {})
            features[name] = max(float(features.get(name) or 0.0), float(value or 0.0))
            match.features = features
    db.flush()
    return created


# ------------------------------------------------------------------ release

def publish(db: Session, *, top_n: int | None = None) -> dict:
    """Build and publish a release from a frozen candidate pool.

    The pool is frozen before scoring so a concurrent write cannot shift ranks mid-build,
    and a release is written once and never mutated: pagination and immutable selections
    both depend on that.
    """
    seed_sources(db)
    build_events(db)
    top_n = int(top_n or rules.DIVERSITY["top_n"])
    items = list(db.scalars(select(models.HotspotItem)).all())
    by_id = {item.id: item for item in items}
    members = list(db.scalars(select(models.HotspotEventMember)).all())
    per_event: dict[str, list[models.HotspotEventMember]] = {}
    for member in members:
        per_event.setdefault(member.event_id, []).append(member)

    checks = list(db.scalars(select(models.HotspotSourceCheck).order_by(
        models.HotspotSourceCheck.checked_at.desc())).all())
    verified_by_source: dict[str, models.HotspotSourceCheck] = {}
    for check in checks:
        if check.source_id not in verified_by_source and check.verified_upstream_at:
            verified_by_source[check.source_id] = check

    candidates = []
    for event in db.scalars(select(models.HotspotEvent)).all():
        links = per_event.get(event.id) or []
        if not links:
            continue
        # Distinct origins, not rows: two providers serving one origin must count once.
        origins = {link.origin_platform or link.source_id for link in links}
        ranks = [by_id[link.item_id].rank for link in links if link.item_id in by_id]
        rank = min(ranks) if ranks else 1
        board = max(1, max(ranks) if ranks else 1)
        state = "unknown"
        origin_updated = None
        for link in links:
            check = verified_by_source.get(link.source_id)
            if check is not None:
                state = rules.freshness_state(check.verified_upstream_at,
                                              check.origin_updated_at)
                origin_updated = check.origin_updated_at or origin_updated
                break
        general = rules.general_score(rank, board, len(origins), state)
        candidates.append({
            "event_id": event.id, "title": event.canonical_title, "domain": event.domain,
            "features": dict(event.features or {}), "risk_flags": list(event.risk_flags or []),
            "distinct_origins": len(origins), "rank": rank, "board_size": board,
            "freshness": state, "general_score": general,
            "origin_updated_at": origin_updated,
            "primary_source": sorted(origins)[0] if origins else "",
            "commercial_blocked": rules.commercial_blocked(event.risk_flags),
            "position_score": round(rules.position_score(rank, board), 4),
            "coverage": round(rules.coverage_score(max(0, len(origins) - 1)), 4)})

    verified_any = bool(verified_by_source)
    synthetic_only = bool(items) and not verified_any and allow_synthetic()
    data_mode = (DATA_MODE_LIVE if verified_any
                 else (DATA_MODE_SYNTHETIC if synthetic_only else DATA_MODE_NO_SOURCES))
    release = models.HotspotBoardRelease(rule_version=rules.RULE_VERSION,
                                         taxonomy_version=rules.TAXONOMY_VERSION,
                                         data_mode=data_mode, published=False,
                                         candidate_pool=[{"event_id": row["event_id"],
                                                          "general_score": row["general_score"]}
                                                         for row in candidates],
                                         source_statuses=approval_blockers(db),
                                         production_ready=False,
                                         notice=NOTICE_NOT_PRODUCTION + " " + NOTICE_DISPLAY)
    db.add(release)
    db.flush()

    for view_key in rules.VIEW_WEIGHTS:
        scored = []
        for row in candidates:
            if row["commercial_blocked"]:
                # Excluded from the views used to sell, while the source view may still
                # show it: the plan separates those two questions deliberately.
                continue
            item = dict(row)
            item["scenario_score"] = rules.scenario_score(row["general_score"],
                                                          row["features"], view_key)
            item["feature_fit"] = round(rules.feature_fit(row["features"], view_key), 4)
            scored.append(item)
        for rank, row in enumerate(rules.diverse(scored, top_n=top_n), start=1):
            db.add(models.HotspotBoardEntry(release_id=release.id, view_key=view_key,
                                            rank=rank, event_id=row["event_id"],
                                            general_score=row["general_score"],
                                            scenario_score=row["scenario_score"],
                                            reason=rules.explain(row)))
    release.published = True
    db.flush()
    return board_summary(db, release.id)


def latest_release(db: Session) -> models.HotspotBoardRelease | None:
    return db.scalars(select(models.HotspotBoardRelease).where(
        models.HotspotBoardRelease.published.is_(True)).order_by(
        models.HotspotBoardRelease.computed_at.desc()).limit(1)).first()


def board_summary(db: Session, release_id: str) -> dict:
    release = db.get(models.HotspotBoardRelease, release_id)
    if release is None:
        return {"error": "release_not_found"}
    return {"release_id": release.id, "rule_version": release.rule_version,
            "taxonomy_version": release.taxonomy_version, "data_mode": release.data_mode,
            "computed_at": release.computed_at, "production_ready": release.production_ready,
            "notice": release.notice, "source_statuses": release.source_statuses,
            "views": list(rules.VIEW_WEIGHTS)}


def board(db: Session, *, view_key: str = "xiaohongshu", source_id: str = "",
          limit: int = 20, cursor: int = 0) -> dict:
    """Read a published board. Browsing never triggers a fetch."""
    release = latest_release(db)
    if release is None:
        return {"release_id": "", "data_mode": DATA_MODE_NO_SOURCES, "items": [],
                "computed_at": None, "production_ready": False,
                "notice": NOTICE_NOT_PRODUCTION + " " + NOTICE_NO_SOURCES,
                "source_statuses": approval_blockers(db), "next_cursor": None}
    if view_key not in rules.VIEW_WEIGHTS:
        view_key = "xiaohongshu"
    rows = db.scalars(select(models.HotspotBoardEntry).where(
        models.HotspotBoardEntry.release_id == release.id,
        models.HotspotBoardEntry.view_key == view_key).order_by(
        models.HotspotBoardEntry.rank)).all()
    items = []
    for row in rows:
        event = db.get(models.HotspotEvent, row.event_id)
        if event is None:
            continue
        members = db.scalars(select(models.HotspotEventMember).where(
            models.HotspotEventMember.event_id == event.id)).all()
        if source_id and not any(m.source_id == source_id for m in members):
            continue
        items.append({"event_id": event.id, "title": event.canonical_title,
                      "domain": event.domain, "rank": row.rank,
                      "general_score": row.general_score,
                      "scenario_score": row.scenario_score, "reason": row.reason,
                      "risk_flags": list(event.risk_flags or []),
                      "sources": sorted({m.source_id for m in members}),
                      "commercial_blocked": rules.commercial_blocked(event.risk_flags)})
    window = items[cursor:cursor + max(1, min(50, limit))]
    next_cursor = cursor + len(window) if cursor + len(window) < len(items) else None
    return {"release_id": release.id, "view_key": view_key, "data_mode": release.data_mode,
            "computed_at": release.computed_at, "production_ready": release.production_ready,
            "notice": release.notice + " " + NOTICE_DISPLAY,
            "items": window, "total": len(items), "next_cursor": next_cursor,
            "source_statuses": release.source_statuses}


def event_detail(db: Session, event_id: str) -> dict | None:
    event = db.get(models.HotspotEvent, event_id)
    if event is None:
        return None
    members = db.scalars(select(models.HotspotEventMember).where(
        models.HotspotEventMember.event_id == event_id)).all()
    items = []
    for member in members:
        item = db.get(models.HotspotItem, member.item_id)
        if item is None:
            continue
        checks = db.scalars(select(models.HotspotSourceCheck).where(
            models.HotspotSourceCheck.source_id == item.source_id,
            models.HotspotSourceCheck.verified_upstream_at.isnot(None)).order_by(
            models.HotspotSourceCheck.checked_at.desc()).limit(1)).first()
        items.append({"item_id": item.id, "source_id": item.source_id, "title": item.title,
                      "url": item.url, "rank": item.rank,
                      "first_seen_at": item.first_seen_at, "last_seen_at": item.last_seen_at,
                      "origin_updated_at": item.origin_updated_at,
                      "verified_upstream_at": checks.verified_upstream_at if checks else None,
                      "domain": item.domain, "features": item.features,
                      "risk_flags": item.risk_flags})
    return {"event_id": event.id, "title": event.canonical_title, "domain": event.domain,
            "features": event.features, "risk_flags": event.risk_flags,
            "first_seen_at": event.first_seen_at, "last_seen_at": event.last_seen_at,
            "distinct_origins": len({item["source_id"] for item in items}),
            "items": items, "production_ready": False,
            "notice": "事件由保守聚合得到；同一原平台的多条记录只计一票。"}


# ------------------------------------------------------------------ selections

def save_selection(db: Session, *, tenant_id: str, user_id: str, release_id: str,
                   event_ids: list[str]) -> dict:
    """Persist an immutable hand-off. Does not trigger generation."""
    release = db.get(models.HotspotBoardRelease, release_id) if release_id else None
    if release is None:
        release = latest_release(db)
    if release is None:
        return {"error": "no_release"}
    snapshot = []
    for event_id in event_ids[:20]:
        detail = event_detail(db, event_id)
        if detail:
            snapshot.append({"event_id": detail["event_id"], "title": detail["title"],
                             "domain": detail["domain"],
                             "sources": sorted({item["source_id"] for item in detail["items"]}),
                             "risk_flags": detail["risk_flags"]})
    selection = models.HotspotSelection(tenant_id=tenant_id, user_id=user_id,
                                       release_id=release.id, event_ids=list(event_ids[:20]),
                                       payload={"events": snapshot,
                                                "release_id": release.id,
                                                "data_mode": release.data_mode})
    db.add(selection)
    db.flush()
    return {"selection_id": selection.id, "release_id": release.id,
            "event_count": len(snapshot), "events": snapshot,
            "generation_triggered": False,
            "notice": "已保存不可变热点材料；本次操作不触发生成。"}


def get_selection(db: Session, *, tenant_id: str, user_id: str, selection_id: str) -> dict | None:
    row = db.get(models.HotspotSelection, selection_id)
    # Isolation: another tenant's or user's selection is reported as absent, not as forbidden,
    # so the endpoint does not confirm that someone else's id exists.
    if row is None or row.tenant_id != tenant_id or row.user_id != user_id:
        return None
    return {"selection_id": row.id, "release_id": row.release_id,
            "event_ids": row.event_ids, "payload": row.payload,
            "created_at": row.created_at}


def add_favorite(db: Session, *, tenant_id: str, user_id: str, event_id: str) -> dict:
    existing = db.scalars(select(models.HotspotFavorite).where(
        models.HotspotFavorite.tenant_id == tenant_id,
        models.HotspotFavorite.user_id == user_id,
        models.HotspotFavorite.event_id == event_id)).first()
    if existing is not None:
        return {"event_id": event_id, "created_at": existing.created_at, "replayed": True}
    row = models.HotspotFavorite(tenant_id=tenant_id, user_id=user_id, event_id=event_id)
    db.add(row)
    db.flush()
    return {"event_id": event_id, "created_at": row.created_at, "replayed": False}


def list_favorites(db: Session, *, tenant_id: str, user_id: str) -> list[dict]:
    rows = db.scalars(select(models.HotspotFavorite).where(
        models.HotspotFavorite.tenant_id == tenant_id,
        models.HotspotFavorite.user_id == user_id).order_by(
        models.HotspotFavorite.created_at.desc())).all()
    out = []
    for row in rows:
        event = db.get(models.HotspotEvent, row.event_id)
        out.append({"event_id": row.event_id, "created_at": row.created_at,
                    "title": event.canonical_title if event else ""})
    return out


def remove_favorite(db: Session, *, tenant_id: str, user_id: str, event_id: str) -> None:
    row = db.scalars(select(models.HotspotFavorite).where(
        models.HotspotFavorite.tenant_id == tenant_id,
        models.HotspotFavorite.user_id == user_id,
        models.HotspotFavorite.event_id == event_id)).first()
    if row is not None:
        db.delete(row)
        db.flush()


def source_status(db: Session) -> dict:
    seed_sources(db)
    rows = []
    for row in source_rows(db):
        last = db.scalars(select(models.HotspotSourceCheck).where(
            models.HotspotSourceCheck.source_id == row.source_id).order_by(
            models.HotspotSourceCheck.checked_at.desc()).limit(1)).first()
        rows.append({"source_id": row.source_id, "display_name": row.display_name,
                     "origin_platform": row.origin_platform, "provider": row.provider,
                     "enabled": bool(row.enabled), "approval_status": row.approval_status,
                     "locked_commit": row.locked_commit,
                     "container_digest": row.container_digest,
                     "last_checked_at": row.last_checked_at,
                     "last_outcome": last.outcome if last else "",
                     "verified_upstream_at": last.verified_upstream_at if last else None,
                     "cooldown_until": row.cooldown_until,
                     "consecutive_failures": row.consecutive_failures})
    return {"sources": rows, "approved_count": sum(1 for r in rows if r["approval_status"] == "approved"),
            "enabled_count": sum(1 for r in rows if r["enabled"]),
            "production_ready": False,
            "notice": NOTICE_NOT_PRODUCTION + " " + NOTICE_NO_SOURCES}


# ------------------------------------------------------------------ synthetic (tests only)

def synthetic_provider(entries: list[dict], *, reached_origin: bool = True,
                       data_mode: str = DATA_MODE_SYNTHETIC,
                       origin_updated_at: datetime | None = None):
    """A labelled fake collector. Only ever registered by tests or an explicit flag."""
    def fetch(_row, _timeout):
        return {"entries": entries, "reached_origin": reached_origin, "data_mode": data_mode,
                "http_status": 200,
                "origin_updated_at": origin_updated_at or (_now() - timedelta(minutes=5))}
    return fetch


def enable_synthetic(db: Session, source_id: str = "weibo",
                     entries: list[dict] | None = None) -> None:
    """Switch one source on *for an internal test*, with the label kept attached."""
    if not allow_synthetic():
        raise RuntimeError("HOTSPOTS_ALLOW_SYNTHETIC 未开启，拒绝注入合成数据")
    seed_sources(db)
    row = db.get(models.HotspotSource, source_id)
    if row is None:
        raise RuntimeError("未知来源：" + source_id)
    row.enabled = True
    row.approval_status = "approved"
    row.approval_evidence = "internal-test-only: synthetic fixture, not a real approval"
    row.approved_by = "test-harness"
    row.approved_at = _now()
    row.locked_commit = "synthetic"
    register_provider(source_id, synthetic_provider(entries or [
        {"title": "合成测试条目 A", "rank": 1, "url": "https://example.invalid/a",
         "features": {"practical": 1.0, "visual": 0.8}},
        {"title": "合成测试条目 B", "rank": 2, "url": "https://example.invalid/b",
         "features": {"conversational": 1.0}},
    ]))
    db.flush()