# Todo — v0.21.4 bulk-upload reliability fix

> Strict ordering. Do not start TASK-N+1 until TASK-N's acceptance criteria are met.
> Spec: `tasks/plan.md`

## Slice A — deterministic body-text matcher

- [ ] **TASK-1** — Add `body_text_match(pdf_text, filename, competitors, ...)` to `agent/bulk_report_classifier.py` with **structural co-signal gate** (must-fix #1) + 8 unit tests (clear-winner-with-each-co-signal-source, no-co-signal-returns-none "industry report" case, ambiguous, weak, no-match, legal-suffix, case-insensitive)
  - DoD: pytest green; sub-ms per call; the no-co-signal counter-example test passes
- [ ] **TASK-2** — Update `classify()` resolution order: filename → body_text → llm (opt-in via `allow_llm: bool = False` default — safer per should-fix #8). Existing 19 tests still pass + 3 new tests including `test_classify_industry_report_returns_none` (must-fix #1 integration check)
  - DoD: regression suite green; new tests cover body_text_count match_method + LLM-skip behavior

## Slice B — endpoint integration test

- [ ] **TASK-3** — `tests/fixtures/make_pdf.py` synthetic-PDF helper. **Pin `reportlab` in `requirements-dev.txt`** (should-fix #6; silent-skip is forbidden — use `pytest.xfail` with loud message if missing)
  - DoD: `make_synthetic_pdf("text")` returns bytes whose first 4 chars are `%PDF`; pypdf round-trips
- [ ] **TASK-4** — `tests/test_bulk_upload_endpoint.py` integration test: 8 synthetic PDFs → POST endpoint → assert wall-clock <15s + correct manifest counts including new `deferred[]` array + magic-byte fail case
  - DoD: test passes locally; baseline-failure: same test against committed v0.21.3 code times out or gets >15s

## Slice C — wire fix into endpoint

- [ ] **TASK-5** — `webapp/api/routes/knowledge.py:bulk_upload_reports` — **AMENDED**
  - **API surface**: `classification_strategy: Literal["fast", "thorough"] = "fast"` (NOT bool — should-fix #5)
  - **Pre-extraction magic-byte check** (`blob[:4] == b'%PDF'` else fail with `not_a_pdf_magic_bytes`) (must-fix #4)
  - **`ThreadPoolExecutor(max_workers=3)` with `as_completed()` drain pattern** — DB writes on main thread per-future as it resolves (must-fix #2)
  - **Per-future try/except** so one bad PDF doesn't poison the batch (must-fix #4)
  - **Soft-deadline 25s**; remaining files cancel and land in `deferred` array, NOT `failed` (must-fix #3)
  - **`_synth_one_detached`** writes a stub artifact with `error` field on Groq failure instead of silent log (must-fix #4)
  - DB pool sizing documented (5+10=15 capacity vs 7 max load) — no change needed
  - `content_md[:200_000]` vs synthesis `MAX_TEXT_CHARS=60_000` divergence documented inline (should-fix #9)
  - DoD: TASK-4 integration test passes; new endpoint tests for malformed-PDF and deadline-deferred behavior also pass
- [ ] **TASK-6** — Frontend
  - Manifest UI handles `body_text_count` match_method label
  - **NEW**: render `deferred` array with "Re-upload these N" CTA (must-fix #3)
  - DoD: tsc clean; deferred section appears when manifest contains entries

## Checkpoint 1 — code-reviewer agent on slices A-C

- [ ] Spawn `code-review-and-quality` agent with diff
- [ ] Resolve any "must-fix" items
- [ ] DoD: no blocking issues remain

## Slice D — ship + verify

- [ ] **TASK-7** — Version bump 0.21.3 → 0.21.4; CHANGELOG; commit; tag; push; railway up
  - DoD: live OpenAPI reports 0.21.4
- [ ] **TASK-8** — Deployed-endpoint smoke: 5 synthetic PDFs against live URL
  - DoD: 200 OK in <15s, all PDFs accounted for, no CD8

## Checkpoint 2 — post-task-eval

- [ ] Invoke `/post-task-eval` with `feature_endpoint` task type
- [ ] All 4 new feature_endpoint checks PASS (plan-trail, endpoint-latency smoke, integration test, failure-mode coverage)
- [ ] All standard checks PASS (python/ts module, git, deployment)
- [ ] DoD: eval_pass logged to memory/issues_log.jsonl
- [ ] LESSONS.md chapter added via `/project-chronicle`

## Known should-fix follow-ups (deferred to v0.21.5)

- [ ] Filename co-signal naive substring: `_has_structural_signal` does `needle_lower in fn_lower`, so "apple" matches "pineapple-research.pdf". Use word-boundary regex `\bapple\b`. Acceptable to defer because the SEC-marker tightening + dominance gate already neutralize the highest-impact false-match path; a competitor literally named "apple" with a "pineapple" filename is a low-probability edge case.
