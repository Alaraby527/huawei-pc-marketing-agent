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
- **无证据时的产品数字声明** → 标记 `unverified_numbers`，状态降为 `partial`，并追加"本轮没有读取产品事实，以上数字未经核实"（不写"知识库不可用"——知识库可能是好的，只是本轮没读事实）。
- 已知假阳性：模型**拒绝**写某个数字时（"64GB 这个数字我不能写"）该数字也会被标记。这是偏保守的方向——不放过，但会带来噪音，后续可区分"断言"与"拒绝"。

## 8.3 两条选型路径

知识库里本来就有人群与场景结论（`P1-产品参数库/06-型号检索卡/<型号>/06-适用人群与限制卡.md`）
和 14 个跨型号字段矩阵，但适配器此前只索引 `产品事实.md` 与 6 张场景卡，这两类数据模型看不到。

| 路径 | 触发 | 做法 |
|---|---|---|
| A：用户点名型号 | “给 MateBook 14 写”“换成 Fold” | `read_product_fit` 取人群/场景/限制，`read_product_evidence` 取事实，**不反问用户适用人群**，引用时保留限定条件 |
| B：需求不明确（常态） | “想买个笔记本，不知道选哪个” | 只问真正会改变选型的信息 → `search_products` + `read_field_matrix` 匹配 → 给 2–4 款真实候选 → **等用户选定** → 结合已收集的人群/场景信息生成推文 |

这是默认顺序而非固定流程：用户已把需求与型号讲清楚就直接产出；换型号、改页数、只改某页都按新指令走。
`search_products` 的结果里带一句动作时提醒（未点名型号时先让用户选），因为实测中模型曾直接选一款就开始写整篇。

## 8.4 上下文成本与工具结果上限

- `read_product_fit` 只返回属于它的小节（检索结论/限定条件/适用任务/不适用/待核验项），
  不含“已确认事实”（与 `read_product_evidence` 重复）：单次调用从约 7.1K 字符降到约 600 字符。
- 回灌给模型的工具结果上限 12000 字符，超出时**显式标注截断**并给出原文字符数。
- 首轮提示词成本：系统提示词约 1355 字符（≈677 tokens）+ 12 个工具定义约 2913 字符（≈971 tokens）。

## 8.5 交付态、独立审校与影子查重

**交付态与任务态分开。** `completed` 只表示本轮机器检查通过，不等于人工批准，文字模块也不发布：

| run status | content_status | 含义 |
|---|---|---|
| completed（审校已跑、0 问题） | `review_ready` | 机器流程通过，可进入人工审核 |
| completed（审校记录到问题） | `review_required` | 有建议性问题，交付但需人看 |
| partial | `research_draft` | 未通过或被标记，不可当作定稿 |
| 审校**未跑成**（预算/超时/模型错） | `research_draft` | 未执行的检查绝不写成"通过" |

任何情况下 `can_publish` 都是 `false`。

**独立审校节点**（图：`finalize → review → END`）：上下文独立——只给"待交付文案 + 本轮允许使用的事实与条件"，不给作者的对话与自我评价；核验对象是**全文**，不只是作者标注过的主张。阻断级（`high`）问题或 `block/needs_input/human_review/choose_new_plan` 结论会把本轮降为 `partial` 并追加诚实提示；建议性问题只记录、不阻断交付。审校调用与写作调用一样计入台账与预算。

**影子查重**（`app/runtime/dedup.py`）：

- 规范化视图（NFKC、去空白与标点）+ 全文 sha256（精确重复）+ 64 置换 MinHash（近似重复，4-gram shingle，上限 4000）。
- 在**交付**时登记（不是发布时），同一工作区内比较；**不应用任何阈值、不阻断、不改状态**——未校准的分数不能做决定。
- 明确未做：语义/向量视图、跨门店授权范围、阈值校准。因此**不能**把它描述为品牌级去重。

## 8.6 最终准入（T15）与 SQLite 并发边界

**要解决的问题**：查重证据如果在锁外读取，两个并发 run 可能都读到"没有对方"的历史而一起通过。

**做法**（`app/runtime/admission.py`）：

1. 指纹在**锁外**计算（锁内绝不调模型/嵌入/网络）；
2. 取一个作用域短锁；
3. **在锁内重读已提交历史**——不用锁前快照；
4. 同一事务内决定并登记；
5. 提交即释放锁。

