# Changelog

## 0.8.0 — 2026-10-19

### Added

- **Left task sidebar**: 新建任务, 热点搜索, 自动化/插件市场 placeholders, and a recent-task list backed by `localStorage` + `GET /api/v1/conversations/{id}/state`.
- **Hotspot search view**: a dedicated screen with the Xiaohongshu ranking (or a clearly labelled public/editorial fallback), topic detail, and two actions — 查看热点详情 / 使用热点. Using a hotspot inserts the topic title and link into the composer and returns to chat.
- **Collapsible work panel**: the old right canvas is now a side panel that expands/collapses; the chat takes the remaining width and stays free of overlays.
- **Keyboard shortcuts**: `⌘/Ctrl+N` for a new task, `⌘/Ctrl+K` for hotspot search.

### Changed

- Chat is the main area; evidence remains available as a work-panel tab and hidden DOM is kept for its renderer.
- Toasts moved to the bottom-right so they never cover the composer.

## 0.7.0 — 2026-10-18

### Added

- **Free-form first**: the workspace opens with a normal chat composer instead of a mandatory three-step card; the agent understands the message first and only asks for genuinely missing information, one question at a time.
- **Compact composer**: rounded input, model picker with a server-backed allowlist, default-model label, context menu (`官方知识库` / `小红书热点`), and a circular send button.
- **Publish actions below the composer**: approve / shorten / regenerate / refresh live in the compact action row under the input instead of a long-lived card above it.
- **Six-page PNG renderer**: `GET /api/v1/conversations/{id}/posters.zip` and `/posters/{n}.png` render 1080×1440 posters from the plan, verified product images and fact callouts with Pillow; no Playwright/Chromium runtime required.
- **Xiaohongshu QR login**: the main workbench can show a QR from an internal Xiaohongshu MCP, let the user scan/authorize, and then load real note titles/links/metrics as hotspot leads.
- **Cross-platform fallback**: rednote → Weibo → editorial angles; every source is labelled and never presented as Xiaohongshu real-time data unless it is. Unrelated public trends are dropped in favour of product-relevant editorial angles.
- **Single user entry**: the MCP is an internal service; the main app remains the only platform users open.
- **Tool/skill trace**: assistant messages and the running state show `skill · 需求解析`, `tool · 官方知识库`, `skill · 候选核验`, `skill · 六页方案`, `skill · 合规扫描`, `gate · 人工确认`.

### Changed

- Option cards now appear only while a question is open and disappear after the answer; the objective step reloads hotspot chips if the public feed finishes late.
- The old mandatory quick-start panel and topbar engine switch were removed; model choice moved into the composer.
- Docker installs `fonts-noto-cjk` and Pillow for poster rendering.

## 0.6.0 — 2026-10-18

### Added

- **Local official KB adapter**: `KB_SOURCE=local` reads the provided official knowledge base v7 ZIP directly (10 key models, scene cards, image indexes and images). It is marked `local_official_export`, not a live ima call; `competition_ready` stays false.
- **One-click requirement builder**: the initial workbench exposes audience, budget and scenario chips; selecting all three sends one structured request.
- **Smoother flow**: candidate-ready summary in chat, automatic switch to the plan tab, wider two-pane layout at 1280px, evidence available as a tab, shorter hotspot strip, sticky quick-start action.
- **Local KB verification**: product facts, P8 scene cards, image index and image bytes are read from the ZIP without network; the six-page draft, compliance scan and approval work on the official data.

### Changed

- `MaterialBundle` accepts `local_official_export` sources in live mode.
- Image identity skips are warnings; only insufficient usable image count blocks a handoff.
- Docker image includes `data/`; the official ZIP is gitignored and supplied at deployment time.

## 0.5.0 — 2026-10-18

### Added

- **Six-page Xiaohongshu plan**: hook / scene / proof / detail / decision / close, with per-page fact IDs and image assignments; LLM output is schema-validated and rule fallback produces the same structure.
- **Guided requirement options**: audience, budget, scenario, preferences, required software and objective can be chosen as chips; candidate cards have one-click selection.
- **HarmonyOS-style PC workbench**: three-pane layout, five-step progress, candidate board, six-page canvas, evidence panel, draft verification, toast/loading states, dark mode and responsive fallbacks.
- **DashScope native image generation**: `wanx2.1-t2i-turbo` asynchronous submit/poll/download, `768*1024` cover atmosphere; compatible `/images/generations` remains as a fallback.
- **Hot-topic inspiration**: structured 60s feed with an explicit editorial fallback when the public feed is unavailable.
- **Six-page samples on GitHub**: three style contact sheets, six full pages, and an AI cover sample under `app/static/samples/`.
- **Real integration record**: personal ima KB (13 facts, 6 images), qwen-plus six-page generation, approval/export, and DashScope cover verified.

### Changed

- Catalog originals are read concurrently with per-file fault isolation; a single unreadable document no longer aborts the whole catalog.
- LLM drafts that fail the deterministic scan automatically fall back to the rule draft, with the reason surfaced to the operator.
- Rule fallback copy truncates at punctuation instead of cutting numbers, and scene tags are normalized.
- Version bumped to `0.5.0`; README, OpenAPI snapshot and static asset cache keys updated.

## 0.4.0 — 2026-10-18

### Fixed

- **Publication gate**: approval and draft are now bound to a digest of the current handoff (product, configuration, facts, images, price, task). Requirement changes, selection changes, failed knowledge-base reads, and evidence changes immediately invalidate the draft.
- **Export**: `/api/v1/conversations/{id}/export` now re-checks the binding instead of trusting stale `draft_approved` flags.
- **Refresh / select**: `refresh` and explicit candidate selection are no longer swallowed once a draft exists; they re-enter the selection pipeline.
- **LLM robustness**: untrusted model output is validated before use; malformed JSON shapes fall back to the deterministic rule engine instead of returning HTTP 500.
- **Budget parsing**: supports `预算不超过6000`, `预算最多6000`, `一万二`, `1万2`, `两千五`, and `没有预算限制`.
- **OS parsing**: negated requirements such as `不需要Windows` are no longer treated as Windows requirements.
- **CLI export**: repeated exports reuse local image files instead of marking the handoff unavailable.
- **Event loop**: synchronous LLM, image generation, and hot-topic calls no longer block the FastAPI event loop.
- **Reporting honesty**: export and readiness now describe compliance as a deterministic rule scan, not semantic verification.
- **UI**: candidates are rendered with price/eligibility/unknowns; publish state is reset when verification is cleared.

### Added

- Regression tests for approval invalidation, export rejection, refresh, malformed LLM output, budget/OS parsing, and CLI re-export (100 tests total).
- GitHub Actions CI and GHCR container build workflow.
- `pyproject.toml`, `compose.yaml`, and an updated project README.

## 0.2.0 — 2026-10-18

- Initial front-half handoff: requirement clarification, ima retrieval, specification extraction, image verification, input risks, and material export.
