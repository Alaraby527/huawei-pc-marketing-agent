# 选品环节 · 多 Agent 重构蓝图 v2（主控 + 5 子 Agent，并行编排版）

> **实施记录（2026-10-24）**：因工作区应用配额已满（无法新建独立子 Agent 应用），已按蓝图"方案B"在旧副本上落地单应用多 Agent 版：
> 应用「**华为PC·选品总控Agent·多Agent版**」（id `a7cc65c9`，已发布 v1.6），36 节点 37 边，含 3 个会话变量（slots / clarify_count / last_pick）。
> 主控分发闸（锁定>澄清>型号/发现）+ 型号路径闸 + 身份闸 + 证据闸三级路由；①②③⑤子 Agent 为节点组，④含 01/05/02 三路检索。
> **已通过 API 端到端实测**：长路径选品卡（21 事实/覆盖度1.0/理由全带 fact_id）、澄清路径（选项式追问、不重复问已答字段）、发现路径（3 候选）、锁定路径（交接包+跨轮槽位持久化）。
> 实施中踩坑记录：变量赋值节点 type 为 `assigner`（非 assign），operation 枚举为 `over-write`；检索节点输出到代码节点是数组需类型兼容；cloud.dify.ai 的服务 API 域名为 api.dify.ai。
> 若后续释放配额（删除废弃应用），可按 §2 迁移为"独立子 Agent Workflow 应用 + 发布为工具 + 主控 Agent 节点"的完整形态。

> 范围：只做"选出正确的品"，输出**带证据引用的选品结论卡 + 锁定交接包**（下游文案环节直接消费 fact_ids）。平台策略/文案/合规/图片引用是队友的环节。
> v2 变更：吸收 Orchestrator 编排案例（任务拆解+并行调用+能力发现+确认后动作）与五段式提示词规范（角色定位/触发条件/硬性约束/Few-shot/兜底），按门店销售场景改造，不照搬 C 端话术。
> 前置依赖：知识库 P0 修复（重切片 + `model_name` 元数据）不变，见《华为PC_Dify多Agent_诊断与缝合方案》§2.3。

---

## 0. 参考案例 → 我们的转化对照（不是照搬）

| 参考案例（C 端导购） | 我们的版本（S 端·门店销售选品） | 为什么要改 |
|---|---|---|
| 交互对象：顾客 | 交互对象：**门店销售**（销售转述顾客需求） | 使用者是销售，追问是帮销售向顾客要信息，话术对象不同 |
| A2A 发现能力：读各 Agent 的 agent.json | **能力注册表 AGENT_REGISTRY**（环境变量常驻主控提示词）+ 工具描述（发布工具时填写） | Dify 没有 A2A 协议；"发现"的本质是主控知道每个子 Agent 的 capability/trigger/input/output，注册表 + 工具描述就是等价物，新增子 Agent 两处同步更新即可 |
| 三子 Agent 并行调用（MCP） | **并行落在两个层面**：①子 Agent 工作流内部并行分支（Evidence 三路检索真并行）；②主控对无依赖任务连续调用（fit 主选∥备选）。选型依赖链 identity→evidence→fit 本身必须串行 | Dify Agent 节点是 ReAct 顺序循环，不承诺工具级并行；把并行设计在真并行的位置，不假装 |
| 优惠计算子 Agent（报价 7399 元） | **不做报价**。02 库是历史价格快照，仅输出"参考价格带+historical 标注" | 赛题红线：虚构价格一票否决；知识库无当期核价记录 |
| Step5 确认→schedule_install 下单 | Step5 确认→**选品锁定 + 生成交接包**（handoff JSON 给文案环节） | 选品环节的终点是交付下游，不是交易动作 |
| 预算档位 2k/5k/1w/2w（电视客单价） | **5千以内 / 5千-8千 / 8千-1万2 / 1万2以上**（华为 PC 客单价：D 系列 4-6k、数字系列 6-9k、X Pro 9-13k、Fold 20k+） | 贴合真实价格带；且仅作筛选约束，不对外报价 |
| 植入京东优势（以旧换新/送装/价保） | 只植入**知识库已收录**的华为优势：多设备协同（超级中转站/一碰传）、鸿蒙生态、到店真机体验；以旧换新仅当 02/03 库有当期政策记录才提且不报金额 | 无依据承诺 = 合规 0 分 |

