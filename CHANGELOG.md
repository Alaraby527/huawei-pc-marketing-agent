# Changelog

## 0.17.0 — 2026-10-21

### Added

- **Reference-image generation, wired and gated** (`generate_scene_image`, 13th tool).
  A knowledge-base *verified* product image becomes the reference, the prompt describes the
  scene, and the result is returned as an asset labelled `kind = "generated_scene"` with a
  notice that it is not a product photo. Output stays separate from real product imagery,
  and there is no compositing path: this is generative re-drawing, as the user chose.
- **Probe-backed capability registry** (`app/image_capabilities.py`). Capability is recorded
  per `(model, has_reference_image)` with three states: verified yes, verified no, and
  unknown. Text-to-image working says nothing about reference input, and the probes prove
  that matters here: `gpt-image-2.5` accepts a reference image, while `gpt-image-2-w`
  returns 503 for it but 200 without. An unprobed combination is `unverified` and is never
  reported as working. A deployment can supply its own evidence file.
- The registry also selects the request shape: `image_channel` now skips the
  `/images/edits` attempt that was measured at a 90 s read timeout, saving about a minute
  and a half per image on this endpoint.
- **Budget guard**: a reference-image call takes 24-102 s, so the tool refuses early with
  `INSUFFICIENT_TIME` when the run's remaining budget cannot fit one, rather than starting a
  call that would strand the delivery.

### Measured (probe record: docs/20)

| probe | result |
|---|---|
| `1080x1440` accepted | yes - the plan's exact spec, no resizing needed |
| returned size | `1086x1448` for a `1080x1440` request (~+0.55%) |
| transparent background | PNG RGBA (color type 6), but 104 s vs ~24 s |
| `gpt-image-2.5` + reference | 200, real PNG, 24-102 s |
| `gpt-image-2-w` + reference | 503 three times |
| `/images/edits` | 90.8 s read timeout, no response |

### Honest limit

The output is a **generative re-drawing, not a faithful reproduction**: the model, colour,
round-key keyboard and wallpaper are kept, while body proportions, viewing angle and the
exact key layout change (a front view came back as a 3/4 perspective). It must not be used
as evidence of product appearance, ports or keyboard layout.

Tests: 348 pass.

- **Reliable handoff outbox** (`app/runtime/outbox.py`, `agent_outbox_events`, T16). The
  event is written **in the same transaction as the content registration**, as the plan
  requires, so a delivery cannot exist with no record of what should happen next. Delivery
  happens separately with bounded attempts (`LG_OUTBOX_MAX_ATTEMPTS`); an unavailable
  consumer delays the event rather than losing it, and one that exhausts its attempts stays
  visible as `failed` with its error. Read-only `GET /api/v2/outbox` and
  `POST /api/v2/outbox/drain` expose it, and pending events are drained at startup.

  It does **not** push to any external system: there is no CRM, publishing or analytics
  integration in this codebase, so the default handler records the handoff as done and
  nothing more. Claiming a real external handoff would misstate what is connected.

Tests: 333 pass (180 legacy + 153 new/extended).

- **First-pass pair labelling** (`scripts/label_pairs.py`) and calibration from a labelled
  sheet (`calibrate_dedup.py --from-labelled --metric`). Labels authored by the system under
  test carry `label_source = "ai_first_pass"` and `confirmed_by_human = false`, and the
  calibration **refuses to conclude** while any label is unconfirmed: enabling a blocking
  threshold on them would be grading its own exam. Only the most similar pairs are labelled,
  because pairs far apart tell a threshold nothing.

### Measured on 23 real labelled pairs

| layer | duplicates (n=7) | distinct (n=16) | separable |
|---|---|---|---|
| literal (MinHash) | 0.047-0.109 | 0.047-0.125 | **no — fully interleaved** |
| semantic (embedding) | 0.721-0.910 (median 0.822) | 0.389-0.833 (median 0.664) | no, but only one negative sits above the lowest positive |

The literal layer has **no discriminative power on real deliverables**: both classes occupy
the same range. The semantic layer's best measured operating point is **0.75** — precision
86%, recall 86%, false-positive rate 6%, false-negative rate 14%.

