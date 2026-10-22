"""热点中心 HTTP 接口：``/api/hotspots`` 前缀下的 10 个操作。

这一层只做四件事，别的都不做：

1. **身份**：``user_id`` 只来自 ``Authorization: Bearer <token>``，租户只来自
   ``X-Tenant-Id``（缺省 ``default``）。``X-Studio-Request`` 之类的开发头不是身份，本模块
   根本不读它——契约明确禁止用开发头替代平台认证，缺失 Authorization 一律 401。
2. **幂等**：写请求必须带 8-128 字符的 ``Idempotency-Key``：同键同 body 复用首次响应并回
   ``Idempotent-Replay: true``，同键不同 body 返回 409，长度不符返回 400。资源与幂等键写在
   同一个 savepoint 里，并发重复请求不会留下两条资源（见 ``_write_once``）。
3. **诚实**：每个成功响应都带 ``production_ready: false`` 与模块里的 notice 常量；不把 202
   说成"已抓取"，不把缓存命中说成"访问过原平台"，空态解释原因而不是拿假数据填充。错误响应
   只带 ``code`` / ``message`` / ``request_id``（契约 Error 形状），DELETE 的 204 按 HTTP
   语义不带正文——这两处没有成功响应可挂诚实标记，不是漏写。
4. **隔离**：selection / refresh job / favorite 的读取都按 ``(tenant_id, user_id)`` 过滤，
   他人的 id 一律 404——不确认它存在，也不泄露它属于谁。

与 ``contracts/openapi.json`` 的差异（试点已知差距，写在这里而不是藏起来）：

* ``view`` 枚举是 ``hotspot_rules.VIEW_WEIGHTS`` 的键（xiaohongshu / moments_personal /
  moments_store / article / poster）。契约里的 source / general / moments 视图没有对应的
  公共数据源，本实现返回 400，而不是悄悄换成另一个榜冒充它。
* ``cursor`` 是本实现绑定的整数偏移（服务层语义），不是契约里的不透明字符串；release 过期
  的 410 语义未实现——读到的永远是最后一份已发布榜单。
* 刷新在本进程内同步执行（试点没有独立 worker 与 PostgreSQL），但对外保持 202 + job_id 的
  语义：调用方按 job_id 轮询，且响应里的 notice 会明说这一次是同步执行。
* selections 的"过期热点二次确认（409 STALE_CONFIRMATION_REQUIRED）"未实现：本实现的
  release 不可变、没有下架流程，``confirm_stale`` 只参与幂等摘要，不改变写入结果。
* 契约字段名与服务层字段名同值并列（``state``/``status``、``sources``/``results``、
  ``view_key``/``view``、``sources``/``items``），避免调用方两边各猜一半；它们是同一个值，
  不是第二份数据。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import db as db_layer
from . import hotspot_rules as rules
from . import hotspots
from . import models

router = APIRouter(prefix="/api/hotspots", tags=["hotspots"])

DEFAULT_VIEW = "xiaohongshu"
MAX_PAGE_SIZE = 50
POLL_AFTER_SECONDS = 3
INLINE_REFRESH_NOTE = ("本试点没有独立 worker：来源检查在本次请求内同步执行完；"
                       "仍以 job_id 为准，不要把 202 当成已经重新验证过原平台。")
MOMENTS_MODES = ("personal", "store")


# ------------------------------------------------------------------ 请求体

class RefreshRequest(BaseModel):
    """``POST /refresh`` 请求体。省略 source_ids 表示检查全部已启用来源，契约不提供 force。"""
    model_config = ConfigDict(extra="forbid")
    source_ids: list[str] = Field(default_factory=list, max_length=20)


class SelectionRequest(BaseModel):
    """``POST /selections`` 请求体：契约的单事件字段 + 服务层已有的批量字段。"""
    model_config = ConfigDict(extra="forbid")
    event_id: str = ""
    event_ids: list[str] = Field(default_factory=list, max_length=20)
    release_id: str = ""
    view: str = ""
    moments_mode: str | None = None
    confirm_stale: bool = False


class FavoriteRequest(BaseModel):
    """``POST /favorites`` 请求体。"""
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1)


# ------------------------------------------------------------------ 身份 / 数据库

@dataclass(frozen=True)
class Identity:
    """请求身份；``user_id`` 为空字符串表示未认证。"""
    user_id: str
    tenant_id: str


def hs_identity(authorization: str = Header(default=""),
                x_tenant_id: str = Header(default="")) -> Identity:
    """只从平台真实鉴权取身份。

    试点没有独立身份库，平台把 subject 放在 bearer token 里，因此 token 直接充当
    ``user_id``（本模块不解析 token 内容，也就不必相信其中的任何声明；生产接入时应换成
    平台签发的 subject）。开发头（X-Studio-Request 等）不参与身份判定。
    """
    scheme, _, token = (authorization or "").partition(" ")
    tenant = (x_tenant_id or "").strip() or "default"
    token = token.strip()
    if scheme.strip().lower() != "bearer" or not token:
        return Identity(user_id="", tenant_id=tenant)
    return Identity(user_id=token, tenant_id=tenant)


def _deny_unauthenticated(identity: Identity) -> JSONResponse | None:
    if identity.user_id:
        return None
    return _error(401, "UNAUTHENTICATED",
                  "缺少 Authorization: Bearer <token>；身份只能来自平台真实鉴权")


_ready_url = ""


def _ensure_schema() -> None:
    """按库地址只建一次表。测试用 reset_for_tests 换库后地址会变，所以不会漏建。"""
    global _ready_url
    url = db_layer.database_url()
    if url and url != _ready_url:
        db_layer.init_db()
        _ready_url = url


def hs_db():
    """一个请求一个会话：业务写入与幂等登记在同一个事务里提交或回滚。"""
    if not db_layer.configured() and not db_layer.ensure_configured():
        raise HTTPException(status_code=503, detail={
            "code": "CAPABILITY_DISABLED",
            "message": "热点中心需要数据库；请配置 DATABASE_URL 或可写的 AGENT_DB_PATH"})
    _ensure_schema()
    session = db_layer.session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ------------------------------------------------------------------ 响应小工具

def _error(status: int, code: str, message: str) -> JSONResponse:
    """契约 Error 形状：code / message / request_id。错误码是给调用方分支用的。"""
    return JSONResponse(status_code=status, content={
        "code": code, "message": message, "request_id": uuid.uuid4().hex[:16]})


def _jsonable(value: Any) -> Any:
    """把服务层返回值变成 JSON 可写、可原样回放的形式。

    幂等表是 JSON 列，datetime 不能直接序列化；这里统一转 ISO 8601。SQLite 读回来的
    datetime 是 naive 的 UTC 墙钟时间（写入的也都是 ``_now()`` 的 UTC），因此一律按 UTC
    补上偏移量，保证同一个时间在首次响应和复用响应里长得一模一样，且符合契约的
    "带时区 ISO 8601" 要求。
    """
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    return value


def _cursor_offset(raw: str) -> tuple[int, JSONResponse | None]:
    text = (raw or "").strip()
    if not text:
        return 0, None
    try:
        value = int(text)
    except (TypeError, ValueError):
        return 0, _error(400, "CURSOR_INVALID", "cursor 必须是整数偏移")
    if value < 0:
        return 0, _error(400, "CURSOR_INVALID", "cursor 不能为负数")
    return value, None


# ------------------------------------------------------------------ 幂等写入

def _idempotency_gate(db: Session, identity: Identity, key: str, payload: dict,
                      *, replay_status: int) -> tuple[JSONResponse | None, bool]:
    """检查幂等键。返回 (需要立即返回的响应, 是否复用)。读操作不经过这里。"""
    if not key or not 8 <= len(key) <= 128:
        code = "IDEMPOTENCY_KEY_REQUIRED" if not key else "IDEMPOTENCY_KEY_INVALID"
        return _error(400, code, "写接口必须带 Idempotency-Key 请求头，长度 8-128 个字符"), False
    stored, state = hotspots.idempotent_replay(
        db, tenant_id=identity.tenant_id, user_id=identity.user_id, key=key, payload=payload)
    if state == "conflict":
        return _error(409, "IDEMPOTENCY_KEY_CONFLICT",
                      "同一 Idempotency-Key 已用于不同的请求体；请换一个新的键"), False
    if state == "replay":
        return JSONResponse(status_code=replay_status, content=stored,
                            headers={"Idempotent-Replay": "true"}), True
    return None, False


def _write_once(db: Session, identity: Identity, key: str, payload: dict,
                work: Callable[[], dict], *, status: int) -> JSONResponse:
    """写接口通用流程：幂等检查 → savepoint 内创建资源并登记键 → 返回可回放的响应。

    为什么是 savepoint（与 ``runtime/lease.py`` 的 claim 同一写法）：并发下同一把键只有
    一个请求能登记成功，输的一方必须把自己刚创建的资源连同键一起回滚，再读赢家的响应按
    复用返回。直接 ``db.rollback()`` 会把调用方已写入的业务行一起丢掉；吞掉
    IntegrityError 则会留下两条资源，却对外声称这是同一次请求。
    """
    blocked, _ = _idempotency_gate(db, identity, key, payload, replay_status=status)
    if blocked is not None:
        return blocked
    savepoint = db.begin_nested()
    try:
        result = work()
        if not isinstance(result, dict):
            savepoint.rollback()
            raise RuntimeError("热点服务层返回了非字典结果，拒绝登记幂等记录")
        if result.get("error"):
            # 兜底：端点已经做了前置校验，这里只保证失败不会被当成成功记进幂等表。
            savepoint.rollback()
            return _error(409, "WRITE_REJECTED", "服务层拒绝了本次写入：" + str(result["error"]))
        body = _jsonable(result)
        hotspots.remember(db, tenant_id=identity.tenant_id, user_id=identity.user_id,
                          key=key, payload=payload, response=body)
        savepoint.commit()
        return JSONResponse(status_code=status, content=body)
    except IntegrityError:
        savepoint.rollback()
        stored, state = hotspots.idempotent_replay(
            db, tenant_id=identity.tenant_id, user_id=identity.user_id, key=key, payload=payload)
        if stored is not None and state == "replay":
            return JSONResponse(status_code=status, content=stored,
                                headers={"Idempotent-Replay": "true"})
        return _error(409, "IDEMPOTENCY_KEY_CONFLICT",
                      "同一 Idempotency-Key 正被一个不同请求体的并发请求占用")


# ------------------------------------------------------------------ 1. 榜单

@router.get("")
def read_board(view: str = DEFAULT_VIEW, source_id: str = "", limit: int = 20,
               cursor: str = "", identity: Identity = Depends(hs_identity),
               db: Session = Depends(hs_db)):
    """读取固定发布版本的榜单。只读：不创建来源任务、不调用模型（A04）。"""
    denied = _deny_unauthenticated(identity)
    if denied is not None:
        return denied
    if view not in rules.VIEW_WEIGHTS:
        return _error(400, "VIEW_UNSUPPORTED",
                      "view 必须是 " + "、".join(rules.VIEW_WEIGHTS) + " 之一")
    offset, invalid = _cursor_offset(cursor)
    if invalid is not None:
        return invalid
    payload = hotspots.board(db, view_key=view, source_id=source_id,
                             limit=max(1, min(MAX_PAGE_SIZE, int(limit))), cursor=offset)
    release = hotspots.latest_release(db)
    # 契约字段从 release 补齐；没有 release 时给出即将使用的规则版本，而不是留空。
    payload["rule_version"] = release.rule_version if release else rules.RULE_VERSION
    payload["taxonomy_version"] = (release.taxonomy_version if release
                                   else rules.TAXONOMY_VERSION)
    payload["view"] = payload.get("view_key", view)
    payload["production_ready"] = False
    return _jsonable(payload)


# ------------------------------------------------------------------ 2. 来源状态

@router.get("/sources")
def read_sources(identity: Identity = Depends(hs_identity), db: Session = Depends(hs_db)):
    """来源准入与实时健康状态：每个未启用来源都要说明它缺什么证据。"""
    denied = _deny_unauthenticated(identity)
    if denied is not None:
        return denied
    payload = hotspots.source_status(db)
    # 健康状态与准入阻塞项来自服务层的两个视图，这里按 source_id 合并：空态页面要能告诉
    # 用户"这个来源还缺哪份证据"，而不是只给一个 pending。
    blockers = {row["source_id"]: row for row in hotspots.approval_blockers(db)}
    for row in payload["sources"]:
        known = blockers.get(row["source_id"], {})
        row["blockers"] = list(known.get("blockers") or [])
        row["approved"] = bool(known.get("approved"))
    payload["items"] = payload["sources"]  # 契约字段名；与 sources 同值
    payload["production_ready"] = False
    return _jsonable(payload)


# ------------------------------------------------------------------ 3. 事件依据

@router.get("/events/{event_id}")
def read_event(event_id: str, identity: Identity = Depends(hs_identity),
               db: Session = Depends(hs_db)):
    """读取事件依据：每条来源记录带自己的可验证访问时间，缺失就是 null。"""
    denied = _deny_unauthenticated(identity)
    if denied is not None:
        return denied
    detail = hotspots.event_detail(db, event_id)
    if detail is None:
        return _error(404, "EVENT_NOT_FOUND", "事件不存在；依据只来自已发布榜单的聚合结果")
    release = hotspots.latest_release(db)
    detail["release_id"] = release.id if release else ""
    detail["rule_version"] = release.rule_version if release else rules.RULE_VERSION
    detail["data_mode"] = release.data_mode if release else hotspots.DATA_MODE_NO_SOURCES
    detail["production_ready"] = False
    return _jsonable(detail)


# ------------------------------------------------------------------ 4/5. 刷新任务

def _job_payload(db: Session, group: models.HotspotRefreshGroup) -> dict:
    """把持久任务组和它的来源检查行还原成响应，字段与 POST /refresh 对齐。"""
    checks = db.scalars(select(models.HotspotSourceCheck).where(
        models.HotspotSourceCheck.group_id == group.id).order_by(
        models.HotspotSourceCheck.checked_at)).all()
    sources = []
    for check in checks:
        source = db.get(models.HotspotSource, check.source_id)
        sources.append({
            "source_id": check.source_id,
            "display_name": source.display_name if source else check.source_id,
            "state": check.state, "outcome": check.outcome, "checked_at": check.checked_at,
            "verified_upstream_at": check.verified_upstream_at,
            "origin_updated_at": check.origin_updated_at,
            "snapshot_changed_at": check.snapshot_changed_at,
            "data_mode": check.data_mode, "http_status": check.http_status,
            "entries_count": check.entries_count, "latency_ms": check.latency_ms,
            "error": check.error})
    return {"job_id": group.id, "state": group.state, "status": group.state,
            "requested_at": group.requested_at, "submitted_at": group.requested_at,
            "completed_at": group.completed_at, "sources": sources, "results": sources,
            "poll_after_seconds": 0 if group.completed_at else POLL_AFTER_SECONDS,
            "production_ready": False,
            "notice": hotspots.NOTICE_NOT_PRODUCTION}


@router.post("/refresh", status_code=202)
def request_refresh(payload: RefreshRequest | None = None,
                    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                    identity: Identity = Depends(hs_identity),
                    db: Session = Depends(hs_db)):
    """提交或复用来源检查任务。202 + job_id；调用方轮询，不阻塞等待上游。"""
    denied = _deny_unauthenticated(identity)
    if denied is not None:
        return denied
    wanted = [str(item).strip() for item in (payload.source_ids if payload else [])
              if str(item).strip()]
    idem_payload = {"source_ids": wanted}

    def work() -> dict:
        result = hotspots.refresh(db, tenant_id=identity.tenant_id,
                                  user_id=identity.user_id, source_ids=wanted)
        result["status"] = result["state"]            # 契约字段名
        result["submitted_at"] = result["requested_at"]
        result["results"] = result["sources"]
        result["poll_after_seconds"] = 0 if result["completed_at"] else POLL_AFTER_SECONDS
        result["production_ready"] = False
        result["notice"] = str(result.get("notice") or "") + " " + INLINE_REFRESH_NOTE
        return result

    return _write_once(db, identity, idempotency_key, idem_payload, work, status=202)


@router.get("/refresh-jobs/{job_id}")
def read_refresh_job(job_id: str, identity: Identity = Depends(hs_identity),
                     db: Session = Depends(hs_db)):
    """读取本人发起的刷新任务组；他人或不存在一律 404（不确认 id 是否存在）。"""
    denied = _deny_unauthenticated(identity)
    if denied is not None:
        return denied
    group = db.get(models.HotspotRefreshGroup, job_id)
    if (group is None or group.tenant_id != identity.tenant_id
            or group.user_id != identity.user_id):
        return _error(404, "REFRESH_JOB_NOT_FOUND", "刷新任务不存在或不属于当前用户")
    return _jsonable(_job_payload(db, group))


# ------------------------------------------------------------------ 6/7. 选择材料

def _released_event_ids(db: Session, release_id: str) -> set[str]:
    return set(db.scalars(select(models.HotspotBoardEntry.event_id).where(
        models.HotspotBoardEntry.release_id == release_id)).all())


@router.post("/selections", status_code=201)
def save_selection(payload: SelectionRequest,
                   idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                   identity: Identity = Depends(hs_identity),
                   db: Session = Depends(hs_db)):
    """保存不可变热点材料交给创作 Agent：只落库，不触发生成。"""
    denied = _deny_unauthenticated(identity)
    if denied is not None:
        return denied
    event_ids: list[str] = []
    for value in [payload.event_id, *payload.event_ids]:
        text = str(value or "").strip()
        if text and text not in event_ids:
            event_ids.append(text)
    if not event_ids:
        return _error(400, "SELECTION_EMPTY", "至少要选择一个事件（event_id 或 event_ids）")
    if len(event_ids) > 20:
        return _error(400, "SELECTION_TOO_LARGE", "一次最多保存 20 条热点材料")
    if payload.view and payload.view not in rules.VIEW_WEIGHTS:
        return _error(400, "VIEW_UNSUPPORTED",
                      "view 必须是 " + "、".join(rules.VIEW_WEIGHTS) + " 之一")
    if payload.moments_mode:
        if (payload.moments_mode not in MOMENTS_MODES
                or not str(payload.view).startswith("moments")):
            return _error(400, "MOMENTS_MODE_INVALID",
                          "moments_mode 只允许 personal/store，且只能与 moments_* 视图同用")
    release = (db.get(models.HotspotBoardRelease, payload.release_id) if payload.release_id
               else hotspots.latest_release(db))
    if release is None or not release.published:
        return _error(404, "RELEASE_NOT_FOUND",
                      "没有可用的已发布榜单（release_id 不存在或未发布），拒绝保存选择材料")
    published = _released_event_ids(db, release.id)
    for event_id in event_ids:
        if db.get(models.HotspotEvent, event_id) is None:
            return _error(404, "EVENT_NOT_FOUND", "事件不存在：" + event_id)
        if event_id not in published:
            return _error(404, "EVENT_NOT_IN_RELEASE",
                          "事件不属于该发布版本：" + event_id)
    idem_payload = _jsonable(payload.model_dump())

    def work() -> dict:
        result = hotspots.save_selection(db, tenant_id=identity.tenant_id,
                                         user_id=identity.user_id, release_id=release.id,
                                         event_ids=event_ids)
        result["production_ready"] = False
        result["notice"] = (str(result.get("notice") or "") + " "
                            + hotspots.NOTICE_NOT_PRODUCTION)
        return result

    return _write_once(db, identity, idempotency_key, idem_payload, work, status=201)


@router.get("/selections/{selection_id}")
def read_selection(selection_id: str, identity: Identity = Depends(hs_identity),
                   db: Session = Depends(hs_db)):
    """当前用户读取自己的选择材料；跨租户/跨用户按不存在处理。"""
    denied = _deny_unauthenticated(identity)
    if denied is not None:
        return denied
    payload = hotspots.get_selection(db, tenant_id=identity.tenant_id,
                                    user_id=identity.user_id, selection_id=selection_id)
    if payload is None:
        return _error(404, "SELECTION_NOT_FOUND", "选择材料不存在或不属于当前用户")
    payload["production_ready"] = False
    payload["notice"] = hotspots.NOTICE_NOT_PRODUCTION + " " + hotspots.NOTICE_DISPLAY
    return _jsonable(payload)


# ------------------------------------------------------------------ 8/9/10. 收藏

@router.get("/favorites")
def read_favorites(identity: Identity = Depends(hs_identity), db: Session = Depends(hs_db)):
    """读取本人收藏。收藏按 (tenant, user) 隔离，不复用榜单游标。"""
    denied = _deny_unauthenticated(identity)
    if denied is not None:
        return denied
    return _jsonable({"items": hotspots.list_favorites(db, tenant_id=identity.tenant_id,
                                                       user_id=identity.user_id),
                      "next_cursor": None, "production_ready": False,
                      "notice": hotspots.NOTICE_NOT_PRODUCTION
                      + " 收藏只在当前 tenant/user 范围内可见。"})


@router.post("/favorites")
def add_favorite(payload: FavoriteRequest,
                 idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                 identity: Identity = Depends(hs_identity), db: Session = Depends(hs_db)):
    """收藏事件；重复收藏（同键或不同键）都返回原记录，不新增行。事件不存在返回 404。"""
    denied = _deny_unauthenticated(identity)
    if denied is not None:
        return denied
    if db.get(models.HotspotEvent, payload.event_id) is None:
        return _error(404, "EVENT_NOT_FOUND", "事件不存在，不能收藏")
    idem_payload = {"event_id": payload.event_id}

    def work() -> dict:
        result = hotspots.add_favorite(db, tenant_id=identity.tenant_id,
                                       user_id=identity.user_id, event_id=payload.event_id)
        result["production_ready"] = False
        result["notice"] = hotspots.NOTICE_NOT_PRODUCTION
        return result

    return _write_once(db, identity, idempotency_key, idem_payload, work, status=200)


@router.delete("/favorites/{event_id}")
def delete_favorite(event_id: str,
                    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                    identity: Identity = Depends(hs_identity),
                    db: Session = Depends(hs_db)):
    """删除本人收藏：不存在也返回 204（删除本身就是幂等的，不因缺键失败）。"""
    denied = _deny_unauthenticated(identity)
    if denied is not None:
        return denied
    # DELETE 天然幂等，契约也只要求 204；带了键就按同一套长度规则校验，不带不报错。
    if idempotency_key and not 8 <= len(idempotency_key) <= 128:
        return _error(400, "IDEMPOTENCY_KEY_INVALID",
                      "Idempotency-Key 若提供，长度必须是 8-128 个字符")
    hotspots.remove_favorite(db, tenant_id=identity.tenant_id, user_id=identity.user_id,
                             event_id=event_id)
    return Response(status_code=204)