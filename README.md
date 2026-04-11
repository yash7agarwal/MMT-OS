# MMT-OS · v0.7.0

> APK-driven UAT operating system — drop in a build, compare it to your Figma spec, get a per-frame report. Vision-guided navigation, multi-planner test suite, Telegram-first capture, self-healing execution.

MMT-OS started as a MakeMyTrip-specific UAT agent and has grown into a generic platform for **running UAT against any Android app**. Drop in a candidate APK, point it at your Figma file, and the system installs the build, autonomously drives the app through every substantive Figma frame using Claude/Gemini vision, compares each app screen to the design, and produces a comparison report with per-frame diff images — no manual tapping required.

---

## What It Does

- **Vision-guided navigation** — Claude Haiku sees the screen, identifies the target by visible label (not position), taps via ADB. Handles modals, splash screens, and dynamic layouts that break deterministic flows.
- **Generic web app (`webapp/`)** — Next.js 14 + FastAPI + SQLite. Any PM, any company. Bulk-upload screenshots, get them auto-analyzed and connected into a navigation graph.
- **Telegram-first capture** — Send screenshots from your phone directly to a bot (`/setproject`, then send photos). Bot uploads to the active AppUAT project. Use `/uat <feature>` to generate a test plan in seconds.
- **Auto flow inference** — When 2+ screens exist, Claude reverse-engineers the navigation graph using forward (`leads_to_hint`) and backward (`context_hints`) signals. Edges with confidence ≥0.85 are auto-created.
- **Test plan generator** — Feed a feature description, get a structured list of UAT cases (with target screen, navigation path, acceptance criteria, branch label) covering all relevant funnels — not just the happy path.
- **QuickUAT runner** — Sub-30s targeted UAT execution. Vision/deterministic/manual modes. Reuses existing FigmaComparator for design diffs and Claude verification for criteria.
- **Self-healing engine** — Detects APP_CRASHED, NAVIGATION_STUCK, WRONG_SCREEN, DEVICE_UNRESPONSIVE; auto-recovers with per-state playbooks; circuit breaker after 3 attempts.
- **Figma-first UAT mode** — Parse Figma file → navigate app to each design screen → screenshot → visual + semantic compare → compliance report. No baseline APK needed.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  Browser :3000  │  Telegram bot  │  CLI (run_quick_uat)       │
└────────┬─────────────────┬────────────────┬───────────────────┘
         │                 │                │
         ▼                 ▼                ▼
   ┌────────────────────────────────────────────────┐
   │  FastAPI :8000   (webapp/api)                  │
   │  - Projects, screens, edges, plans             │
   │  - Screen analyzer + flow inferrer + planner   │
   │  - SQLite via SQLAlchemy                       │
   └─────────────────────┬──────────────────────────┘
                         │
         ┌───────────────┴───────────────┐
         │                               │
         ▼                               ▼
   ┌──────────────┐               ┌──────────────────┐
   │ Claude API   │               │ AndroidDevice    │
   │ - vision     │               │ + VisionNavigator│
   │ - reasoning  │               │ + ADB tap/screenshot │
   └──────────────┘               └──────────────────┘
```

---

## Project Structure

```
MMT-OS/
├── webapp/                          # Generic UAT web app (NEW in 0.5)
│   ├── api/                         # FastAPI backend
│   │   ├── main.py                  # app + CORS + router mounts
│   │   ├── db.py                    # SQLAlchemy + lightweight migrations
│   │   ├── models.py                # Project, Screen, Edge, TestPlan, TestCase
│   │   ├── schemas.py               # Pydantic IO
│   │   ├── routes/                  # projects, screens, edges, plans
│   │   └── services/
│   │       ├── screen_analyzer.py   # Claude vision → name + elements + hints
│   │       ├── flow_inferrer.py     # graph reverse-engineering
│   │       └── test_planner.py      # feature → test case list
│   ├── web/                         # Next.js 14 frontend
│   │   ├── app/                     # project list, project detail, plan review
│   │   ├── components/              # ScreenUploader, FlowInferencePanel, TestCaseCard
│   │   └── lib/                     # api client + types
│   └── data/                        # SQLite db + screenshots (gitignored)
├── tools/
│   ├── android_device.py            # uiautomator2 wrapper, gesture taps
│   ├── vision_navigator.py          # NEW: screenshot → Claude → ADB loop
│   ├── quick_navigator.py           # NEW: deterministic step executor
│   ├── apk_manager.py               # install, launch, force_stop
│   └── emulator_manager.py          # cloud AVD lifecycle
├── agent/
│   ├── quick_uat.py                 # NEW: sub-30s UAT runner
│   ├── run_quick_uat.py             # NEW: CLI entry point
│   ├── orchestrator.py              # full multi-agent UAT pipeline
│   ├── health_monitor.py            # self-healing engine
│   ├── figma_journey_parser.py      # Figma file → journey spec
│   ├── figma_comparator.py          # screenshot vs design diff
│   └── figma_uat_runner.py          # Figma-first UAT mode
├── telegram_bot/
│   └── bot.py                       # /run, /uat, /projects, photo handler
├── utils/
│   ├── claude_client.py             # ask, ask_fast, ask_vision (NEW)
│   └── config.py                    # YAML config dot-access
├── config/
│   ├── settings.yaml
│   └── lob_config.json              # NEW: per-LOB hints + nav steps
└── memory/                          # learnings, patterns, gaps log
```

---

## Setup

```bash
# 1. Python deps
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install sqlalchemy aiosqlite python-multipart websockets

