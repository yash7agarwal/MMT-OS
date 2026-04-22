# Changelog

All notable changes are documented here following [Semantic Versioning](https://semver.org/).

## [0.15.1] — 2026-04-20 — Lens-detail PG fix + visible error surfaces

Patch cut after the MakeMyTrip "Lenses → 0 found" report. Root cause was the third occurrence of the same bug class: a number displayed somewhere diverged from the list endpoint behind it, and the frontend swallowed the resulting error with `.catch(() => {})`. This release fixes the specific query, replaces silent catches with visible error banners across five tabs, and expands the invariant suite to 32 tests so the class can't recur unnoticed.

### Fixed
- `webapp/api/routes/knowledge.py:547` — `/api/knowledge/lens/{name}` used `.like()` on a JSON column, which Postgres rejects with `operator does not exist: json ~~ unknown`. Now casts `lens_tags` to `String` via `sqlalchemy.cast` so the predicate works on both SQLite (TEXT-backed JSON) and Postgres (json/jsonb).
- `webapp/api/routes/knowledge.py:664` — trends-view's `observation_count` was computed from a `.limit(5)` slice, so any trend with >5 observations under-reported. Now uses a separate `func.count()` query for the total.

### Added
- `webapp/web/components/ErrorBanner.tsx` — shared error surface. Replaces silent `.catch(() => {})` with a visible banner on Lenses, Lens detail, Trends, Impacts, and Intelligence tabs. Silent failures are how three bug reports ("3 competitors but empty", "Lenses 0 found", "Trends undercount") survived — every user-facing fetch now has a loud error path.
- Six new invariant tests in `tests/test_stats_consistency.py` (32 total, up from 22):
  - `test_lens_detail_returns_data_when_matrix_has_counts` — if matrix reports non-zero counts, `/lens/{name}` must return ≥1 entity.
  - `test_lens_matrix_totals_roughly_match_detail` — matrix sum ≤ detail observation count.
  - `test_trends_observation_count_is_not_truncated` — `observation_count ≥ len(observations[])`.
  - `test_entities_endpoint_honors_high_limit` — `?limit=500` is honored.
  - `test_no_tab_endpoint_returns_5xx` — every tab endpoint returns <500 for every project.
  - `test_lens_detail_endpoint_never_500s` — the specific endpoint that was broken must return 200 for every known lens.

### Changed
- `tests/test_stats_consistency.py` — collection no longer aborts when the target API is unreachable; instead individual tests are parametrized with zero cases and fixture-based tests call `pytest.skip` at runtime.

## [0.15.0] — 2026-04-21 — Prism↔Loupe PRD bridge + metric-consistency guard

### Added
- `utils/loupe_client.py` — graceful HTTP client for Loupe's REST API. Returns empty evidence bundles when Loupe is unreachable so callers never wrap in try/except.
- `agent/prd_synthesizer.py` — PRD/Insights synthesizer. Binds project ResearchBrief + Prism KG entities (fuzzy-matched by feature name) + Loupe UAT evidence → single Sonnet call → strict-shape Markdown. Saved as `KnowledgeArtifact(artifact_type='prd_doc')`.
- `webapp/api/routes/prd.py` — `POST /api/prd/generate`, `GET /api/prd/recent`, `GET /api/prd/feature-candidates` endpoints.
- **Telegram `/prd`**: with an arg (`/prd hotel rebooking`) → direct generation. Without an arg → inline keyboard of recent TestPlans ∪ starred trends (F1 from UX-friction plan). F2: every digest card now has a `[📝 Deep-dive (PRD)]` button that generates a PRD scoped to that trend.
- `tests/test_stats_consistency.py` — invariant suite that asserts `stats.competitor_count == len(/competitors)`, `stats.entity_count == len(/entities)`, and related consistency checks across all projects. 22 tests pass against live Railway.
- LESSONS.md chapters 10, 11, 12: deployment journey · Prism↔Loupe integration · the competitors-mismatch bug + metric-consistency principle.

### Fixed
- `/api/knowledge/competitors` used to require a `competes_with` `KnowledgeRelation` that not every discovery path created, while the project detail card counted raw `entity_type='company'` entities. Result: Intuit and Sarvam.ai showed "3 competitors" in stats but empty lists in the tab. Fixed by aligning `/competitors` on the same `entity_type='company'` filter — one source of truth.

### Deployment notes
- New env var: `LOUPE_API_URL` (default `http://localhost:8001`). Set it in `prism-api` on Railway once Loupe is deployed.

## [0.14.1] — 2026-04-20 — Postgres live + cleaner Vercel URL

### Deployed
- **Postgres cutover complete**: Railway Postgres attached, `DATABASE_URL` wired into `prism-api`, data migrated. All 15 tables matched source row-for-row (3 projects · 169 entities · 273 observations · 108 sessions · 2884 test_cases).
- **Cleaner Vercel alias**: `https://prism-intel.vercel.app` now points at the same deploy as the verbose `prism-y4shagarwal-3895s-projects.vercel.app`.

### Changed
- `webapp/api/main.py` CORS `allow_origins` includes `prism-intel.vercel.app` + `prism-three-alpha.vercel.app` (Vercel's auto-assigned short alias) so the frontend can call the API from either URL.

### Fixed
- `tools/migrate_sqlite_to_postgres.py` — three successive bugs discovered while running the live migration, all fixed:
  - Single-row executemany → Postgres COPY FROM STDIN (~100x faster over the Railway TCP proxy; killed a 7-min stuck run).
  - `csv.writer(escapechar='\\')` was mangling the `\N` NULL marker so Postgres rejected integer columns. Replaced with manual TSV escaping.
  - Bytes columns (KnowledgeEmbedding.embedding_blob) needed `\x<hex>` bytea literal, not `str(bytes_value)`.

## [0.14.0] — 2026-04-20 — Dual-mode DB (Postgres-ready)

### Added
- `webapp/api/db.py` now reads `DATABASE_URL` and uses Postgres when set; falls back to the local SQLite file otherwise. Normalizes Railway's legacy `postgres://` scheme → `postgresql://` for SQLAlchemy 2+.
- `tools/migrate_sqlite_to_postgres.py` — SQLAlchemy-based row-by-row copier. Walks tables in FK dependency order, preserves primary keys, resets Postgres sequences, and verifies source-vs-target counts. Has `--dry-run` to preview without writing.
- `psycopg2-binary` added to `requirements.txt` (unused locally until `DATABASE_URL` is set).

### Changed
- `_dedup_knowledge_entities` in `db.py` now aggregates duplicates in Python (`collections.defaultdict`) instead of SQLite's `GROUP_CONCAT` — portable across backends.

### Deployment path
Once a Postgres service is attached on Railway (`railway add --database postgres` via dashboard/CLI) and `DATABASE_URL` is referenced on `prism-api`:
1. Redeploy `prism-api` — image picks up `psycopg2-binary`, `init_db()` creates the schema on Postgres.
2. Run `DATABASE_URL=<railway-postgres-url> python -m tools.migrate_sqlite_to_postgres` from local → copies all 3 projects, 169 entities, 273 observations, 108 sessions.
3. Detach the old Railway SQLite volume (data no longer lives there).

## [0.13.4] — 2026-04-20 — CORS for Vercel frontend + is-a.dev

### Added
- CORS allow-list expanded: `prism.is-a.dev` (pending PR #36711) + Vercel production alias via regex `prism(-<hash>)?-y4shagarwal-3895s-projects.vercel.app` so every immutable deploy URL is accepted without per-deploy edits.
- `CORS_ALLOW_ORIGINS` env var — comma-separated extra origins for ad-hoc previews without code changes.

### Deployed
- **Vercel (webapp/web)**: https://prism-y4shagarwal-3895s-projects.vercel.app — Next.js frontend, free hobby tier, `NEXT_PUBLIC_API_URL` points at Railway. Deployment Protection disabled so the URL is publicly reachable.
- **is-a.dev PR**: https://github.com/is-a-dev/register/pull/36711 claims `prism.is-a.dev` → CNAME → Railway API URL. Pending maintainer merge.

## [0.13.3] — 2026-04-20 — Add python-multipart so FastAPI Form routes load

### Fixed
- `requirements.txt` was missing `python-multipart`, which FastAPI now requires at import time for any route that declares `Form()` or `UploadFile`. The v0.13.2 image built cleanly but crashed the API at route registration with a `RuntimeError` before serving a single request. Fixed by adding `python-multipart>=0.0.9` to the manifest. First live Railway deploy confirmed after this landed — `/api/health` on `prism-api-production-18bf.up.railway.app` returns `{"status":"ok"}`.

## [0.13.2] — 2026-04-20 — Per-service ENTRYPOINT dispatch

### Fixed
- The first Railway deploy attempt ran the Telegram bot on both services because `RAILWAY_RUN_COMMAND` isn't actually an honored Railway variable — the Dockerfile CMD wins. Two simultaneous pollers hit Telegram's 409 conflict, api container crashed, public URL 502'd.
- `docker-entrypoint.sh` now dispatches on `SERVICE_TYPE`: `api` → `uvicorn webapp.api.main:app --host 0.0.0.0 --port $PORT`, `bot` (default) → `python -m telegram_bot.run_bot`. Set `SERVICE_TYPE=api` on `prism-api` and `SERVICE_TYPE=bot` on `prism-bot` in Railway Variables.

## [0.13.1] — 2026-04-20 — Railway-ready Dockerfile

### Changed
- `Dockerfile` now installs from the full `requirements.txt` (was `requirements.bot.txt`) so both `prism-api` and `prism-bot` can run from one image. Default CMD stays as the bot; `prism-api` overrides via `RAILWAY_RUN_COMMAND=uvicorn webapp.api.main:app --host 0.0.0.0 --port $PORT`.
- `requirements.txt` trimmed to the live stack: added explicit `sqlalchemy>=2.0`, `httpx>=0.27`; removed UAT-era deps (`uiautomator2`, `pixelmatch`, `lxml`, `mcp`, `aiohttp`) that moved to Loupe in v0.10.0.
- `railway.json` no longer hard-codes `startCommand` — per-service `RAILWAY_RUN_COMMAND` variables drive it now so both services can share one config.

## [0.13.0] — 2026-04-20 — Phase 2 + Phase 3 complete

Closes out the research-architecture roadmap. Every surface from `/Users/yash/.claude/plans/polished-hatching-bubble.md` is now in the repo and verified.

### Added
- `agent/decay.py` — daily sweep marks trends + regulations with no observation in 60 days as `decay_state='needs_revalidation'`. The research brief surfaces them as validation targets so the next planner run probes for fresh evidence.
- `agent/semantic_dedupe.py` + `utils/gemini_embeddings.py` — embedding cosine dedupe layer (Gemini text-embedding-004, 768-dim, stored as float32 bytes in the existing `KnowledgeEmbedding` table). AUTO_MERGE at 0.90, LLM tie-breaker (Haiku) in the ambiguous 0.78–0.90 band. Graceful fallback when provider unavailable: upsert proceeds as if the layer didn't exist. Verified via monkey-patched test (similar "Dark store fulfilment" variants merge; unrelated concepts stay distinct).
- `agent/pattern_writer.py` — post-session hook that extracts successful planner queries into `memory/patterns.md` when both `retrieval_yield ≥ 0.7` AND `novelty_yield ≥ 0.5`. Idempotent by session id. Live test back-filled the Swiggy session 98 plan.
- `config/source_authority.yaml` + loader in `tools/web_research` — hard blocklist for paywalled / anti-bot domains (Moneycontrol, Business Standard, Medium, LinkedIn Pulse), plus tier 1–4 authority mapping. Every search result is tier-ranked before return; `search()` over-fetches +5 so blocklist doesn't starve `max_results`.
- `tools/rss_retriever.py` + `tools/reddit_retriever.py` + `config/rss_feeds.yaml` + `config/reddit_subreddits.yaml` — feature-flagged alt retrieval surfaces gated by `PRISM_RETRIEVERS=rss,reddit`. Both normalize industry strings (underscore → space) when matching config keys, so "food delivery and quick commerce" from the planner correctly maps to the `food_delivery` + `quick_commerce` buckets. Merge into the same retrieval bundle as web search and pass through the source_url validator.
- `CrossProjectHypothesis` table + `webapp/api/routes/xproj.py` — suggestion queue for cross-project transfer. `POST /api/xproj/suggest` registers a hypothesis; `GET /api/xproj/suggestions` lists by target project; `POST /{id}/accept` clones the entity at confidence 0.4 (forcing re-validation on the target); `POST /{id}/reject` marks it. Human-gated by construction — no auto-promotion, ever. Prevents the v0.11.0 contamination class.
- Trends-page Keep / Dismiss / Star / Purge buttons at `/projects/[id]/trends` (`webapp/web/app/projects/[id]/trends/page.tsx`) — mirrors the Telegram digest feedback surface for desktop. Dismissed entities disappear optimistically; purge confirms before cascade-delete + re-enqueue. Wires the `user_signal` column end-to-end.
- `KnowledgeEntity.decay_state` column — idempotent ALTER TABLE in `db.init_db()`.
- `agent/quality_regression._run_regression_check` now also calls `agent.decay.sweep_once()` on its 24h tick.

### Changed
- `agent/efficient_researcher.research_industry_trends` — RSS + Reddit results join the retrieval bundle when `PRISM_RETRIEVERS` is set. Kept at `raw_data.append` level so all downstream stages (source_url validator, synthesis cap, quality scoring) treat them identically.
- `agent/research_brief` — unions observation-age staleness with the persistent `decay_state='needs_revalidation'` flag when building `stale_trend_canonicals`.
- `agent/knowledge_store.upsert_entity` — embedding layer slotted between trigram and insert. Lazily writes embeddings on every new entity via `semantic_dedupe.store_new_embedding`.
- `tools/web_research.WebResearcher.search` — now post-processes every provider's results through `_rank_by_authority`; blocklisted hosts dropped, each result carries a `tier` field, sorted tier-ascending.

## [0.12.2] — 2026-04-20 — Railway deploy prep

### Added
- `PRISM_AUTO_DAEMON` env flag — when set to `1`, the FastAPI startup hook launches a `ProductOSOrchestrator` daemon per project on boot. Gated because local `uvicorn --reload` would otherwise spam provider APIs on every restart. Intended for Railway + any long-running production target.
- Refreshed `.env.example` to match the current provider stack (Claude / Gemini / Groq / Tavily / Brave), current Telegram variables (`TELEGRAM_PM_CHAT_ID`, `TELEGRAM_CHAT_ID`), the `PRISM_SYNTH_CHEAP` synthesis-provider toggle, and the new `PRISM_AUTO_DAEMON`. Removed stale UAT-era variables (`FIGMA_API_TOKEN`, `UAT_ACCOUNTS_FILE`, `UAT_FEATURE`, `DEVICE_SERIAL`).

### Deployment notes
- Two-service Railway setup:
  - `prism-api`: `uvicorn webapp.api.main:app --host 0.0.0.0 --port $PORT`, env `PRISM_AUTO_DAEMON=1`
  - `prism-bot`: `python -m telegram_bot.run_bot`, env `PRISM_API_URL=http://prism-api.railway.internal:$PORT`
- Persistent storage: mount a Railway volume at `/app/webapp/data` so SQLite + screenshots survive redeploys.

## [0.12.1] — 2026-04-20 — Fix: competitor profile observations leaked to `app` sibling

### Fixed
- `agent/competitive_intel_agent.execute_work_item` — the profile-save loop was calling `find_entities(name_like=competitor_name)` without an `entity_type` filter, so the first match (often an `app`-typed sibling like "Blinkit (by Zomato) App" created by ux_intel) collected all `add_observation` writes. The company-typed entity received zero observations, which caused `/api/knowledge/competitors` to compute 10% confidence (the "0 obs → 0.1" branch) for any competitor whose name overlapped an app entity. Affected 3 of 8 Swiggy competitors (Blinkit, Zepto, Rapido). Fix resolves `target_company_id` once per work item via `find_entities(entity_type="company", name_like=…)` and reuses that id for every observation + artifact write. Verified via monkey-patched test: 3 stub findings landed on company id=26, 0 on app sibling id=27, artifact `entity_ids_json=[26]`.

## [0.12.0] — 2026-04-20 — Phase 1.5: quality regression alerts + one-click purge

Closes the feedback loop that v0.11.0 opened: the system now notices when its own quality is degrading and gives the PM a one-tap remedy for bad data.

### Added
- `agent/quality_regression.py` — computes 7-day rolling `retrieval_yield` + `novelty_yield` per project from `AgentSession.quality_score_json`, compares to prior 7-day window, and sends a Telegram alert when either metric drops >30% w/w. Includes cheap heuristic hypotheses: "daemon may not be firing", "validator drop rate up (model regression?)", "novelty_yield low (KG saturated — broaden brief)". Runnable as `python -m agent.quality_regression` or via the orchestrator daemon's once-per-24h tick.
- `POST /api/knowledge/entities/{id}/purge` — tombstones a mis-tagged entity (`user_signal='dismissed'` + `dismissed_reason`), cascade-deletes its observations + relations, and enqueues a high-priority `niche_trend_discovery` work item. The canonical name is automatically picked up by the next `ResearchBrief` as a dismissed negative example.
- Telegram `/purge <entity_id> [reason]` — mirrors the HTTP endpoint so bad data can be fixed from the phone.

### Changed
- `webapp/api/routes/knowledge.get_trends_view` now filters out entities with `user_signal='dismissed'` — purged or user-dismissed items no longer clutter the trends page.
- `webapp/api/routes/knowledge.get_entity` detail endpoint now returns `user_signal` + `dismissed_reason` (was constructing `KnowledgeEntityDetail` by hand and missed the new fields).
- `agent/product_os_orchestrator` daemon now schedules a daily quality-regression check as a detached thread alongside the existing per-agent session scheduling.

## [0.11.0] — 2026-04-20 — Compounding research architecture (Phase 1)

Cross-industry contamination bug (Swiggy getting travel trends because `efficient_researcher.py` hardcoded 6 of 8 trend queries to travel terms) is now architecturally impossible. Queries derive from a typed per-project brief + Haiku-planned research plan, not from templates. Synthesis output passes a deterministic source-URL validator before touching the KG. Feedback loop wired from Telegram buttons.

### Added
- `agent/research_brief.py` — typed `ResearchBrief` + builder. All project context (name, description, competitors, recent trends, user-starred/dismissed, low-confidence entities, stale trends) flows through this object; no other path exists for project metadata to reach downstream stages.
- `agent/query_planner.py` — single Haiku call per brief returns a structured plan (discovery + deepening + validation + lateral queries). Plans persist as `KnowledgeArtifact(artifact_type='research_plan')` keyed on `(project_id, brief_hash)` with a 24h TTL; re-runs on unchanged briefs hit the cache.
- `agent/synthesis_validator.py` — deterministic check that every candidate observation's `source_url` appears in the retrieval bundle. Drops anything the model invents, logs drop count+reasons to `AgentSession.quality_score_json`. No LLM, no embedding — cheapest possible hallucination guardrail.
- `telegram_bot/digest.py` — outbound digest sender. After every `industry_research` session, posts one compact message per new high-confidence trend with inline `[👍 Keep] [✖ Dismiss] [⭐ Star]` buttons. Raw-httpx, importable from any process.
- `KnowledgeEntity.user_signal` + `dismissed_reason` columns — captures button taps. Dismissed canonicals become negative examples in the next brief.
- `AgentSession.quality_score_json` column — deterministic per-run metrics (retrieval_yield, novelty_yield, validator counts, inferred_industries, plan_cached_ratio, plan_query_count_avg). Powers future regression alerts.
- `POST /api/knowledge/entities/{id}/signal` — writes user_signal; Telegram callbacks and web UI both use it.
- Trigram normalized-name dedupe layer in `knowledge_store.upsert_entity` — strips `.com`/`Inc`/`Ltd`/etc., unicode-folds, merges on Jaccard ≥0.9. "Booking" and "Booking.com Inc." converge on a single entity.

### Changed
- `efficient_researcher.research_industry_trends` now takes `(brief, plan)` — hardcoded travel search queries and travel-specific synthesis exemplars removed. Returns retrieval bundle alongside candidates so callers validate before writing.
- Synthesis provider default flipped: **Claude Sonnet** (accuracy-first for the hallucination-sensitive stage), Groq behind explicit `PRISM_SYNTH_CHEAP=1`, Gemini as fallback.
- `industry_research_agent.execute_work_item` now builds the brief, fetches/generates the plan, runs retrieval, validates, and accumulates per-item quality metrics.
- `base_autonomous_agent.run_session` aggregates per-item quality into `AgentSession.quality_score_json` at session close and fires the Telegram digest for trend runs.
- `industry_research_agent._build_work_prompt` — travel-specific examples ("solo travelers", "bleisure", "pet-friendly travel") replaced with domain-agnostic lens families; forbids cross-industry leakage.
- `webapp/api/routes/knowledge.py` trends-view now exposes `user_signal`, `dismissed_reason`, `confidence` on each trend.
- `KnowledgeEntityOut` + `AgentSessionOut` DTOs extended with the new fields.

### Fixed
- **Cross-project contamination (root cause)**: Swiggy (food delivery) had 8 travel-themed trends tagged to it from session 97 (`2026-04-19 19:07:08`). Cleaned up; re-ran yields food-delivery-native trends (Dark Store Fresh-Prep Upselling, Hyper-Local Ultra-Fast Hot Meal, QSR Speed, Food Hygiene).
- **Timezone display**: added `UTCDatetime` serializer to `webapp/api/schemas.py` so naive `datetime.utcnow()` timestamps serialize with a `Z` suffix. "Last ran 5h ago" bug (IST offset error) fixed.
- **Gemini tool-schema round-trip**: `utils/gemini_client.ask_with_tools` schema converter now recurses properly into array-of-object and nested-object types. Previously flattened to STRING, dropping the object shape.
- `telegram_bot/digest._md_escape` escapes backslashes before special characters to avoid double-escaping; source URLs in digest messages now escape too (previously triggered Telegram 400s).

### Migration notes
- Schema additions land via idempotent `ALTER TABLE IF NOT EXISTS` in `webapp/api/db.init_db()` — no Alembic step required.
- Set `TELEGRAM_PM_CHAT_ID` (or keep `TELEGRAM_CHAT_ID`) in `.env` to receive digest messages. Button callbacks require `python -m telegram_bot.run_bot` polling process.
- `PRISM_SYNTH_CHEAP=1` env var reverts synthesis to Groq Llama for budget-conscious runs.

## [0.10.0] — 2026-04-19 — UAT carved out to Loupe

Prism is now a pure product intelligence platform. The UAT half was carved into a sibling repo, **Loupe** (`yash7agarwal/loupe`), using `git filter-repo` to preserve UAT commit history back to v0.1.0. See LESSONS.md for the decision rationale.

### Removed (moved to Loupe)
- 15 UAT agent files: `flow_explorer_agent`, `scenario_runner_agent`, `evaluator_agent`, `diff_agent`, `figma_comparator`, `figma_journey_parser`, `figma_uat_runner`, `variant_detector`, `health_monitor`, `use_case_registry`, `report_writer_agent`, `quick_uat`, `run_uat`, `run_quick_uat`, `orchestrator` (legacy)
- 8 UAT tools: `android_device`, `apk_manager`, `emulator_manager`, `quick_navigator`, `vision_navigator`, `visual_diff`, `screenshot`, `report_generator`
- 3 UAT services: `uat_runner`, `figma_importer`, `figma_test_planner`
- 2 UAT API routes: `uat_runs`, `figma`
- 4 UAT tables removed from `models.py`: `UatRun`, `UatFrameResult`, `FigmaImport`, `FigmaFrame` (existing SQLite rows retained, just no code path)
- UAT Pydantic schemas: `UatRunCreate`, `UatRunOut`, `UatRunSummary`, `UatFrameResultOut`, `FigmaImportCreate`, `FigmaImportSummary`, `FigmaImportOut`, `FigmaFrameOut`
- Frontend UAT pages: `/projects/[id]/uat`, `/projects/[id]/runs/*`, `FrameComparisonCard` component
- UAT tab from the project layout navigation (8 tabs → 7)
- Telegram bot UAT handlers: `/run`, `/status`, `/report`, `/list`, `/cases`, `/builds`, `/use_build`, `/upload_local`, `/run_figma`, all `/appuat_*`, plus `RunTracker`, photo/document/text message handlers, and UAT background workers — bot.py shrunk from 1539 to 467 lines
- `design_fidelity` plan type and Figma branch in `plans.py` (Figma plans live in Loupe now)
- `Dockerfile` (Android SDK + emulator) — Loupe owns this now
- `setup_emulator.sh`, `run_details_uat.py`, `smoke_test.py`, `apks/`, `candidate.apk` handling
- `mcp_server/` (Android-device MCP tools — UAT-side)

### Changed
- Renamed `Dockerfile.bot` → `Dockerfile` (now the single canonical image)
- Updated `docker-compose.yml` and `railway.json` to reference the new Dockerfile; dropped KVM/emulator volumes
- FastAPI `version` bumped to `0.10.0`; description updated to "Product intelligence platform"
- Telegram bot `/start` and `/help` texts rewritten around intel commands only
- `product_os_orchestrator` retains defensive `ux_intel` guards — UX Intel won't instantiate without device tools; this is expected v0.10.0 behaviour. Restoring ADB device capture as a Prism-native capability is v0.10.1 work.

### Migration notes
- On an upgraded install: existing `UatRun` / `FigmaImport` / `FigmaFrame` rows remain in the SQLite file but are no longer surfaced. Clean them via `DROP TABLE` if desired, or keep as historical.
- If you depend on UAT, clone [Loupe](https://github.com/yash7agarwal/loupe) and run it standalone — it has its own `loupe.db` and its own API on port 8001.

## [0.9.2] — 2026-04-18
### Changed
- Aligned entire frontend to DESIGN.md design system — removed all forbidden patterns
- Replaced all violet/indigo/purple accents with emerald/cyan/sky (9 files fixed)
- Removed box-shadow glows, replaced with border-color transitions
- Reduced oversized typography (text-5xl → text-2xl on match scores)
- Changed 3-column equal grids to 2-column asymmetric where applicable
- Updated prism logo SVG spectrum to use design-compliant colors (no violet)

## [0.9.1] — 2026-04-18
### Added
- `utils/groq_client.py` — free Llama 3.3 70B synthesis via Groq (14,400 RPD, zero cost)
- `agent/efficient_researcher.py` — deterministic search + single synthesis (1-2 LLM calls instead of 10-15)
- `LESSONS.md` — living project chronicle with 7 chapters, decision tradeoff register, provider stack
- `/project-chronicle` skill — cross-project historian, auto-invoked after git syncs, mandatory tradeoff analysis
- Anti-hallucination guards on all synthesis prompts (ecosystem-level rule)
- Tavily as primary web search provider (1000 free/month)

### Changed
- All 3 agents (competitive, industry, impact) now use efficient researcher — zero tool-use loop dependency
- Run-all endpoint includes impact_analysis (was only competitive + industry)
- Gemini thought_signature round-trip preserved (was causing 400 errors)
- FakeBlock serialization: content blocks converted to plain dicts for JSON compatibility
- Auto-retry failed work items + dedup + early-stop on 2 consecutive failures

### Fixed
- DuckDuckGo rate limiting (IP blocked after 50+ rapid searches) → Tavily fallback
- Groq model updated from decommissioned llama-3.1-70b to llama-3.3-70b
- Duplicate entities merged (ixigo, Yatra)
- Trend entities updated with timeline/category metadata (was all MISSING)
- Tavily API key duplicate prefix fix

## [0.9.0] — 2026-04-18
### Added
- **Analytical Lenses**: 8 strategic lenses (Product Craft, Growth, Supply, Monetization, Technology, Brand & Trust, Moat, Trajectory) for structured competitive analysis
- `lens_tags` column on observations — agents auto-tag findings with relevant lenses
- Lens matrix view (`/projects/[id]/lenses`) — competitors × lenses with drill-down per cell
- Lens detail view (`/projects/[id]/lenses/[lens]`) — all findings for one lens across competitors
- **Impact Analysis Agent** (`agent/impact_analysis_agent.py`) — traces macro trend → 2nd order effect → 3rd order company-specific impact
- Impact cascade view (`/projects/[id]/impacts`) — expandable trend cards with severity/timeframe badges
- **Industry Trends view** (`/projects/[id]/trends`) — timeline (past/present/emerging/future) with category filter, quantification data, and competitor adoption mapping
- Niche trend discovery (women-friendly travel, pet travel, accessibility, sustainability, etc.)
- Trend quantification work items (market size, search volume, growth rate)
- Trend adoption mapping (which competitors address which trends)
- **Financial intelligence** in competitor profiles — revenue, PAT, market cap, YoY growth, stock sentiment
- **Contrarian competitor discovery** — indirect competitors (substitutes, adjacent categories, platform threats, disruptors)
- `financial_deep_dive` and `contrarian_discovery` work item categories
- New API endpoints: `/lens-matrix`, `/lens/{name}`, `/impact-graph`, `/trends-view`
- Design refresh: prism triangle logo, pill-style tabs, card glow effects, favicon, spectrum accents

### Changed
- Tab bar restructured: Overview | Competitors | Lenses | Trends | Impacts | Ask | Intelligence | UAT
- Competitor detail page: markdown renderer for bold/lists/headers, expandable reports, grouped findings by type
- Overview page: clickable stats, product timeline with source links, live agent activity panel
- Impacts page: replaced SVG circle graph with intuitive expandable cascade list

### Fixed
- `_FakeBlock not JSON serializable` — content blocks now serialized to plain dicts in tool-use loop
- Gemini converter handles `tool_use` dict blocks from serialized messages
- Failed work items auto-retry on next session start
- Work item deduplication prevents redundant profiling of already-researched competitors
- Early-stop after 2 consecutive failures prevents wasting API calls on systemic errors
- Multi-project orchestrator: per-project instances instead of singleton (parallel projects work)

## [0.8.1] — 2026-04-18
### Changed
- Rebranded all source files from "MMT-OS" / "AppUAT" to "Prism" across 13 files (docstrings, comments, user-facing strings, FastAPI title, package.json name)
- Added GitHub project banner (`assets/banner.png`) with prism icon, spectrum, feature pills
- Added social preview image for GitHub cards
- README updated with centered banner, shield badges (version, Python, Next.js, license)

### Added
- `POST /api/product-os/run-all` — runs competitive intel + industry research agents in parallel
- "Run all agents" button on Intelligence page
- Gemini tool-use support (`gemini_client.ask_with_tools`) with Anthropic-compatible response objects
- Auto-fallback: when Claude hits credit/billing limits, agents seamlessly switch to Gemini
- Gemini ARRAY type schema conversion (was causing 400 errors)

### Fixed
- `runningAgent` React state never cleared — caused permanent stale "Running" status in UI
- Claude `ask()` now also falls back to Gemini on credit limit (not just `ask_with_tools`)
- Gemini 400 errors now logged with response body for debugging

## [0.8.0] — 2026-04-18
### Added
- **Multi-agent Product OS**: 3 autonomous agents (Competitive Intel, Industry Research, UX Intelligence) that self-direct research, build cumulative knowledge, and persist findings to a shared knowledge graph
- `agent/base_autonomous_agent.py` — base class with work queue, tool-use loop, bounded sessions
- `agent/knowledge_store.py` — knowledge graph CRUD: entities, relations, temporal observations, artifacts, screenshots, embeddings
- `agent/competitive_intel_agent.py` — discovers competitors, profiles features/pricing/strategic moves with evidence-backed findings
- `agent/industry_research_agent.py` — tracks industry trends, regulations, market data from analyst publications
- `agent/ux_intel_agent.py` — deep-maps app flows via Android device, curates user journeys
- `agent/product_os_orchestrator.py` — schedules agent sessions, manages device locks, generates daily digests
- `agent/query_engine.py` — natural language query pipeline: intent classification, knowledge retrieval, Claude-synthesized answers
- `tools/web_research.py` — web search (Tavily/Brave/DuckDuckGo fallback), page content extraction, Play Store app discovery
- 8 new DB tables: `knowledge_entities`, `knowledge_relations`, `knowledge_observations`, `knowledge_artifacts`, `knowledge_screenshots`, `work_items`, `agent_sessions`, `knowledge_embeddings`
- `webapp/api/routes/knowledge.py` — 12 read-only knowledge graph API endpoints including `/timeline` feed
- `webapp/api/routes/product_os.py` — 6 orchestrator + query API endpoints
- Unified tabbed project hub: Overview, Intelligence, UAT, Competitors, Ask, Backlog — all under `/projects/[id]/`
- Product Timeline on Overview page with color-coded findings, source links, and data freshness
- Telegram `/new` command — create a product and start research agents from phone in one message
- Telegram `/intel` commands — status, competitors, ask, digest, run agents from phone
- Vercel deployment config (`vercel.json`, `NEXT_PUBLIC_API_URL` env var support)

### Changed
- **Rebranded "AppUAT" to "Prism"** — generic product intelligence platform, not tied to any company
- Unified project creation: "New product" form with intelligence toggle, industry field, competitor hints
- `ProjectStats` enriched with `entity_count`, `observation_count`, `competitor_count`
- `ProjectCreate` extended with `enable_intelligence`, `industry`, `competitors_hint` for auto-start
- Competitor confidence now dynamic (0.1 stub → 0.9 profiled) based on actual observation count
- Agent system prompts rewritten for specificity: "SPECIFIC over broad", evidence-backed findings, 8-10 tool call efficiency bounds
- Removed confusing "autopilot" concept — each agent has independent Run button with clear status

### Fixed
- `runningAgent` React state never cleared after agent finished — caused permanent stale "Running" in UI
- Stale `.next` cache causing MODULE_NOT_FOUND at runtime after page moves (always clear before dev restart)

## [0.7.1] — 2026-04-12
### Fixed
- `tools/vision_navigator.py`: bumped `max_tokens` 256 → 1024 — fixes Gemini returning truncated JSON that caused every navigation step to fail with parse errors
- `tools/vision_navigator.py`: on JSON parse failure, now calls `device.press_back()` before retrying — recovers from stuck app states instead of repeating the same failing step
- `tools/vision_navigator.py`: increased `step_wait_s` default 1.5 → 2.5 — reduces Gemini free-tier RPM violations during navigation loops

## [0.7.0] — 2026-04-12
### Added
- `webapp/api/services/figma_importer.py` — proactive Figma importer that does ONE full fetch of a Figma file (structure + all frame images) and persists everything locally; subsequent UAT runs source data from DB + disk with zero Figma API calls
- `webapp/api/routes/figma.py` — 5 endpoints: `POST/GET/DELETE /figma/imports`, `GET /figma/imports/{id}`, image serving per frame
- `FigmaImport` + `FigmaFrame` ORM tables with structured design data columns (width/height/x/y, text_content, colors, fonts) extracted from the raw Figma node tree
- `_extract_frame_metadata` + `_walk_node` helpers — pure-Python extraction of dimensions, unique hex colors, and font tuples from Figma node trees (no LLM, no additional API calls)
- Frontend "🎨 Figma Imports" section on project detail page — inline form to trigger imports, list view with status/frame counts/error messages
- Alert banner on new-run page that blocks UAT run submission if no matching ready `FigmaImport` exists for the chosen `figma_file_id`

### Changed
- `webapp/api/services/uat_runner.py` — replaced `_cached_figma_parse` call with a DB lookup for the latest `status=ready` `FigmaImport` matching the project + `figma_file_id`; per-frame loop now uses `FigmaFrame.image_path` directly; results in **zero Figma API calls** once an import has been made
- `webapp/api/main.py` — mounts `figma` router (38 → 43 routes)
- `webapp/web/app/projects/[id]/page.tsx` — adds Figma Imports panel above UAT Runs; shows file name + frame count + status badge for each import
- `webapp/web/app/projects/[id]/runs/new/page.tsx` — polls `/api/projects/{id}/figma/imports`, disables submit when no matching import exists, shows which import will be used

### Fixed
- Graceful failure for Figma 429: the `POST /figma/imports` endpoint now persists a `status=failed` row with the full error trace, so the user can see what happened and retry without re-triggering the whole flow

## [0.6.0] — 2026-04-11
### Added
- `webapp/api/services/uat_runner.py` — APK-driven E2E execution engine: installs candidate APK, parses Figma file (cached), launches app, drives VisionNavigator through each substantive Figma frame, screenshots, compares via FigmaComparator, persists `UatRun` + `UatFrameResult` rows, writes markdown report
- `webapp/api/routes/uat_runs.py` — 10 endpoints: start/list/get/delete runs, download report.md, serve figma/app/diff images
- `webapp/api/models.py` — `UatRun` and `UatFrameResult` tables with 17 + 11 columns
- `webapp/api/services/graph_analyzer.py` — pure-Python utilities: `find_orphan_screens`, `find_dead_end_screens`, `find_dangling_hints`, `find_unreachable_screens`, `reachability_from`
- `webapp/api/services/functional_flow_planner.py` — per-element click verification plan (deterministic, zero LLM calls)
- `webapp/api/services/deeplink_utility_planner.py` — graph integrity cases (orphans, dead-ends, dangling references, unreachable screens — deterministic)
- `webapp/api/services/edge_cases_planner.py` — empty/error/slow network/long content/missing fields (1 batched LLM call)
- `webapp/api/services/figma_test_planner.py` — per-frame design-fidelity test cases via vision LLM comparison of composite (Figma+app) images; reuses cached Figma images
- `utils/gemini_client.py` — drop-in Gemini provider with `ask`, `ask_fast`, `ask_vision`; switch via `LLM_PROVIDER=gemini` env var; uses `gemini-flash-latest` (free tier)
- `webapp/web/components/PlanTypeBadge.tsx` — colored badge per plan type (design_fidelity / functional_flow / deeplink_utility / edge_cases / feature_flow)
- `webapp/web/components/FrameComparisonCard.tsx` — per-frame Figma/app/diff side-by-side card with issues list
- `webapp/web/app/projects/[id]/runs/` — 3 new pages: run list, new run form, run detail (auto-polls every 3s, shows overall match score + per-frame comparison cards + downloadable report.md)
- `telegram_bot/bot.py` — `/uatsuite` command to generate the full multi-planner suite from mobile

### Changed
- `webapp/api/routes/plans.py` — planner registry pattern dispatches by `plan_type`; new `POST /projects/{id}/plans/suite` endpoint runs all applicable planners; plan case persistence now dedups by normalized `(title, target_screen)`; throttle between planners reduced 8s → 2s (suite time: 45s → 22s); `DELETE /projects/{id}/plans?status=draft` bulk delete for noise cleanup
- `agent/figma_journey_parser.py` — `parse(enrich=False)` flag skips the internal Claude enrichment call; `depth=4` → `depth=2` in `/v1/files` request (~4x cheaper, stretches Figma's monthly compute quota)
- `agent/figma_comparator.py` — `compare_screenshot_to_frame` accepts new `figma_image_path` kwarg to reuse pre-fetched Figma images instead of re-hitting `/v1/images`
- `utils/claude_client.py` — `ask`/`ask_fast`/`ask_vision` route to Gemini when `LLM_PROVIDER=gemini` is set in env (transparent provider switch)
- `webapp/api/services/screen_analyzer.py` — `_sniff_media_type` detects PNG/JPEG/GIF/WEBP from magic bytes so Telegram JPEG uploads work; `max_tokens` bumped 1500 → 4096 to avoid truncated JSON on dense screens
- `webapp/api/models.py` — `TestPlan.plan_type` column added; `Screen.context_hints` column added (backward-compatible via lightweight `ALTER TABLE` migration in `init_db()`)
- `webapp/api/main.py` — mounts `uat_runs` router (36 → 38 routes)
- `webapp/web/lib/api.ts` — `AbortController`-based per-request timeout (300s for `/suite`, 600s for `/uat/runs`) — prevents browser fetch from cancelling long-running operations
- `webapp/web/lib/types.ts` — `PlanType` union, `UatRun`/`UatRunSummary`/`UatFrameResult`/`UatVerdict`/`UatRunStatus` interfaces
- `webapp/web/app/projects/[id]/page.tsx` — prominent "▶ UAT Runs" section at top with Start button, de-emphasized screenshot upload + plan generation as secondary
- `webapp/web/app/projects/[id]/plans/[planId]/page.tsx` — shows `PlanTypeBadge` in header

### Fixed
- Figma API monthly quota exhaustion: added on-disk `_cached_figma_parse` helper (1h TTL) in `uat_runner.py` that survives restarts and falls back to stale cache on 429; per-frame image cache at `webapp/data/figma_cache/` shared across runs
- Gemini `gemini-2.0-flash` free tier quota is 0/day → switched default model to `gemini-flash-latest`
- Suite endpoint 500 errors: reduced browser-side fetch cancellations with explicit `AbortController` timeout + backend throttle cut

## [0.5.0] — 2026-04-11
### Added
- `tools/vision_navigator.py` — generic vision-guided navigation engine. Screenshot → Claude Haiku vision (normalized 0-1 coords) → ADB tap → repeat. Replaces brittle deterministic `wait_for_text` flows. Handles modals, splash screens, dynamic layouts. Includes `relaunch_app` recovery action and wrong-screen detection.
- `tools/quick_navigator.py` + `agent/quick_uat.py` + `agent/run_quick_uat.py` — sub-30s targeted UAT runner. Force-stop optional for warm starts. Vision/deterministic/manual nav modes. Reuses existing `FigmaComparator` for design diffs.
- `config/lob_config.json` — LOB routing table with optional `vision_hints` per funnel (Hotels, Flights, Trains, Bus, Holidays).
- `webapp/` — full Next.js 14 + FastAPI + SQLite generic UAT planning web app for any PM at any company. Bulk screenshot upload, parallel Claude vision analysis (with PNG/JPEG/GIF/WEBP magic-byte sniffing), auto-flow-inference creating high-confidence (≥0.85) edges, manual flow review panel, inline screen renaming, test plan generator from feature description, plan review page with per-case approve/edit/delete, plus REST API and CORS-enabled proxy.
- `webapp/api/services/screen_analyzer.py` — extracts name, display_name, purpose, interactive elements (with `leads_to_hint` per element), and `context_hints` (predecessor screen guess) from any screenshot.
- `webapp/api/services/flow_inferrer.py` — reverse-engineers navigation graph from a set of analyzed screens; uses both forward (`leads_to_hint`) and backward (`context_hints`) signals; identifies branches and home screen with confidence scores.
- `webapp/api/services/test_planner.py` — Claude reasoning over feature description + screen graph → list of test cases with target_screen, navigation_path, acceptance_criteria, branch_label.
- `telegram_bot/bot.py` — `/projects`, `/setproject`, `/uat <description>` commands and a photo handler that auto-uploads screenshots to the active AppUAT project. Per-chat active-project state in `webapp/data/telegram_state.json`.
- `utils/claude_client.py` — `ask_vision()` helper accepting raw image bytes with retry logic; reused by VisionNavigator, screen analyzer, and the existing `_verify()` flow.

### Changed
- `tools/android_device.py`: `tap_text()` now uses gesture-based `d.click(cx, cy)` (resolves coordinates from element bounds) instead of accessibility `ACTION_CLICK` — fixes navigation on MMT LOB tiles where accessibility click silently no-ops.
- `agent/health_monitor.py`: `NAVIGATION_STUCK` detection skips rich content pages (UI tree > 3000 chars) to eliminate false positives on legitimately-static screens like hotel details.
- `agent/orchestrator.py`: increased scenario generation `max_tokens` to 8192 and reduced default scenarios from 10-20 to 5-8; added LOB resolution from `config/lob_config.json` to inject correct `navigation_steps` into FlowExplorerAgent based on feature description keywords.

### Fixed
- Cold-start vision navigation: switched from absolute pixel coordinates (which Claude vision miscalculates due to image downscaling server-side) to **normalized 0-1 fraction coordinates** — eliminates the wrong-tile-tap bug where Hotels taps landed on Flights.
- Telegram screenshot uploads (JPEG) failing analyzer with HTTP 400: media type now sniffed from image magic bytes instead of hardcoded `image/png`.

## [0.4.0] — 2026-04-09
### Added
- `agent/figma_journey_parser.py` — parses Figma file into full journey spec; classifies frames as main/sheet/persuasion/modal; extracts all text + CTAs; batched Claude enrichment (navigation steps + assertions per screen); exports all frames as PNG via Figma Images API
- `agent/figma_uat_runner.py` — navigates app to each Figma screen, screenshots, compares via FigmaComparator, checks assertions (text via UI tree + visual via Claude vision), writes per-screen compliance report
- `workflows/context_efficiency.md` — pre-task delegation checklist + 5 reusable agent templates (explore, log-analysis, debug-triage, deployment-monitor, parallel)
- `memory/learnings.md` — 8 operational learnings incl. context waste patterns and deployment gotchas
- `memory/patterns.md` — 8 reusable patterns with bad → correct delegation code examples

### Changed
- `telegram_bot/bot.py`: Figma URL conversation flow after APK upload; `/run_figma` command; `handle_text_message` auto-detects figma.com URLs; live progress notifications during Figma UAT
- `agent/orchestrator.py`: `run_figma_uat()` classmethod — Figma-first UAT entry point (no baseline APK needed)
- `CLAUDE.md`: mandatory pre-task delegation gate + 4 new anti-patterns (double-read, log inline, debug chunks, polling loops)

## [0.3.0] — 2026-04-09
### Added
- `agent/health_monitor.py` — self-healing engine detecting APP_NOT_OPEN, APP_CRASHED, DEVICE_UNRESPONSIVE, NAVIGATION_STUCK, WRONG_SCREEN; auto-recovers with per-state playbooks; circuit breaker (3 attempts max); logs all gaps to `memory/gaps_log.jsonl`
- `tools/emulator_manager.py` — cloud-ready AVD lifecycle: boots headless, polls `sys.boot_completed`, auto-installs APK on fresh emulators; `cold_start_for_cloud()` entry point for CI
- `agent/use_case_registry.py` — persistent use case registry (`memory/use_cases.json`); validates scenario coverage via Claude (keyword fallback); pre-flight gate before each run; markdown checklist export
- `agent/figma_comparator.py` — compares app screenshots against Figma frames using Claude vision; design-spec validation when no baseline APK exists
- `telegram_bot/bot.py` — async Telegram bot (`/run`, `/status`, `/report`, `/list`, `/cases`, `/help`); APK upload via chat; UAT runs in background thread; completion notification
- `Dockerfile.bot` + `requirements.bot.txt` — lightweight (~200MB) bot-only image for Railway cloud deploy (no Android SDK)
- `docker-compose.yml` + `railway.json` — one-command cloud deploy with KVM passthrough for full emulator image
- `Orchestrator.run_cold_start()` — cloud entry point: boots emulator then delegates to normal UAT run

### Changed
- `agent/orchestrator.py`: wired health monitor pre-run check, use case pre-flight gate (`_run_preflight_gate`), `package_name` stored for downstream runners
- `agent/scenario_runner_agent.py`: per-iteration health check; recovery injected into Claude context; `package_name` + `health_monitor` params added
- `agent/diff_agent.py`: added `run_figma_validation()` and `figma_mode` support alongside existing baseline/candidate diff
- `requirements.txt`: added `python-telegram-bot>=20.0`, `requests>=2.31.0`

### Fixed
- Dockerfile CMD changed to `python -m` invocation so `/app` is on `sys.path` (fixes startup crash on Railway)
- `_run_uat_in_background` corrected to match `Orchestrator.__init__` signature (`candidate_apk`, `feature_description`, `accounts`)

## [0.2.0] — 2026-04-09
### Added
- `run_details_uat.py` — fully autonomous hotel details page UAT runner for 10.7.0 vs 11.3.0 comparison
- Screen state verification (`get_screen_state`) using live UI tree: detects `on_mmt`, `on_details_page`, `gallery_cleared`
- Autonomous app launch (`launch_mmt`) + hotel navigation (`navigate_to_hotel_details`) — no manual steps required
- `ensure_on_details_page` pre-flight: launches MMT and navigates to hotel before handing control to Claude
- Agent tools: `check_screen` (returns live state JSON), `open_mmt_app`, `scroll_fast` (gallery escape), `scroll_down` with new-content detection
- `scroll_fast` uses safe mid-screen swipe coords (65%→30%) to avoid Android home gesture zone
- `consecutive_no_new_content` auto-stop: ends capture after 3 empty scrolls
- UAT report generation inline in `run_report()` with visual diff appendix
- `--hotel` CLI arg to specify hotel search query per run

### Changed
- `tools/android_device.py`: `tap()` switched to `adb shell input tap` to fix INJECT_EVENTS on MIUI/Motorola
- `tools/android_device.py`: `swipe()` switched to `adb shell input swipe` with explicit coordinate mapping
- `tools/android_device.py`: `swipe_coords()` switched to `adb shell input swipe`

## [0.1.0] — 2026-04-09
### Added
- Full AOS layer scaffold: tools/, workflows/, memory/, utils/, config/
- MCP server with 13 Android device control tools (screenshot, tap, swipe, get_ui_tree, install_apk, launch_app, etc.)
- AndroidDevice wrapper over uiautomator2 with tap, swipe, type, UI tree, screenshot
- APK manager using ADB + aapt for install, launch, version extraction
- Multi-agent UAT orchestration: OrchestratorAgent, FlowExplorerAgent, ScenarioRunnerAgent
- A/B variant detector: fingerprints post-login home screen, groups accounts by variant, classifies REGRESSION vs VARIANT_DIFFERENCE
- Build comparison layer: visual_diff (pixelmatch + PIL fallback), DiffAgent, EvaluatorAgent
- ReportWriterAgent assembling full structured UAT Markdown reports
- report_generator.py: Jira defect list, Slack summary, JSON export
- EvidenceCapture for timestamped screenshot + step log management
- Seed memory files: learnings, patterns, decisions, user_context (MMT product context + account registry)
- UAT run and flow discovery workflow SOPs
- smoke_test.py validating config, Claude API, ADB, uiautomator2, screenshot, MCP server
- setup_emulator.sh for one-time Android AVD creation
- Python 3.11 venv with all dependencies (mcp, uiautomator2, pillow, pixelmatch, fastapi, anthropic)
