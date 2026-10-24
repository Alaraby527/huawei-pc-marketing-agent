# Dify 选品环节（多 Agent 版）接入说明

选品环节的 Dify 实现：主控分发闸 + 5 个子 Agent（①需求澄清 ②选品发现 ③型号规范化
④证据取证（01 参数 / 05 场景话术 / 02 历史价格快照三路检索 + 证据闸）⑤适配判定），
会话变量累积槽位，输出带 fact_id 溯源的选品结论卡；销售确认后输出锁定交接包，
供下游文案环节直接消费。

## 密钥纪律（与 ima 凭证完全一致）

- 仓库中**只含占位符**：`.env.example` 里 `DIFY_API_KEY=` 留空；
- 真实 Key 只存在于**运行环境**：本地 `export DIFY_API_KEY=app-xxx`，部署平台（魔搭创空间 /
  容器平台）配置 Secrets / 环境变量注入；
- 泄露后立即在 Dify 控制台「API 访问」删除并重建。

## 程序集成（Python 侧）

1. 把 `dify_picker.py` 放入 `app/`，`requirements.txt` 已有 `requests`（无需新增依赖）；
2. 环境变量：`DIFY_API_BASE`（默认 https://api.dify.ai/v1）、`DIFY_API_KEY`；
3. 调用：

```python
from app.dify_picker import pick, extract_handoff, DifyPickerUnavailable

try:
    result = pick("开学季想给大学新生选一台，有什么候选？",
                  conversation_id=session.dify_conversation_id or "")
    session.dify_conversation_id = result.conversation_id  # 多轮槽位依赖它，必须透传
    print(result.answer)          # 选品卡 / 追问 / 候选列表（纯文本，直接展示）
    handoff = extract_handoff(result.answer)  # 锁定轮返回 {"locked":true, "fact_ids"...}
except DifyPickerUnavailable:
    ...  # 停在 unavailable，不用旧结果冒充
```

## 三种对外提供方式（都不暴露 Key）

| 方式 | 适用对象 | 做法 |
|---|---|---|
| **Dify WebApp 链接** | 评审 / 门店销售直接体验 | 应用「发布」后 Overview → WebApp 链接（https://udify.app/chat/<token>），浏览器直接聊，无需任何 Key；token 可在站点设置中重置 |
| **自部署实例 API** | 程序集成（推荐做法） | 魔搭创空间/容器平台配置 Secrets 注入 `DIFY_API_KEY`，评审访问部署实例，全程见不到 Key |
| **DSL 自建** | 评审复现 | 导入 `dify-dsl-华为PC选品总控Agent.yml` 到自己的 Dify（cloud 或自托管），自建知识库与模型后自建 Key |

## 注意

- 评测报告见 `多Agent选品_三层评测报告.md`（9 用例全通过：正常/边界/异常三层）；
- 知识库（Dify datasets）不随 DSL 导出——自建复现需按 `华为PC_Dify多Agent_诊断与缝合方案.md`
  §2.3 的分层配置重建 8 个知识库后，在检索节点重新绑定；
- 本应用引用的知识库：01_商品基础参数 / 05_客户场景与型号话术 / 02_历史价格快照。