SQLite 用 `BEGIN IMMEDIATE`；Postgres 按方案用 `pg_advisory_xact_lock`——**Postgres 分支在本环境没有执行过**（无 PG/无 Docker），不作为已验证能力。`LG_ADMISSION_LOCK_TIMEOUT=2`、`LG_ADMISSION_RECHECKS=2`。

**真实并发实测**（三个相同请求同时发出）：三个都 HTTP 200、终态、有真实回复；`locked=True`、`attempts=1`、等待 0–2ms；`compared` 分别为 4/5/6（逐个递增，证明每次都读到了其它并发交付）；`stale=False`、无超时、无登记丢失。

**必须说清的边界**：

- **作用域是单工作区**，方案要求的授权跨门店范围（`brand_content_scope_id`）未实现，不得称为品牌级去重。
- **SQLite 只允许一个写者**，且是文件级串行化，不是按作用域。因此这是单实例试点能力，**不是容量结论**。
- 锁拿不到时结论是 `unverified`（**绝不写成通过**），并尽力登记；若登记也失败，会明确返回 `register_error`，不静默丢记忆。

**本轮并发实测还暴露并修掉了三个缺陷**（详见 CHANGELOG）：SQLite 写冲突导致 HTTP 500 且 run 卡在 `running`；图把存储异常当成"失败的模型决策"吞掉；终态写入失败却返回 `200 + status:running + 空回复`。修法：连接加忙等待并启用 WAL、存储异常向上抛、处理器校验终态、模型调用与工具执行后立即提交（把原来长达约 60 秒的写事务切成短事务）。

## 8.7 阈值校准（Phase D）：工具就绪，校准未完成

方案要求"语义阈值必须人工标注校准""不采用通用 0.8""不允许以空阈值冒充语义去重已完成"。

**已做**（`app/runtime/calibration.py` + `scripts/calibrate_dedup.py`）：

- 按标注样本算混淆矩阵与四项指标；**未定义的分母返回 `None`，不打印成 0.0**；
- 只有标注样本才给阈值；没有标注就明确拒绝（`no_labelled_pairs`）；
- 报告**两类样本的可分离性与间隔**——样本完全可分离时，推荐值只是网格下限，会被标为 `provisional` 并说明"不能当作校准结论"；
- `--sheet` 从真实交付抽候选对（JSONL，`label` 留空）；`--constructed` 用构造式真值校准字面层（方案的 Q10–Q14）。

**当前实测**：

| 范围 | 交付数 | 候选对 | 最高相似度 |
|---|---|---|---|
| 只看交付物（默认） | 17 | 136 | **0.125** |
| 含对话性回复 | 43 | 903 | 0.2656 |

**结论：真实语料里没有重复正例**（最高字面相似度仅 0.125），因此**无法从真实数据校准阈值**——工具如实报告，不臆造数字。要推进需要：① 人工标注候选对；② 规模使用后出现的真实重复样本；③ 语义层（嵌入模型 + 独立索引版本）尚未实现，因此**不得声称语义去重已完成**。

**另修一个真实问题**：对话性回复（"你好，我可以帮你…"）此前与成稿一样被登记进内容记忆，污染了历史与校准样本；清单默认只取有交付物的内容。

## 8.8 持久化租约、心跳与崩溃续跑（T16）

**为什么需要**：进程内的会话锁只是**本地租约**——对第二个 worker 无意义，重启后也无意义，所以"崩溃"和"还在跑"无法区分，重启时只能对每个 `running` 一律按最坏情况处理。

| 状态 | 含义 |
|---|---|
| `held`（未到期） | 某 worker 持有，他人不得触碰 |
| `held` 但**已过 `expires_at`** | 持有者停止心跳 → 确实被遗弃，可在尝试上限内接管 |
| `released` | 持有者正常结束并声明 |

- **接管是原子的**：单条条件 `UPDATE ... RETURNING`，两个 worker 争抢同一个遗弃 run 时只有一个能成。
- **尝试有界**：`LG_RUN_MAX_ATTEMPTS`（默认 2），反复失败的 run 会结束而不是无限重试。
- **心跳**：独立会话按 `LG_LEASE_TTL_SECONDS/3` 续租，长任务不会被误判为死亡。
- **释放**：`finally` 中执行；先提交 run 自身会话，否则第二个连接会等满 SQLite 忙等待（这正是全套从 49 秒变成 190 秒的原因）。