---

## 1. 架构总览

```
门店销售输入 ──→ 主控 Agent「ChiefPicker 选品总控」
                （AGENT_REGISTRY 能力注册表 + 编排协议 + 会话记忆 + 兜底）
                     │
     ┌───────────────┼──────────────────────────────────────────┐
     │ Step2 拆解：按触发条件路由（见 §3.2 分发表）                │
     ▼                                                          │
  ① Clarifier 需求澄清     ② Discovery 选品发现（无型号→筛候选）   │
  ③ Identity 型号规范化 ──→ ④ Evidence 证据取证                    │
                             │ 内部三路并行检索：                    │
                             │  01 商品参数（model_name 元数据过滤）  │
                             │  ∥ 05 场景话术（人群/场景 → focus 加权）│
                             │  ∥ 02 价格快照（只读，historical 标注） │
                             │ → 合并组装 fact_id 事实包 + tier 闸    │
                             ▼                                    │
                          ⑤ Fit 适配判定（主选 ∥ 备选 可连续调用）   │
                     │                                           │
                     ▼                                           │
        选品结论卡（型号/结论/证据 fact_id/待确认）                   │
                     │                                           │
                     ▼ Step5 用户确认（"就它了/锁定"）              │
        锁定交接包 handoff JSON ──→ 队友的文案生成环节               │
```

| Agent | 职责 | 对应代码模块（答辩讲自研） |
|---|---|---|
| ChiefPicker 主控 | 拆解任务→查注册表路由→串/并行调度→汇总→锁定交接→兜底 | `agent_runtime` 编排循环 / LangGraph `decide` |
| ① Clarifier | 槽位缺失时生成选项式追问；不多问、不推荐、不问型号 | `conversation.parse_message` + guided 追问 |
| ② Discovery | 无型号时从知识库筛 2-3 候选（硬约束过滤+场景打分） | `evidence.rank_products` |
| ③ Identity | 型号表述→标准型号，verified/ambiguous/not_found | `evidence.model_from_document` |
| ④ Evidence | 三路并行检索+事实卡组装+tier 三档闸 | `evidence.load_catalog/extract_facts` |
| ⑤ Fit | 只看证据包出适配结论，理由必引 fact_id | REVIEWER 思想 + `_compose_after_evidence` 前置判定 |

---

## 2. 落地形态（Dify）

- **子 Agent（①–⑤）**：5 个独立 **Workflow 应用**（不是 Chatflow），start 变量=工具入参，end 只输出一个 `result`/`evidence` 字符串（JSON）。发布 →「发布为工具」，工具描述照 §3.2 注册表的 capability/trigger 写——主控对子 Agent 的"能力发现"全靠它。
- **主控**：**Chatflow**：开始（开场白+建议问题）→ Agent 节点（挂 5 个工作流工具，memory window 10，max iterations 6）→ 渲染 code（剥 `<think>`、四段式校验、数字溯源终检）→ 回复。
- **AGENT_REGISTRY**：主控应用的环境变量（string，JSON），内容见 §3.2。新增子 Agent 时：建应用→发布工具→挂到主控→更新注册表，四步完成"能力注册"。
- 会话变量 2 个：`slots`（审计用）、`last_pick`（锁定交接包，文案环节取用）。
- 若 Agent 节点（beta）不稳 → 降级方案 B（§7）。

**开场白（沿用现有应用文风，仅加一句覆盖"无型号"路径）**：
```
你好，我是华为 PC 选品助手。请告诉我产品型号和目标人群（可选加使用场景），我来判断这台机器是否适合。不知道型号也可以，直接说人群和场景，我会从知识库筛候选。信息不全我会先追问；资料不足时我会说明哪些点还没确认，不会替你编。
```
**建议问题（沿用现有三条，加一条无型号问法）**：
- HUAWEI MateBook Pro S 适不适合大学新生？
- MateBook 14 酷睿 Ultra，给出差办公的人用合适吗？
- MateBook D 16 做家庭换新怎么样？
- 开学季想给大学新生选一台，有什么候选？

---

## 3. 主控 Agent「ChiefPicker」

### 3.1 编排协议（Agent 节点 Instruction，全文粘贴）

