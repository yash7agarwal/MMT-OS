# Changelog

All notable changes are documented here following [Semantic Versioning](https://semver.org/).

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