**崩溃续跑**（`POST /api/v2/runs/{run_id}/resume`）：从自己的 checkpoint 继续，**不需要用户重新输入**——这正是"上游断连就白跑一轮"的解法。会在三种情况下拒绝并说明原因：没有可续跑进度、正在等用户回答（应直接回答）、尝试次数已用尽。`run_payload` 给出 `resumable` 提示，权威判断在续跑时做。

**孤儿恢复已改为租约感知**：另一个活着的 worker 持有的 run **不动**；过期租约或**完全没有租约的历史行**才恢复为 `needs_reconciliation`。

## 8.9 语义层（T13）

字面层看不到"同义改写"——这是它的结构性缺陷，不是参数问题。**用线上嵌入接口实测**：

| 对象 | 字面层 (MinHash) | 语义层（嵌入） |
|---|---|---|
| **同义改写** | **0.0000（完全漏掉）** | **0.8689（抓到）** |
| 无关内容 | 0.0000 | 0.5067 |
| 同机型不同角度 | 0.0000 | 0.4886 |

这张表同时说明**方案为什么禁用通用 0.8**：任意两段中文营销文本的语义基线约 0.5，正负例间隔只有约 0.36，**必须用真实负例校准**，不能套用经验阈值。

**索引按方案要求记账**：模型 ID、维度、视图、以及标识向量空间的 `index_version`（`emb-v1:<model>:<dim>`）；**不同版本的向量永不互相比较**（跨模型的距离没有意义）。检索是**全量余弦扫描**——方案首选的精确基准；因此**没有引入近似索引，也就没有召回率需要监控**。

**执行顺序**：嵌入在**准入锁外**计算，锁内只做纯比对（方案明确禁止持锁调用模型/嵌入/网络）。嵌入失败会被记为"该层未运行"，**绝不写成通过**。

`LG_EMBEDDING_MODE=shadow` 显式开启（默认关闭：每次交付都是付费调用，且测试不得触网）。

## 8.10 真实标注与实测工作点

`scripts/label_pairs.py` 对**最相似的前若干组**做首轮判定（低相似度样本对阈值无信息量，不浪费调用），并在每行标注 `label_source=ai_first_pass`、`confirmed_by_human=false`。校准工具在标签未经人工确认时**拒绝下结论**——用被测系统自己的标签去启用拦截，等于自己给自己判卷。

**23 组真实标注的实测**：

| 层 | 重复 (n=7) | 不同 (n=16) | 可分性 |
|---|---|---|---|
| 字面层 | 0.047–0.109 | 0.047–0.125 | **完全交织，零判别力** |
| 语义层 | 0.721–0.910（中位 0.822） | 0.389–0.833（中位 0.664） | 仍重叠，仅 1 组负例高于最低正例 |

语义层扫描（误杀预算 10%）：

| 阈值 | 精确率 | 召回率 | 误杀率 | 漏检率 |
|---|---|---|---|---|
| 0.70 | 58% | 100% | 31% | 0% |
| **0.75** | **86%** | **86%** | **6%** | 14% |
| 0.80 | 80% | 57% | 6% | 43% |
| 0.85 | 100% | 43% | 0% | 57% |

**实测工作点 0.75（精确率 86% / 召回率 86%）**，且两类重叠（`gap = -0.11`）——不存在完美阈值。

这也**修正了我此前用构造式样本得出的"0.5 完美分离"结论**：真实数据难得多。这正说明方案为什么要求人工标注真实样本，而不是方便构造的样本。

**阈值仍未启用**；启用需要人工确认标签。

## 9. 验证状态

已在本机 Python 3.12 隔离环境验证：**229 项测试通过**（180 项基线 legacy 合同 + 49 项新增/扩展测试），且
全量连跑多次稳定；`node --check` 通过；真实 uvicorn 冒烟通过（新会话默认 LG、验重 200/409、异步 202 +
worker 执行到终态、SSE 事件流、删除清空 checkpoint、重启孤儿恢复）。更新后的 `requirements.lock` 在一个全新 venv 里
从零安装后同样全部通过。

**未验证**：真实 DeepSeek 网关的原生工具多轮往返、官方知识库 ZIP / ima 真实业务任务、跨进程崩溃恢复、
ASGI 多 worker、Linux/魔搭部署。这些属于执行者
必须如实标记的阻塞项，本文不声称已达标。真实模型 API 请求数为 **0**。