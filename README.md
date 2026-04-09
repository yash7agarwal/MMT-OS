# MMT-OS · v0.3.0

> AI-native Android UAT system for MakeMyTrip — autonomous testing, self-healing execution, and Telegram-based remote control.

MMT-OS is a multi-agent operating system that runs end-to-end UAT on MakeMyTrip Android builds without manual intervention. It explores app flows, generates test scenarios, executes them on device, detects A/B variant differences vs regressions, and delivers structured reports. A Telegram bot lets you trigger runs and receive results from your phone, 24/7.

---

## What It Does

- **Autonomous flow exploration** — maps screens and UI elements for a given feature automatically
- **AI-generated test scenarios** — Claude generates 10–20 scenarios from feature description + screen graph
- **Self-healing execution** — detects and recovers from crashes, stuck navigation, wrong screens, and unresponsive devices; logs all recovery events to `memory/gaps_log.jsonl`
- **A/B variant detection** — fingerprints accounts by post-login variant; classifies failures as REGRESSION vs VARIANT_DIFFERENCE
- **Figma design validation** — compares screenshots to Figma frames using Claude vision (no baseline APK required)
- **Use case registry** — pre-flight coverage gate ensures all registered use cases are covered before a run
- **Cloud-ready emulator** — boots headless Android AVD, auto-installs APK, handles fresh device cold-start for CI
- **Telegram bot** — `/run`, `/status`, `/report`, `/list`, `/cases` from your phone; deployed 24/7 on Railway
- **MCP server** — 13 tools exposing Android device control to Claude Code (tap, swipe, screenshot, UI tree, APK install)

---

## Architecture

```
Telegram Bot (Railway, always-on)    Mac / Device Host
┌──────────────────────┐             ┌────────────────────────────────────────┐
│  telegram_bot/bot    │────────────▶│  Orchestrator                          │
│  /run /status/report │             │  ├─ HealthMonitor  (self-healing)      │
│  APK upload handler  │             │  ├─ FlowExplorerAgent (screen map)     │
└──────────────────────┘             │  ├─ UseCaseRegistry (pre-flight gate)  │
                                     │  ├─ ScenarioRunnerAgent × N            │
                                     │  ├─ VariantDetector (A/B grouping)     │
                                     │  ├─ DiffAgent / FigmaComparator        │
                                     │  ├─ EvaluatorAgent                     │
                                     │  └─ ReportWriterAgent → reports/*.md   │
                                     │                                        │
                                     │  AndroidDevice (uiautomator2 + ADB)    │
                                     │  EmulatorManager (headless AVD)        │
                                     └────────────────────────────────────────┘
```

---

## Project Structure

```
MMT-OS/
├── agent/
│   ├── orchestrator.py          # Main UAT run coordinator
│   ├── run_uat.py               # CLI entry point
│   ├── health_monitor.py        # Self-healing: detects + recovers failure states
│   ├── use_case_registry.py     # Use case store + pre-flight coverage gate
│   ├── flow_explorer_agent.py   # Maps app screens via Claude tool loop
│   ├── scenario_runner_agent.py # Executes one scenario via Claude + ADB tools
│   ├── variant_detector.py      # A/B fingerprinting + REGRESSION classification
│   ├── diff_agent.py            # Build comparison + Figma validation mode
│   ├── figma_comparator.py      # Claude vision diff vs Figma frames
│   ├── evaluator_agent.py       # Scores scenario results
│   └── report_writer_agent.py   # Generates Markdown UAT reports
├── tools/
│   ├── android_device.py        # uiautomator2 + ADB device wrapper
│   ├── apk_manager.py           # ADB/aapt install, launch, version extraction
│   ├── emulator_manager.py      # Cloud-ready AVD lifecycle management
│   ├── visual_diff.py           # Pixel-level screenshot comparison
│   ├── screenshot.py            # Evidence capture (timestamped screenshots)
│   └── report_generator.py      # Jira, Slack, JSON export helpers
├── telegram_bot/
│   ├── bot.py                   # Async Telegram bot (all command handlers)
│   └── run_bot.py               # Entry point: python -m telegram_bot.run_bot
├── mcp_server/
│   └── server.py                # FastMCP server (13 Android control tools)
├── memory/
│   ├── use_cases.json           # Persistent use case registry
│   ├── gaps_log.jsonl           # Self-healing gap log (all recovery events)
│   ├── learnings.md             # Operational insights
│   ├── patterns.md              # Reusable patterns
│   ├── decisions.md             # Architecture decision log
│   └── user_context.md          # MMT product context + test accounts
├── config/settings.yaml         # All tunable parameters
├── reports/                     # Generated UAT reports + JSON exports
├── apks/                        # APK uploads (candidate.apk)
├── Dockerfile                   # Full image with Android SDK (device host)
├── Dockerfile.bot               # Lightweight bot-only image for Railway (~200MB)
├── docker-compose.yml           # Full stack with KVM passthrough
├── railway.json                 # Railway deploy config (uses Dockerfile.bot)
├── requirements.txt             # Full dependencies
├── requirements.bot.txt         # Bot-only dependencies
└── smoke_test.py                # Validates Claude API, ADB, device, MCP
```