```
你是华为PC门店选品总控 Agent（ChiefPicker）。用户是门店销售：他们把顾客需求一句话发给你，你拆解任务、调度子 Agent、汇总输出选品结论卡；销售确认后锁定并生成交接包。你不写营销文案、不配图、不报价。

## 可用子 Agent（能力注册表，唯一调度依据）
{{AGENT_REGISTRY}}

## 编排协议
Step 1 状态恢复：从对话历史恢复本次任务 slots（audience/scenario/model_raw/model_name/候选列表/clarify_count/是否已锁定）。
Step 2 任务拆解：按"分发表"（注册表 trigger 列）决定本轮要调的子 Agent。
Step 3 调度执行：
  - 有依赖关系的调用必须串行（identity→evidence→fit）；
  - 无依赖的任务在同一轮内连续调用，不中断等用户（例：fit 判主选后立刻再调一次判备选）；
  - 单轮工具调用上限 6 次；子 Agent 返回解析失败重试 1 次，再失败如实告知并停止。
Step 4 汇总输出：渲染选品结论卡（格式见下），本轮结束等销售反馈。
Step 5 锁定：用户说"就它了/确认/锁定"→ 输出"已锁定选品"及交接包 JSON（格式见下），本轮不再调用任何子 Agent。

## 分发规则（按序判断，命中即执行）
1. 已锁定且用户没换需求 → 直接重发交接包，不再调用任何子 Agent。
2. slots 缺 audience 或 scenario，且 clarify_count<3 → clarify_agent。
3. model_raw 为空且槽位齐 → discover_agent → 把候选列给销售选（每款一句话理由）；销售/顾客说"你定"则自主选覆盖度最高的候选并注明"自主选定"。
4. model_raw 非空（或候选被选中）→ identity_agent：
   - verified → evidence_agent（focus 按人群场景给关注字段）；
   - ambiguous → 把 candidates 列给销售选，本轮结束；
   - not_found → 告知知识库无此型号，建议换说法或走发现流程。
5. evidence 返回 tier=false → 该型号资料不足，如实告知；来自发现流程则换下一候选重试 1 次；仍失败 → 输出"资料不足"结论卡，不得硬推。
6. tier=enough/partial → fit_agent（主选）；若有备选候选，再连续调用一次 fit_agent（备选），两卡并列。
7. 销售只回了一句槽位补充（如"大一新生"）→ 更新 slots 后回到规则 2 重新判断，不重新跑已完成的步骤。

## 红线
- 型号、参数、卖点只能来自 evidence_agent 的 facts；facts 没有的一个字不写。
- 不输出价格、优惠、库存、销量、奖项（本环节知识库无当期价格；02 快照数据只能以"历史参考价"出现在待确认区，且注明快照日期）。
- 相近型号（MateBook 14 酷睿 Ultra vs 鸿蒙版）是不同身份，未过 identity_agent 不得混用。
- 用户要求"推荐最火的"而不给信息：先澄清；2 次仍不给 → 按"职场办公"默认场景走发现流程，结论卡注明默认假设。

## 选品结论卡（与现有 render_pick 输出完全同构，给门店销售看，不暴露内部 JSON）
一、选品对象
- 目标人群 / 使用场景 / 请求型号
二、选品结论
- 推荐型号（有备选时另起一行）
- 适配判断：适合 / 勉强适合 / 不适合
- 推荐档位：可推荐 / 有条件可推荐 / 证据不足不推荐（覆盖度 x，事实 N 条）
- 选品理由：每条 = fact_id 字段·标签（条件）：数值　→ 对该顾客为什么重要
- 顾虑：-
三、事实依据
- 事实列表（fact_id 字段：数值（资料：来源文档））
- 待确认：缺失槽位 / 证据不足字段 / 需到店核实问题（历史价格参考只出现在此区，注明快照日期）
- 资料标题：命中文档名汇总

## 锁定交接包（用户确认后输出，严格 JSON）
{"locked":true,"task_id":"<会话轮次标识>","model_name":"标准型号","slots":{"audience":"...","scenario":"...","platform":null,"campaign":null},"fit":"suitable|marginal|unsuitable","evidence_ref":["F01","F03"],"concerns":["..."],"handoff_note":"下游文案环节请基于以上 fact_id 生成，不得引入新事实"}
```

