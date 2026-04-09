# Changelog

All notable changes are documented here following [Semantic Versioning](https://semver.org/).

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
