# Changelog

All notable changes are documented here following [Semantic Versioning](https://semver.org/).

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