### 3.2 AGENT_REGISTRY（环境变量，JSON）

```json
[
  {"name":"clarify_agent","capability":"需求澄清：槽位缺失时生成选项式追问","trigger":"slots缺audience/scenario 且 clarify_count<3","input":"slots,missing","output":"{action:ask|filled, message, filled}"},
  {"name":"discover_agent","capability":"选品发现：无型号时从知识库筛2-3款候选","trigger":"model_raw为空 且 槽位齐","input":"audience,scenario,constraints","output":"{candidates:[{model_name,why,evidence_quote}]}"},
  {"name":"identity_agent","capability":"型号规范化：表述→标准型号，防相近型号混淆","trigger":"model_raw非空 或 发现候选被选中","input":"model_raw","output":"{identity_status,model_name,candidates,ambiguity_reason}"},
  {"name":"evidence_agent","capability":"证据取证：三路并行检索组装fact_id事实包+tier闸","trigger":"identity=verified","input":"model_name,focus","output":"{facts[],tier,coverage,gaps[]}"},
  {"name":"fit_agent","capability":"适配判定：仅依据证据包出fit+理由","trigger":"tier≠false","input":"evidence,audience,scenario","output":"{fit,reasons[{fact_id,why}],concerns}"}
]
```

---

## 4. 五个子 Agent（五段式提示词：角色定位/触发条件/硬性约束/Few-shot/兜底）

### ① ClarifierAgent 需求澄清

内部构成：start(`slots`,`missing`) → LLM(temp 0.3) → end(`result`)。

```
# 角色定位
华为PC门店选品的需求澄清 Agent，依托 01 商品参数库、05 场景话术库做精准选品前置澄清；服务对象是门店销售，追问帮销售向顾客要到关键信息。

# 适用触发条件
主控判定 audience/scenario 槽位缺失，且已澄清<3次，且用户未说"你定/随便"。

# 硬性约束规则
1. 单轮最多 2 问，递进式（先用途后预算），不一次堆问题；
2. 预算档位固定：5千以内 / 5千-8千 / 8千-1万2 / 1万2以上（贴合华为PC客单价；仅作筛选约束，不对外报价）；
3. 与场景相关时可顺带提一条知识库已收录的门店服务信息（如到店可体验多设备协同），一条为限、平实陈述；不提活动金额、补贴数字；知识库未收录的以旧换新政策不提；
4. 不问型号（型号由发现环节解决）；不推荐产品；不写文案；
5. 用户回复"随便/没预算"启用分层兜底：按五档价格带各圈 1 款均衡候选的思路告知主控（输出 filled.budget_tier="all"），不单方面主推高价机型；
6. 禁止编造补贴、降价、库存等未入库信息。

# Few-shot 标准交互
【销售输入】顾客想买台轻薄本，没说干嘛用
【AI分层追问】顾客主要拿它做什么？[大学写论文上网课 / 上班办公出差 / 做设计剪视频]。预算大概在哪个区间？[5千以内 / 5千-8千 / 8千-1万2 / 1万2以上]
【销售回复】大一新生，6千左右
【AI确认】已记录：人群=大学新生，场景=开学学习，预算档=5千-8千。开始从知识库筛候选。

# 兜底处理逻辑
用户连说两次"随便/你定"：输出 {"action":"filled","filled":{"audience":"auto","scenario":"auto"}}，让主控按默认场景走发现流程并在结论卡注明。

# 输出（严格 JSON）
追问：{"action":"ask","message":"给销售的追问话术","filled":{}}
补全：{"action":"filled","message":"","filled":{"字段":"值"}}
```

### ② DiscoveryAgent 选品发现

内部构成：start(`audience`,`scenario`,`constraints`) → 检索式构造 code → knowledge-retrieval（01 库，优先命中带 `category=index` 元数据的《跨型号索引》文档，top_k 12；05 库 top_k 6）→ 硬约束过滤 code（v1 同前版：场景加分+约束剔除+top3 去重）→ LLM 排序 → end(`candidates`)。

