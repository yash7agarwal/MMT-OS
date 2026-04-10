# Learnings

Operational insights from UAT runs, mistakes, and session audits.

---

## Context Bandwidth Audit — Session 2026-04-09

### L1: Pre-spawn file reading is the #1 context waste
**What happened**: Before spawning 3 build agents (self-healing, emulator, Telegram bot), the main session read ~1,900 lines of source files (orchestrator.py, scenario_runner_agent.py, CLAUDE.md, android_device.py, setup_emulator.sh). The spawned agents then re-read the same files themselves.

**Cost**: ~3,800 lines of context consumed for what should have been a single 200-word codebase summary.

**Rule**: Never read source files in the main session before spawning an implementation agent. Instead:
1. Spawn an Explore agent: "Summarize the relevant parts of this codebase for implementing X. Return under 300 words."
2. Pass that summary to the implementation agent's prompt.

---

### L2: Log analysis must always be delegated
**What happened**: `railway logs` returned 41KB of build/runtime output. It was read inline, then grepped inline. Two main-session turns wasted on raw log processing.

**Cost**: 41KB inline + grep results in context.

**Rule**: Any command that may produce > 2KB of output must be delegated to a subagent with a specific question. Pattern:
```
Agent: "Run `railway logs --service X`, find why the container crashed, return the error in under 50 words."
```

---

### L3: Debugging reads should be scoped subagents, not inline chunks
**What happened**: When the Railway deployment failed, bot.py was read in 3 separate chunks (lines 1-60, 60-140, 460-500) plus a grep, all in main context, to find a startup crash.

**Cost**: ~300 lines of context + 3 turns to identify 2 bugs that a focused subagent would find in 1 turn.

**Rule**: Any debugging that requires reading > 1 file or > 100 lines total → spawn a debug subagent:
```
"File X is crashing at startup on Railway. Read telegram_bot/bot.py and identify the startup crash. Return: root cause + fix in under 100 words."
```

---

### L4: Deployment polling burns turns
**What happened**: Used 4× `sleep + railway deployment list` loops in the main session to monitor a Railway deployment. Each consumed a conversation turn.

**Cost**: 4 wasted turns waiting for an external process.

**Rule**: Background deployment monitoring via `run_in_background=True` on a monitoring agent, or use a single long-timeout Bash command. Never poll in a loop in the main session.

---

### L5: Double-read pattern when delegating late
**What happened**: Main session read files to understand context, then spawned agents that re-read the same files to do the work.

**Rule**: Default assumption is "delegate first". If you catch yourself reading a file to understand context for a future agent → stop, put that reading IN the agent's prompt as a task, not as pre-work in the main session.

---

## UAT Operational Learnings

### L6: Emulator image size matters for cloud deployment
The full Android SDK Docker image (~4GB) crashes Railway's free tier immediately. Keep a lightweight `Dockerfile.bot` (~200MB) for the always-on Telegram interface. Heavy emulator runs stay on the device host.

### L7: python -m vs direct script for package imports
Running `python telegram_bot/run_bot.py` adds `telegram_bot/` to sys.path. Running `python -m telegram_bot.run_bot` adds the CWD to sys.path. Always use `-m` for packages that import from sibling packages.

### L8: Orchestrator signature must be passed correctly to background threads
The Telegram bot's background UAT thread must use the exact Orchestrator.__init__ signature: `candidate_apk`, `feature_description`, `accounts` (as a list, not a file path). Loading accounts.json must happen before instantiating the Orchestrator.

---

## Validation and Self-Healing Learnings

### L9: "Done" must mean validated, not just written
The system confirmed skill creation, bot deployment, and Orchestrator integration as "done" without running any validation. All three had bugs caught only at runtime. Every completion must run the post-task-eval checklist. Written code is an untested hypothesis — validation is what makes it done.

### L10: Issue patterns must be logged at discovery, not just mentally noted
When a bug is found and fixed, the fix is applied and the session moves on. But if the same pattern recurs (wrong invocation style, wrong API signature), it won't be caught faster next time. Log every issue at discovery so future sessions can consult `memory/issues_log.jsonl` before debugging from scratch. The log is the institutional memory — without it, every recurrence is a first encounter.
