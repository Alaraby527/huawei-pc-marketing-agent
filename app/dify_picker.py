"""Dify 选品总控 Agent 客户端（多 Agent 版）。

选品环节由 Dify Chatflow「华为PC·选品总控Agent·多Agent版」承担：
主控分发闸 → ①需求澄清 ②选品发现 ③型号规范化 ④证据取证（三路检索+证据闸）
→ ⑤适配判定 → 选品结论卡（fact_id 溯源）→ 确认锁定后输出交接包。

密钥纪律（与 IMA_OPENAPI_* 凭证相同）：
- 本模块只从环境变量读取 DIFY_API_KEY，仓库中不出现任何真实凭据；
- 部署时由平台 Secrets 注入；本地开发 export 或写入不入库的 .env；
- 泄露后立即在 Dify 控制台删除并重建 Key。

用法（FastAPI 链路内）：
    from app.dify_picker import pick, extract_handoff

    result = pick("MateBook 14 酷睿 Ultra，给出差办公的人用合适吗？",
                  conversation_id=session.dify_conversation_id or "")
    session.dify_conversation_id = result.conversation_id   # 多轮槽位依赖它
    ...
    handoff = extract_handoff(result.answer)                # 锁定轮返回交接包
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests

DIFY_API_BASE = os.environ.get("DIFY_API_BASE", "https://api.dify.ai/v1")
DIFY_API_KEY = os.environ.get("DIFY_API_KEY", "")
DIFY_TIMEOUT = float(os.environ.get("DIFY_TIMEOUT", "120"))


class DifyPickerUnavailable(RuntimeError):
    """Dify 选品环节不可用（缺凭证 / 上游失败）。调用方应停在此处，不得降级编造选品。"""


@dataclass
class PickResult:
    answer: str
    conversation_id: str
    raw: dict[str, Any]


def pick(message: str, *, conversation_id: str = "", user: str = "huawei-pc-workbench") -> PickResult:
    """单轮调用选品 Agent。

    conversation_id 必须透传上一轮返回值：澄清槽位、澄清计数与锁定交接包
    都存放在 Dify 会话变量里，换 ID 等于丢状态。
    """
    if not DIFY_API_KEY:
        raise DifyPickerUnavailable(
            "DIFY_API_KEY 未配置：选品环节需要 Dify API 凭证（部署环境注入，不入仓库）"
        )
    try:
        resp = requests.post(
            f"{DIFY_API_BASE}/chat-messages",
            headers={"Authorization": f"Bearer {DIFY_API_KEY}"},
            json={
                "inputs": {},
                "query": message,
                "response_mode": "blocking",
                "user": user,
                "conversation_id": conversation_id,
            },
            timeout=DIFY_TIMEOUT,
        )
    except requests.RequestException as exc:  # 网络层失败：如实上抛，不静默重试烧 token
        raise DifyPickerUnavailable(f"Dify 选品调用失败：{exc}") from exc
    if resp.status_code != 200:
        raise DifyPickerUnavailable(
            f"Dify 选品调用失败：HTTP {resp.status_code} {resp.text[:200]}"
        )
    data = resp.json()
    return PickResult(
        answer=data.get("answer", ""),
        conversation_id=data.get("conversation_id", ""),
        raw=data,
    )


def extract_handoff(answer: str) -> dict[str, Any] | None:
    """从锁定回复中提取交接包 JSON（下游文案环节只消费包内 fact_ids，不得引入新事实）。

    选品卡是纯文本，锁定回复内嵌一段 {"locked": true, ...} JSON；
    解析不出就返回 None，调用方据此判定"尚未锁定"。
    """
    try:
        start = answer.index("{")
        end = answer.rindex("}")
        pack = json.loads(answer[start : end + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    return pack if isinstance(pack, dict) and pack.get("locked") else None