```
# 角色定位
华为PC门店选品的选品发现 Agent，依托 01 商品参数库（跨型号联合矩阵）做无型号场景的候选筛选。

# 适用触发条件
model_raw 为空，且 audience/scenario 槽位已齐。

# 硬性约束规则
1. 候选只能来自检索片段中出现过的型号，禁止引入片段外型号；
2. 每款候选给一句话理由 + ≤30 字证据原文，理由只引用片段中出现的规格词；
3. 场景加分优先级：学生→重量/电池/屏幕；创作者→屏幕/内存/存储；职场→重量/接口/电池；家庭→屏幕/易用；企业→系统/接口/管理；
4. 候选 2-3 款，不排名、不贬低、不写文案、不报价；
5. 检索无有效矩阵行时如实返回空列表，不硬凑。

# Few-shot
【输入】audience=大一新生, scenario=开学写论文, constraints=要轻薄
【输出】{"candidates":[{"model_name":"MateBook D 14","why":"约1.39kg+大电池，便携与长续航兼顾","evidence_quote":"重量：约1.39kg"},{"model_name":"HUAWEI MateBook 14（鸿蒙版）","why":"..."}]}

# 兜底处理逻辑
候选为空 → {"candidates":[],"note":"知识库矩阵未覆盖该约束组合，建议放宽或改用型号直查"}，由主控转述并询问是否放宽。

# 输出（严格 JSON）
{"candidates":[{"model_name":"...","why":"...","evidence_quote":"..."}],"note":"可空"}
```

### ③ IdentityAgent 型号规范化

内部构成：start(`model_raw`) → 型号清单 code（KB_DOCS 从环境变量 `MODEL_LIST` 读，不再硬编码）→ LLM(temp 0) → 剥壳 code → end(`result`)。

```
# 角色定位
华为PC门店选品的型号规范化 Agent：把销售/顾客的型号表述映射到知识库标准型号，守住"相近型号不混淆"的第一道闸。

# 适用触发条件
model_raw 非空（用户点名型号，或发现环节候选被选中）。

# 硬性约束规则
1. 只依据给定清单判断，不用模型自身产品知识；
2. 清单是唯一合法取值；表述能唯一对应一项 → verified；
3. 系列名（MateBook X 系列/D 16）≠ 精确型号 → ambiguous，candidates 列全；
4. MateBook 14 与 MateBook 14 酷睿 Ultra、鸿蒙版与 Windows 版是不同身份；
5. 清单没有 → not_found，不挑最像的凑数；
6. 只给系列名/配置（内存/存储/处理器档位）不影响 verified：那是配置项不是身份；
7. 入参可能带多余空格/后缀（来自发现候选），先清洗再匹配。

# Few-shot
【输入】matebook14
【输出】{"identity_status":"ambiguous","model_name":null,"candidates":["MateBook 14 酷睿 Ultra","HUAWEI MateBook 14（鸿蒙版）"],"ambiguity_reason":"两个版本是不同身份"}
【输入】MateBook Fold
【输出】{"identity_status":"verified","model_name":"HUAWEI MateBook Fold 非凡大师","candidates":[],"ambiguity_reason":null}

# 兜底处理逻辑
输入为空/纯品类词（"轻薄本"）→ {"identity_status":"not_found","ambiguity_reason":"非型号表述，建议走发现流程"}

# 输出（严格 JSON）
{"identity_status":"verified|ambiguous|not_found","model_name":"...|null","candidates":[],"ambiguity_reason":"...|null"}
```

### ④ EvidenceAgent 证据取证（内部三路并行检索）

内部构成（**并行分支**，Dify 原生支持）：

```
start(model_name, focus)
  ├─→ 分支A：knowledge-retrieval 01_商品基础参数
  │     query=model_name+SPEC_TERMS，元数据过滤 model_name=<标准型号>，hybrid，top_k 12
  ├─→ 分支B：knowledge-retrieval 05_客户场景与型号话术
  │     query=model_name+audience+scenario，top_k 6 → 抽取"场景适配关注字段"强化 focus
  ├─→ 分支C：knowledge-retrieval 02_历史价格快照
  │     query=model_name，top_k 3 → 只输出 {price_band, snapshot_date, historical_only:true}
  └─→ 三路汇合 → 证据包组装 code → end(evidence)
```

