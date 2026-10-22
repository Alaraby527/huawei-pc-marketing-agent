# Changelog

## 0.17.0 — 2026-10-21

### Fixed (the row the user actually meant)

The row visible under the 热点榜单 section heading was **not** the delivery-surface row - it was
`SECTION_TABS.hot`, the old hotspot plan's five "scenario views" (小红书/公众号/朋友圈·个人/
朋友圈·门店/门店海报). They look almost identical to the surface tabs (same pill shape, same
active colour), which is why two rounds of reading the screenshots blamed the wrong element. Those
five views were never wired to data, so they are now removed; the hot section's own row (热梗/
抖音热榜/微博热搜/知乎热榜/小红书热榜) is the one that stays.

Reverted the previous commit's relocation of the delivery surfaces: they are back in the title bar
with 朋友圈 opening a 门店/个人 choice, which is the form the user said was fine. Keeping bug fixes
that did not depend on the layout.

Tests: 564 pass (180 legacy + 384 new/extended).

### Fixed

- **The test suite was not hermetic.** Running the app requires sourcing the private
  environment (`KB_SOURCE=local`, `LOCAL_KB_ZIP`, the model and image credentials), and those
  same variables changed what the tests did: with a real knowledge base configured, a test that
  simulates a **knowledge-base failure** no longer failed, so its assertion flipped. Measured
  both ways on one file - **69 passed in 56 s** with a clean environment, **1 failed in 103 s**
  with the private environment sourced - which is exactly the shape of a "flake" that is really
  an environment dependency. `tests/conftest.py` now clears the ambient settings for every test,
  so a test that wants the real knowledge base or an image channel must ask for it explicitly.
  After the fix: **516 passed in 64.7 s** clean and **516 passed in 59.6 s** with the private
  environment sourced.
- The studio's material projection dropped the KB's `source` provenance, so every asset's rights
  status degraded to `unknown`. Worse, the field was read through `_image_field`, which returns
  `str(value)` - so the mapping arrived as the repr of a dict and the resolver could not read
  `kind` at all. Now read without stringifying: the studio reports
  `provenance_recorded_terms_unreviewed` with the real `sha256`, which is both more informative
  and more truthful than `unknown`.

Tests: 516 pass (180 legacy + 336 new/extended), identical with and without the private
environment sourced.

