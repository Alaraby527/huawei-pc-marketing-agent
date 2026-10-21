import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from . import __version__
from .contracts import MaterialBundle, Task
from .verification import check_materials
from .platforms import PLATFORM_RULES
from .retrieval import retrieve_materials
from .retrieval import guide, get_asset
from .intake import Intake
from .ima import ImaClient, ImaError, configured
from .local_kb import default_client, kb_source, local_kb_ready
from .conversation import Turn, service, DialogueError, ask
from .hotspot import related_hot_terms, related_hot_topics
from .xhs import XhsError, configured as xhs_configured, hot_topics as xhs_hot_topics, login_qrcode, login_status
from .model import image_configured, generate_cover_image, ModelError, allowed_models, default_model, model_catalog
from .posters import pages_for, render_page, zip_bytes
from . import db as db_layer
from . import models
from . import rednote
from .agent import AgentTurn, run_agent
from . import agent_runtime
from .runtime import RUNNER_VERSION as LG_RUNNER_VERSION
from .runtime import runner as lg_runner
from .runtime.state import FINAL_STATUSES as LG_FINAL_STATUSES
from . import design_library
from . import image_edit
from . import image_channel
from .rednote import WorkspaceContext, WorkspaceError

service.client_factory=default_client
_workspace_db_ready=False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # The LangGraph checkpoint connection is process-wide: opened once here and closed
    # once on shutdown, never re-created per request.
    await lg_runner.init_saver()
    _recover_orphaned_runs()
    yield
    await lg_runner.close_saver()


def _recover_orphaned_runs() -> int:
    """A run left 'running' by a previous process cannot be assumed to have finished."""
    try:
        if not db_layer.configured() and not db_layer.ensure_configured():
            return 0
        db_layer.init_db()
        session = db_layer.session_factory()()
    except Exception:  # noqa: BLE001 - startup must not fail on a database problem
        return 0
    try:
        count = lg_runner.recover_orphaned_runs(session)
        session.commit()
        return count
    except Exception:  # noqa: BLE001
        session.rollback()
        return 0
    finally:
        session.close()


