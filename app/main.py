import asyncio
import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
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
from .model import image_configured, generate_cover_image, ModelError

service.client_factory=default_client

app = FastAPI(title="华为PC营销Agent · 门店导购与图文生成", version=__version__, docs_url=None, redoc_url=None)
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
