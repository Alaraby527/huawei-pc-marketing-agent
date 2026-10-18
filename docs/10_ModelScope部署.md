# 魔搭创空间部署（Docker SDK）

目标：把本仓库部署到 `cangyuyi/huawei-pc-marketing-agent`，得到一个可访问的 FastAPI + ima + DashScope 实例。

## 1. 前置条件

- 已加入目标 ima 知识库，并能用 `IMA_OPENAPI_CLIENTID` / `IMA_OPENAPI_APIKEY` 调用。
- DashScope 兼容模式可用的文本模型（本项目验证使用 `qwen-plus`）。
- DashScope 原生生图模型（验证使用 `wanx2.1-t2i-turbo`）。
- 魔搭账号对该创空间有写权限；不要把凭证写进仓库、README 或截图。

## 2. 创空间配置

1. 打开 `https://www.modelscope.cn/studios/cangyuyi/huawei-pc-marketing-agent`。
2. 进入“设置 / 部署”，选择 Docker SDK（不要选 Gradio SDK；本应用是 FastAPI，Gradio 外壳会遮住 `/healthz` 和 API）。
3. 确认容器监听端口为 `7860`，启动命令使用仓库 `Dockerfile` 的默认 `CMD`。
4. 上传或连接本仓库代码；至少需要 `app/`、`vendor/`、`prompts/`、`Dockerfile`、`requirements.lock`。

## 3. 环境变量 / Secrets

在创空间的环境变量或 Secrets 中配置（只填值，不要提交到 Git）：

```text
IMA_OPENAPI_CLIENTID=...
IMA_OPENAPI_APIKEY=...
IMA_KB_NAME=你的知识库精确名称

LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
LLM_ALLOWED_MODELS=qwen-plus,qwen-max
LLM_API_KEY=...

LLM_IMAGE_MODEL=wanx2.1-t2i-turbo
LLM_IMAGE_SIZE=768*1024
ENABLE_AI_COVER=1
```

使用官方知识库 ZIP 本地导出时，增加：

```text
KB_SOURCE=local
LOCAL_KB_ZIP=/app/data/official-kb-v7.zip
```

把官方 ZIP 以 `data/official-kb-v7.zip` 放入部署源码；Dockerfile 会复制到镜像。ZIP 已被 `.gitignore` 排除，不会进入 GitHub。该模式不调用实时 ima，`competition_ready` 仍为 false。

可选的小红书扫码热点需要一个内部 MCP 服务：

```text
XHS_MCP_BASE=https://studio-cangyuyi-huawei-xhs-mcp.api-inference.modelscope.net
XHS_MCP_TOKEN=<ModelScope access token>
```

主应用只把 MCP 当内部授权服务：用户在顶栏点“连接小红书”，扫描弹窗二维码并授权，主应用再读取实时笔记。MCP 地址和 token 只放服务端 Secrets，不暴露在页面上；登录会话为实例共享，重启后失效。未配置或未登录时自动回退公开热榜/微博/编辑方向。

可选：

```text
IMA_NODE_BIN=/usr/local/bin/node
LLM_TIMEOUT=90
LLM_IMAGE_TIMEOUT=150
```

## 4. 启动后验收

```bash
curl -s https://<你的运行域名>/healthz
# {"status":"ok","version":"0.7.0",...}

curl -s https://<你的运行域名>/api/v1/readiness
# 检查 ima_credentials / llm_credentials / llm_endpoint_and_model
```

浏览器打开运行域名，按以下顺序验收：

1. 知识库徽标显示“已连接”。
2. 输入“大学新生，预算不限，主要写论文、上网课，偏好轻薄”。
3. 看到候选型号卡片，点击“选 1”。
4. 选择“到店体验”，生成六页方案。
5. 点击“生成 AI 封面氛围图”，确认返回图片。
6. 点击“通过，可以发布”，确认底部状态变为可导出，并下载报告。
7. 打开“六页图文”和“六页样稿”确认画布与样稿可见。

## 5. 常见问题

- **403 / 登录页**：创空间是私有的，或当前账号没有运行权限；先确认空间可见性和登录状态。
- **知识库连接失败**：检查凭证、`IMA_KB_NAME` 是否精确、账号是否已加入该库；Node 18+ 是否在 PATH。
- **生图 503**：`ENABLE_AI_COVER` 不是 `1`，或缺少 `LLM_BASE_URL` / `LLM_API_KEY`。
- **生图超时**：DashScope 原生接口是异步任务，通常 10–30 秒；保持 `LLM_IMAGE_TIMEOUT` 不低于 120。
- **`competition_ready=false`**：当前为个人库联调；赛事指定共享库需要单独加入并在 `IMA_KB_NAME` 中切换。

## 6. 安全

- 不要把 `LLM_API_KEY`、`IMA_OPENAPI_APIKEY` 或 `~/.config/ima` 内容写进仓库、日志、截图或演示视频。
- 如果凭证曾在聊天、截图或公开渠道出现，立即在平台重新生成。
- 公网实例建议加访问网关/鉴权；当前应用没有内置登录和限流。
