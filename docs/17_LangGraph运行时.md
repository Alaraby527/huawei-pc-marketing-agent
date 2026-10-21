# LangGraph 运行时（`langgraph_autonomous_v1`）

2026-10-21。本文说明 0.17.0 新增的自主运行器：它替换的是**编排层**，业务真相仍然留在原有服务里。

## 1. 为什么是"融合"而不是"重写"

新运行器复用现有的全部业务能力，没有复制任何一份真相源：

| 能力 | 仍由谁负责 |
|---|---|
| 产品证据读取、目录回退、图片身份核验 | `app/evidence.py`、`app/local_kb.py`、`app/ima.py` |
| 字段级事实核对、价格拒绝、最后兜底清洗 | `app/grounding.py` |
| 交付物版本、局部 patch、内容哈希、检查结论 | `agent_runtime._save_artifact` 等（原样调用） |
| 排版与 PNG 渲染 | `app/posters.py` |
| 网关适配（含总时限与滴流保护） | `app/model.py`（未改动） |
| 任务历史、置顶、重命名 | `agent_runtime` + `main.py`（语义保持） |

新运行器只负责"下一步做什么、何时停、怎么记账"。

**明确没有做的两种伪改造**（整改包禁止）：没有把 `_guided_turn` 整段塞进一个节点；没有在图层外面再套原来的 12 步循环。决定下一步的权力已经从旧代码移交出去。

## 2. 图结构

```text
START → decide ──工具──→ execute_tools ──┐
          │                             │
          ├─ ask_user → wait_for_user ───┤
          │        （interrupt）         │
          └─ 无工具调用/终态 → finalize ←┘
                                 │
                                 END
```

- `decide`：调用主模型要一个动作。**不预加载知识库**——模型自己决定是否需要证据；标题、创意这类任务不需要。
- `execute_tools`：串行执行工具（同一个 SQLAlchemy Session 不是并发原语）。
- `wait_for_user`：只做 `interrupt`，不在中断前产生任何付费调用或业务写入。
- `finalize`：统一输出围栏，检查回复文本、交付物与导出面。

节点与边只含通用机制，**没有任何选品/确认/六页阶段**。

## 3. State 与 RuntimeContext 的边界

`MarketingState`（`app/runtime/state.py`）只保存可 JSON 序列化的内容：schema 版本、消息投影、用户需求、假设、证据引用（`snapshot_id` / `source_kind` / `source_hash`）、交付物引用（`artifact_id` / `revision` / `content_hash`）、待决调用、待决问题、有界修复计数。

`RuntimeContext`（`app/runtime/context.py`）保存受信协作者：SQLAlchemy Session、模型网关门面、工具网关门面、预算与取消查询、可信会话/运行标识。

**DB Session、模型客户端、知识库客户端、API Key、签名头、图片字节都不进入 checkpoint。** 测试 `test_checkpoint_holds_references_not_secrets` 直接读 checkpoint 文件断言这一点。

## 4. 三本台账

| 台账 | 表 | 解决什么 |
|---|---|---|
| 请求验重 | `agent_request_records` | `(conversation_id, key)` + 规范化 payload 哈希；同键异内容 409；不同会话互不干扰 |
| 模型调用 | `agent_model_attempts` | 每一次真实模型请求一条记录；**缺 usage 记 unknown（NULL），不记 0** |
| 副作用 | `agent_effects` | `save_artifact` 崩溃后重放返回原结果，不新建 artifact revision；结果未知进 `needs_reconciliation` |

这三张都是**新增表**，没有修改任何既有表，因此部署中的 SQLite 文件不需要 ALTER 迁移（`create_all` 只追加）。

## 5. 状态取值

`queued / running / waiting_user / completed / partial / failed / cancelled / needs_reconciliation`

`partial` 与 `completed` 分开：预算耗尽（模型调用次数或整轮时限）**不算成功**。取消是持久意图，一旦取消不会再被改回 `completed`。

## 6. 恢复与删除

- `thread_id` 由服务端从 `conversation_id` 与 runner 版本派生，模型和前端都不能指定。
- 暂停后新用户消息产生**新的 AgentRun**，并用 `Command(resume=用户文本)` 恢复同一 thread；不接受任意 state 补丁。
- 取消：模型/工具节点前后都检查，读取走**独立短事务**（否则请求 session 的 identity map 会读到过期的 `running`）。
- 删除会话：同时清理业务表、三本台账与 checkpoint thread，不留可恢复的孤儿 checkpoint。

