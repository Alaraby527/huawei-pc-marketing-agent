# 华为 PC 营销图文工作台

[![CI](https://github.com/cangyuyi/huawei-pc-marketing-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/cangyuyi/huawei-pc-marketing-agent/actions/workflows/ci.yml)
[![Container](https://github.com/cangyuyi/huawei-pc-marketing-agent/actions/workflows/container.yml/badge.svg)](https://github.com/cangyuyi/huawei-pc-marketing-agent/actions/workflows/container.yml)

面向华为 PC 门店导购与内容运营的营销 Agent：**澄清客户需求 → 在 ima 知识库核验产品事实与图片 → 确认型号 → 生成平台图文 → 合规规则扫描 → 人工确认后导出**。

> **当前版本：v0.17.0**（`app.__version__` / `pyproject.toml`）。已在魔搭公开部署并复验；
> 线上运行的是含「四件事 → 候选型号 → 3 篇文案 + 原图 → 图文工作室」新流程的版本。
> > 版本号口径：代码里是 `0.17.0`；前端静态资源另有 `?v=` 缓存串（当前 `0.36.0-auditfix`），
> > 两者用途不同，不要混用。
>
> 已实现：左侧任务栏（新建任务 / 热点搜索 / 任务列表）、热点搜索独立视图（小红书榜单、热点详情、使用热点带入对话）、可折叠右侧工作栏、自由输入优先（先理解需求，缺什么问什么）、WorkBuddy 式逐题选项卡片、工具/技能调用轨迹、多轮需求澄清、中文槽位提取、选品与硬约束核验、ima 只读检索与目录回退、规格章节与脚注提取、同型号图片核验、规则/LLM 双引擎、输入区内模型选择与服务端白名单、合规规则扫描、两道人工确认、四段式报告导出、**DashScope 原生异步生图**、热点灵感与内容目标步骤可选热点、HarmonyOS 风格 PC 工作台。
>
> 未完成：赛事指定共享知识库接入、逐条语义核验、多 worker 会话存储、公网鉴权与限流。个人库联调始终标记 `competition_ready=false`。

## 线上实例

- 魔搭创空间（Public / Running）：https://www.modelscope.cn/studios/cangyuyi/huawei-pc-marketing-agent
- 直接应用：https://cangyuyi-huawei-pc-marketing-agent.ms.show/
- API 专用地址（脚本调用需 ModelScope token）：https://studio-cangyuyi-huawei-pc-marketing-agent.api-inference.modelscope.net
- 当前知识库：`华为PC全板块深度知识库_v7（本地官方导出）`，10 个重点型号、68 张索引图片；`KB_SOURCE=local` 直接读取官方 ZIP。它不是实时 ima 调用，`competition_ready=false`；ZIP 不提交 GitHub，部署时放入 `data/official-kb-v7.zip`。

![线上工作台](app/static/samples/live-workbench.png)

> 截图来自 Public / Running 的魔搭实例：左侧任务栏、热点搜索入口、中间自由输入对话框和可折叠右侧工作栏。完整验收记录见 [`docs/12_线上验证记录.md`](docs/12_线上验证记录.md)。

### 热点来源（MCP 已移除）

> ⚠️ **小红书扫码热点所用的内部授权 MCP 服务已整体移除**——它没有实际作用且登录困难
> （云端扫码常被小红书风控拦截，要求短信验证而 MCP 无法提交）。相关模块、路由与测试已删除，
> `XHS_MCP_BASE` / `XHS_MCP_TOKEN` 不再被读取。

现在的热点取源是两条，都不依赖扫码登录：

- **热梗（联网搜索）**：走 Serper 检索近期网络用语，再由模型整理成热点清单。需配置 `SERPER_API_KEY`；
  **不配会退回无密钥抓取（duckduckgo-lite / bing-html），容器环境里常被挑战页挡住**，表现为
  「搜索有返回但没有一条是关于网络用语的」→ 空列表（系统按设计拒绝用无关结果凑清单）。
- **平台热榜**：抖音 / 微博 / 知乎 / 小红书的公开热榜条目。

热点可「参考到对话」带入对话并沉淀进本次任务资料，后续生成的 3 篇文案都会结合它。

## 架构

```mermaid
flowchart LR
  U[导购 / 内容运营] --> API[FastAPI<br/>/api/v1/conversations/turn]
  API --> S[需求澄清与槽位]
  S --> R[ima 只读检索]
  R --> E[事实与图片核验]
  E --> P[型号确认]
  P --> C[图文生成<br/>rule / llm]
  C --> V[3 篇文案 + 知识库原图]
  C --> IMG[DashScope 氛围封面]
  V --> A[合规规则扫描]
  A --> H{人工确认}
  H -->|通过| X[导出四段式报告]
  H -->|修改| C
  R <--> KB[(ima 知识库)]
  C -.可选.-> LLM[OpenAI 兼容模型]
  IMG -.可选.-> IMGAPI[DashScope 原生生图]
```

核心原则：**产品事实只来自知识库；搜索相关性不等于产品身份；没有证据就停下来问，不用模型记忆补齐。**

## 快速开始

要求：Python 3.12、Node.js 18+（调用官方 ima Skill）、可访问 ima 与华为资源域名。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock

export IMA_OPENAPI_CLIENTID='...'
export IMA_OPENAPI_APIKEY='...'
export IMA_KB_NAME='你的知识库名称'

# 可选：启用模型文案与 DashScope 生图
export LLM_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
export LLM_MODEL='qwen-plus'
export LLM_API_KEY='...'
export LLM_IMAGE_MODEL='wanx2.1-t2i-turbo'
export ENABLE_AI_COVER=1

python -m uvicorn app.main:app --host 0.0.0.0 --port 7860
```

打开 `http://127.0.0.1:7860`。健康检查：`GET /healthz`；接口说明：`GET /docs`；OpenAPI：`GET /openapi.json`。

也可以使用凭证文件 `~/.config/ima/client_id` 和 `~/.config/ima/api_key`，不必把密钥写进环境变量。

### Docker

```bash
docker build -t huawei-pc-marketing-agent:0.13.1 .
docker run --rm -p 7860:7860 \
  -e IMA_OPENAPI_CLIENTID -e IMA_OPENAPI_APIKEY -e IMA_KB_NAME \
  -e LLM_BASE_URL -e LLM_API_KEY -e LLM_MODEL -e ENABLE_AI_COVER=1 \
  huawei-pc-marketing-agent:0.13.1
docker run --rm -p 7860:7860 \
  -e IMA_OPENAPI_CLIENTID -e IMA_OPENAPI_APIKEY -e IMA_KB_NAME \
  -e LLM_BASE_URL -e LLM_API_KEY -e LLM_MODEL -e ENABLE_AI_COVER=1 \
  huawei-pc-marketing-agent:0.13.1
```

镜像以非 root 用户运行，包含 Node 运行时、健康检查和 `vendor/ima-skill` 只读调用脚本。推送到 `main` 后，GitHub Actions 会自动构建并发布 `ghcr.io/cangyuyi/huawei-pc-marketing-agent:latest`。

## 配置

| 变量 | 必填 | 说明 |
|---|---|---|
| `IMA_OPENAPI_CLIENTID` / `IMA_CLIENT_ID` | 是 | ima 开放平台凭证；也可用 `~/.config/ima/client_id` |
| `IMA_OPENAPI_APIKEY` / `IMA_API_KEY` | 是 | ima 开放平台凭证；也可用 `~/.config/ima/api_key` |
| `IMA_KB_NAME` | 是 | 知识库精确名称；默认值为赛事指定库占位 |
| `KB_SOURCE` | 否 | `ima` 或 `local`；`local` 读取官方知识库 ZIP 导出 |
| `LOCAL_KB_ZIP` | 否 | 本地官方库 ZIP 路径，默认 `/app/data/official-kb-v7.zip` |
| `LOCAL_KB_NAME` | 否 | 本地官方库显示名称 |
| `XHS_MCP_BASE` | 否 | 小红书 MCP 服务地址；留空则使用公开热榜/编辑方向 |
| `XHS_MCP_TOKEN` | 否 | 小红书 MCP 访问令牌；仅服务端使用 |
| `IMA_NODE_BIN` | 否 | Node 绝对路径；PATH 中已有 `node` 时不需要 |
| `LLM_BASE_URL` | 否 | OpenAI 兼容服务地址，必须 HTTPS（本机地址可用 HTTP） |
| `LLM_API_KEY` | 否 | 模型密钥；规则引擎不需要 |
| `LLM_MODEL` | 否 | 单模型模式下的文本模型名称；也是默认值 |
| `LLM_MODELS_JSON` | 否 | 多供应商文本模型注册表（JSON 数组，服务端保存 base_url/api_key/model/context/default）；设置后优先于单模型配置，密钥不下发前端 |
| `LLM_ALLOWED_MODELS` | 否 | 逗号分隔的可选模型白名单；UI 只能选择这里列出的模型 |
| `LLM_TIMEOUT` / `LLM_MAX_TOKENS` | 否 | 超时与输出上限，自动限制在安全范围 |
| `ENABLE_AI_COVER` | 否 | `1` 才启用 AI 氛围封面；只生成背景，不生成产品外观 |
| `LLM_IMAGE_MODEL` | 否 | 生图模型；DashScope 默认 `wanx2.1-t2i-turbo` |
| `LLM_IMAGE_SIZE` | 否 | 生图尺寸，默认 `768*1024`（3:4） |
| `LLM_IMAGE_NATIVE_BASE` | 否 | DashScope 原生 API 基址；Base URL 含 dashscope 时自动推导 |
| `IMAGE_API_BASE_URL` | 否 | 独立图像通道（OpenAI 兼容）；默认 `https://maas.qianwenaiapi.com/compatible-mode/v1` |
| `IMAGE_API_KEY` | 否 | 独立图像通道密钥；只在服务端配置，不下发前端 |
| `IMAGE_MODEL` | 否 | 图像模型；主通道默认 `qwen-image-3.0-pro`。带参考图的能力以 `app/image_capabilities.py` 的探针记录为准（`gpt-image-2-w` 带图实测 503，未作为主通道） |
| `IMAGE_FALLBACK_BASE_URL` / `IMAGE_FALLBACK_API_KEY` / `IMAGE_FALLBACK_MODEL` | 否 | 主通道 429/5xx/超时时的兜底图像通道 |
| `DATABASE_URL` | 否 | 工作区持久化；Postgres 例：`postgresql+psycopg://rednote:rednote@127.0.0.1:5432/rednote`。未设置时工作区接口返回 `CAPABILITY_DISABLED`，对话链路不受影响 |
| `REDIS_URL` | 否 | **预留**：`compose.yaml` 会注入，但当前代码（`app/`、`scripts/`、`tests/`）没有任何地方读取；队列/缓存尚未接入 |
| `REDNOTE_DATA_DIR` | 否 | 工作区资产目录，默认 `data` |
| `HOT_SOURCE` | 否 | **预留**：`compose.yaml` 会注入，但当前代码没有任何地方读取；热点来源由 `HOT_API_BASE` / `HOTSPOTS_*` 控制 |

应用不会自动读取 `.env`；请由启动环境或平台 Secrets 注入。`.env.example` 只有占位符。

## 小红书工作区（M1）

左侧栏“小红书数据运营”只放发布后的数据运营与管理：发布记录、运营监控、评论待办、告警、复盘与经验。创作与审核在工作栏里完成，整条链路由“新建任务”对话驱动。当前交付 M1 主链路：

**上游简报交接 → 用户选主热点 → 6 个创意方向（≥3 表达机制、≥3 视觉家族）→ 最多 3 方向样张比较 → 设计文档 → 提交审核 → 批准 → 人工发布计划 → 回填链接（待核对）。**

模型可以在对话里自主调用这些模块：`POST /agent/turn` 提供工具注册表（简报、热点、方向、样张、设计、审核、发布、运营查询），有界循环并把工具轨迹返回给界面。工作栏按平台组织：平台在最上，标签页随平台变化。

- 工作区 API 前缀：`/api/v1/workspaces/{workspace_id}/apps/rednote`；服务端校验 `X-Workspace-Id`，对象归属不匹配直接拒绝。
- 持久化：`compose.yaml` 提供 Postgres 16 + Redis 7；`python -m app.db init` 建表。未配置 `DATABASE_URL` 时工作区返回 `CAPABILITY_DISABLED`，不伪造成功。
- 编辑器：`/studio` 已同源挂载快图 vue-fabric-editor（MIT 构建产物，已剔除上游商业字体）；构建脚本见 `scripts/build_studio.sh`，许可登记见 `THIRD_PARTY_NOTICES.md`。
- 设计库：16 个视觉家族 × (2 封面 + 2 内页) = 64 个自研布局配方，提供家族/布局接口、布局预览 PNG 和设计检查（尺寸/字体/文字越界/占位图）。
- 发布：连接器写发布登记为待联调；人工路径已走通（导出 → 提醒 → 回填 → “已回填，待核对”，不冒充已公开可见）。
- 运营监控/评论/告警/复盘：M1 提供合同与空态，真实数据接入待 M3；不会用 0 或模拟数字冒充真实值。
- 详细映射、复用卡与未完成清单见 [`docs/13_小红书工作区_M0映射与M1实施.md`](docs/13_小红书工作区_M0映射与M1实施.md)。

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/healthz` | 进程健康检查与版本 |
| `GET` | `/api/v1/readiness` | 配置/接入状态，不返回密钥 |
| `POST` | `/api/v1/conversations/turn` | 主入口：多轮澄清、选品、生成、修改、确认 |
| `GET` | `/api/v1/conversations/{session_id}/state` | 恢复历史任务（会话状态与最近消息） |
| `GET` | `/api/v1/conversations/{session_id}/export` | 导出已审批的四段式报告（text/markdown） |
| `POST` | `/api/v1/guide` | 单轮前段检索，返回材料包；图片核验强度低于对话链路 |
| `POST` | `/api/v1/materials/validate` | 材料包结构与完整度预检查，不代表事实真实 |
| `GET` | `/api/v1/platforms` | 四个平台的规则 |
| `GET` | `/api/v1/knowledge/connection` | ima 连接状态 |
| `GET` | `/api/hotspots` | 热点中心榜单（只读；分页读取固定发布版本，不触发抓取） |
| `GET` | `/api/v1/hot-topics` | 可选的热点榜单参考 |
| `GET` | `/api/v1/hot-charts` | 热点图表数据 |
| `POST` | `/api/v1/hot-topics/refresh` | 刷新热点数据 |
| `POST` | `/api/v1/images/generate` | 生图入口（知识库核验原图作参考） |

主流程示例：

```bash
curl -s http://127.0.0.1:7860/api/v1/conversations/turn \
  -H 'Content-Type: application/json' \
  -d '{"message":"大学新生，预算6000以内，主要写论文、上网课，偏好轻薄"}'
```

响应中的 `session_id` 用于后续轮次。只有 `can_publish=true` 才能导出；它要求草稿与当前交接材料绑定、合规规则扫描通过、且运营人员明确确认。

## 证据与安全约束

- **型号身份**：知识库标题/正文型号归一化后必须精确匹配；搜索命中相近型号不会替代。
- **价格**：只接受当日、同 SKU、同地区/渠道、来源为官方页面的维护记录；历史价格、补贴、赠品和库存不自动补写。
- **图片**：对话链路要求图片索引、官方商品页/型号 slug、以及 ima 下载字节与官方原图 SHA256 一致；只证明型号级来源，不证明颜色/键盘/屏幕/SKU。
- **材料包**：`MaterialBundle v1.0` 禁止重复 ID、跨产品事实/图片、live 模式出现 development 来源。
- **网络**：只允许 ima/华为资源域名，禁止重定向，认证头不跨域转发。
- **输出**：错误响应不回显原始输入或凭证；发布状态由 `draft_binding` 与当前 handoff 共同决定，需求变更、读库失败或证据变化会立即作废旧草稿。

合规核验目前是**确定性规则扫描**（绝对化用语、无依据体验、未核实价格/促销、数字可追溯性），不是逐条语义事实核验；导出报告会明确标注这一点。

## 证据与样图（评审对照）

### 风格配方实际生成样图（24 个配方）

配方库 R01–R24 逐张生成样图，**已人工逐张验收：批准 22 个、剔除 2 个**（R04 水墨空间、R22 文字主视觉
都丢了产品本体）。批准是带具名审批人、时间与**逐张 sha256 证据**的记录，不是把配方字段改成 approved。

<table>
<tr>
<td><a href="docs/evidence/recipes/R01_杂志编辑.jpg"><img src="docs/evidence/recipes/R01_杂志编辑.jpg" width="150" alt="R01 杂志编辑"></a><br>R01 杂志编辑</td>
<td><a href="docs/evidence/recipes/R05_电影叙事.jpg"><img src="docs/evidence/recipes/R05_电影叙事.jpg" width="150" alt="R05 电影叙事"></a><br>R05 电影叙事</td>
<td><a href="docs/evidence/recipes/R12_黏土世界.jpg"><img src="docs/evidence/recipes/R12_黏土世界.jpg" width="150" alt="R12 黏土世界"></a><br>R12 黏土世界</td>
<td><a href="docs/evidence/recipes/R13_像素游戏背景.jpg"><img src="docs/evidence/recipes/R13_像素游戏背景.jpg" width="150" alt="R13 像素游戏"></a><br>R13 像素游戏</td>
</tr>
<tr>
<td><a href="docs/evidence/recipes/R04_水墨空间.jpg"><img src="docs/evidence/recipes/R04_水墨空间.jpg" width="150" alt="R04 水墨空间（已剔除）"></a><br>R04 水墨空间 <b>已剔除</b></td>
<td><a href="docs/evidence/recipes/R22_文字主视觉.jpg"><img src="docs/evidence/recipes/R22_文字主视觉.jpg" width="150" alt="R22 文字主视觉（已剔除）"></a><br>R22 文字主视觉 <b>已剔除</b></td>
<td><a href="docs/evidence/reference/kb_reference.jpg"><img src="docs/evidence/reference/kb_reference.jpg" width="150" alt="参考原图"></a><br>参考原图（图生图输入）</td>
<td><a href="docs/evidence/screenshots/04_图文工作室重绘结果_generated_scene.jpg"><img src="docs/evidence/screenshots/04_图文工作室重绘结果_generated_scene.jpg" width="150" alt="重绘结果"></a><br>重绘结果（generated_scene）</td>
</tr>
</table>

**全部 28 张**见 [`docs/evidence/recipes/`](docs/evidence/recipes/)（含 R01/R02 的多次迭代版），
生成方式与验收结果见该目录 [README](docs/evidence/recipes/README.md)。

生成链路只有一条：**知识库核验产品原图 + 配方提示词 → 图生图**。不提供纯文生图。

> 该目录是**压缩后的展示副本，不是 sha256 证据原件**（原图 51.6 MB → 展示版 1.8 MB）。
> 这些是生成图（`kind = generated_scene`）**不是产品实拍**，不得用于证明产品外观或当作所选配置实拍。
> 产品图片版权归原权利人，仅用于项目展示与效果评估。

### 说明书附录插图

从《作品说明书_复赛版.docx》附录 A 提取（内嵌在 docx 里评审看不到）：

<table>
<tr>
<td><a href="docs/evidence/screenshots/01_工作台界面_0.16x线上版_历史.jpg"><img src="docs/evidence/screenshots/01_工作台界面_0.16x线上版_历史.jpg" width="230" alt="工作台界面"></a><br>工作台界面（<b>0.16.x 历史版</b>）</td>
<td><a href="docs/evidence/screenshots/03_知识库核验原图.jpg"><img src="docs/evidence/screenshots/03_知识库核验原图.jpg" width="150" alt="知识库核验原图"></a><br>知识库核验原图</td>
<td><a href="docs/evidence/screenshots/05_配方R16知识手账_实际生成样图.jpg"><img src="docs/evidence/screenshots/05_配方R16知识手账_实际生成样图.jpg" width="150" alt="R16 知识手账"></a><br>R16 知识手账生成样图</td>
</tr>
</table>

⚠️ 第一张是 **0.16.x 引导式流程版本的界面**（说明书第 1 章自述"线上截图仅代表 0.16.x 行为"），
**不代表当前版本**；当前版本已改为 LangGraph 自主 + 三板块（对话 / 热点榜单 / 图文工作室）。
详见 [`docs/evidence/screenshots/README.md`](docs/evidence/screenshots/README.md)。

### 六页样稿（历史形态）

> ⚠️ **六页图文已从当前版本整体删除**：工具 `render_visuals`、页面结构、海报路由与长图 ZIP 一并移除。
> 交付形态改为「3 篇文案 + 知识库原图 + 图文工作室」。以下样稿仅作**历史形态**展示。
> 删后旧对话重放调用该工具会显式返回 `TOOL_REMOVED`，不静默失效。

下面是使用真实产品资料和原图渲染的六页样稿，用于展示图文结构与版式，**不代表当前会话的生成结果**。

[![六页样稿总览](app/static/samples/preview.png)](app/static/samples/preview.png)

<table>
<tr>
<td><a href="app/static/samples/sketch/page-01.png"><img src="app/static/samples/sketch/page-01.png" width="180" alt="封面"></a></td>
<td><a href="app/static/samples/sketch/page-02.png"><img src="app/static/samples/sketch/page-02.png" width="180" alt="场景"></a></td>
<td><a href="app/static/samples/sketch/page-03.png"><img src="app/static/samples/sketch/page-03.png" width="180" alt="事实"></a></td>
</tr>
<tr>
<td><a href="app/static/samples/sketch/page-04.png"><img src="app/static/samples/sketch/page-04.png" width="180" alt="细节"></a></td>
<td><a href="app/static/samples/sketch/page-05.png"><img src="app/static/samples/sketch/page-05.png" width="180" alt="决策"></a></td>
<td><a href="app/static/samples/sketch/page-06.png"><img src="app/static/samples/sketch/page-06.png" width="180" alt="收尾"></a></td>
</tr>
</table>

三种风格总览：

<table>
<tr>
<td><a href="app/static/samples/sketch/contact-sheet.png"><img src="app/static/samples/sketch/contact-sheet.png" width="240" alt="数码手账"></a><br>数码手账</td>
<td><a href="app/static/samples/fresh/contact-sheet.png"><img src="app/static/samples/fresh/contact-sheet.png" width="240" alt="清爽桌搭"></a><br>清爽桌搭</td>
<td><a href="app/static/samples/pop/contact-sheet.png"><img src="app/static/samples/pop/contact-sheet.png" width="240" alt="轻漫画"></a><br>轻漫画</td>
</tr>
</table>

AI 封面氛围图示例（只做背景，不是产品实拍）：

<img src="app/static/samples/ai-cover-demo.png" width="240" alt="AI 封面氛围图示例">

> 样稿中的产品图片来自项目联调知识库，版权归原权利人；仅用于项目展示与效果评估，不构成产品官方素材。

## 真实联调记录（2026-10-18）

- ima：`Alaraby的知识库`（个人库）真实读取 **13 组规格事实、6 张同型号图片**；`competition_ready=false`。
- 文本：DashScope `qwen-plus` 生成六页方案，引用事实 ID，规则扫描通过。
- 人工确认：产品选择 + 成稿确认两道门；确认后导出四段式 Markdown（含六页方案）返回 200。
- 生图：DashScope `wanx2.1-t2i-turbo` 原生异步接口返回 768×1024 PNG，用于封面氛围背景。
- 测试：**624 个用例通过 / 0 failed**（2026-10-24 本机实测，耗时约 287 秒）；CI 在 GitHub Actions 上通过；容器镜像启动后 `/healthz` 冒烟测试通过。

## 团队分工

| 职责 | 负责人 |
|---|---|
| 部署与运维 | 王泽栋 |
| 知识库与证据 | 杨惠雯 |
| 热点与生图 | 王泽栋 |

## 测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

当前 **624 个用例**通过，覆盖需求澄清、预算/系统要求解析、选品硬约束、价格时效、图片身份与字节一致性、确认门与审批失效、LLM 输出回退、本地官方库 ZIP 适配、LangGraph 运行时与工具网关、任务资料累加（人群/平台/场景/活动/型号/热点）、3 篇文案校验（恰好 3 篇，否则拒绝保存）、生图并发与计费写锁、配方批准记录、CLI 重复导出、SSRF/分页/密钥不回显等。（六页事实引用与小红书 MCP 映射的用例已随功能删除一并移除。）测试中的产品、来源与图片均为合成对象，不代表真实知识库资料。

## 部署说明

**GitHub 适合放源码、CI 和镜像，不适合直接托管这个后端。** GitHub Pages 只能托管静态文件，不能运行 FastAPI、ima 调用和 LLM 调用。仓库提供：

- `ci.yml`：每次 push/PR 跑编译检查与全量测试。
- `container.yml`：在 `main`/版本 tag 上构建并推送 GHCR 镜像，并启动容器请求 `/healthz` 做冒烟测试。
- `Dockerfile` / `compose.yaml`：本地或任意容器平台运行。

当前镜像标签包括 `latest`、`0.17.0`、`v0.17.0` 和 `sha-<commit>`：

```bash
docker run --rm -p 7860:7860 \
  -e IMA_OPENAPI_CLIENTID -e IMA_OPENAPI_APIKEY -e IMA_KB_NAME \
  -e LLM_BASE_URL -e LLM_API_KEY -e LLM_MODEL -e ENABLE_AI_COVER=1 \
  ghcr.io/cangyuyi/huawei-pc-marketing-agent:0.13.1
  ghcr.io/cangyuyi/huawei-pc-marketing-agent:0.13.1
```

要得到可访问的完整实例，需要容器托管环境（ModelScope 创空间、Render、Fly.io、Railway、云服务器等）并配置 ima/模型凭证。当前实例已按 ModelScope Docker SDK 部署在 7860 端口；重建步骤见 [`docs/10_ModelScope部署.md`](docs/10_ModelScope部署.md)。

完整形态还缺三件事：赛事共享知识库权限、逐条语义核验、以及面向公网的鉴权/限流与多 worker 会话存储。

## 目录

```text
app/
  main.py          FastAPI 路由、静态页、导出
  conversation.py  多轮状态机、槽位解析、两道确认门
  evidence.py      目录/事实/价格/图片核验与排序
  retrieval.py     单轮前段检索与材料包
  ima.py           官方 ima 只读适配器
  compose.py       规则/LLM 文案生成
  compliance.py    合规规则扫描
  contracts.py     材料包与任务契约
  intake.py        单轮任务解析与场景策略
  model.py         OpenAI 兼容文本 / DashScope 原生异步生图
  posters.py       六页 1080×1440 PNG 海报渲染与 ZIP 打包
  static/          HarmonyOS 风格工作台
  static/samples/  六页样稿与 AI 封面示例
scripts/           CLI、预检、真实联调记录
tests/             合成 fixture 与回归测试
docs/              交接、配置、进度与设计文档
prompts/           下游 Agent 系统提示词
vendor/ima-skill/  官方 ima 只读调用脚本
```

## 已知限制

- 个人知识库联调，`competition_ready=false`；尚未接入赛事指定共享库。
- 会话与图片临时地址保存在进程内，只适合单 worker；多实例部署需要 Redis/对象存储。
- 公开接口没有鉴权与限流，必须放在网关或访问控制之后。
- `/api/v1/guide` 的图片核验只到“文件名与型号 slug 一致”，弱于对话链路的字节级核验；新接入请优先使用对话接口。
- 规则引擎按平台规则生成有限模板；LLM 输出虽经结构校验与数字复核，仍不等同于语义事实核验。
- 外部依赖（ima、模型服务、华为图片域名）不可用时，系统会停在 `unavailable`，不会用旧结果冒充当前材料。

## 文档

- [初赛要求与分工](docs/06_初赛要求与两小时执行方案.md)
- [前段交接与使用说明](docs/07_前段交接与使用说明.md)
- [接口约定与分工](docs/02_接口约定与分工.md)
- [配置与联调验收](docs/03_配置与联调验收.md)
- [魔搭创空间部署](docs/10_ModelScope部署.md)
- [线上验证记录](docs/12_线上验证记录.md)
- [华为设计语言与工作台规范](docs/11_华为设计语言与工作台规范.md)
- [进度与限制](docs/04_进度与限制.md)
- [系统提示词](prompts/agent-system.md)

## License

Apache-2.0（见 `LICENSE`）。第三方组件的许可与版权单独登记；未引入 GPL/AGPL 代码，模板/字体只用可商用授权资源。
