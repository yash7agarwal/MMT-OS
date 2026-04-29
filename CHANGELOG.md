# Changelog

All notable changes are documented here following [Semantic Versioning](https://semver.org/).

## [0.19.1] — 2026-04-29 — Reseed-discovery endpoint so existing projects benefit from v0.19.0

Existing projects (Sarvam, MakeMyTrip, Platinum) had already completed their `industry_identification` + `contrarian_discovery` seed work-items in older deploys. The next intel run found nothing pending in those categories and skipped the new LLM-as-search path entirely — silent no-op.

### Added
- `POST /api/knowledge/work-items/reseed-discovery?project_id=X` — re-injects the two LLM-discovery seeds as pending. Idempotent: skips if pending items for that category already exist. Lets the next intel run actually fire `_llm_discover_competitors()`.

### Operating note
- After every architectural change to a discovery work-item handler, hit reseed on existing projects. Otherwise the change only affects newly-created projects.

## [0.19.0] — 2026-04-29 — LLM-as-search for competitor discovery

User insight: *"if I ask Claude or GPT 'who are the competitors of company XYZ' they answer from training data — why does Prism need a Tavily call?"*

Right. For named-entity discovery, modern LLMs (Llama 3.3 70B on Groq, Claude, GPT) already encode the answer with better fidelity than search-grounded synthesis — search drift (e.g. "Platinum Industries" → platinum-metal pollution) doesn't happen, and there's no quota wall. Live UAT against Sarvam.ai now produces:

```
direct_global: OpenAI, Anthropic, Google, Cohere, Mistral AI    ← finally
direct_local:  Indian AI startups
indirect:      Hugging Face, AWS SageMaker, Google Cloud AI
```

The architectural change is bounded — search infrastructure stays intact for `competitor_profile`, `regulatory_scan`, dated/quantified queries where it actually adds value. Discovery just bypasses it.

### Added
- `agent/llm_search.py` — `llm_competitor_discovery(project_name, project_description, portfolio_summary?) → CompetitorDiscovery`. One Groq call (Claude fallback) returns `direct_local + direct_global + indirect` competitor lists with one-line differentiators and (optionally) homepage URLs. Each emitted URL gets a single HEAD request (5s timeout, 8-way parallel pool) — verified URLs become first-class source citations; unverified URLs are kept with the entity but the observation content notes "training-data citation, URL unverified" so the report's hallucination guard sees the provenance.
- `agent/competitive_intel_agent._llm_discover_competitors` — new path for `industry_identification` and `contrarian_discovery` work items. Pulls portfolio_summary from the brief (v0.16.2 grounding), runs LLM discovery, validates each name through `extraction_guard.validate_extraction` (which catches placeholders, self-references, trivial names per v0.16.0 + v0.18.3), upserts `company` entities + `competes_with` relations + initial observations.
- `tests/test_llm_search.py` — 10 unit tests pinning JSON parsing (strict / fenced / prose-wrapped / garbage), URL verifier behavior, and LLM-discovery happy path + degradation paths (empty response, non-list categories, unnamed entries).

### Changed
- `agent/competitive_intel_agent.execute_work_item` — routes `industry_identification` and `contrarian_discovery` work items through the new LLM-discover path instead of the autonomous tool-use loop. Other categories (`competitor_profile`, `regulatory_scan`, etc.) unchanged — they still benefit from search.

### Tradeoff register
- **Gained:** competitor discovery now works overnight even with all four search providers exhausted ($0 cost, 100% availability against any free LLM tier). Global category leaders + indirect substitutes appear automatically — the user's two outstanding complaints from yesterday's UAT both addressed by the same architectural cut.
- **Lost:** discovery is bounded to the LLM's training cutoff (≈Jan 2026 for current Llama 3.3 / Claude). Companies founded in the last few months may be missed. Source URLs are LLM-emitted (verified-or-labeled) rather than pulled from live search results.
- **Net:** worth it. The "0 findings overnight" failure mode is the worse outcome by an order of magnitude. Time-sensitive queries already route through search-grounded paths (`regulatory_scan`, `competitor_profile`) and stay there.

### Why this lever didn't exist before
The original architecture was built defensively against LLM hallucination — every claim had to cite a search-pulled URL. That's correct for *invented metrics and percentages* but over-applied to *common-knowledge entity facts*. The tier-verification work in v0.18.0 set up the conceptual split; this release operationalizes it for the discovery step where it matters most.

## [0.18.6] — 2026-04-29 — Live activity panel on Intelligence tab

User: *"it's showing Starting but no idea if it's stuck or if something is happening."* The fix for v0.18.5's silent-404 bug got the agent firing, but the user had no visibility into what the agent was actually doing once spawned. Just a "Starting…" spinner until the run resolved 4–8 minutes later.

### Added
- A live-activity panel between the Run-all header and the agent cards, shown whenever something is in progress OR a run is queued. Surfaces:
  - **Currently running work items** — `agent_type / category`, the work item description, an elapsed-time counter (e.g. `2m 14s`)
  - **"Last 5 finished"** collapsible — green/red status icons + the result_summary so you can see what the agent actually concluded
  - **Pre-spawn message** — if the run is queued but no work items are in-flight yet (the 5–15s gap), the panel says so explicitly with a hint to check the Backlog tab if it doesn't pick up in 30s
- Polling cadence tightened from every 8s to every 3s while a run is active. Counter ticks visibly.

### Changed
- `webapp/web/app/projects/[id]/intelligence/page.tsx` — adds `inProgressNow` + `recentlyCompleted` derived state, `fmtElapsed()` helper, and the new panel JSX. Tightens the auto-refresh interval.

### Why this surfaced now
The silent-404 fix in v0.18.5 was the necessary precondition: previously buttons did nothing visible because the API never accepted the request. Now that requests succeed, the *real* UX gap (no progress visibility during multi-minute runs) is unmasked.

## [0.18.5] — 2026-04-29 — Intelligence-tab Run buttons fired silently into 404

User: *"I click on Run Competitive Intelligence but it stops and gets back to normal state again without any understanding of why it isn't starting."* The Intelligence tab's AGENTS array used `key='competitive_intel'` and `key='industry_research'` for the first two cards — but the orchestrator's valid agent_types are `intel`, `ux_intel`, `impact_analysis`, `digest`. `competitive_intel` and `industry_research` are *legs* of the `intel` agent, not standalone entries. Since v0.16.1, the `/api/product-os/run/{agent_type}` endpoint correctly returns 404 for unknown agent_types — but `intelligence/page.tsx`'s `handleRun` had a silent `catch {}` that swallowed it, leaving the user staring at a button that briefly went "Starting…" and reset.

### Fixed
- `webapp/web/app/projects/[id]/intelligence/page.tsx` — both "Competitive Intelligence" and "Industry Research" cards now route to `intel` (the top-level agent that runs both legs in sequence). Card descriptions updated to make the relationship explicit.
- Same file — `handleRun` and `handleRunAll` now setError on catch instead of silent swallow, and the existing `<ErrorBanner />` (already wired to `error` state from v0.15.1) surfaces the message visibly.

### Why this hid for so long
v0.16.1's 404 fix was correct on the API side, but the Intelligence tab predates the rule. The Reports tab (v0.17.0) was built using `intel` correctly because I knew the gotcha by then — but the older Intelligence tab still had the legacy keys. Surfaced the moment the user actually tried to use a Run button after the cleanup.

## [0.18.4] — 2026-04-28 — `/competitors` and `competitor_count` filter dismissed entities

After v0.18.3 added the placeholder-name guard and we retro-purged 4 bad entities (2 placeholders on Platinum, self-references on Platinum + Sarvam), the entities were correctly marked `user_signal='dismissed'` but **still appeared on `/api/knowledge/competitors` and in `stats.competitor_count`** — those endpoints were missing the dismissed filter that `trends-view` had.

### Fixed
- `webapp/api/routes/knowledge.py:list_competitors` — query now excludes `user_signal='dismissed'`. Same predicate as the trends-view route.
- `webapp/api/routes/projects.py:get_project` — `competitor_count` derivation now applies the same exclusion. The stats-consistency invariant (`test_stats_consistency.py`) requires `stats.competitor_count == len(/competitors)`; both must filter identically.

## [0.18.3] — 2026-04-28 — Reject "Competitor N" placeholders + request global category leaders + indirect competitors

User report after the first successful report run: *"on Platinum I see competitors literally named 'Competitor 1' and 'Competitor 2'; on Sarvam only Indian companies are listed — why aren't OpenAI / Anthropic / Google Gemini there as competitors? I still don't see indirect competitors."* Two real bugs:

1. The `competitive_intel` synthesizer, when search returned recognizable findings but no nameable companies, fell back to numbered placeholders ("Competitor 1 from the 4 findings"). The save_competitor tool persisted them as-is.
2. The `industry_identification` prompt's queries were too parochial — `"{project_name} competitors"` returns local players. For Indian projects (Sarvam, Krutrim) this excludes the global category leaders (OpenAI, Anthropic, Gemini) that customers actually compare against.

### Fixed
- `agent/extraction_guard.py` — added `_PLACEHOLDER_PATTERNS` (regex set) catching: `Competitor N`, `Company A`, `Player X`, `Example 1`, `from the N findings`, `TBD/TODO/XXX/N/A`, etc. The guard now rejects these patterns BEFORE persistence with reason `"placeholder/templated name"`. 16 new test cases pin both rejection (16 placeholder forms) and false-positive avoidance (real names with digits like "Sarvam.ai", "PVC Industries 2026 Annual Report" pass through).
- `agent/competitive_intel_agent._tool_save_competitor` — guard runs at the tool boundary, not just at upsert. The LLM cannot end-run validation by emitting placeholder names.
- `agent/competitive_intel_agent._build_work_prompt::industry_identification` — prompt rewritten to require: (a) local/direct queries, (b) alternatives queries, (c) **explicit category-leader queries with concrete examples** ("for an Indian LLM platform that's OpenAI, Anthropic, Gemini, Mistral, Cohere"), (d) **indirect/substitute queries** ("for an OTA: airline direct booking, Google Flights"). The prompt also explicitly forbids placeholder names with the rule: *"if you can't find a real name, save FEWER entities — quality over quantity."*

### Why this matters
Two regressions surfaced by the same bug class — synthesizer fallback to scaffolding text. v0.16.0's guard caught self-references and trivial generics but missed numbered placeholders because they're surface-distinct ("Competitor 1" doesn't match "Industry"/"Market"). Adding regex coverage is a one-time gate that will catch this class for every future synthesizer drift. Plus the prompt change addresses the deeper product issue: customers don't compare only locally, and reports should reflect the full competitive set.

## [0.18.2] — 2026-04-27 — Fix KnowledgeStore positional-arg mismatch in report persistence

v0.18.1's first end-to-end run on Groq actually completed all six synthesis calls in ~12s — finally proving the architecture works under the new provider mix — but failed at the very last step (`Rendering Excel…`) with `KnowledgeStore.__init__() missing 1 required positional argument: 'project_id'`. The orchestrator was passing `(db, project_id)` while `KnowledgeStore` takes `(db, agent_type, project_id)`. Fixed.

### Fixed
- `agent/report_generator._persist_manifest` — pass `agent_type='report_generator'` as the second positional arg (was incorrectly passing `project_id` as the second arg, satisfying neither parameter correctly).

### Why this didn't fail in unit tests
`test_report_generator.py` mocks the synthesis path and tests primitives in isolation — the orchestrator + persistence path runs only on the live Railway instance. The bug surfaced exactly the moment everything else worked — the first complete pipeline run.

## [0.18.1] — 2026-04-27 — Report synthesis on Groq instead of Anthropic

After v0.18.0 cut report-call volume by ~5×, the Anthropic credit balance still ran dry within a day of heavy testing because *bursty* traffic (a report = ~10 LLM calls in <2 min) hits Anthropic faster than steady-state daemon usage. Switched the report synthesizer's primary provider to Groq (Llama 3.3 70B). Free tier 30 RPM / 14,400 RPD is plenty for routine report generation. Claude is now the fallback for the rare case Groq is hard-down.

### Changed
- `agent/report_synthesis._ask` — primary provider now `groq_client.synthesize`. Calls fall back to `claude_client.ask` only when Groq is unavailable or errors. Anti-hallucination guard (`_gate_urls`) is provider-agnostic and applies to Groq output identically.

### Tradeoff
- **Gained:** routine report generation costs $0; no Anthropic credit burn on the bursty workload that was the root cause of the 2026-04-27 exhaustion. Groq's 30 RPM matches our throttle from v0.15.5 → no cascade collapse on bursts.
- **Lost:** Llama 3.3 70B is a step below Claude Sonnet 4.6 on dense analytical writing. For the structured prompts in this pipeline (executive summary, lens insights, recommendations) the gap is small; for free-form long-form synthesis it would be more visible.
- **Net:** correct trade for the cost profile. Quality difference is offset by the website grounding (v0.16.2) and tier verification (v0.18.0) doing more of the heavy lifting upstream — the LLM is mostly composing well-structured paragraphs, not making complex reasoning leaps.

## [0.18.0] — 2026-04-27 — Tier verification by claim type (stop forcing URLs on every fact)

User correction: *"if I ask Claude or GPT 'who are the competitors of company XYZ' they answer from training data — why does Prism need a Tavily call for that?"* Right. v0.17.x's URL gate over-applied the "zero hallucination" rule by demanding every claim cite a search-pulled URL. The result: even well-known facts (Yatra is an OTA, MakeMyTrip's main competitors are Cleartrip + ixigo + EaseMyTrip) burned a search call → which contributed to the multi-provider quota exhaustion on 2026-04-26/27.

The fix splits sections into two verification tiers:

- **`common_knowledge`** — executive summary, competitive framing, strategic implications. The system prompt allows training-data answers for well-known facts; explicitly forbids fabricating specific numbers / percentages / dates / quotes (those still need source data); marks output with a footnote so the reader knows.
- **`needs_grounding`** — lens insights, regulatory framing, recommendations. Strict URL-citation gate unchanged. These are domain-specific, time-sensitive, or credibility-critical and must stay anchored in the KG.

### Added
- `agent/report_synthesis.TIER_BY_SECTION` — explicit map of every section to its tier. New sections must be added here or they fall through to the strict default (with a test pinning the coverage).
- `_SYSTEM_COMMON` and `_SYSTEM_GROUNDED` system prompts. Common forbids fabricated specifics but allows training-data framing; Grounded keeps the v0.17 strict gate.
- `_ask(prompt, tier=...)` parameter — every section function now passes its tier explicitly. No silent default — must be specified at call site.
- 3 new tests in `tests/test_report_generator.py` pinning: every section has a tier; common-knowledge prompt does NOT carry the strict URL rule; grounded sections stay strict.

### Changed
- `agent/report_templates/report.html.j2` — executive summary section gets a small italic footnote acknowledging the blend of KG citations + analyst training knowledge.
- `agent/report_templates/report.css` — `.tier-note` styling.

### Why this isn't a quality regression
The hallucination guard is unchanged — `_gate_urls` still flags URLs not in scope. The relaxation is *what we expect of the LLM* (cite when relevant, not always), not *what we accept as output*. Specific numbers, dates, regulations, and competitor-by-competitor tactics still must come from the KG; only generic framing and well-known industry facts get the relaxed path. Net cost: ~5× fewer LLM/search calls per report on common-domain projects.

## [0.17.3] — 2026-04-27 — Stop misclassifying every Anthropic 400 as a credit problem

Reports were stuck at "Synthesizing executive summary…" for 5+ minutes per call. Diagnosis: `claude_client.ask()` and `ask_with_tools()` had a too-loose check — every 400 from Anthropic fell through to the Gemini fallback chain on the assumption it was a credit/billing issue. In reality, the 400s were "prompt too long" / "invalid model" / "messages malformed" (root cause TBD; the previous code never logged the body so we couldn't see). The Gemini cascade then 429'd through 30+60+120s retries → Groq 429'd → ~5 min wasted per call before raising.

### Fixed
- `utils/claude_client.py` — extracted `_is_credit_or_billing()` with a tight whitelist of Anthropic's canonical credit/billing strings (`credit balance`, `usage limits`, `monthly usage limit`, `billing`, `payment required`). Only those trigger Gemini fallback. Everything else surfaces immediately so the user can fix the actual underlying cause.
- `utils/claude_client::ask` and `ask_with_tools` — log the full Anthropic error body (first 400 chars) at WARNING level so future 400s diagnose themselves on first occurrence instead of staying invisible behind the cascade.

### Added
- `tests/test_claude_client_credit_branch.py` — 13 tests pinning the credit-detection contract. Six positive cases (each canonical Anthropic error string) and seven negatives (prompt-too-long, invalid model, rate-limit, etc. must NOT trigger fallback).

## [0.17.2] — 2026-04-27 — Single canonical URL: `prism-ros.vercel.app`

The Vercel project had accumulated three half-working aliases (`prism.is-a.dev` 302'd to a splash page; `prism-intel.vercel.app` was squatted by another Vercel team after our project was deleted in an earlier reorg; `prism-three-alpha` was an auto-generated string nobody wanted). Consolidated to one clean canonical: `prism-ros.vercel.app`. The CORS allowlist is updated to match.

### Changed
- `webapp/api/main.py` — CORS `_default_origins` now lists only `prism-ros.vercel.app` for production. Removed `prism.is-a.dev` (DNS broken), `prism-intel.vercel.app` (owned by another team), `prism-three-alpha.vercel.app` (auto-generated, replaced).

## [0.17.1] — 2026-04-27 — Vercel auto-deploy wired

After v0.17.0 shipped to GitHub, the Vercel-hosted frontend (`prism-three-alpha.vercel.app`) was 6 days stale because the project had no git connection — pushes to `main` weren't triggering builds. Same root cause as the Railway auto-deploy gap surfaced earlier this session.

### Fixed
- Installed Vercel's GitHub App on `yash7agarwal/prism` and ran `vercel git connect`. Project now wired to `main` branch; future commits auto-deploy. This commit is the first under the new flow — its successful production build is the verification.

## [0.17.0] — 2026-04-26 — Downloadable executive reports (BCG/McKinsey-grade PDF + Excel)

After v0.16.x closed the data-quality drift class, the natural next question was: *how does this turn into something a CEO can hand to their board?* v0.17.0 adds a full report-generation pipeline — PDF cover, executive summary, competitive landscape, 8 strategic-lens insight sections, regulatory + technology landscapes, impact cascades, evidence-anchored recommendations, methodology, and a sources appendix. Plus a 9-tab Excel for analyst deep-dive with hyperlinked sources on every observation.

The whole pipeline runs through a single chokepoint with hallucination guards: every claim cites a source URL that's in scope; recommendations without citations are dropped before persistence. Claude Sonnet writes the prose; matplotlib renders the charts; WeasyPrint produces the PDF; openpyxl produces the Excel.

### Added
- `agent/report_snapshot.py` — deterministic KG snapshot (entities × types, lens matrix, impact graph, sources, sessions). `content_hash()` excludes timestamps + Loupe runs so re-runs and Loupe reachability don't bust the narrative cache.
- `agent/report_synthesis.py` — six Claude Sonnet narrative functions: `executive_summary`, `competitive_landscape_framing`, `lens_insights_batch` (eight lenses in one call), `regulatory_framing`, `strategic_implications`, `recommendations` (returns `Recommendation(title, body, evidence_urls)`). Every function flows through `_gate_urls` which logs hallucinated citations; recommendations without an `evidence_refs` entry are dropped.
- `agent/report_charts.py` — server-side matplotlib (Agg backend) producing three PNGs: lens × competitor heatmap, trend timeline, three-tier impact cascade tree. Empty-input gates return None so the template skips the section instead of rendering empty axes.
- `agent/report_xlsx.py` — 9-tab `.xlsx` with hyperlinked source URLs on every observation row + ColorScaleRule heatmap on the lens matrix tab.
- `agent/report_templates/report.html.j2` + `report.css` — print stylesheet with branded cover page, A4 margins, page-break discipline, Liberation Serif body + Liberation Sans tables, page numbers via `@page` rules.
- `agent/report_generator.py` — orchestrator. Pulls snapshot, runs Loupe enrichment if reachable, checks the manifest cache by `content_hash`, calls synthesis only on miss, renders charts, renders PDF + Excel, persists manifest as a `KnowledgeArtifact(artifact_type='executive_report')`. `render_from_manifest()` is the cheap path used on download — no LLM calls.
- `webapp/api/routes/reports.py` — four endpoints: `POST /api/reports/generate` (kicks off thread, returns `job_id`), `GET /api/reports/jobs/{job_id}` (poll progress), `GET /api/reports/{artifact_id}/download?format=pdf|xlsx` (stream binary), `GET /api/reports/recent` (list past reports).
- `webapp/web/app/projects/[id]/reports/page.tsx` — new "Reports" tab listing past reports + Generate button.
- `webapp/web/components/GenerateReportModal.tsx` — format-select modal with live progress display (queued → running → done) by polling `/jobs/{id}` every 2s.
- `webapp/web/lib/api.ts` — `generateReport`, `reportJobStatus`, `recentReports`, `reportDownloadUrl` clients.
- `tests/test_report_generator.py` — 12 tests pinning: snapshot determinism, hash excludes volatile fields, hash changes on real data shifts, URL gate detects hallucinated citations, recommendations without evidence are dropped, chart empty-input gates, xlsx 9-tab structure + hyperlinks. All pass; combined unit suite is now 53 tests.

### Changed
- `requirements.txt` — added `jinja2`, `weasyprint`, `openpyxl`, `matplotlib`. Adds ~50MB to the installed footprint.
- `Dockerfile` — added `apt-get install libpango-1.0-0 libpangoft2-1.0-0 fonts-liberation` for WeasyPrint's Pango runtime + serif/sans fonts.
- `webapp/api/main.py` — registered the reports router.
- `webapp/web/app/projects/[id]/layout.tsx` — added Reports entry to the tab nav.

### Why narrative is cached, binaries are regenerated
The expensive part of a report is the six Claude calls (~$0.50–1.50 per fresh report). The cheap part is rendering HTML→PDF and writing an xlsx (~2s). So the manifest persists the synthesized narrative + recommendations; binaries are regenerated on every download from the cached narrative + a fresh KG snapshot. KG drift between manifest creation and download means the data tables refresh while the prose stays consistent — which is the right tradeoff for "I want to re-download what I just generated" UX. Fully consistent re-synthesis requires generating a new report (which checks the cache, finds a content_hash mismatch, and re-runs the LLM).

### Why a job queue instead of synchronous response
Generation takes 60–90s for a fresh report. Holding a request open that long is fragile (timeouts, mobile networks, Vercel edge runtime). The thread + `_jobs` dict pattern matches the existing `product_os.run_agent` setup. Multi-replica scaling needs Redis-backed jobs; documented in v0.17.2.

## [0.16.2] — 2026-04-26 — Website grounding: anchor research on what the company actually does

User feedback after a "Platinum Industries" UAT: "a simple Claude/ChatGPT query would give detailed competitors — why is it such a difficult task?" Audit confirmed: of 70 entities the agent had created, ~50 were drift — platinum-the-metal mining commentary, news-industry decline trends, German scientific institutions, EU clean-air policy. The user had explicitly provided `platinumindustriesltd.com` as `app_package`, but the agent's query planner only saw the keyword "Platinum Industries" — never opened the URL. So queries like "Platinum Industries competitors" pulled platinum-metal commodity reports from Reuters, and the synthesizer dutifully extracted them.

A simple Claude/ChatGPT query works because Claude reads the URL FIRST and grounds everything on what the company actually does. v0.16.2 gives the agent the same discipline.

### Added
- `agent/website_grounding.py` — `fetch_portfolio_summary(app_package, project_name) -> str | None`. Fetches the project's homepage (one-shot), feeds it to Claude with a strict structured prompt that returns: products/services, industry, target customers, geographic focus, **what this company is NOT** (the load-bearing disambiguation block), and likely competitors mentioned on the page. `lru_cache(32)` keyed by (url, project_name) so the LLM call only fires once per session per project.
- `ResearchBrief.portfolio_summary: str | None` — populated by `build_brief()` automatically when `app_package` is set. Renders FIRST in `to_prompt_context()` so the planner sees authoritative grounding before the user-typed description.
- `agent/efficient_researcher::research_industry_trends` — synthesis prompt now includes a `portfolio_block` that prepends the portfolio summary with the instruction: "if a finding contradicts this or comes from an unrelated industry, drop it."
- `tests/test_website_grounding.py` — 4 tests pinning: empty url → None; `WHAT THIS COMPANY IS NOT` block must remain in prompt template; lru_cache wired; bare-domain URL normalized to `https://`.

### Verified live
Smoke-tested against `platinumindustriesltd.com` — Claude extracted: "Zinc/Calcium/Barium/Aluminium Stearates, PVC Hybrid™ Low Lead Stabilizer, PE/OPE Wax, Highstab™, Lubpack, CPVC Addpack" + the disambiguation: "NOT a platinum-metal mining company; NOT precious-metals investment fund; NOT jewelry; NOT related to the platinum commodity market." That disambiguation block is what the agent has been missing for every keyword-collision case.

### Why this isn't just "tighten the prompt again"
v0.16.0 already added the project name + DO-NOT-extract list to the synthesis prompt. That helped with the obvious self-references, but the synthesizer still didn't know what the company actually *does* — only what the user typed. With 30+ words of typed description, an LLM still has to guess. With the homepage as ground truth, it doesn't.

## [0.16.1] — 2026-04-26 — `/run/{agent_type}` returns 404 on unknown agent_type

While verifying v0.16.0 end-to-end on a fresh project, every `POST /api/product-os/run/competitive_intel?project_id=X` returned `200 OK` and reported `"status": "started"` — but no session was ever created and no work occurred. The orchestrator's `run_agent_session("competitive_intel")` returned `{"status": "unknown_agent"}` because `competitive_intel` is a *leg* of the top-level `intel` agent, not a configured top-level agent_type itself. The route's background thread had a blanket `except Exception: pass` that swallowed this, leaving the caller with a fake-success response. Hours of debugging traced symptoms ("project 6 has 0 new entities!") that were really "the trigger was a no-op."

### Fixed
- `webapp/api/routes/product_os::run_agent` — checks `agent_type in orch.config` BEFORE spawning the thread; returns `404` with the list of valid agent_types when unknown. Future invalid triggers fail loudly. The blanket `except` inside the thread is preserved (it still has work to do — catching genuine runtime errors after a valid agent_type has been confirmed).

### Why a v0.16.1 patch and not folded into v0.16.0
v0.16.0 is the architectural fix (extraction guard). This is the silent-no-op symptom that hid v0.16.0's effects from us. They are independently shippable; the changelog separates them so future readers can pattern-match each from its bug report.

## [0.16.0] — 2026-04-26 — Extraction guard: end-to-end fix for the "every new project surfaces these bugs" class

After v0.15.5 fixed rate-limiting and produced the first successful extraction on project 6 ("Platinum industries limited"), the OUTPUT itself was wrong: 10 entities created, all `entity_type='trend'`, including the project itself ("Platinum Industries is a leading PVC stabilizer manufacturer"), random named people ("Dr. Michael Schiller"), regulators ("European Chemicals Agency"), and platinum-the-metal commentary that polluted the search results. Same class of bug as the v0.11.0 Swiggy/MakeMyTrip travel-trend contamination — a fresh symptom every time a new project is created.

This release closes the class. New `agent/extraction_guard.py` is a single chokepoint with three layers (type whitelist, self-extraction guard, trivial-name reject) plus a `coerce_entity_type` mapper that respects the synthesizer's category instead of force-coercing everything to `trend`. Wired into every persistence site so future synthesis drift gets caught at write-time, not after a user files a bug report.

### Added
- `agent/extraction_guard.py` — `validate_extraction(name, entity_type, project_name) -> ValidationResult`. Rejects: too-short names, trivial generics ("Industry", "Market", "Trends"), unknown `entity_type`, and self-references. Self-reference uses normalized substring containment so "Platinum Industries is a leading PVC stabilizer manufacturer" matches "Platinum Industries Ltd." after stripping common corporate suffixes.
- `agent/extraction_guard::coerce_entity_type` — maps synthesizer category strings (`"market_structure"`, `"regulatory"`, `"company"`, etc.) to the canonical entity_type whitelist. Replaces the silent `entity_type="trend"` force-coercion that was collapsing companies/regulations/people into the wrong bucket.
- `tests/test_extraction_guard.py` — 32 tests covering self-reference parameterized by phrasing, type-whitelist parameterized by valid type, trivial-name rejection, and the full category coercion table including the 4 Platinum UAT categories that were broken.

### Changed
- `agent/industry_research_agent.py:504` — extracted entities now use `coerce_entity_type(trend["category"])` instead of hardcoded `"trend"`. Validates through the guard before upsert; rejected items are logged with reason and counted, not silently created.
- `agent/industry_research_agent.py:560` (fallback path for non-industry-research categories) — same guard wiring.
- `agent/industry_research_agent.py::_tool_save_finding` — guard runs before upsert so the autonomous tool-use path can't end-run the validator.
- `agent/competitive_intel_agent.py:482` — refuses to spin up a competitor profile when the requested competitor name is a self-reference, before any LLM call. Saves the cost of a guaranteed-bad work item.
- `agent/efficient_researcher.py::research_industry_trends` synthesis prompt — explicit "DO NOT extract" list now names the project as the first item, names "specific people / executives", names "specific organizations / regulators / agencies", and adds a commodity-market-vs-company-with-shared-keyword example tied to the Platinum UAT failure. Forbids using a person or organization NAME as the trend NAME.

### Why this isn't just a "tighten the prompt" fix
Prompts drift; LLMs hallucinate; the same prompt change that fixes the Platinum case can regress on the next industry. The guard runs AFTER synthesis so even a misbehaving prompt can't write garbage to the KG — it logs `[industry_research] dropped extraction: self-reference: 'X' matches project 'Y'` and skips the upsert. Belt and suspenders.

## [0.15.5] — 2026-04-26 — Per-provider rate limiter (no more burst-429s)

After v0.15.4 the Claude → Gemini → Groq cascade was complete, but live UAT revealed a deeper problem: the agent fires bursts of LLM calls (parallel work items + multiple sessions running concurrently), and combined free-tier RPM caps (Gemini 15 + Groq 30 = 45/min nominal) couldn't absorb the bursts. Result: only 26% of Groq calls succeeded (5 OK / 19 total) — even with the cascade, every LLM was throttled simultaneously.

### Added
- `utils/rate_limiter.py` — module-level `throttle(provider)` context manager that combines (a) a `Semaphore` capping concurrent in-flight calls per provider with (b) a min-interval gate that spaces calls just under the documented free-tier RPM. Gemini gets 4.5s spacing (under 60s/15 RPM = 4s); Groq gets 2.5s spacing (under 60s/30 RPM = 2s). Smoke-tested locally — three sequential `with throttle("gemini")` calls land at t+0, t+4.5, t+9.0s as expected.

### Changed
- `utils/gemini_client::_post` and `ask_with_tools` HTTP call sites are wrapped in `throttle("gemini")`.
- `utils/groq_client::synthesize` and `ask_with_tools` HTTP call sites are wrapped in `throttle("groq")`.

### Tradeoff
- **Gained:** 0% expected 429 rate from rate-limit triggers under expected agent load. Free tiers stay free; no card needed.
- **Lost:** session latency — each LLM call now waits up to 4.5s before firing. Worst case, a single session that makes 10 LLM calls now takes ~45s longer. Acceptable for autonomous background research; would not be acceptable for an interactive UI flow.

## [0.15.4] — 2026-04-26 — Groq fallback for text-only synthesis path

v0.15.3 wired Groq as 3rd-tier fallback in the **tool-use** path (`gemini_client.ask_with_tools`) but left the **text-only** path (`gemini_client.ask` → `_post`) unpatched. Live UAT on project 6 surfaced this immediately: tracebacks ended with `RuntimeError: Gemini call failed after 3 retries: None` raised from `_post()` — competitive_intel sessions failed within minutes despite the new Groq wiring.

### Fixed
- `utils/gemini_client::ask` — wraps `_post()` and falls back to `groq_client.synthesize()` when Gemini exhausts retries. Closes the gap so both LLM call patterns (text-only and tool-use) have the full Claude → Gemini → Groq cascade.

## [0.15.3] — 2026-04-26 — Groq tool-use fallback + AgentSession status

After v0.15.2 fixed search rate-limiting via Exa, the next bottleneck surfaced: Gemini's free tier (`gemini-flash-latest`, 15 RPM) was 429-ing on every synthesis call, leaving the agent stuck on `Gemini call failed after 3 retries`. v0.15.3 wires Groq Llama 3.3 70B (free, 30 RPM, 14,400 RPD — fresher quota bucket) as a 3rd-tier LLM fallback after Claude → Gemini, and fixes the always-null `AgentSession.status` field that was making the UI's "agent done?" indicator unreadable.

### Added
- `utils/groq_client.py::ask_with_tools` — OpenAI-compatible function-calling on Llama 3.3 70B, returns an Anthropic-shape `_FakeMessage` (reusing the shims from `gemini_client`) so the agent's tool-use loop is provider-agnostic. Smoke-tested locally: tool roundtrip returns the expected `tool_use` block.
- `utils/gemini_client::ask_with_tools` — final fallback: when Gemini retries exhaust and `groq_client.is_available()`, calls Groq before raising. Logs `[gemini] retries exhausted — falling back to Groq`.
- `webapp/api/schemas::AgentSessionOut.status` — derived field (no DB migration). `in_progress` when `completed_at IS NULL`, `failed` when zero items completed but ≥1 failed, `completed` otherwise.

### Changed
- LLM cascade is now Claude → Gemini → Groq (was Claude → Gemini → fail). Groq's free tier alone gives ~10× the daily headroom of Gemini's free tier, so this is the single largest robustness improvement to the agent loop since the typed-ResearchBrief work.

### Why this isn't "switch primary to Claude" instead
That would also fix the symptom but trade rate-limit failures for a Claude bill. v0.15.3 keeps the cost profile flat (all three free providers stay free) while extending the cliff before the agent runs out of LLM headroom.

## [0.15.2] — 2026-04-25 — Exa.ai search fallback

Patch cut after a fresh project ("Platinum industries limited") returned 0 competitors despite agents reporting "completed". Root cause was the search cascade collapsing: Tavily's dev-tier key (`tvly-dev-`) hit its monthly quota → 432, Brave key wasn't set, and DuckDuckGo lite times out under load. Result: every research query returned zero sources, agents finished with "No relevant data found in search", competitors stayed empty.

### Added
- `tools/web_research.py` — Exa.ai as a 2nd-tier provider in the cascade. Order is now Tavily → Exa → Brave → DuckDuckGo. Exa uses neural / semantic search, which is genuinely better than keyword-matching for "find companies similar to X" research-intent queries — so even when Tavily quota is fine, Exa pulls more relevant results for tail-niche industries that keyword search struggles on (plastic additives manufacturers, specialty chemicals, etc).
- `EXA_API_KEY` env var on Railway prism-api.

### Changed
- Search cascade docstring updated to reflect the new four-provider order.

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
