# Changelog

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
