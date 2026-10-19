# Changelog

## 0.12.5 — 2026-10-19
## 0.12.4 — 2026-10-19

### Changed

- Image-to-image is the default again so the product blends naturally into the scene and art style; the prompt keeps a reasonable guard (correct model/appearance/logo, no invented parts, no gibberish) instead of a rigid pixel-level demand. Short, correct Chinese titles are allowed.
- Ratio selector in the UI is now two options: 原比例（跟随知识库原图）and 3:4（小红书）.
- Pixel-identical product compositing remains available via `mode=composite` (or `IMAGE_PRESERVE_PRODUCT=composite`) for cases that need the exact original product pixels.


## 0.12.2 — 2026-10-19

### Added

- **Cookie-based Xiaohongshu login**: the internal MCP image now restores `/app/data/cookies.json` from the `XHS_COOKIES_JSON` secret at startup, so a session created by a normal local login survives restarts and works from the cloud instance (QR login on datacenter IPs is blocked by Xiaohongshu risk control).
- **Connection guide** (`docs/15_小红书连接上线指南.md`): root cause, four options (cookie injection, local MCP + tunnel, residential proxy, public fallback) and step-by-step verification.
- The login dialog now explains the risk-control case after 40 seconds and links to the guide.


## 0.12.3 — 2026-10-19

### Added

- **Output ratio control**: generation follows the reference product image ratio by default (`ratio=kb`); `3:4` (Xiaohongshu), `1:1`, `16:9`, `9:16`, `4:3` are selectable. The generator picks the closest supported generation size and then center-crops to the exact target ratio, so the final image matches the chosen ratio precisely.
- Knowledge-base images are mixed ratio (near-square 817x892, landscape 1048x588/860x684, etc.), so following the reference keeps the generated image consistent with the official asset used in the post.


## 0.12.0 — 2026-10-19

### Added

- **Dedicated image channel**: `IMAGE_API_BASE_URL` / `IMAGE_API_KEY` / `IMAGE_MODEL` (default `qwen-image-3.0-pro` on the user-provided compatible endpoint). Image-to-image is done by sending the product photo as an `image` data URL to `/images/generations`; verified to keep the product unchanged while replacing the scene.
- **13 switchable visual styles**: 数码手账 / 清爽桌搭 / 轻漫画 / 杂志编辑 / 极简产品 / 未来科技 / 复古胶片 / 手绘说明 / 校园学习 / 居家生活 / 通勤办公 / 节日氛围 / 真实场景摄影. Prompts forbid any text/letters/numbers (including handwritten and gibberish) so real Chinese copy is overlaid later.
- **知识库原图 option**: `source=kb` returns the verified official images without calling any image model; the draft card has a 配图来源 selector (AI 生图 / 知识库原图).
- `GET /api/v1/images/styles` reports styles, channel status and sources; DashScope remains as fallback when the dedicated channel is not configured.


## 0.11.1 — 2026-10-19

### Changed

- One-click image generation now uses the hybrid pipeline (cutout composite / image edit) and accepts an optional custom prompt.

## 0.11.0 — 2026-10-19

### Added

- **一键生图（图生图）**: `POST /api/v1/images/generate` takes the current session's product photo plus a prompt and calls DashScope 通义万相图像编辑 (`wanx2.1-imageedit`, async task API) to produce atmosphere images. The product photo is sent as a base64 data URL, so no public hosting is needed; the prompt keeps product appearance locked and forbids text/watermark/price.
- **Style presets**: clean desk / campus / commute / home / tech / festival; the draft card has a 一键生图 button and a style selector, and results are shown inline.
- **Hybrid pipeline for cutouts**: transparent product PNGs are flattened and routed to a text-to-image background + untouched product composite (best quality for official cutouts); photos that already have a scene go to `wanx2.1-imageedit`. Mode is `auto` by default and can be forced with `mode: scene|edit`.
- **Custom prompt**: the draft card accepts an optional prompt; empty means the server builds one from the scenario, audience, facts and style.
- **Agent tool**: `generate_image` joins the tool registry, so the model can generate images from the chat.


## 0.10.0 — 2026-10-19

### Added

- **Design library (M2 foundation)**: 16 visual families x (2 cover + 2 inner) = 64 self-authored layout recipes with structural archetypes, layout preview PNGs, `from-layout` document creation and deterministic design checks (canvas, fonts, text overflow, placeholder images).
- **Same-origin editor**: the MIT vue-fabric-editor build is served at `/studio` (commercial upstream fonts removed, upstream LICENSE kept); `scripts/build_studio.sh` rebuilds it and `THIRD_PARTY_NOTICES.md` records the licence boundary.
- **Design APIs/tools**: `/design-library/styles`, `/design-library/layouts`, `/design-library/layouts/{id}/preview.png`, `/design-documents/from-layout`, `/design-documents/{id}/checks`, and matching agent tools.


## 0.9.0 — 2026-10-19

### Added

- **Rednote workspace module (M1)**: `/api/v1/workspaces/{id}/apps/rednote` with brief handoff, structured trend selection, six creative directions (>=3 mechanisms / >=3 visual families), up to three sample boards, native design documents, review/approval and the manual publish path.
- **Postgres/Redis via compose**: `compose.yaml` now ships Postgres 16 and Redis 7 for local development; SQLite remains available for tests and demo.
- **Data operations workspace**: the left-sidebar entry now holds only post-publish operations (publish records, analytics, comments, alerts, retrospectives); the creative pipeline stays in the chat and the work panel.
- **Platform-first work panel**: platforms (小红书/公众号/朋友圈/海报) sit above the tabs, and each platform has its own tab set and generation/publish rules.
- **Autonomous tool loop**: `POST /agent/turn` exposes the Rednote modules as tools; the model chooses tools in the chat and the tool trace is shown.
- **Same-origin editor host**: `/studio` is reserved for the vue-fabric-editor build; until the bundle is built the page states that plainly and the JSON/PNG fallback is used.
- **Apache-2.0 LICENSE**.

### Changed

- Workspace APIs are disabled with `CAPABILITY_DISABLED` when `DATABASE_URL` is unset, so the existing chat pipeline keeps working unchanged.

## 0.8.3 — 2026-10-19

### Added

- `HOT_SOURCE=curated` mode: the public deployment returns curated product angles without calling public social feeds, because the hosting gateway can reject social hot-list responses with a content-security error. The UI keeps labelling them as editorial, never as real-time Xiaohongshu data.

## 0.8.2 — 2026-10-19

### Added

- The Xiaohongshu login dialog explains the SMS-verification case and adds a **先用公开热榜** button, so the hotspot feature is usable even when the cloud browser is blocked by Xiaohongshu risk control.

## 0.8.1 — 2026-10-19

### Fixed

- Xiaohongshu QR login: the dialog now polls for the full 4-minute QR lifetime, shows a “confirm on your phone” hint after 15 seconds, keeps the QR visible when it expires, and adds a **刷新二维码** button. Restarting the internal MCP invalidates any QR generated before the restart, so a fresh QR is required.

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
