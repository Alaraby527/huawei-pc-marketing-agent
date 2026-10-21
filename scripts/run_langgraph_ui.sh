#!/usr/bin/env bash
# 本地启动 LangGraph 版工作台。
#
# 设计原则：缺什么就明确说缺什么，不静默降级，也不假装已经能跑真实效果。
# 用法：
#   ./scripts/run_langgraph_ui.sh              # 默认 7860 端口
#   PORT=7870 ./scripts/run_langgraph_ui.sh    # 换端口
set -uo pipefail

cd "$(dirname "$0")/.."
PORT="${PORT:-7860}"
PY=".venv/bin/python"

fail() { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }
warn() { printf '\033[33m! %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$1"; }

echo "=== 华为 PC 营销工作台 · LangGraph 本地启动 ==="
echo

# ---------- 1. 环境 ----------
[ -x "$PY" ] || fail "找不到 $PY。请先执行：
    python3.12 -m venv .venv
    .venv/bin/python -m pip install -r requirements-dev.txt"
ok "Python $($PY --version 2>&1 | awk '{print $2}')"

if ! "$PY" -c 'import langgraph' 2>/dev/null; then
  fail "当前 venv 没有 langgraph。请执行：
    .venv/bin/python -m pip install -r requirements.lock"
fi
ok "langgraph $($PY -c 'import importlib.metadata as m;print(m.version("langgraph"))')"

# ---------- 2. 主模型 ----------
MODEL_MISSING=""
[ -n "${LLM_BASE_URL:-}" ] || MODEL_MISSING="LLM_BASE_URL"
[ -n "${LLM_API_KEY:-}" ]  || MODEL_MISSING="${MODEL_MISSING:-}${MODEL_MISSING:+ / }LLM_API_KEY"
[ -n "${LLM_MODEL:-}" ]    || MODEL_MISSING="${MODEL_MISSING:-}${MODEL_MISSING:+ / }LLM_MODEL"

echo
if [ -n "$MODEL_MISSING" ]; then
  warn "缺少主模型配置：$MODEL_MISSING"
  warn "没有主模型时，每次对话都会如实返回“主模型当前不可用”，不会伪造成功。"
  warn "这样只能看界面和错误处理，体验不了真实生成。"
  cat <<'TIP'
  建议这样注入（值只在当前 shell，不写进仓库）：
    export LLM_BASE_URL='https://maas.qianwenaiapi.com/compatible-mode/v1'
    export LLM_MODEL='deepseek-v4.1-flash'
    read -rs -p 'LLM_API_KEY: ' LLM_API_KEY && export LLM_API_KEY && echo
TIP
else
  ok "主模型：$LLM_MODEL @ $LLM_BASE_URL（密钥已注入，长度 ${#LLM_API_KEY}）"
fi

# ---------- 3. 知识库 ----------
echo
SOURCE="${KB_SOURCE:-}"
if [ "$SOURCE" = "local" ]; then
  if [ -n "${LOCAL_KB_ZIP:-}" ] && [ -f "${LOCAL_KB_ZIP}" ]; then
    ok "知识库：本地官方 ZIP → ${LOCAL_KB_ZIP}"
  else
    warn "KB_SOURCE=local 但 LOCAL_KB_ZIP 不存在或未设置：${LOCAL_KB_ZIP:-<空>}"
    warn "没有知识库时，选品/参数/原图相关请求会如实说明资料缺口，不会编造型号。"
  fi
elif [ -n "${IMA_OPENAPI_CLIENTID:-}" ] && [ -n "${IMA_OPENAPI_APIKEY:-}" ]; then
  ok "知识库：ima 实时接口（KB=$SOURCE）"
else
  warn "没有可用知识库（KB_SOURCE=${SOURCE:-未设置}，也未见 ima 凭证）"
  warn "可以体验：标题/创意类任务、任务历史、取消、计量。"
  warn "体验不了：推荐真实型号、参数核对、知识库原图。"
  cat <<'TIP'
  用官方 ZIP 时：
    export KB_SOURCE=local
    export LOCAL_KB_ZIP='/你放官方知识库的路径/official-kb-v7.zip'
TIP
fi

# ---------- 4. 数据库与 checkpoint ----------
DATA_DIR="${AGENT_DB_PATH:-$(pwd)/data/agent_v2.db}"
mkdir -p "$(dirname "$DATA_DIR")" 2>/dev/null || true
[ -w "$(dirname "$DATA_DIR")" ] || fail "数据库目录不可写：$(dirname "$DATA_DIR")，请设置 AGENT_DB_PATH 到可写位置"
ok "业务库：$DATA_DIR"
ok "checkpoint：${LG_CHECKPOINT_PATH:-$(dirname "$DATA_DIR")/agent_checkpoints.sqlite}"

# ---------- 5. 启动 ----------
cat <<EOF

=== 启动中 ===
  打开：http://127.0.0.1:${PORT}/
  接口：http://127.0.0.1:${PORT}/docs
  停止：Ctrl+C

  新会话默认走 langgraph_autonomous_v1（自主模式，无固定选品/确认流程）。
  想对比旧的引导式流程，可在接口调用时显式传 {"mode":"guided"}。
EOF
echo

exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers 1