# 2. Frontend deps (Node 20+ required)
cd webapp/web && npm install && cd ../..

# 3. Environment variables in .env
ANTHROPIC_API_KEY=<your key>
TELEGRAM_BOT_TOKEN=<bot token from BotFather>
TELEGRAM_CHAT_ID=<your chat id>
FIGMA_ACCESS_TOKEN=<optional, for Figma comparison>

# 4. Connect Android device via USB and verify
adb devices
```

---

## Usage

### Run the web app

| Service | Command | URL |
|---|---|---|
| Backend | `.venv/bin/python3 -m uvicorn webapp.api.main:app --reload --port 8000` | http://localhost:8000/docs |
| Frontend | `cd webapp/web && npm run dev` | http://localhost:3000 |
| Telegram bot | `.venv/bin/python3 -m telegram_bot.run_bot` | (polling) |

### From the browser
1. Open http://localhost:3000 → create a project
2. Drag-drop screenshots into the upload zone (multiple files at once)
3. Watch each get analyzed by Claude in parallel
4. Click "Infer flow" → review proposed edges → accept
5. Type a feature description → get a generated test plan to review

### From Telegram (mobile-first)
```
/projects                                  → list AppUAT projects
/setproject 1                              → set active project
[send N screenshots]                       → bot uploads + analyzes each
/uat hotel details page with photos, amenities, price, and Book Now
                                           → generates plan, replies with web link
```

### From the CLI (sub-30s targeted UAT)
```bash
.venv/bin/python3 agent/run_quick_uat.py \
  --skip-install \
  --feature "hotel details page" \
  --criteria "Shows hotel name, photos, amenities, price, and Book Now"

# Modes
.venv/bin/python3 agent/run_quick_uat.py --nav-mode vision ...        # Claude vision loop (default)
.venv/bin/python3 agent/run_quick_uat.py --nav-mode deterministic ... # predefined steps
.venv/bin/python3 agent/run_quick_uat.py --manual-nav ...             # user navigates, system verifies
```

---

## Configuration

| Variable | Description | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API key for vision + reasoning | console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | Bot token for Telegram interface | @BotFather |
| `TELEGRAM_CHAT_ID` | Your chat id for run notifications | Send any message to your bot, then `curl https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `FIGMA_ACCESS_TOKEN` | Optional, for Figma design comparison | figma.com → Settings → Personal access tokens |
| `DEVICE_SERIAL` | Optional ADB serial | `adb devices` |
| `APPUAT_API_URL` | Webapp API base for the bot (defaults to localhost:8000) | n/a |

---

## Changelog

**v0.7.0 (2026-04-12)** — Proactive Figma importer. `FigmaImport` + `FigmaFrame` tables, one-shot fetch of Figma file structure + all frame images + structured design data (dimensions, colors, fonts). Every UAT run after an import makes ZERO Figma API calls.

**v0.6.0 (2026-04-11)** — APK-driven UAT runs with per-frame comparison reports, multi-planner suite (design_fidelity / functional_flow / deeplink_utility / edge_cases), Figma cache layer, Gemini provider swap, plan dedup + bulk delete.

**v0.5.0 (2026-04-11)** — Vision-guided navigation, generic AppUAT web app, Telegram screenshot upload + `/uat` command, auto flow inference, test plan generator, JPEG/PNG sniffer.

**v0.4.0 (2026-04-09)** — Figma-first UAT mode, Figma journey parser, context efficiency module with delegation patterns.

**v0.3.0 (2026-04-09)** — Self-healing engine, Telegram bot, cloud-ready emulator manager, use case registry, Figma comparator, Railway deploy.

**v0.2.0 (2026-04-09)** — Autonomous UAT runner with screen verification.

See [CHANGELOG.md](CHANGELOG.md) for full history.

---

## Roadmap

- **Phase 4B** — Test case approval via Telegram reactions (👍/👎/✏️ on each case in a thread)
- **Phase 3** — React-flow graph visualization in the web app for manual edge editing
- **Phase 5** — CLI bridge: approved test plans loaded by `run_quick_uat.py --plan <id>` for batch execution
- **Multi-tenant deployment** — Auth, hosted version of AppUAT for cross-company use
- **Cloud device farm integration** — BrowserStack/Sauce Labs option for PMs without local devices
