# 华为PC营销Agent：前段导购与证据交接

**当前版本：0.2.0。前段已实现并用个人ima知识库真实验证。** 包含需求澄清、规则式自然语言槽位提取、场景与平台策略、精确型号确认、ima搜索与目录回退、完整规格章节提取、同型号图片读取、输入风险识别和材料包导出。后段营销文案、图片排版/合成与最终成稿核验由队友接入，尚不能声称完成全部赛题闭环。

本项目参考自己的[电视选购Copilot](https://github.com/Alaraby527/tv-buying-copilot)的需求槽位、澄清、RAG、约束复核与trace思路；资料改为ima实时调用，不使用旧电视数据。

## 立即测试前段

本机双击`测试前段导购.command`即可逐项输入并导出材料；结果在`.runtime/last-handoff.json`，图片在`.runtime/assets/`。脚本默认个人库`Alaraby的知识库`，使用本机既有ima凭证文件，不要求大模型密钥。

```bash
# 在本项目目录执行；凭证支持环境变量或 ~/.config/ima/client_id 和 api_key。
.venv/bin/python scripts/guide.py libraries
.venv/bin/python scripts/guide.py connection --kb 'Alaraby的知识库'
.venv/bin/python scripts/guide.py run --kb 'Alaraby的知识库' --task examples/task.personal.json
.venv/bin/python scripts/guide.py interactive --kb 'Alaraby的知识库'
```

真实验证：`HUAWEI MateBook 14（鸿蒙版）`，13组规格章节、3张实际读取的同型号图片。个人库联调始终标记`competition_ready=false`，不能作为已经满足主办方指定库要求的证明。库权限允许后仅需修改`IMA_KB_NAME`切换到指定库，再重新验证。

完整赛事提取：[初赛要求与分工](docs/06_初赛要求与两小时执行方案.md)。前段接口、限制与队友用法：[前段交接与使用说明](docs/07_前段交接与使用说明.md)。系统提示词：[agent-system.md](prompts/agent-system.md)。

## 从这里开始

- 部署负责人：[队友部署指南](docs/01_队友部署指南.md)
- 两人联调：[接口约定与分工](docs/02_接口约定与分工.md)
- 配置与验收：[配置与联调验收](docs/03_配置与联调验收.md)
- 当前状态：[进度与限制](docs/04_进度与限制.md)
- 可直接转发的文字：[发给队友的说明](发给队友的说明.md)

## 本地启动

要求Python 3.12。macOS/Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m uvicorn app.main:app --host 0.0.0.0 --port 7860
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 7860
```

打开本机7860端口。个人库使用`export IMA_KB_NAME='Alaraby的知识库'`后启动；不设置时默认赛事指定库。应用不会自动读取`.env`，本地需要由启动环境设置变量；魔搭环境变量/Secrets由平台注入。

API：`POST /api/v1/guide`接收完整或部分任务，返回前段交接信封。缺项时返回`needs_input`及`questions`；原有`POST /api/v1/tasks`保留后段待接入状态，不能用它冒充完整生成。

```bash
curl -X POST http://127.0.0.1:7861/api/v1/guide -H 'Content-Type: application/json' --data-binary @examples/task.personal.json
curl -X POST http://127.0.0.1:7861/api/v1/guide -H 'Content-Type: application/json' -d '{"message":"想买轻薄本"}'
```

上方7861为本次已启动的测试服务；新启动服务若用默认7860，替换命令里的端口即可。HTTP图片地址相对当前服务，30分钟后或重启后失效，需重新检索。CLI则导出本地图片，连同JSON交接，接收方映射路径。

## Docker启动

```bash
docker build -t huawei-pc-marketing-agent:0.1.0 .
docker run --rm -p 7860:7860 huawei-pc-marketing-agent:0.1.0
```

镜像监听`0.0.0.0:7860`，包含Python、Node运行时和官方ima只读调用脚本。仍需在部署环境配置凭证与知识库名；本地通过不代表云端权限/网络已验证。Docker未在本机实际构建。

## 测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

测试中的产品、来源及图片标识均为合成测试对象，不代表真实知识库资料。示例材料包明确标识为开发模式，不能用于发布。

## 目录职责

- `app/contracts.py`：双方共同遵守的数据约定。
- `app/intake.py`：槽位提取、缺项追问、场景策略。
- `app/ima.py`：官方只读接口、分页、原文与素材读取。
- `app/retrieval.py`：前段检索、事实提取、型号复核、图片交接。
- `app/content.py`：第二部分业务接入位置，当前明确返回待开发状态。
- `app/verification.py`：现有材料预检查，不等于成稿语义核验。
- `app/platforms.py`：赛题四个平台的基础规则。
- `app/static/`：部署联调页面。
- `Dockerfile`、`requirements.lock`：部署基础。

提交前仍需接通赛事指定库、队友后段、最终语义核验，并准备完整图文运行样例；前段材料包不等于最终软文。不要将`.runtime/`原文/图片、凭证或完整知识库上传公共仓库。