This also corrects the earlier constructed sample, which suggested a clean split at 0.5: on
real data the classes overlap (`gap = -0.11`). It is a good illustration of why the plan
insists on human-labelled real pairs rather than convenient ones.

Thresholds remain **unapplied**. Enabling one requires confirmed labels.


- **Semantic similarity layer** (`app/runtime/semantic.py`, `agent_content_embeddings`,
  T13). The literal layer cannot see a paraphrase: measured against the live embedding
  endpoint, a rewritten post that makes the same points scores **0.0000** on character
  shingles and **0.8689** on embeddings, while unrelated copy scores ~0.51. Everything the
  plan requires of an index is recorded: model id, dimension, view and an `index_version`
  identifying the vector space, and vectors from two versions are never compared. Search is
  a full cosine scan — the plan's preferred exact baseline — so no approximate index exists
  and there is no recall to monitor yet. Thresholds are still unapplied.
- `embed_texts` reuses the same guarded transport as the chat calls, so an embedding cannot
  reintroduce the unbounded connect/header phase. Opt-in via `LG_EMBEDDING_MODE=shadow`
  because it is a paid call on every delivery; off by default, which also keeps the test
  suite off the network.
- The embedding is computed **before** the admission lock and only compared inside it, as
  the plan requires: no model, embedding or network call happens while the critical section
  is held.

### Note

The measurement also shows why the plan forbids a universal 0.8 threshold: two unrelated
Chinese marketing texts sit at about 0.5, so the gap between the paraphrase and the
negatives is roughly 0.36 and must be calibrated on labelled pairs.

Tests: 327 pass (180 legacy + 147 new/extended).

- **Durable run leases with heartbeat, expiry and bounded attempts** (`app/runtime/lease.py`,
  `agent_run_leases`, T16). The in-process conversation lock is a *local* lease: it says
  nothing to a second worker and nothing after a restart, so a crash was indistinguishable
  from a run still working. A lease is now claimed durably, renewed by a heartbeat task on
  its own session, and released whatever happens. Reclaiming an abandoned lease is a single
  conditional `UPDATE ... RETURNING`, so two workers racing to recover one run cannot both
  win, and `LG_RUN_MAX_ATTEMPTS` (2) stops a run that keeps dying from being retried
  forever.
- **Crash resume** (`POST /api/v2/runs/{run_id}/resume`). An interrupted run continues from
  its own checkpoint instead of costing the user the whole attempt — the case that used to
  end as "the upstream connection dropped, start over". It refuses when there is no pending
  progress, when the run is waiting for the user, and when the attempt budget is spent, and
  each refusal says which. `run_payload` exposes a `resumable` hint; the authoritative check
  happens when a resume is attempted.
- Startup orphan recovery is now lease-aware: a run another live worker is still beating on
  is left alone, while an expired lease — or a pre-lease row with no lease at all — is
  recovered as `needs_reconciliation`.

### Fixed

- Releasing the lease on a second connection while the run's own session still held an open
  write transaction meant every delivery waited out SQLite's five-second busy timeout. The
  run's session is committed before the release, and the release session uses a short
  timeout. The suite went from 190 s back to 49 s.
- `next_seq` and the lease reclaim both read a value back with a follow-up `SELECT`, which
  answered from the identity map with the pre-increment object. Both now read the value the
  statement wrote (`UPDATE ... RETURNING`).

Tests: 310 pass (180 legacy + 130 new/extended).