app = FastAPI(title="华为PC营销Agent · 门店导购与图文生成", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.middleware("http")
async def guard_request(request: Request, call_next):
    # Bound streamed input as well as declared Content-Length.
    if request.method in {"POST", "PUT", "PATCH"}:
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 256_000:
                return JSONResponse({"message": "请求过大，材料包上限为256KB"}, status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'self' https://modelscope.cn https://www.modelscope.cn; base-uri 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Do not echo raw user input or credentials into validation logs/responses.
    return JSONResponse(status_code=422, content={"message": "数据格式不符合接口约定", "errors": [
        {"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in error.errors()
    ]})

@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")

@app.get("/docs", include_in_schema=False)
async def api_documentation():
    return FileResponse(static_dir / "api.html")

@app.get("/healthz")
async def health():
    return {"status": "ok", "version": __version__, "service": "huawei-pc-marketing-agent"}

@app.post('/api/v1/conversations/turn')
async def conversation_turn(turn: Turn):
    try:
        return await service.turn(turn)
    except DialogueError as error:
        return JSONResponse(status_code=error.status,content={'message':error.message})
    except ValueError:
        return JSONResponse(status_code=422,content={'message':'本轮信息存在无效值，请检查预算范围及明确的字段值；不自动填默认值'})

@app.get("/api/v1/readiness")
async def readiness():
    return {
        "version": __version__,
        "deployment_ready": True,
        "business_ready": False,
        "stage": "需求澄清、知识库检索、小红书图文生成、两道人工确认与合规规则扫描已接通；逐条语义核验与指定赛事共享库仍待完成",
        "configured": {
            "ima_credentials": configured(),
            "local_kb_ready": local_kb_ready(),
            "xhs_mcp": xhs_configured(),
            "llm_credentials": bool(os.getenv("LLM_API_KEY")),
            "llm_endpoint_and_model": bool(os.getenv("LLM_BASE_URL") and os.getenv("LLM_MODEL")),
        },
        "knowledge_source": kb_source(),
        "integrations": {
            "retrieval": "implemented_requires_live_connection_check",
            "generation": "implemented_rule_and_llm_engine",
            "semantic_verification": "not_implemented_deterministic_rule_scan_only",
        },
        "note": "已填写凭证不等于验证成功；本接口不会输出凭证内容。",
    }

@app.get("/api/v1/platforms")
async def platforms():
    return PLATFORM_RULES

@app.get("/api/v1/questions")
async def questions():
    return {"core": {key: ask(key) for key in ("audience", "budget", "scenario")}}

@app.get("/api/v1/models")
async def model_options():
    text_models = allowed_models()
    return {"rule": True, "text": text_models, "models": model_catalog(),
            "default": default_model() if text_models else None,
            "image": os.getenv("LLM_IMAGE_MODEL", "") or None,
            "note": "只返回服务端允许的模型与上下文信息；密钥不会下发。新增模型请配置 LLM_MODELS_JSON 或 LLM_ALLOWED_MODELS。"}

@app.get("/api/v1/contracts/materials")
async def materials_schema():
    return MaterialBundle.model_json_schema()

@app.post("/api/v1/materials/validate")
async def validate_materials(bundle: MaterialBundle):
    return check_materials(bundle)

@app.get("/api/v1/knowledge/libraries")
async def libraries():
    try:
        return {"status":"ok","libraries":await default_client().libraries()}
    except ImaError as error:
        return JSONResponse(status_code=503,content={"status":"unavailable","reason":str(error)})

@app.get("/api/v1/knowledge/connection")
async def connection():
    client=default_client()
    try:
        return {"status":"ok",**await client.connect(),"trace":client.trace}
    except ImaError as error:
        return JSONResponse(status_code=503,content={"status":"unavailable","reason":str(error)})

@app.post("/api/v1/guide")
async def guide_task(intake: Intake):
    return await guide(intake)

@app.get("/api/v1/assets/{token}")
async def asset(token: str):
    content=get_asset(token)
    if not content:
        return JSONResponse(status_code=410,content={"message":"图片不存在或已过期，请重新检索"})
    return Response(content[0],media_type=content[1],headers={"Cache-Control":"no-store"})

@app.post("/api/v1/tasks")
async def create_task(task: Task):
    bundle = await retrieve_materials(task)
    return JSONResponse(status_code=503, content={
        "status": "integration_pending",
        "message": "此接口已接真实检索；后段生成与成稿核验待队友接入，请使用/api/v1/guide取得前段材料。",
        "materials": bundle.model_dump(mode="json"),
        "can_publish": False,
    })

@app.get("/api/v1/hotspots")
def hotspots(scenario: str = "", audience: str = ""):
    topics = related_hot_topics(scenario, audience)
    mode = topics[0].get("data_mode", "editorial_fallback") if topics else "editorial_fallback"
    if mode == "live":
        note = "来自60s小红书热榜的轻关联参考；不是实时验证结果，不替代产品事实或平台实时数据"
    elif mode == "live_fallback":
        note = "小红书热榜暂不可用；以下为微博热榜，仅作跨平台话题参考，不是小红书实时热点"
    else:
        note = "公开热榜暂不可用；以下为编辑内容方向，不是实时热点，也不代表传播效果"
    return {"topics": topics, "terms": [topic["title"] for topic in topics], "data_mode": mode, "note": note}

@app.get("/api/v1/xhs/status")
async def xhs_status():
    try:
        return {"status": "ok", **await login_status()}
    except XhsError as error:
        return JSONResponse(status_code=error.status, content={"status": "unavailable", "message": error.message})

@app.get("/api/v1/xhs/login/qrcode")
async def xhs_qrcode():
    try:
        return {"status": "ok", **await login_qrcode()}
    except XhsError as error:
        return JSONResponse(status_code=error.status, content={"status": "unavailable", "message": error.message})

@app.get("/api/v1/xhs/login/status")
async def xhs_login_status():
    try:
        return {"status": "ok", **await login_status()}
    except XhsError as error:
        return JSONResponse(status_code=error.status, content={"status": "unavailable", "message": error.message})

@app.get("/api/v1/xhs/hot")
async def xhs_hot(scenario: str = "", audience: str = ""):
    try:
        topics = await xhs_hot_topics(scenario, audience)
        return {"status": "ok", "topics": topics, "data_mode": "live_xhs",
                "note": "来自当前实例已授权小红书账号的实时搜索；笔记标题和互动数仅作话题线索，不替代产品事实"}
    except XhsError as error:
        return JSONResponse(status_code=error.status, content={"status": "unavailable", "message": error.message})

@app.get("/api/v1/conversations/{session_id}/export")
async def export_result(session_id: str, format: str = "text"):
    s = service.sessions.get(session_id)
    if not s or not service.publishable(s):
        return JSONResponse(status_code=409, content={"message": "当前会话没有已通过核验、可导出的图文方案"})
    draft, handoff = s.draft, s.handoff_cache
    body = export_markdown(draft, handoff, s.compliance_report)
    if format == "markdown":
        return Response(body, media_type="text/markdown",
                         headers={"Content-Disposition": 'attachment; filename="xiaohongshu-draft.md"'})
    return Response(body, media_type="text/plain; charset=utf-8",
                     headers={"Content-Disposition": 'attachment; filename="xiaohongshu-draft.txt"'})


@app.get("/api/v1/conversations/{session_id}/state")
async def conversation_state(session_id: str):
    s = service.sessions.get(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"message": "会话不存在或已过期，请新建任务"})
    payload = service.response(s, 'state', s.last_questions, handoff=s.handoff_cache)
    payload['history'] = s.history[-40:]
    return payload


@app.get("/api/v1/conversations/{session_id}/posters/{page}.png")
def poster_page(session_id: str, page: int):
    s = service.sessions.get(session_id)
    if not s or not s.draft or not s.handoff_cache:
        return JSONResponse(status_code=409, content={"message": "当前会话还没有可渲染的六页方案"})
    if page < 1 or page > 6:
        return JSONResponse(status_code=404, content={"message": "页码必须是 1-6"})
    pages = pages_for(session_id, s.revision, s.draft, s.handoff_cache)
    return Response(pages[page - 1], media_type="image/png",
                    headers={"Cache-Control": "private, max-age=60"})


@app.get("/api/v1/conversations/{session_id}/posters.zip")
def poster_zip(session_id: str):
    s = service.sessions.get(session_id)
    if not s or not s.draft or not s.handoff_cache:
        return JSONResponse(status_code=409, content={"message": "当前会话还没有可渲染的六页方案"})
    pages = pages_for(session_id, s.revision, s.draft, s.handoff_cache)
    return Response(zip_bytes(pages), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="huawei-pc-posters.zip"'})


def export_markdown(draft, handoff, compliance):
    """Four-part output required by the brief: scenario / evidence / copy / verification."""
    task = handoff["materials"]["task"]
    product = handoff["materials"]["product"]
    lines = ["# 一、场景判断", f"- 目标人群：{task['audience']}", f"- 使用场景：{task['scenario']}",
              f"- 用户问题/内容目标：{task['objective']}", f"- 发布平台：{draft['platform_name']}",
              "", "# 二、知识库依据", f"- 产品型号：{product['model_name']}（{product['configuration']}）"]
    for fid in draft.get("fact_ids", []):
        fact = next((f for f in handoff["facts"] if f["fact_id"] == fid), None)
        if fact:
            lines.append(f"- [{fid}] {fact['text'][:120]} —— 来源：{fact['source']['title']}")
    lines += ["", "# 三、图文软文", f"**{draft['title']}**", "", draft["body"], "",
              "行动引导：" + draft.get("cta", ""), "话题：" + " ".join("#" + t for t in draft.get("tags", []))]
    pages = draft.get("pages") or []
    if pages:
        lines += ["", "## 六页图文方案"]
        role_names = {"hook": "封面", "scene": "场景", "proof": "事实", "detail": "细节",
                      "decision": "决策", "close": "收尾"}
        for index, page in enumerate(pages, 1):
            refs = "、".join(page.get("fact_ids") or []) or "无"
            lines += ["", f"### 第{index}页 · {role_names.get(page.get('role'), page.get('role'))}",
                      f"**{page.get('title', '')}**", page.get("text", ""), f"事实依据：{refs}"]
    for item in draft.get("image_plan", []):
        image = next((i for i in handoff["images"] if i["image_id"] == item["image_id"]), None)
        caption = image["caption"] if image else item["image_id"]
        lines.append(f"- 图片［{item['image_id']}］{caption} —— 插入位置：{item['position']}；来源：ima")
    lines += ["", "# 四、核验结果",
              "- 事实与合规核验：规则扫描通过（数字可追溯、无高风险用语）；未做逐条语义核验" if compliance["passed"] else "- 事实与合规核验：未通过",
              "- 图片核验：均为ima来源、型号级核验通过的素材",
              "- 合规核验：" + ("通过（规则扫描）" if compliance["passed"] else "未通过"),
              "- 待确认信息：" + ("；".join(handoff["risks_and_gaps"]["blocking"]) or "无"),
              "", "> 本结果为规则/模型辅助生成，不代表法律合规保证，发布前请人工复核。"]
    return "\n".join(lines)


@app.get("/api/v1/images/styles")
def image_styles():
    return {"styles": image_channel.style_list(),
            "channel": {"configured": image_channel.configured(),
                        "model": os.getenv("IMAGE_MODEL", image_channel.DEFAULT_MODEL),
                        "base_url": image_channel.DEFAULT_BASE},
            "sources": [{"id": "ai", "label": "AI 生图"}, {"id": "kb", "label": "知识库原图"}],
            "note": "AI 生图用提示词 + 产品原图；知识库原图不调用生图模型，直接用核验过的官方素材。"}


@app.post("/api/v1/images/generate")
async def generate_images(payload: image_edit.GenerateRequest):
    """一键配图：AI 生图（产品原图 + 提示词）或直接使用知识库原图。"""
    session = service.sessions.get(payload.session_id) if payload.session_id else None
    handoff = session.handoff_cache if session else None
    if not handoff or not handoff.get("images"):
        return JSONResponse(status_code=422, content={"code": "EVIDENCE_REQUIRED",
                                                      "message": "需要先有产品原图：请先完成选品与核验，或指定 image_id"})
    images = handoff["images"]
    if payload.source == "kb":
        return {"status": "ok", "source": "kb", "prompt": None, "model": None,
                "images": [row.get("access_url") for row in images],
                "note": "已选择知识库原图，未调用生图模型；素材来自官方知识库核验结果。"}
    if not image_channel.configured() and not image_edit.configured():
        return JSONResponse(status_code=503, content={"code": "CAPABILITY_DISABLED",
                                                      "message": "生图通道未配置 IMAGE_API_KEY（或 LLM_API_KEY）"})
    image = next((row for row in images if row.get("image_id") == payload.image_id), images[0])
    token = (image.get("access_url") or "").rstrip("/").split("/")[-1]
    stored = get_asset(token)
    if not stored:
        return JSONResponse(status_code=409, content={"code": "SOURCE_UNAVAILABLE",
                                                      "message": "产品图片临时地址已过期，请刷新材料后重试"})
    body, mime = stored
    slots = session.slots.model_dump(mode="json") if session else {}
    facts = [str(fact.get("text", "")).partition("：")[0] for fact in handoff.get("facts", [])[:3]]
    draft_title = (session.draft or {}).get("title", "") if session else ""
    subject = draft_title or slots.get("instructions", "").split("\n")[0]
    target_ratio = image_channel.RATIO_PRESETS.get(payload.ratio)
    if target_ratio is None:
        target_ratio = image_channel.reference_ratio(body)
    channel_prompt = payload.prompt or image_channel.build_prompt(
        style=payload.style, subject=subject, scene=slots.get("scenario", ""),
        audience=slots.get("audience", ""), facts=facts, extra=payload.extra)
    channel = "image_api"
    mode = "img2img"
    try:
        if image_channel.configured():
            try:
                # Default is image-to-image so the product blends into the
                # scene/style; composite (pixel-identical product) is opt-in.
                preserve = (os.getenv("IMAGE_PRESERVE_PRODUCT") or "").strip().lower() == "composite"
                if preserve or payload.mode == "composite":
                    # Transparent cutouts: generate the background only, then
                    # composite the original product pixels back, so size,
                    # ratio, logo and ports are guaranteed unchanged.
                    background_prompt = (channel_prompt +
                                         "。背景中不要出现任何笔记本电脑、平板、手机或其他数码产品实体，"
                                         "只保留环境、桌面与生活道具；不要出现软件界面或品牌图标")
                    backgrounds, used = await asyncio.to_thread(
                        image_channel.generate_background, background_prompt, payload.model,
                        payload.size, payload.n, target_ratio)
                    results = [image_edit._composite(body, background) for background in backgrounds]
                    channel = "image_api_composite" if used == "primary" else "image_api_composite_fallback"
                    mode = "composite_guaranteed"
                else:
                    results, used = await asyncio.to_thread(image_channel.generate_from_product, body,
                                                            channel_prompt, mime, payload.model,
                                                            payload.size, payload.n, target_ratio)
                    channel = "image_api" if used == "primary" else "image_api_fallback"
                prompt = channel_prompt
            except image_channel.ImageChannelError:
                if not image_edit.configured():
                    raise
                channel = "dashscope_fallback"
                prompt = payload.prompt or image_edit.build_prompt(
                    scene=slots.get("scenario", ""), audience=slots.get("audience", ""),
                    objective=slots.get("objective", ""), title=subject, facts=facts,
                    style=payload.style, extra=payload.extra)
                if image_edit.has_alpha(body):
                    mode = "scene"
                    results = await asyncio.to_thread(image_edit.generate_scene_image, body,
                                                      payload.style, payload.prompt or "")
                else:
                    mode = "edit"
                    results = await asyncio.to_thread(image_edit.edit_image, body, prompt, mime,
                                                      payload.size, payload.n, payload.model)
        else:  # DashScope fallback keeps the previous behaviour
            channel = "dashscope"
            prompt = payload.prompt or image_edit.build_prompt(
                scene=slots.get("scenario", ""), audience=slots.get("audience", ""),
                objective=slots.get("objective", ""), title=subject, facts=facts,
                style=payload.style, extra=payload.extra)
            if payload.mode == "scene" or (payload.mode == "auto" and image_edit.has_alpha(body)):
                mode = "scene"
                results = await asyncio.to_thread(image_edit.generate_scene_image, body,
                                                  payload.style, payload.prompt or "")
            else:
                mode = "edit"
                results = await asyncio.to_thread(image_edit.edit_image, body, prompt, mime,
                                                  payload.size, payload.n, payload.model)
    except (image_channel.ImageChannelError, image_edit.ImageEditError) as error:
        return JSONResponse(status_code=error.status, content={"code": "IMAGE_GENERATE_FAILED",
                                                               "message": error.message})
    if target_ratio:
        results = [image_channel.crop_to_ratio(content, target_ratio) for content in results]
    from .retrieval import store_asset
    urls = [store_asset(content, "image/png") for content in results]
    ratio_note = ""
    if target_ratio:
        ratio_note = f"输出比例 {target_ratio:.3f}（" + ("跟随知识库原图" if payload.ratio == "kb" else payload.ratio) + "）"
    return {"status": "ok", "source": "ai", "channel": channel, "mode": mode, "ratio": ratio_note,
            "prompt": prompt, "model": payload.model or os.getenv("IMAGE_MODEL", image_channel.DEFAULT_MODEL),
            "images": urls,
            "note": "生成图只作氛围背景，不是产品实拍；产品外观以原图为准，发布前需人工核对。"}


@app.post("/api/v1/covers/generate")
async def generate_cover(payload: dict):
    if not image_configured():
        return JSONResponse(status_code=503, content={"message": "AI封面氛围图功能未开启或未配置"})
    prompt = str(payload.get("prompt", ""))[:400] or "简约数码桌搭氛围"
    try:
        body, mime = await asyncio.to_thread(generate_cover_image, prompt)
    except ModelError as error:
        return JSONResponse(status_code=502, content={"message": str(error)})
    from .retrieval import store_asset
    url = store_asset(body, mime)
    return {"status": "ok", "access_url": url, "note": "仅为背景氛围图，不代表产品实拍，不得用于证明产品外观"}


# ---------------------------------------------------------------- Rednote workspace (M1)

def rednote_scope(workspace_id: str, x_workspace_id: str = Header(default=""),
                  x_actor: str = Header(default="local-operator")):
    """Server-side workspace scope. Single-workspace mode today; the header is
    validated so a client cannot silently act as another workspace."""
    if x_workspace_id and x_workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail={"code": "WORKSPACE_ACCESS_DENIED",
                                                     "message": "工作区不匹配"})
    if not db_layer.configured():
        raise HTTPException(status_code=503, detail={"code": "CAPABILITY_DISABLED",
                                                     "message": "工作区数据库未配置；请设置 DATABASE_URL 并执行 python -m app.db init"})
    global _workspace_db_ready
    if not _workspace_db_ready:
        db_layer.init_db()
        _workspace_db_ready = True
    ctx = WorkspaceContext(workspace_id=workspace_id, actor=x_actor)
    session = db_layer.session_factory()()
    try:
        rednote.ensure_workspace(session, ctx.workspace_id)
        session.commit()
    except Exception:
        session.rollback()
        session.close()
        raise
    try:
        yield ctx, session
        session.commit()
    except WorkspaceError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@app.exception_handler(WorkspaceError)
async def workspace_error_handler(request: Request, error: WorkspaceError):
    return JSONResponse(status_code=error.status, content={"code": error.code, "message": error.message})


@app.get("/api/v1/workspaces/{workspace_id}/apps/rednote/capabilities")
def rednote_capabilities(scope=Depends(rednote_scope)):
    ctx, session = scope
    return {
        "workspace_id": ctx.workspace_id,
        "database": "ready",
        "editor": {"engine": "vue-fabric-editor", "status": os.getenv("REDNOTE_EDITOR_STATUS", "adapter_ready"),
                   "note": "编辑器构建产物就绪后由 /studio 提供；未就绪时保留 JSON/PNG 导出"},
        "connector": {"read": "public_sources", "write": "pending_manual_verification"},
        "image_channel": {"dashscope": bool(image_configured()), "qwen_image_edit": "pending_configuration"},
        "auth": {"multi_tenant": False, "rbac": "pending", "note": "M1 单工作区；登录/成员/角色待接入"},
        "publishing": {"manual": "ready", "connector": "pending"},
    }


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/brief-handoffs/consume")
def rednote_consume_brief(payload: rednote.BriefConsume, scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.consume_brief(session, ctx, payload)


@app.get("/api/v1/workspaces/{workspace_id}/apps/rednote/brief-snapshots/{snapshot_id}")
def rednote_brief_snapshot(snapshot_id: str, scope=Depends(rednote_scope)):
    ctx, session = scope
    from .models import BriefSnapshot
    snapshot = rednote._require(session, ctx, BriefSnapshot, snapshot_id)
    return {"id": snapshot.id, "content_hash": snapshot.content_hash, "brief": snapshot.payload}


@app.get("/api/v1/workspaces/{workspace_id}/apps/rednote/content-items/{item_id}")
def rednote_content_item(item_id: str, scope=Depends(rednote_scope)):
    ctx, session = scope
    from .models import ContentItem
    item = rednote._require(session, ctx, ContentItem, item_id)
    return {"id": item.id, "status": item.status, "objective": item.objective,
            "account_ref": item.account_ref, "brief_snapshot_id": item.brief_snapshot_id,
            "product_id": item.product_id}


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/trend-selections")
def rednote_trend_selection(payload: rednote.TrendSelectionIn, scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.create_trend_selection(session, ctx, payload)


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/creative-directions")
def rednote_directions(payload: rednote.DirectionsIn, scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.create_creative_directions(session, ctx, payload)


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/creative-boards")
def rednote_board(payload: rednote.BoardIn, scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.create_creative_board(session, ctx, payload)


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/creative-boards/{board_id}/preview")
def rednote_board_preview(board_id: str, scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.style_preview(session, ctx, board_id)


@app.get("/api/v1/workspaces/{workspace_id}/apps/rednote/design-library/styles")
def rednote_design_styles(scope=Depends(rednote_scope)):
    return {"families": design_library.styles(), "fonts": design_library.FONTS,
            "note": "16 个自研视觉家族，每家族 2 封面 + 2 内页；不包含第三方模板素材"}


@app.get("/api/v1/workspaces/{workspace_id}/apps/rednote/design-library/layouts")
def rednote_design_layouts(family_id: str = "", scope=Depends(rednote_scope)):
    return {"layouts": design_library.layouts(family_id or None),
            "count": len(design_library.layouts(family_id or None))}


@app.get("/api/v1/workspaces/{workspace_id}/apps/rednote/design-library/layouts/{layout_id}/preview.png")
def rednote_layout_preview(layout_id: str, scope=Depends(rednote_scope)):
    recipe = design_library.get_recipe(layout_id)
    if not recipe:
        raise WorkspaceError("EVIDENCE_REQUIRED", "布局不存在：" + layout_id, 422)
    role = "hook" if recipe.role == "cover" else "proof"
    draft = {"pages": [{"role": role, "title": recipe.name,
                        "text": f"{recipe.family_name}：布局 {layout_id}（{recipe.archetype}）",
                        "fact_ids": [], "image_id": ""}]}
    handoff = {"images": [], "facts": [], "product_identity": {"model_name": "HUAWEI MateBook"}}
    content = render_page(draft, handoff, 0)
    return Response(content, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/design-documents/from-layout")
def rednote_design_from_layout(payload: dict, scope=Depends(rednote_scope)):
    ctx, session = scope
    layout_id = str(payload.get("layout_id", ""))
    content_item_id = str(payload.get("content_item_id", ""))
    if not design_library.get_recipe(layout_id):
        raise WorkspaceError("EVIDENCE_REQUIRED", "布局不存在：" + layout_id, 422)
    built = design_library.build_document(layout_id, payload.get("bindings") or {},
                                          str(payload.get("product_name", "")))
    doc = rednote.create_design_document(session, ctx, rednote.DesignDocumentIn(
        content_item_id=content_item_id, engine=built["engine"], engine_version=built["engine_version"],
        native_payload=built["native_payload"], sidecar=built["sidecar"], manifest=built["manifest"]))
    return {"document": doc, "layout_id": layout_id, "checks": design_library.check_document(doc)}


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/design-documents/{document_id}/checks")
def rednote_design_checks(document_id: str, scope=Depends(rednote_scope)):
    ctx, session = scope
    from .models import DesignDocument
    row = rednote._require(session, ctx, DesignDocument, document_id)
    document = {"native_payload": row.native_payload, "sidecar": row.sidecar, "manifest": row.manifest}
    return design_library.check_document(document)


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/design-documents")
def rednote_design_document(payload: rednote.DesignDocumentIn, scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.create_design_document(session, ctx, payload)


@app.patch("/api/v1/workspaces/{workspace_id}/apps/rednote/design-documents/{document_id}")
def rednote_patch_design_document(document_id: str, payload: rednote.DesignDocumentPatch,
                                  scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.patch_design_document(session, ctx, document_id, payload)


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/review-submissions")
def rednote_review(payload: rednote.ReviewSubmission, scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.submit_review(session, ctx, payload)


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/approvals")
def rednote_approve(payload: rednote.ApprovalIn, scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.approve(session, ctx, payload)


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/publish-plans")
def rednote_publish_plan(payload: rednote.PublishPlanIn, scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.create_publish_plan(session, ctx, payload)


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/publish-plans/{plan_id}/execute")
def rednote_execute_publish(plan_id: str, scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.execute_publish(session, ctx, plan_id)


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/publish-plans/{plan_id}/manual-results")
def rednote_manual_result(plan_id: str, payload: rednote.ManualResultIn, scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.manual_result(session, ctx, plan_id, payload)


@app.post("/api/v1/workspaces/{workspace_id}/apps/rednote/agent/turn")
def rednote_agent_turn(payload: AgentTurn, scope=Depends(rednote_scope)):
    ctx, session = scope
    return run_agent(session, ctx, payload.message, payload.conversation_id, payload.model)


@app.get("/api/v1/workspaces/{workspace_id}/apps/rednote/publish-plans")
def rednote_publish_plans(scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.list_publish_plans(session, ctx)


@app.get("/api/v1/workspaces/{workspace_id}/apps/rednote/retrospectives")
def rednote_retrospectives(scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.list_retrospectives(session, ctx)


@app.get("/api/v1/workspaces/{workspace_id}/apps/rednote/analytics/overview")
def rednote_overview(scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.overview(session, ctx)


@app.get("/api/v1/workspaces/{workspace_id}/apps/rednote/comments")
def rednote_comments(scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.list_comments(session, ctx)


@app.get("/api/v1/workspaces/{workspace_id}/apps/rednote/alerts")
def rednote_alerts(scope=Depends(rednote_scope)):
    ctx, session = scope
    return rednote.list_alerts(session, ctx)


@app.get("/api/v1/workspaces/{workspace_id}/apps/rednote/assets/{asset_id}")
def rednote_asset(asset_id: str, scope=Depends(rednote_scope)):
    ctx, session = scope
    path = rednote.get_asset_path(session, ctx, asset_id)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@app.get("/studio")
def studio_host():
    """Same-origin host page for the vue-fabric-editor build.

    The editor bundle is not committed yet; until it is built this page states
    that plainly and points at the JSON/PNG fallback instead of faking an editor.
    """
    index = static_dir / "studio" / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>设计编辑器</title><link rel=\"stylesheet\" href=\"/static/style.css\"></head>"
        "<body style=\"padding:32px;font-family:system-ui\"><h1>快图编辑器构建未就绪</h1>"
        "<p>仓库预留了同源挂载点：把 vue-fabric-editor 的预构建产物放到 "
        "<code>app/static/studio/</code> 后，本页会直接加载它。</p>"
        "<p>在此之前，工作区“设计文档”提供 JSON 往返与 PNG 样张导出，不会假装编辑器已接通。</p>"
        "<p><a href=\"/\">返回工作台</a></p></body></html>")


# ---------------------------------------------------------------- V2 autonomous assistant

def v2_db():
    if not db_layer.configured() and not db_layer.ensure_configured():
        raise HTTPException(status_code=503, detail={"code": "CAPABILITY_DISABLED",
                                                     "message": "V2 会话需要数据库；请配置 DATABASE_URL 或可写的 AGENT_DB_PATH"})
    global _workspace_db_ready
    if not _workspace_db_ready:
        db_layer.init_db()
        _workspace_db_ready = True
    session = db_layer.session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@app.get("/api/v2/conversations")
def v2_list_conversations(limit: int = 50, db=Depends(v2_db)):
    return agent_runtime.list_conversations(db, limit)


@app.post("/api/v2/conversations")
def v2_create_conversation(payload: dict | None = None, db=Depends(v2_db)):
    payload = payload or {}
    # New conversations default to the LangGraph autonomous runner. The legacy runners
    # stay available explicitly, and existing conversations keep the version they were
    # created with (no silent rewrite of history).
    mode = str(payload.get("mode") or LG_RUNNER_VERSION)
    if mode not in {LG_RUNNER_VERSION, "guided", "autonomous"}:
        mode = LG_RUNNER_VERSION
    conversation = models.AgentConversation(workspace_id=str(payload.get("workspace_id") or "default"),
                                            runner_version=mode)
    db.add(conversation)
    db.flush()
    return {"conversation_id": conversation.id, "workspace_id": conversation.workspace_id,
            "mode": mode, "runner_version": mode}


@app.post("/api/v2/conversations/{conversation_id}/messages")
async def v2_post_message(conversation_id: str, payload: agent_runtime.MessageIn, db=Depends(v2_db)):
    conversation = db.get(models.AgentConversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND",
                                                     "message": "会话不存在"})
    if conversation.runner_version == LG_RUNNER_VERSION:
        async_mode = (payload.response_mode or "blocking").lower() == "async"
        try:
            prepared = await lg_runner.submit(db, conversation, payload.message,
                                              payload.model, payload.idempotency_key)
        except lg_runner.RunnerError as error:
            raise HTTPException(status_code=error.status,
                                detail={"code": "REQUEST_CONFLICT",
                                        "message": error.message}) from None
        if prepared["replayed"]:
            return prepared["payload"]
        if async_mode:
            # The run is already committed, so another connection can see it running.
            # The worker owns its own session; the request returns immediately.
            lg_runner.schedule_run(prepared["run_id"])
            return JSONResponse(status_code=202, content={
                "run_id": prepared["run_id"], "conversation_id": conversation.id,
                "runner_version": LG_RUNNER_VERSION, "status": "running",
                "events_url": f"/api/v2/runs/{prepared['run_id']}/events"})
        run = db.get(models.AgentRun, prepared["run_id"])
        return await lg_runner.execute_run(db, conversation, run, payload.message,
                                           payload.model)
    return await agent_runtime.run_turn(db, conversation, payload.message,
                                        model=payload.model,
                                        idempotency_key=payload.idempotency_key)


@app.get("/api/v2/runs/{run_id}/events")
def v2_run_events(run_id: str, db=Depends(v2_db)):
    run = db.get(models.AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "运行不存在"})
    return {"run_id": run.id, "status": run.status, "events": run.events or [],
            "usage": run.usage or {}, "stop_reason": run.stop_reason}


@app.get("/api/v2/runs/{run_id}/events/stream")
async def v2_run_events_stream(run_id: str, after_seq: int = 0, db=Depends(v2_db)):
    """Server-sent replay of persisted events.

    Each event carries its index as the SSE id, so a client that reconnects with
    ``after_seq`` resumes without duplicating what it already saw. The stream only
    replays committed events — it never forwards raw graph state or unverified tokens.
    """
    run = db.get(models.AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "运行不存在"})

    async def replay():
        cursor = max(0, int(after_seq or 0))
        idle = 0
        while True:
            session = db_layer.session_factory()()
            try:
                fresh = session.get(models.AgentRun, run_id)
                events = list(fresh.events or []) if fresh else []
                status = fresh.status if fresh else "failed"
            finally:
                session.close()
            for index in range(cursor, len(events)):
                data = json.dumps(events[index], ensure_ascii=False, default=str)
                yield f"id: {index}\nevent: run\ndata: {data}\n\n"
            cursor = len(events)
            if status in LG_FINAL_STATUSES:
                yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
                return
            idle += 1
            if idle > 600:  # ~5 minutes; the client can reconnect with after_seq
                yield 'event: end\ndata: {"status": "stream_timeout"}\n\n'
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(replay(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/v2/conversations/{conversation_id}")
def v2_get_conversation(conversation_id: str, db=Depends(v2_db)):
    conversation = db.get(models.AgentConversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND",
                                                     "message": "会话不存在"})
    return agent_runtime.conversation_payload(db, conversation)


def _update_conversation(conversation_id: str, payload: dict | None, db):
    conversation = db.get(models.AgentConversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND",
                                                     "message": "会话不存在"})
    payload = payload or {}
    if "title" not in payload and "pinned" not in payload:
        raise HTTPException(status_code=400, detail={"code": "NOTHING_TO_UPDATE",
                                                     "message": "只支持 title 和 pinned"})
    return agent_runtime.update_conversation(
        db, conversation,
        title=payload.get("title") if "title" in payload else None,
        pinned=payload.get("pinned") if "pinned" in payload else None)


async def _delete_conversation(conversation_id: str, db):
    conversation = db.get(models.AgentConversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND",
                                                     "message": "会话不存在"})
    runner_version = conversation.runner_version
    thread_id = f"{conversation.id}:{runner_version}"
    # Ledger rows and the checkpoint thread are part of the conversation's footprint;
    # deleting only the business tables would leave a resumable orphan checkpoint.
    lg_runner.delete_ledgers(db, conversation.id)
    agent_runtime.delete_conversation(db, conversation)
    if runner_version == LG_RUNNER_VERSION:
        db.commit()
        await lg_runner.purge_thread(thread_id)
    return {"deleted": conversation_id, "checkpoint_purged": runner_version == LG_RUNNER_VERSION}


@app.patch("/api/v2/conversations/{conversation_id}")
def v2_update_conversation(conversation_id: str, payload: dict | None = None, db=Depends(v2_db)):
    return _update_conversation(conversation_id, payload, db)


@app.post("/api/v2/conversations/{conversation_id}/update")
def v2_update_conversation_post(conversation_id: str, payload: dict | None = None, db=Depends(v2_db)):
    """POST alias for gateways that block PATCH."""
    return _update_conversation(conversation_id, payload, db)


@app.delete("/api/v2/conversations/{conversation_id}")
async def v2_delete_conversation(conversation_id: str, db=Depends(v2_db)):
    return await _delete_conversation(conversation_id, db)


@app.post("/api/v2/conversations/{conversation_id}/delete")
async def v2_delete_conversation_post(conversation_id: str, db=Depends(v2_db)):
    """POST alias for gateways that block DELETE."""
    return await _delete_conversation(conversation_id, db)


@app.get("/api/v2/conversations/{conversation_id}/runtime")
def v2_conversation_runtime(conversation_id: str, db=Depends(v2_db)):
    """Redacted runner state for the manager surface — never the raw graph state."""
    conversation = db.get(models.AgentConversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND",
                                                     "message": "会话不存在"})
    return lg_runner.state_view(db, conversation)


@app.post("/api/v2/runs/{run_id}/cancel")
def v2_cancel_run(run_id: str, db=Depends(v2_db)):
    run = db.get(models.AgentRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND", "message": "运行不存在"})
    if run.status == "running":
        run.status = "cancelled"
        run.stop_reason = "user_cancelled"
        db.flush()
    return {"run_id": run.id, "status": run.status}


@app.get("/api/v2/artifacts/{artifact_id}/revisions/{revision}")
def v2_artifact_revision(artifact_id: str, revision: int, db=Depends(v2_db)):
    row = db.scalars(select(models.AgentArtifactRevision).where(
        models.AgentArtifactRevision.artifact_id == artifact_id,
        models.AgentArtifactRevision.revision == revision)).first()
    if not row:
        raise HTTPException(status_code=404, detail={"code": "REVISION_NOT_FOUND", "message": "版本不存在"})
    return {"artifact_id": artifact_id, "revision": row.revision, "content": row.content,
            "content_hash": row.content_hash, "check": row.check_status,
            "created_at": row.created_at.isoformat()}