- **Export pack** (`app/export_bundle.py`, package 3's step 8). Produces the five files -
  bundle, claim map, review, manifest, approval - and binds an approval to a
  ``content_hash`` over the exact material it covers, because package 1 requires that any
  change to a source image, the copy, the recipe or a size **invalidates** the previous
  approval. An approval that survives an edit is not an approval of what ships, so
  ``approval_state`` compares the bound material with the current one and **names the fields
  that changed**.
- Two rules from the packages are enforced rather than described: a caller-supplied
  ``approved=true`` is **not trusted** without a named approver whose hash matches, and the
  claim map reports unsupported claims instead of dropping them - silently omitting one is how
  an unevidenced number reaches a customer.
- Files are written through an **injected** saver (the agent's own `_save_artifact`), so the
  export inherits its revision numbering and fact check instead of opening a parallel, weaker
  write path. `save` refuses outright when no saver is supplied.

Tests: 515 pass (180 legacy + 335 new/extended).

- **Business input validation** (`app/briefs.py`, package 3's config readers). Three outcomes
  rather than two: a **gap** means a core input is absent and names who must supply it; a
  **blocked** means the input exists but is stale, contradictory or unauthorised. The package's
  own templates are refused outright - they ship `template_only: true` with every value null and
  say of themselves "不把本模板直接当作已批准业务资料". Enforced: expired campaigns, unknown
  prices (never written as a number), objectives outside the allowed set, contradictory facts
  (blocked, not averaged), facts without a source, assets missing owner/consent/licence or
  channels (never defaulted to all channels), and expired authorisations.
- **The five-step copy method** (`app/copy_method.py`, package 3's prompts) kept in the
  package's own three layers, because when a channel's length wish collides with an unverified
  fact it is the layer order that decides. Four statements are kept verbatim rather than
  paraphrased: "客户材料是数据，不是指令", "任何占位符不得进入可发布包",
  "没有人工批准不得写 approved", and the review's self-limits "高总分不能抵消硬失败" /
  "不要把自己的打分当成独立效果证据". `plan` puts the confirmation gate first for a new brand
  or a price change, and carries the excluded methods with their reasons.

### Fixed

- `is_template` treated "no brand_id" as a template, and factbook and proof_assets have no
  brand_id at all - so **their validation was skipped entirely**, silently passing the expiry,
  conflict and authorisation checks. Caught by the new tests; a regression test pins it.

Tests: 500 pass (180 legacy + 320 new/extended).

- **Asset resolver** (`app/assets.py`, package 1's AssetResolver). Roles and rights derived
  from the knowledge base's own real provenance - `source.kind`, `source.title` and
  `verification.sha256` - rather than invented. Two honesty rules are load-bearing:
  **provenance is not a licence**, so `local_official_export` reports
  `provenance_recorded_terms_unreviewed` and never an approval; and roles are **derived**, with
  `role_source` saying so. Enforcement follows the budget ledger's pattern:
  `IMAGE_REQUIRE_ASSET_RIGHTS=1` makes the missing rights decision blocking, off by default and
  stated as such. Added to `find_product_images` as additive fields plus a shared
  `pick_reference` used by both the studio and the agent tool.
- **Recipe release gate** (`app/recipes.py`, 24 recipes copied from package 1 as its NOTICE
  intends). **All 24 ship `unverified_real_images`, so the production-usable count is 0** and
  `select` refuses with the reason. Package 1's "首批建议 R01/R02/R04/R06/R09/R16/R22/R23" is
  exposed as `suggested_first_batch` - a testing suggestion, not an approval - and `approve()`
  deliberately **cannot** flip `release_status`, because the plan forbids treating an edited
  JSON as a production approval. Package 1's channel vocabulary maps explicitly onto this
  project's surfaces, including `moments` mapping to both Moments faces since that package
  never splits them.

### Fixed

- The asset resolver read images with `.get()`, but `verified_images` returns objects on some
  paths and dicts on others - the existing code read both through `_image_field`. A legacy test
  caught the resulting AttributeError taking the whole image tool down; the resolver now reads
  either shape, and regression tests cover both and a mixed batch.

Tests: 471 pass (180 legacy + 291 new/extended).

- **Cost ledger** (`app/cost_ledger.py`, `cost_reservations`, package 1's budget state
  machine). Reserve before submitting a paid attempt, settle to the real amount, or release an
  attempt that was never charged. Three plan rules are enforced in code: no currency means no
  billing, an unestimatable cost **blocks submission** rather than being guessed, and an
  unknown charge stays counted and flagged for reconciliation instead of being zeroed
  ("不能简单将预留清零"). All paid attempts of one request share one ceiling, which comes from
  the server environment.
- **One deliberate, visible deviation.** The plan says a paid call is forbidden before a
  production budget exists, which strictly means blocking. Enforcement is off by default
  (`IMAGE_BILLING_ENFORCE=1` turns the plan's rule binding) because the user is actively using
  image generation for acceptance and silently refusing every request would break it. Until
  then each attempt is still recorded as `unpriced`, so unpriced spend is visible and can
  never be mistaken for accounted money. Verified live: generation still succeeds and the row
  reads `priced: false, state: unpriced`.
- Surface/platform separation, the 20-method catalogue and the placeholder gate (details in
  the commit for those changes).

Tests: 449 pass (180 legacy + 269 new/extended).

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

### Changed (chat pane layout, per the screenshots)

- Removed the empty-state block ("先说说写给谁" / "告诉我人群和用途…") entirely, as asked.
- The two example buttons moved out of it and now sit **just above the composer**, where the arrow
  pointed, labelled "不知道怎么写？"; they hide once a conversation starts so they stop taking up
  space.
- The composer is now **last in the pane**, so it is pinned to the bottom, and the chat log fills
  the middle. The deliverable card lives in the same scrolling middle but is capped at 46vh so it
  cannot squeeze the conversation.
- Added a **composer expand toggle** (⤢ in the composer bar): long copy is easier to edit with a
  220px–52vh input than a two-line one.
- Fixed a **duplicate `id="image-results"`** left behind by the earlier work-panel migration: two
  elements shared the id, so `$('image-results')` only ever reached the first and the second was
  dead markup. Removed with the rest of the layout work.

Tests: 553 pass (180 legacy + 373 new/extended).

- Removed, as asked: the topbar 小红书 button, the sidebar menu items (热点搜索 / 小红书工作区 /
  自动化 / 插件市场), the five-step indicator (01 需求澄清 … 05 导出交付), and the whole right-hand
  工作栏 including its platform switch and tab panels. The 工作栏's deliverable card moved into the
  chat area so the flow still works; the four deleted panels stay in the DOM, fully hidden, so the
  existing render paths do not break while the studio is designed.
- Added: a three-section nav (对话 / 热点榜单 / 图文工作室) above the old stepper position, a
  per-section sub-tab row (热点榜单 carries the plan's five scenario views), and four delivery-surface
  tabs in the middle of the 对话 header.
- **Clicking a surface tab is the lock.** The separate 锁定 button is gone: making a tab blue locks
  the conversation to that surface on the server. A failure reverts the tab by re-reading the
  server, so the UI cannot show "locked" when it is not. 朋友圈 keeps a small 门店/个人 split because
  those are two different surfaces and guessing one would be wrong.
- The lock is real, not a label: it is stored on the conversation's task state and injected into the
  model's system prompt as a directive. Verified live - with `store_poster` locked, the reply came
  back as a poster (主标题 8 字 / 副标题) rather than a note.

### Added (task batch deletion)

- `POST /api/v2/conversations/batch-delete` plus a batch mode in the sidebar: tick several tasks,
  全选, delete. The response separates **deleted / missing / failed** and says so ("不合并成一句成功"),
  because a bare total hides a partial failure. Duplicate ids count once, an empty selection is 400,
  and a batch over 100 ids is refused. The single-delete endpoint keeps its 404.

Tests: 533 pass (180 legacy + 353 new/extended).

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

### Removed

- **The Xiaohongshu MCP is gone**: `app/xhs.py`, `app/hotspot.py`, `/api/v1/xhs/*` (status,
  login qrcode, login status, hot) and `/api/v1/hotspots`, plus the 连接小红书 button, the QR
  modal and the polling code. It was measured to be useless: the cloud IP triggers risk control,
  the upstream MCP has no interface to submit the SMS code, and its chart contains no memes.

### Fixed

- **The board tabs were squeezed into one vertical strip** ("抖音热…" truncated, with a scrollbar).
  Root cause: the base `.section-tabs` rule carries `flex: 0 0 44px` - a *height* for the global
  bar inside a column container, but inside a row container it becomes a **44px width**, and with
  `overflow-x: auto` that turns the whole row into a narrow scrolling column. The hot row now reuses
  the same pill styling as the 对话 surface tabs (`.surface-tabs` / `.surface-btn`) instead, which is
  also exactly the row the user pointed at as the target look.

Tests: 564 pass (180 legacy + 384 new/extended).

### Changed (hot section layout, per the annotated screenshot)

- **「清单说明」panel removed**, along with the whole right-hand column. The list now fills the
  width as a **grid of ordinary-sized cards** (`auto-fill, minmax(230px, 1fr)`) instead of one tall
  narrow column beside an empty panel. Cards are compact: title clamped to two lines, gist to
  three, intro to five, with the actions and source folded in.
- The mode tabs (热梗/抖音热榜/微博热搜/知乎热榜/小红书热榜) moved **up into the header row**, in
  the same position as the 对话 section's surface tabs. This is the reading I took from the
  "替换上去" arrow; the surface tabs stay in 对话 because the lock depends on them. If the intent
  was to delete the surface tabs outright, that is a one-line change - say the word.

### Fixed

- **`取到 undefined 条` in the hot header.** The search path's `source` object carries
  `result_count` / `providers` while the chart path carries `available` / `source`, and the
  renderer only read the latter, so the count was always undefined on the working path. Both shapes
  are now handled.
- Two more leftovers from the earlier migrations: an empty `原始条目：` line rendered when a topic
  had no source titles, and the whole old `showHotDetail` / `useHotTopic` / `renderHotList` family
  still writing into the deleted panel.

### Self-correction

Cutting the old detail panel also removed the closing brace of `renderOrganizedTopics`, which broke
the file (`SyntaxError: Unexpected token ')'`). Re-added, and the file passes `node --check`.

Tests: 563 pass (180 legacy + 383 new/extended).

- **`app/hot_charts.py` + `GET /api/v1/hot-charts`**: the raw public charts (抖音/微博/知乎/小红书)
  that the user asked to keep, as their own cards. Only the sources measured to work are listed -
  baidu 404, bili 500, toutiao times out. Each board reports its own success, and a board that
  returned nothing says so rather than looking empty.

### Fixed

- **The hot list vanished when leaving the section.** `GET /api/v1/hot-topics` still called the old
  chart path while the refresh button called the new search path, so a refresh was correct but
  coming back re-loaded the broken version - which is the duplicated error and empty cards in the
  screenshot. Both now call `organize_from_search`, and the cached read is the same data, so the
  list survives navigating to 对话 and back until 刷新 is pressed again.

Tests: 559 pass (180 legacy + 379 new/extended).

- **Serper (Google) as the preferred search provider**, configured with `SERPER_API_KEY` from
  `.private/search.env` (outside the repo, mode 600, never printed or committed). The keyless
  routes become a fallback rather than an equal. Measured: **1.6 s for 10 results, with a date on
  each**, against rate-limited keyless routes returning SEO listicles months old.
- Recency is now enforced by the provider (`tbs=qdr:m`, last month) and the queries are worded for
  it ("最近/本月/现在最火", "是什么梗/出处/含义"), instead of carrying a year - "2026 网络热词"
  returned April and first-half articles, which is exactly where "又旧" came from.
- `date` is carried from each search result into the material and the prompt **requires** using it.
  First run had every `when` as `unknown` despite Serper returning a date per result.

### Result

The list now contains the meme the web version produced, with a current date and a real source:
**不烧心文学 2026年10月10日** ("贵是贵点，但吃了不烧心" 等台词掀起模仿风潮，中国媒体称其为
"不烧心文学"), plus **从夯到拉 / 爱你老己 / 手搓 2026年9月27日** sourced to 《咬文嚼字》责编施隽南.
Items from undated listicles are marked `unknown` rather than given a guessed date.

### Fixed

- `NameError: name 'os' is not defined` in `app/web_search.py` - `os.getenv` was used without
  importing `os`. A plain `import` check passed because the error only fires on call, and the
  endpoint returned a 21-byte error body until it was caught.
- `.private/search.env` needed `export`: a bare `SERPER_API_KEY=...` sets a shell variable but
  child processes never see it, so the key silently loaded as missing.

Tests: 566 pass (180 legacy + 386 new/extended).

- **联网搜索 `app/web_search.py`** (no API key) and `organize_from_search`, now the default for
  the 热点榜单 refresh. This is what closes the quality gap, and the reason was measured, not
  guessed: asked for recent memes the main model **refused honestly** ("我的知识截止时间早于该
  时段……不能凭推测编造", zero topics), and **no chart is a meme corpus** - 小红书 0/20, 微博 0/50,
  抖音 1/49, 知乎 0/30 items about memes. The web version of the same model produced a good list
  because it could search; the difference was never the prompt.
- Search also removes the fabrication problem from the previous round. With bare chart titles as
  the only input, a 100-character intro could only be invented - every intro claimed what "大家"
  was discussing, and all 19 were fabricated while reading plausibly. Search snippets give real
  sentences to work from, and the model now grounds each claim: 奶绿波 "形容高速堵车时车流密集"
  (2026-04-08), 酸黄瓜 "自嘲普通、收入不高", 龙虾 "AI 智能体 OpenClaw 的昵称".

### Fixed

- **The prompt asked for information that did not exist, then forbade inventing it.** With only a
  title as input, "为什么好玩、大家在讨论什么" could only be fabricated - confirmed by counting
  "讨论" in the output: 19 of 19 intros. Now the intro is grounded in search snippets and the model
  is told not to pad with "资料未说明" either.
- **An ironic prompt bug that produced 0 topics:** the rule "涉及其他数码品牌的内容不要——我们只做
  华为 PC" was read as "only include memes related to Huawei PC", so the model answered "摘要中未出现
  任何与华为 PC 相关的具体梗，故无可整理条目". Now stated explicitly that the brand rule is about
  *excluding* competitors and that relevance to Huawei PC is not the model's call at this stage.
- **Search queries reworded after measuring them:** "2026 网络热词" returned the 数英 热词 TOP30, a
  头条 新词解析 and a 知乎 100-条 list, while "最近爆火的梗" returned a Baidu Baike entry for the
  *song* 最近 and a 网易云 track. Irrelevant results are now dropped deterministically, and the
  count of dropped items is reported.

### Known limit

The keyless route is **intermittent**: DuckDuckGo lite started answering with HTTP 202 +
`anomaly`/`challenge` after repeated use, and Bing's HTML page is the working fallback. Failures
are reported as `SEARCH_FAILED` / `NO_RELEVANT_RESULTS` - never as an empty list pretending to be
success, and never by falling back to the model's memory. A search API key would make this stable.

Tests: 569 pass (180 legacy + 389 new/extended).

- **每条热点带约 100 字短文、来源链接与真实搜索词.** The intro explains what the topic is, why
  it is fun and what people discuss, and is instructed to expand only from the given entries -
  no invented names, numbers or incidents. Verified live: 19 topics, intros of 95-119 characters.
- Links and keywords are taken from the **source entries, not written by the model**, so they can
  be trusted: the keyword is the actual Xiaohongshu search term behind the chart item
  (e.g. 我的家庭旅行更像是打副本). A "用这个热点写文案" button carries the topic - name, intro and
  search terms - into the conversation so the copy is written from the topic itself.

### Answered, not implemented

The ask was whether a topic link could be fetched and its content turned into copy. **No, and the
reason is concrete rather than a preference.** The link is a Xiaohongshu *search* page, not a note;
opening it needs a login session; and fetching it server-side runs straight into the risk control
that this project already measured and documented in `docs/15` ("云端 IP 触发风控要求短信验证码，
而 MCP 没有提交验证码的接口"). The app also has no web-fetch capability at all. So the link is an
entry point for a person to open, and the copy is generated from the topic itself - which is what
the new button does. The response says this in `link_note` rather than leaving it to be discovered.

Tests: 553 pass (180 legacy + 373 new/extended).

- **热点清单（取源 + 模型整理）** `app/hot_topics.py`, `GET/POST /api/v1/hot-topics*`, and the
  热点榜单 section's 刷新 button. Point of the architecture, all of it measured rather than
  assumed: **recency comes from the live chart, meaning comes from the model.** Asked for
  "近期热梗" the model spent its whole budget reasoning and returned *nothing*
  (`finish_reason=length`, `text_tokens=0`); asked to organise a chart it had in front of it, it
  answered in seconds with a clean list.
- The user's editorial direction is enforced, and one part of it in code: lean fun, skip news
  events, and **never involve other digital brands** - that last rule is a deterministic filter
  applied before the model sees the list, so it cannot be argued out of it, plus a post-filter.
  Dropped items are reported, never silently removed.
- No ranking and no scoring, as asked: a plain list of `topic / kind / gist / source_titles`.
  The `marketing_angle` field from the first attempt was **removed** - requiring an angle per
  topic pushed the model to either force a connection ("日常美食教程 → 厨房与书房多屏协同") or
  reject everything (0 of 20). Neither is what was asked for.

### Fixed

- **`chat_json_messages` could return an empty reply on any judgement task.** This gateway's
  model reasons before answering and the reasoning counts against `max_tokens`; on larger inputs
  it consumed the entire budget, so the reply came back empty with `finish_reason=length`.
  Measured: `enable_thinking=false` gives **1.5 s / 34 tokens** where the default gives
  **31.2 s / 2858 tokens**, and the larger failures produced no answer at all. Structured calls
  now send it unless `LLM_ENABLE_THINKING` says otherwise. The first attempt at this fix landed
  in `complete_json` instead of `chat_json_messages` — two functions with near-identical bodies —
  and the empty replies continued until that was caught.

### Known limit

The public chart caps at **20 items**, not the 50 requested; the response says so
(`cap_note`) rather than implying a full 50. It also carries **no update time**, so the response
states that `observed_at` is our fetch time, not the platform's. Its content is a general
lifestyle/entertainment chart, so how many usable topics come out varies by hour.

Tests: 550 pass (180 legacy + 370 new/extended).

- **Delivery-surface suggestion with confirmation** (`suggest_surface`, tool 14). The user's
  rule has two halves and both are enforced: when the intent is unclear the assistant works out
  the right surface and offers it, and when the user has already been explicit it must not switch
  on its own. A lock *is* the explicit choice, so the tool returns `skipped: already_locked`,
  records nothing and says so - verified live, where a locked run replied "这个交付面是你最初就
  锁定的方向，我直接按它交付" with no suggestion attached.
- The suggestion never switches anything by itself: it is proposed, the UI asks, and only a
  confirmed click locks the surface, moves the tab and updates the conversation's instruction.
  Both halves were verified against the live model: unlocked and vague produced a suggestion with
  a real reason, locked produced none.

### Fixed

- The suggestion was lost whenever a run ended in `waiting_user`. It was only carried out through
  `finalize`, which a paused run never reaches - and "work out the surface while asking a
  question" is the common case, not an edge one. Found by running it: the tool had been called,
  the suggestion recorded, and the payload contained nothing. It is now also written to the run's
  events, and `_surface_suggestion` prefers the candidate and falls back to the event.

Tests: 544 pass (180 legacy + 364 new/extended).

- **Real task history**: the sidebar now lists backend v2 conversations (`GET /api/v2/conversations`) with auto titles, stage, message count and last-updated time. Clicking a task restores its messages, artifacts, candidates, plan and images, so multi-turn work actually continues.
- **Task menu (⋯)**: pin/unpin (pinned tasks stay on top), rename, and delete (also removes messages, runs, artifacts, revisions, tool executions and task state server-side).
- `PATCH /api/v2/conversations/{id}` and `DELETE /api/v2/conversations/{id}`.

### Changed

- The sidebar no longer stores one localStorage entry per message; localStorage is only an offline fallback.
- Auto titles skip greetings and short commands, using the first substantive user message.

## 0.15.0 — 2026-10-20

### Recipes: approved, packaged, and wired into image generation

24 style recipes were generated one image each against a real KB product photo
(HUAWEI MateBook 14 / 原野绿正面产品展示) and reviewed by the project owner.

- **22 approved, 2 rejected.** R04 水墨空间 and R22 文字主视觉 both **dropped the product
  entirely** - and the cause is structural, not random: the recipe text says "本图不出现产品、
  替代商品、品牌标识" while the一 approval criterion needs the product present. Both rejected
  recipes are the abstract/graphic kind, where that sentence wins; the environment/scene recipes
  carry a "产品预留区" reference, so the model keeps the product.
- **The approval is a record, not an edited field.** `app/data/recipes/releases.json` holds who
  decided, when, the approved/rejected lists, the prompt rule and a sha256 per recipe as evidence.
  `usable()` and `is_approved()` read that record; the recipe files still all say
  `unverified_real_images`, which is exactly what the package demands ("手改 JSON 不等于生产批准").
  `approve()` now really writes the record instead of returning `RELEASE_RECORD_NOT_IMPLEMENTED`.
- **The user's sentence is baked in**: `PRODUCT_CLAUSE` = "产品要求和原图中的产品一致，大小比例
  logo外观保持不变。" and `prompt_for()` composes it into every prompt. The conflicting recipe
  clause is replaced at build time (`CONFLICTING_CLAUSE` → `REPLACEMENT_CLAUSE`), because it is the
  documented cause of both the two missing products and the missing `HUAWEI` wordmarks.
- **Wired into generation**: `studio.generate_image` takes `recipe_id`, or auto-selects an approved
  recipe for the conversation's locked surface via `recipes.select`. An unapproved recipe is
  **refused** (`RECIPE_NOT_APPROVED`) rather than silently used, and the chosen recipe travels in
  the result as provenance; when no approved recipe exists it falls back to the generic prompt and
  says so (`why_no_recipe`).

Evidence images (22) are archived outside the repo in `.private/recipe-evidence/`, and all test
images were packaged to `~/Desktop/配方测试图_20261022/` (22 approved + `_已剔除/` for the 2).

### Known and carried forward

`HUAWEI` wordmark was not resolvable at preview scale in R06/R07/R13/R15/R19/R24 - to be checked on
the full-size files. Tests: 575 pass (180 legacy + 395 new/extended).

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

### Added

- **图文工作室** (`/studio/workbench`, `app/studio.py`). The user's flow: conversation →
  copy → the knowledge-base original photo comes out → the user either goes to the studio or
  keeps editing the copy → in the studio they generate images and edit the copy. The entry
  appears on the deliverable card and carries the conversation id, so the fixed product and
  the rough direction travel with it; nothing is retyped.
- The studio's copy editor saves **through the agent's own `_save_artifact`**, so an edited
  copy faces the same fact check, the same revision numbering and the same STALE_REVISION
  guard. A weaker side door would make the studio a way around verification rather than a
  workspace, which the tests pin down explicitly.
- `GET/POST /api/v2/conversations/{id}/studio` and `.../studio/image`; generation reuses the
  probe-backed capability gate and labels output `generated_scene`.

### Added (hotspot centre core, routes land separately)

- `app/hotspot_rules.py`: generic scoring with no brand, product profile or model dependency
  (the plan forbids all three) - position/coverage weights, freshness factors, five scenario
  views, soft diversity and the commercial risk policy. The rank-decay curve is named as an
  implementation choice because the package gives the weights but not the curve.
- `app/hotspots.py` + 12 `hs_` tables: source admission (six candidates, all
  `enabled=false, approval_status="pending"` as the plan's templates ship them), the six
  separate times, the refresh state machine, conservative clustering, atomic release
  publication, selections, favourites and idempotency.
- Two honesty rules are enforced in code rather than in prose: a fetch that did not reach
  the origin is `cache_only` and **does not advance** `verified_upstream_at` ("HTTP 200 only
  proves the response succeeded"), and a `stale_fallback` counts as `failed`. Same-origin
  rows from two providers count as one vote.
- Running inside this app on SQLite is stated on every response
  (`production_ready: false`): no NewsNow, no approved source, and the plan's PostgreSQL-only
  concurrency guarantees are not exercised.

Tests: 357 pass.