- **Calibration tooling for duplicate detection (plan Phase D).** `app/runtime/calibration.py`
  evaluates a threshold against labelled pairs (precision, recall, false-positive rate,
  false-negative rate), and refuses to recommend anything from an empty or unlabelled set —
  an undefined rate is `None`, never a measured `0.0`. It reports the **separation** between
  the classes, so a clean split is not mistaken for a calibrated value: with every duplicate
  high and every distinct pair at zero, the recommended number is the grid floor and is
  labelled `provisional`. `scripts/calibrate_dedup.py --sheet` writes a JSONL labelling
  sheet from real deliveries; `--constructed` calibrates the lexical layer on pairs whose
  ground truth follows from how they were built (the plan's Q10-Q14 cases).
- The sheet separates **deliverables from chat**: a conversational reply is registered in
  the content memory like any draft, and those rows dominated the most-similar pairs
  (0.2656 for all replies vs 0.125 for artifact-backed ones). They are excluded by default
  rather than silently skewing a threshold.

### Note

Real deliveries currently contain **no duplicate positives**: 17 deliverables produce 136
pairs whose highest lexical similarity is 0.125. A threshold therefore cannot be calibrated
from real data yet — the tooling says so instead of inventing one.


- **Final admission with a real critical section** (`app/runtime/admission.py`, T15). The
  dedup evidence was previously read outside any lock, so two concurrent runs could both
  see a history without the other and both be admitted. Admission now computes the
  fingerprint outside the lock, then takes a short scope lock, **re-reads the committed
  history inside it**, decides, and registers in the same transaction. Postgres uses
  `pg_advisory_xact_lock` as the plan specifies; that branch is **not exercised here** (no
  Postgres or Docker in this environment) and is not claimed to work.
- `LG_ADMISSION_LOCK_TIMEOUT` (2 s) and `LG_ADMISSION_RECHECKS` (2), matching the plan's
  policy example.

### Fixed

Three concurrency defects found by firing three identical requests at the running server:

- **Two concurrent runs collided on SQLite's single writer** and one became
  `sqlite3.OperationalError: database is locked` -> `PendingRollbackError` -> HTTP 500, with
  the run left in `running`. SQLite connections now set a busy timeout (5 s) and enable WAL.
- **The graph swallowed storage errors as a failed decision**, so the session stayed
  unusable and the run's own terminal write failed too. `SQLAlchemyError` is now re-raised
  so the run can be finished honestly after a rollback.
- **A failed terminal write was returned as HTTP 200 with `status: running` and an empty
  reply.** The handler now verifies the run reached a terminal state and otherwise reports
  an explicit failure instead of handing back a running payload.
- **Write transactions were held for the whole run** (the first ledger write happened
  before the first model call and nothing committed until the end, holding SQLite's write
  lock for ~60 s). Model attempts and tool executions now commit as they are written.

- **Two concurrent turns on one conversation raced on the task-state row.** Both read
  "no row" and both inserted, and one died on `UNIQUE constraint failed:
  agent_task_states.conversation_id`. This was the cause of a test that failed about one run
  in four. The write is now a collision-tolerant upsert (savepoint, then a fresh re-read).
  Verified by running that test 24 times: 0 failures.
- `next_seq` allocated the counter with an `UPDATE` followed by a separate `SELECT`, so a
  reading transaction holding a pre-increment snapshot could hand out the same number
  twice. The increment now returns the value it wrote, in one statement. This hardening was
  applied while investigating the flake above; it was **not** the cause of it.

Verified by three concurrent live requests: 0 x HTTP 500, 0 x "database is locked",
0 x PendingRollbackError, all three reached a terminal state with real replies, and each
admission saw the others (`compared` 4/5/6) with a 0-2 ms lock wait.


- **Independent review pass** (`review` graph node, plan G7/T08). It runs on the finalised
  text with its own context — the delivered copy plus the allowed facts and conditions,
  never the author's conversation or self-assessment — and it checks the whole text rather
  than only the claims the author labelled. Blocking findings (`high`) or a `block` /
  `needs_input` / `human_review` / `choose_new_plan` verdict end the run as `partial` with
  an honest notice; advisory findings are recorded without failing the delivery. Real
  output: it caught that a closing "告诉我主推哪款…" line is requester-facing meta-discourse
  that must not ship inside customer-facing copy.
- **Delivery state is now separated from task state.** `completed` never means approved:
  the run reports `content_status` (`review_ready` / `review_required` / `research_draft`)
  and `can_publish: false`. A review that did not run cannot be reported as `review_ready`.
- **Shadow-mode content memory for near-duplicate evidence** (`app/runtime/dedup.py`,
  `agent_content_fingerprints`). Delivered text is registered at delivery (not only at
  publication) as a normalised digest plus a 64-permutation MinHash sketch. It records
  exact and near matches and **blocks nothing**: no threshold is applied, because an
  uncalibrated score must not decide anything. Scope is a single workspace — cross-store
  authorisation is not implemented and is not claimed. Real output: a second identical
  request compared against 1 prior delivery, similarity 0.0.
- Tool-call ceiling (`LG_MAX_TOOL_CALLS`, default 12): the model-call cap did not bound
  tool fan-out. Exceeding it ends the run as `partial` with a stated reason.


- **LangGraph autonomous runner** (`langgraph_autonomous_v1`) as the default for new
  conversations: one generic loop (`decide → execute_tools | wait_for_user → decide →
  finalize`) where the main model decides whether to ask, query, write, revise or
  deliver. There is no intake/recommend/plan stage and no fixed step count, and the
  legacy `_guided_turn` is not wrapped in a node nor the old 12-step loop wrapped in an
  outer graph. Existing conversations keep the runner version they were created with.
- **Metered model gateway** (`app/runtime/model_gateway.py`): every real model request —
  decision, writing, editing and repair alike — is recorded in `agent_model_attempts`.
  Missing usage is stored as *unknown* (NULL), never as a measured zero. This fixes the
  edit fast path that spent tokens and recorded `input_tokens: 0`.
- **Request-scoped idempotency** (`agent_request_records`): keyed by
  `(conversation_id, key)` plus a normalized payload hash. Same key with different
  content is a `409`, and the same key in a different conversation is independent. The
  previous global `agent_runs.idempotency_key` lookup could collide across conversations
  and never compared payloads.
- **Effect ledger** (`agent_effects`): a `save_artifact` replayed after a crash returns
  the stored outcome instead of creating a duplicate artifact revision. An upstream call
  whose outcome cannot be confirmed becomes `needs_reconciliation` rather than being
  retried blindly.
- **Checkpointing** via `AsyncSqliteSaver` on its own file (`LG_CHECKPOINT_PATH`,
  default `agent_checkpoints.sqlite` beside the business database). The saver is opened
  once per process (application lifespan), `thread_id` is derived server-side from the
  conversation and runner version, and pause/resume uses `interrupt`/`Command(resume=…)`
  with the validated user text only — no arbitrary client state patch.
- `GET /api/v2/conversations/{id}/runtime` — a redacted runner view (status, stop reason,
  usage totals); it never returns raw graph state.
- **Optional asynchronous execution** (`response_mode: "async"`): the request returns
  `202` with `run_id` / `status` / `events_url` after a short transaction, and a worker
  with its own session executes the graph. `blocking` stays the default, so existing
  clients are unaffected. Because the run is committed before any model call, another
  connection can already see it running, and a cancel arriving mid-run is observable.
- **Event stream** (`GET /api/v2/runs/{id}/events/stream?after_seq=N`): a server-sent
  replay of committed events. Each event carries its index as the SSE id, so a
  reconnect resumes without duplicating what the client already saw. It never
  forwards raw graph state or unverified token streams.
- **Orphan recovery on startup**: a run left `running` by a previous process becomes
  `needs_reconciliation` with `stop_reason=orphaned_by_restart`. Its outcome is unknown,
  so it is neither retried blindly nor reported as a success.

### Added

- **The two product-selection paths now have knowledge-base backing.**
  - The library adapter previously indexed only `产品事实.md` and six scene cards, so the
    per-model cards and the cross-model matrices were unreachable (85 → 160 entries).
  - `read_product_fit` returns a named model's own audience/scenario conclusion
    (检索结论 / 适用任务 / 不适用或需先验证的任务 / 限定条件 / 待核验项). A user who names a model
    is no longer asked to describe an audience the library has already concluded on.
  - `read_field_matrix` returns the cross-model field matrices (14 of them) for matching
    several candidates on one consistent scale.
  - The system prompt states the flow: a vague need collects the few facts that change the
    answer, then offers 2–4 real candidates and **waits** for the user to choose before
    writing; a named model goes straight to its fit card and facts. It is the default
    order, not a fixed pipeline, and a user who already specified everything gets output
    directly.

### Fixed

- **Rendered images never reached the client.** `render_visuals` returns asset URLs while
  `find_product_images` returns image records; the state update required dicts, so a
  successful render produced no gallery while the reply said the cards were rendered.
- A connection failure is no longer reported as a timeout. A 7 ms failure and a 22 s
  upstream `RemoteDisconnected` were both described as "连接或读取超时", which sends
  whoever reads the log after the wrong cause. The exception class is kept, the deadline
  is checked separately, and one retry is allowed **only** for failures that prove nothing
  was sent (DNS, connection refused) — an ambiguous failure is never repeated.
- Tool results replayed into the transcript are marked when cut, not silently truncated.
- `read_product_fit` no longer repeats the fact section that `read_product_evidence`
  already returns: one call carried ~7.1 K characters, four calls ~28 KB. It now returns
  ~600 characters.
- Numbers a reply explicitly refuses ("没写 14 寸") are no longer flagged as unverified.


- **The model deadline now bounds the whole exchange.** `urllib`'s timeout is per socket
  operation, so a gateway that trickles bytes inside that window reset it forever, and
  the previous body reader only started once the response headers had arrived. A real
  request hung for 527 s with `LLM_TIMEOUT=120`. The chat calls now use `http.client`
  with a watchdog that `shutdown()`s the socket at the deadline (verified against a
  trickling server). A caller-side cap also bounds the worker path.
- A client disconnect no longer leaves a run stuck in `running`: `CancelledError` is a
  `BaseException`, so the previous handler missed it. Such a run now ends as
  `needs_reconciliation` / `client_disconnected`.
- The composer's knowledge-base pill and the context menu no longer claim
  "官方知识库 · 已连接" unconditionally; they follow
  `GET /api/v1/knowledge/connection`, and the user-facing tooltip no longer repeats the
  server's configuration hint.
- **Fabricated first-person experience and unverified numbers in delivered prose are
  now flagged** (`app/runtime/content_policy.py`). The platform rules already forbade
  invented experience, but nothing enforced it: grounding only inspects numeric claims
  bound to a product record, and only for a saved artifact. Real testing produced a
  first-person story and a 14-inch claim while the knowledge base was down. Such a
  delivery now ends as `partial` with `unverified_experience` / `unverified_numbers`
  reported and an honest notice appended.

### Changed

- New conversations default to `langgraph_autonomous_v1`; `guided` and `autonomous`
  remain selectable for explicit calls and old conversations.
- **Cancellation actually stops the loop.** The cancel intent is now read on its own
  short transaction, because reading it through the request session's identity map
  returned a stale `running` status and the run continued. A cancelled run can no longer
  be rewritten as `completed`.
- **Budget exhaustion is `partial`, not `completed`**, so a spent budget cannot be
  counted as success in management metrics.
- **Message sequence** uses an atomic per-conversation counter (`agent_seq_counters`)
  incremented in SQL inside the writer's transaction. The old per-batch formula
  (`base_seq + 2 + step*4` vs `base_seq + 3 + step*4 + call_index`) produced duplicate
  `seq` values, and a Python `max(seq) + 1` loses updates when two writers interleave —
  both were reproduced by a concurrency test before this fix.
- **One conversation executes at a time** within a process (a local lease, not a
  distributed lock), so two in-flight turns cannot interleave graph state.
- **The checkpoint saver is per event loop** and is never closed by another loop's
  request; the previous single-slot saver closed a connection that an in-flight task was
  still using (`Cannot operate on a closed database`).
- **Errors shown to users are sanitized**: the knowledge base is no longer loaded before
  every decision, so a KB outage degrades to one tool error the model can react to
  instead of aborting the turn; and user-facing text never contains a credential name, an
  environment variable or a filesystem path.
- Deleting a conversation also purges its ledger rows and its checkpoint thread, so no
  resumable orphan checkpoint is left behind.

## 0.16.1 — 2026-10-21

### Fixed

- ModelScope's `.ms.show` gateway rejects `PATCH`/`DELETE` with 403, so task rename/pin/delete now use POST aliases (`/api/v2/conversations/{id}/update`, `/delete`); the original REST methods remain for direct API clients.

## 0.16.0 — 2026-10-21

### Added

- **Real task history**: the sidebar now lists backend v2 conversations (`GET /api/v2/conversations`) with auto titles, stage, message count and last-updated time. Clicking a task restores its messages, artifacts, candidates, plan and images, so multi-turn work actually continues.
- **Task menu (⋯)**: pin/unpin (pinned tasks stay on top), rename, and delete (also removes messages, runs, artifacts, revisions, tool executions and task state server-side).
- `PATCH /api/v2/conversations/{id}` and `DELETE /api/v2/conversations/{id}`.

### Changed

- The sidebar no longer stores one localStorage entry per message; localStorage is only an offline fallback.
- Auto titles skip greetings and short commands, using the first substantive user message.

## 0.15.0 — 2026-10-20

### Added

- **Guided conversation** (default UI mode): ask who the content is for and where it will be used → recommend three verified models with reasons → let the user pick one and confirm the plan → write the copy → show original KB images → ask whether to keep, rewrite the copy, or change the images. The flow is a guide, not a locked funnel: explicit product requests skip recommendation, and any step can be revisited.
- **Candidate cards and action chips** in the web UI, plus an original-image gallery and review buttons.
- **Soft task state** (`agent_task_states`): needs, candidates, selected product, plan and images survive turns and restarts.
- **Parameter budget in copy**: the prompt asks for 3–6 facts relevant to the narrative instead of dumping every spec; grounding still verifies every number.

### Fixed

- Raw decision JSON (`{"action":"reply",...}`) is no longer shown in chat; reply text is extracted and cleaned.
- Deterministic answer when the user asks for a non-existent value (for example 64 GB): the copy uses the real values and says so.
- Repeated artifact saves no longer collide on revision 1; a new revision is appended.
- KB image records (dicts) are handled consistently instead of assuming object attributes.
- Source/provenance boilerplate is no longer treated as a product applicability condition, removing warning floods from grounding.

## 0.14.0 — 2026-10-20

### Added

- **V2 autonomous assistant** (default chat entry): persistent conversations, runs, messages, tool executions and versioned artifacts; native OpenAI-compatible tool calling with a JSON fallback; `ask_user` pause/resume; local artifact patches; variable page counts.
- **Field-level grounding** (`app/grounding.py`): each numeric claim is bound to product / field / value / unit / qualifier / conditions; prices and unsupported values are rejected; non-product numbers are ignored.
- **Honest fact repair**: when the model insists on a value absent from the evidence, the runtime retries twice, then replaces only that value with the field's real values, re-checks and discloses the change. Unsupported page counts are never saved.
- **Model wall-clock limits**: total request deadline plus per-run budget (`LLM_TIMEOUT`, `AGENT_RUN_BUDGET_SECONDS`), including protection against gateways that trickle bytes.
- **Demo database fallback**: v2 uses `DATABASE_URL`; when it is absent, a file-backed SQLite DB is created at `AGENT_DB_PATH` (or `/app/data/agent_v2.db`) instead of an in-memory store.

### Changed

- Product search matches model-defining tokens (14, pro, fold, x…) instead of any brand word, so an unknown model such as “MateBook X Pro 2027” returns no hit instead of the wrong product.
- Delivery replies come from the model and explain corrections; a deterministic guard adds the real values when the model omits them.
- DeepSeek v4.1 flash remains the default model. qwen-plus showed long gateway stalls in testing and is not treated as a validated fallback.

## 0.13.1 — 2026-10-20

### Changed

- `/api/v1/models` now returns labels and context windows for the model picker (still no keys).
- DeepSeek v4.1 flash runs with `enable_thinking=false` for structured JSON drafting: six pages generated in ~14s instead of consuming thousands of reasoning tokens, avoiding gateway timeouts; qwen models are unchanged.

## 0.13.0 — 2026-10-20

### Added

- **Multi-provider text models**: `LLM_MODELS_JSON` registers several OpenAI-compatible providers (id, label, base_url, api_key, model, context, default). The model picker shows each label and context window; keys stay server-side and `/api/v1/models` never returns them.
- **DeepSeek v4.1 flash as the default conversation model**: 1M context, JSON mode verified end-to-end (six-page draft generated with `engine_meta.model=deepseek-v4.1-flash`); qwen-plus / qwen-max remain selectable through the same gateway.


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