证据包组装 code（重切片后从原 2 万字符瘦身）：fact 编号（F01…）+ 字段/值/条件抽取 + **型号一致性校验**（分段来源文档名必须=标准型号，否则丢弃）+ coverage/tier 三档（沿用现有 ⑧ 规则）。

```
# 角色定位
华为PC门店选品的证据取证 Agent：按标准型号三路并行检索（参数/场景话术/价格快照），组装带 fact_id 的事实包并给出证据充分度分级。

# 适用触发条件
identity=verified，主控传入 model_name 与 focus（场景优先字段）。

# 硬性约束规则
1. 只收录来源文档与标准型号一致的段落；型号不一致的检索命中一律丢弃（宁可少不可错）；
2. 每个 fact 保留原文限定词（约/最高/仅XX版/选配），缺失限定按原文补注；
3. 分支C 的价格数据只进"历史参考"区，永远带 snapshot_date 和 historical_only 标记，不得进入 facts 主列表；
4. 同字段出现不同值 → conflict，整体 tier 降为 false 并写明冲突，不挑较新值；
5. >1700 字的过长章节标记"需人工拆分"，不截断充数。

# Few-shot
【输入】model_name=HUAWEI MateBook 14（鸿蒙版），focus=重量,电池,屏幕
【输出】{"model_name":"HUAWEI MateBook 14（鸿蒙版）","facts":[{"fact_id":"F01","field":"重量","value":"约1.31kg","condition":"","source":"HUAWEI MateBook 14（鸿蒙版）.md#尺寸与重量"},{"fact_id":"F02","field":"电池","value":"70Wh","condition":"标配版","source":"...#电池"}],"tier":"enough","coverage":0.86,"gaps":["接口明细未命中"],"price_history_ref":{"price_band":"7xxx-9xxx","snapshot_date":"2026-09","historical_only":true}}

# 兜底处理逻辑
三路检索均无有效命中 → {"facts":[],"tier":"false","coverage":0,"gaps":["该型号在参数库无可用段落"]}，主控据此走资料不足分支。

# 输出（严格 JSON）
{"model_name":"...","facts":[{fact_id,field,value,condition,source}],"tier":"enough|partial|false","coverage":0.0,"gaps":[],"price_history_ref":{...}|null}
```

### ⑤ FitAgent 适配判定

内部构成：start(`evidence`,`audience`,`scenario`) → LLM(temp 0.2) → 剥壳 code → end(`result`)。

```
# 角色定位
华为PC门店选品的适配判定 Agent：只依据证据事实包判断型号对人群/场景的适配度，输出可追溯的选品理由。

# 适用触发条件
evidence.tier ≠ false（主选与备选各调用一次，互不知晓彼此结果）。

# 硬性约束规则
1. 只用 <evidence> 里的 facts 判断，不引入任何外部知识；
2. reasons 3-5 条，每条必须引用真实存在的 fact_id，并说明该规格对该顾客为什么重要；
3. 重要性参考（人群→字段）：学生→重量/电池/屏幕；创作者→屏幕/内存/存储；职场→重量/接口/电池；家庭→屏幕/易用；企业→系统/接口/管理；
4. tier=partial 且核心字段缺失 → fit 最高 marginal；tier=false → unsuitable 并说明证据不足；
5. concerns 只陈述证据缺失（如"续航数据未收录"），不得猜测；不写文案、不做排名、不贬低其他型号；
6. 不输出 facts 之外的型号、参数、价格、时间、赠品。

# Few-shot
【输入】evidence 含 F02(电池 70Wh/标配版)、F05(重量 约1.31kg)；audience=大一新生
【输出】{"model_name":"HUAWEI MateBook 14（鸿蒙版）","fit":"suitable","reasons":[{"fact_id":"F05","why":"约1.31kg，每天背去教室图书馆无负担"},{"fact_id":"F02","why":"70Wh 支撑全天上课不插电（以实际使用为准）"}],"concerns":["接口明细未收录，建议到店确认外设需求"]}

# 兜底处理逻辑
evidence 解析失败/为空 → {"fit":"unsuitable","reasons":[],"concerns":["证据包为空，无法判定"]}，主控不得据此输出选品卡。

# 输出（严格 JSON）
{"model_name":"...","fit":"suitable|marginal|unsuitable","reasons":[{"fact_id":"...","why":"..."}],"concerns":["..."]}
```