---

## Setup

**Prerequisites:** Python 3.11+, Android SDK (for device runs), Java 17 (for emulator)

```bash
# 1. Clone and create venv
git clone https://github.com/yash7agarwal/MMT-OS.git
cd MMT-OS
python3.11 -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — fill in ANTHROPIC_API_KEY and TELEGRAM_BOT_TOKEN

# 4. (First time) Set up Android emulator
bash setup_emulator.sh

# 5. Validate stack
python smoke_test.py
```

---

## Usage

| Task | Command |
|------|---------|
| Run UAT (CLI) | `python agent/run_uat.py --candidate apks/candidate.apk --feature "hotel search" --accounts accounts.json` |
| Cloud cold-start (emulator) | `python -c "from agent.orchestrator import Orchestrator; Orchestrator.run_cold_start('apks/candidate.apk', 'hotel search', [])"` |
| Start Telegram bot locally | `python -m telegram_bot.run_bot` |
| Start MCP server | `python mcp_server/server.py` |
| Run smoke test | `python smoke_test.py` |

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/run <feature>` | Start a UAT run for the given feature |
| `/status` | Show current run status |
| `/report` | Send the latest UAT report |
| `/list` | List recent runs with pass rates |
| `/cases <feature>` | Show registered use cases for a feature |
| `/help` | List all commands |

Upload a `.apk` file directly in chat to set the candidate build.

---

## Configuration

| Variable | Description | Where to get it |
|----------|-------------|-----------------|
| `ANTHROPIC_API_KEY` | Claude API key | console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | Bot token | @BotFather on Telegram |
| `FIGMA_API_TOKEN` | Figma personal token (optional) | figma.com/settings |
| `DEVICE_SERIAL` | ADB device serial (optional) | `adb devices` |
| `UAT_ACCOUNTS_FILE` | Path to accounts JSON | create manually |
| `UAT_FEATURE` | Default feature for Telegram `/run` | set to your feature name |

All agent tuning parameters (timeouts, depth limits, pass thresholds) live in `config/settings.yaml`.

---

## Cloud Deploy (Railway)

```bash
brew install railway
railway login
railway init            # name: mmt-os
railway service         # link service
railway variable set ANTHROPIC_API_KEY=... TELEGRAM_BOT_TOKEN=...
railway up --service mmt-os-bot
```

The bot image (`Dockerfile.bot`) is ~200MB and runs the Telegram interface always-on. UAT execution runs on a machine with a connected device or emulator.

---

## Changelog

### [0.3.0] — 2026-04-09
- Self-healing engine with 5-state detection, auto-recovery playbooks, and gap logging
- Cloud emulator manager with headless AVD boot and APK auto-install
- Use case registry with pre-flight coverage gate and Claude-powered semantic validation
- Figma design comparator using Claude vision (no baseline APK required)
- Telegram bot deployed on Railway — trigger UAT from phone, receive reports in chat
- Lightweight `Dockerfile.bot` (~200MB) for Railway; full `Dockerfile` for device hosts

### [0.2.0] — 2026-04-09
- Autonomous hotel details UAT runner for v10.7 vs v11.3 build comparison
- Screen state verification using live UI tree
- ADB-based tap/swipe to fix INJECT_EVENTS on MIUI/Motorola

### [0.1.0] — 2026-04-09
- Initial system: MCP server (13 tools), multi-agent orchestration, A/B variant detection, build comparison, report generation

---

## Roadmap

- **Phase 4**: Web dashboard (FastAPI + Jinja2) — build upload, live run monitor, report viewer, use case editor
- **Phase 5**: Jira auto-filing, Figma token sync, Slack notifications, memory compounding across runs
- Multi-device parallelism (distribute accounts across devices for faster runs)
- Login automation (auto-login per account, no pre-logged-in sessions required)