## 7. 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `LG_CHECKPOINT_PATH` | 业务库同目录 `agent_checkpoints.sqlite` | checkpoint 独立文件，避免与 SQLAlchemy 争锁 |
| `LG_MAX_MODEL_CALLS` | `12` | 单轮真实模型调用次数上限；与 `recursion_limit` 不是同一指标 |
| `AGENT_RUN_BUDGET_SECONDS` | `180` | 整轮时限（沿用原语义） |
| `LLM_TIMEOUT` | `120` | 单次模型请求总时限，由 `app/model.py` 执行，**未改动** |

## 8. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v2/conversations` | 不传 `mode` 时默认 `langgraph_autonomous_v1` |
| `POST` | `/api/v2/conversations/{id}/messages` | 同一入口；按会话的 runner 版本分流 |
| `GET` | `/api/v2/conversations/{id}/runtime` | 脱敏运行视图（状态、停止原因、usage 汇总） |
| `GET` | `/api/v2/runs/{id}/events/stream?after_seq=N` | SSE 事件流；每条事件以索引作为 SSE id，重连不重复 |

`guided` / `autonomous` 仍然可选，旧会话保持各自原版本，不被静默改写。

### 阻塞与异步

`messages` 新增可选字段 `response_mode`：

- `blocking`（默认）：与旧行为一致，直接返回最终 payload，现有前端不受影响。
- `async`：短事务提交后返回 `202`，body 为 `{run_id, conversation_id, runner_version, status, events_url}`；
  由持有**独立 Session** 的 worker 执行。因为 run 在任何模型调用之前就已提交，另一个连接立刻能看到它处于
  `running`，此时取消也能被真正观察到。

异步只在 LangGraph runner 下生效；未知取值一律回退 `blocking`。

### 重启后的孤儿运行

进程重启会丢掉内存中的 worker。启动时会把仍是 `running` 的运行改为
`needs_reconciliation`，`stop_reason=orphaned_by_restart`。这类运行的上游结果未知，
既不自动重试，也不记为成功。

## 8.1 时限与断连（实测后加固）

- **总时限覆盖连接+响应头+响应体。** `urllib` 的 `timeout` 是单次 socket 操作超时，网关在窗口内滴流一个字节就能让它永不过期；而原来的响应体读取只在响应头到达之后才开始，所以连接与响应头阶段完全没有时限。实测曾出现 `LLM_TIMEOUT=120` 下挂起 **527 秒**。现在 `app/model.py` 用 `http.client` + 看门狗，在到期时 `shutdown(SHUT_RDWR)` 切断连接（只 `close()` 无法唤醒阻塞读）。
- 调用侧另有一道上限（`asyncio.wait`，不是 `wait_for`：后者会等被取消的线程结束），它约束**异步 worker 路径**；阻塞 HTTP 路径由传输层时限约束，因为 `BaseHTTPMiddleware` 的 anyio task group 在退出时会等待被放弃的任务。
- 客户端断开由 `CancelledError` 处理（它是 `BaseException`，`except Exception` 抓不到），运行会落到 `needs_reconciliation / client_disconnected`，不再永久停在 `running`。
- 注意：被放弃的阻塞线程会让该事件循环的关闭等待其结束；因为传输层时限已经生效，这个等待是有界的。

## 8.2 内容围栏（`app/runtime/content_policy.py`）

确定性检查，不依赖提示词自觉：

- **第一人称使用叙述**（"亲测/我用了/背了三年"等）→ 标记 `unverified_experience`，状态降为 `partial`，并追加"属于未核实的创作表达，不能作为真实体验发布"。
- **无证据时的产品数字声明** → 标记 `unverified_numbers`，状态降为 `partial`，并追加"知识库当前不可用，以上数字未经核实"。
- 已知假阳性：模型**拒绝**写某个数字时（"64GB 这个数字我不能写"）该数字也会被标记。这是偏保守的方向——不放过，但会带来噪音，后续可区分"断言"与"拒绝"。

## 9. 验证状态

已在本机 Python 3.12 隔离环境验证：**229 项测试通过**（180 项基线 legacy 合同 + 49 项新增/扩展测试），且
全量连跑多次稳定；`node --check` 通过；真实 uvicorn 冒烟通过（新会话默认 LG、验重 200/409、异步 202 +
worker 执行到终态、SSE 事件流、删除清空 checkpoint、重启孤儿恢复）。更新后的 `requirements.lock` 在一个全新 venv 里
从零安装后同样全部通过。

**未验证**：真实 DeepSeek 网关的原生工具多轮往返、官方知识库 ZIP / ima 真实业务任务、跨进程崩溃恢复、
ASGI 多 worker、Linux/魔搭部署。这些属于执行者
必须如实标记的阻塞项，本文不声称已达标。真实模型 API 请求数为 **0**。