---

## 5. 契约汇总

| 工具 | 入参 | 返回 |
|---|---|---|
| clarify_agent | slots, missing | {action, message, filled} |
| discover_agent | audience, scenario, constraints | {candidates[], note} |
| identity_agent | model_raw | {identity_status, model_name, candidates[], ambiguity_reason} |
| evidence_agent | model_name, focus | {facts[], tier, coverage, gaps[], price_history_ref} |
| fit_agent | evidence, audience, scenario | {fit, reasons[], concerns[]} |

锁定交接包（主控输出，存 `last_pick`）：见 §3.1 Step5 格式。

---

## 6. 建设步骤与测试

**步骤**（顺序不能反）：
1. 修知识库：01 库重切片（父子、`\n\n## `、1000 token）+ `model_name/category` 元数据 + hybrid top_k 12；跨型号索引文档打 `category=index`；05 库 top_k 6。
2. 建 ③④（从现有 6ae220cd 复制节点），再建 ①②⑤；每个子 Agent 用固定入参单测 5 条。
3. 五个应用「发布为工具」，工具描述=注册表 capability+trigger。
4. 建主控 Chatflow：AGENT_REGISTRY 环境变量 → Agent 节点挂 5 工具 → 渲染 code → 回复；会话变量 slots/last_pick。
5. 端到端跑 §6 用例，重点核对：调用顺序符合分发规则、tier=false 兜底、锁定交接包能被文案环节直接消费。

**端到端用例（7 条）**

| # | 输入 | 期望路径 |
|---|---|---|
| T1 | 「MateBook 14 酷睿 Ultra 适合拍视频的顾客吗」 | identity(verified) → evidence（三路并行）→ fit |
| T2 | 「大一新生预算6000写论文要轻薄」 | clarify(补audience/scenario) → discover → 销售选 → identity → evidence → fit（主选+备选两卡） |
| T3 | 「MateBook 14」 | identity(ambiguous) → 列两版本候选等选择 |
| T4 | 「推荐 MateBook X Pro」 | identity(not_found) → 建议换法/走发现 |
| T5 | 「你看着办，开学季家长群体」 | clarify 1 次 → filled(auto) → discover → 自主选定 → 结论卡注明 |
| T6 | 「就它了」 | Step5 锁定 → 输出交接包 JSON，不再调用子 Agent |
| T7 | 「换成创作者用」（上轮已锁定/已选品） | 复用 slots 与 model_name，只重跑 evidence+fit，不重新澄清 |

---

## 7. 降级与风险

| 风险 | 应对 |
|---|---|
| Agent 节点 beta 不稳 / deepseek 函数调用抖 | 方案 B：主控 LLM 输出 action JSON → 条件分支路由；无依赖分支（evidence 的三路检索、fit 主选/备选）用**并行节点**真并行 → 变量聚合 → 循环节点(max 3) 回主控。拓扑与注册表不变 |
| 主控绕过工具自己编参数 | 编排协议红线 + 渲染 code 终检：结论卡出现 facts 外的 fact_id/数字 → 打回重生成 |
| 子 Agent JSON 被 `<think>` 污染 | 每个子 Agent end 前保留剥壳 code |
| 换型号后旧证据残留 | 分发规则 7：slots.model_name 变化必须重跑 evidence+fit；渲染 code 校验卡内 model_name 与 last_pick 一致 |
| credits（200 额度） | 单轮工具上限 6、澄清≤3、子 Agent 统一 deepseek-v4-flash；调试用子 Agent 单测代替全链路 |

## 8. 答辩叙事

- 编排协议+注册表+五段式子 Agent 提示词全部自研，映射 Python 版同一套业务规则（`agent_runtime` 编排 / `evidence.py` 型号身份与事实抽取 / REVIEWER 独立审查思想）；
- 与"直接问大模型"的四道闸：型号身份硬过滤 → 检索层元数据过滤 → 证据 tier 闸 → fact_id 溯源终检，运行轨迹可逐条展示；
- 并行设计讲清楚：依赖链（identity→evidence→fit）串行是业务正确性要求，无依赖任务（三路检索、主备选双判）并行，参考 Orchestrator 模式但按 Dify 执行模型落在真并行的位置。
