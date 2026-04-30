# Plan — Bulk-upload reliability fix (v0.21.4)

**Owner:** AI agent following workspace SDLC rule (`feedback_sdlc_enforcement.md`)
**Spec status:** No formal SPEC.md exists for this feature. Writing inline below.
**Mode:** Plan only — no code changes until human approval.
**Status:** AMENDED 2026-04-30 after code-review (must-fix items 1–4 + should-fix 5–9 incorporated). See "Code-review log" at bottom.

---

## 1. Problem statement

`POST /api/knowledge/projects/{id}/bulk-upload-reports` returns **502 ROUTER_EXTERNAL_TARGET_CONNECTION_ERROR_CD8** on Railway when the user uploads a folder of multiple PDFs. v0.21.3 detached business-history *synthesis* to background threads, but **classification + extraction still runs inline** and that's enough to blow past Railway's ~60s edge proxy timeout.

### Where the latency goes (per file, today)

| Step | Cost | Inline today? |
|---|---|---|
| `await f.read()` (buffer file) | ~0.5–2s for 50MB | yes |
| `pypdf.PdfReader(...)` extraction | **5–30s** for a 200-page 10-K | yes |
| `filename_match()` substring | <1ms | yes (fine) |
| `llm_classify()` Groq call | **5–15s per ambiguous filename** | yes (this is the killer) |
| DB save artifact | ~50ms | yes (fine) |
| Synthesis (business profile) | 10–30s per matched competitor | **already detached in v0.21.3** ✓ |

For 5 PDFs where 3 trigger LLM disambiguation: 5×10s extraction + 3×10s LLM = ~80s. CD8 timeout fires at ~60s.

### Root causes

1. **LLM classification on the request thread.** Bulk path doesn't need it — there's a deterministic alternative (count name occurrences in body text).
2. **Extraction not parallelized.** pypdf is single-threaded but releases the GIL on IO; a thread pool with cap 3 cuts wall-clock for N files by ~3×.
3. **No request-budget enforcement.** Endpoint has no soft-deadline tracking; nothing stops it from running until Railway kills the connection.

---

## 2. Goals & non-goals

### Goals
- 30-PDF folder upload returns within **15s p50, 30s p99**
- Zero new 502 / OOM under realistic load (30 files × 50MB cap)
- All matched PDFs become artifacts; unmatched go to a clear bucket for manual reassign
- Synthesis still runs (in background) — no functional regression vs v0.21.3
- **No new hallucination risk** — body-text match is deterministic occurrence-count; LLM remains opt-in only with explicit null-output handling

### Non-goals
- Streaming / chunked uploads (would require API contract change, defer to v0.22.x)
- Persistent file storage (Railway has no durable FS — out of scope)
- Reprocessing existing artifacts (pure forward-fix)

---

## 3. Approach — vertical slices

Each slice ships an independently-mergeable improvement. Slices A–B are infrastructure (no user-visible impact); Slice C is the actual fix; Slice D is deploy + verify.

### Slice A — Deterministic body-text matcher

Replaces in-band LLM classification with sub-millisecond name-occurrence counting on extracted text.

### Slice B — Endpoint-latency integration test fixture

Synthetic PDFs + a `pytest` integration test that hits `bulk_upload_reports` and asserts wall-clock budget. **Caught by upgraded `/post-task-eval` `feature_endpoint` check.**

### Slice C — Wire body-text match + skip-LLM-by-default + parallel extraction

The actual user-facing fix.

### Slice D — Deploy + endpoint smoke test against live Railway

---

## 4. Task list with acceptance criteria

### TASK-1 (Slice A): `body_text_match` function — **AMENDED for must-fix #1**

**Files:** `agent/bulk_report_classifier.py`, `tests/test_bulk_report_classifier.py`

**Contract:**
```python
def body_text_match(
    pdf_text: str,
    filename: str,                       # NEW: needed for co-signal check
    competitors: list[dict],
    min_occurrences: int = 5,
    dominance_ratio: float = 3.0,
) -> tuple[int, str, int] | None:
    """Returns (entity_id, name, occurrence_count) or None.

    Dominance alone is insufficient — an industry report can mention one
    company 100×. We require a STRUCTURAL CO-SIGNAL in addition: the
    candidate name must also appear in EITHER:
      (a) the filename (any substring, case-insensitive), OR
      (b) the first 200 chars of body (cover page / title), OR
      (c) within 500 chars of a 10-K / 20-F / 40-F structural marker
          (e.g. "UNITED STATES SECURITIES AND EXCHANGE COMMISSION",
          "ANNUAL REPORT PURSUANT TO SECTION", or
          "FORM 10-K" / "FORM 20-F").

    Without a co-signal, even a 100× occurrence count returns None.
    This kills the "AI Industry Report mentions OpenAI 100×" failure mode.
    """
```

**Behavior:**
- Lowercase haystack (first 60K chars only — matches synthesis cap)
- For each competitor, count occurrences of `name` (with trailing legal suffixes stripped: `, Inc.`, `LLC`, `Corporation`, etc.)
- Compute dominance: top must be `≥ min_occurrences` AND `≥ dominance_ratio × runner_up` (or `≥ 20` AND `≥ 2× runner_up`)
- **Co-signal gate (NEW):** evaluate `_has_structural_signal(name, filename, pdf_text[:200], pdf_text)` — must return True for the candidate to win
- Otherwise return None.

**Acceptance criteria:**
- 8+ unit tests:
  - Clear winner WITH filename co-signal → match ✓
  - Clear winner WITH cover-page co-signal but NOT filename → match ✓
  - Clear winner WITH 10-K marker proximity → match ✓
  - Clear dominance BUT no co-signal anywhere → returns None (the "industry-report" case) ✓
  - Ambiguous (~equal counts) → returns None ✓
  - Weak (top has 3 occurrences) → returns None ✓
  - No match → returns None ✓
  - Legal-suffix stripping case-insensitive ✓
- Sub-millisecond per call

**Dependencies:** none

---

### TASK-2 (Slice A): Update `classify()` to use body-text before LLM — **AMENDED for must-fix #4 + should-fix #8**

**Files:** `agent/bulk_report_classifier.py`, `tests/test_bulk_report_classifier.py`

**Behavior change:**
```
Order of resolution:
  1. filename_match         (deterministic, sub-ms)
  2. body_text_match        (deterministic, sub-ms — NOW with co-signal gate from TASK-1)
  3. llm_classify           (only if allow_llm=True)
  4. None / unmatched
```

**Updated signature (must-fix #4 + should-fix #8 — safer default):**
```python
def classify(
    filename: str,
    pdf_text: str,
    competitors: list[dict],
    *,
    allow_llm: bool = False,            # CHANGED: was True. Bulk path uses default; per-file UI explicitly passes True if user opts in.
) -> ClassifiedReport:
```

**Magic-byte sanity (must-fix #4):** classifier doesn't read raw bytes; magic-byte check lives in TASK-5 caller (only the route handler has the bytes). Documented here for traceability.

**Acceptance criteria:**
- Existing 19 tests still pass (regression) — note: tests that relied on `allow_llm=True` default must be updated to pass it explicitly
- New tests:
  - `test_classify_uses_body_text_when_filename_misses` — filename has no match, body text mentions competitor 50× WITH co-signal → matched via body_text_count
  - `test_classify_skips_llm_when_allow_llm_false` — ambiguous filename + ambiguous body + `allow_llm=False` → unmatched (no LLM call)
  - `test_classify_industry_report_returns_none` — feed a synthetic "AI Industry Report" mentioning OpenAI 100× without co-signal → unmatched (proves the must-fix #1 fix works at integration level)
- `match_method` literal extended to include `"body_text_count"`

**Dependencies:** TASK-1

---

### TASK-3 (Slice B): Synthetic PDF fixture + helper — **AMENDED for should-fix #6**

**Files:** `tests/fixtures/__init__.py` (new), `tests/fixtures/make_pdf.py` (new), `requirements-dev.txt` (new or extended)

**Behavior:**
- Helper `make_synthetic_pdf(text: str, pages: int = 1) -> bytes` produces a real PDF in memory using `reportlab`
- **`reportlab` becomes a pinned dev dep** — added to `requirements-dev.txt`, NOT silently optional. Should-fix #6 fix: silent-skip means CI is uncovered.
- If `reportlab` is somehow missing at test time, the test uses `pytest.xfail("reportlab not installed — required dev dep")` so CI surfaces it loudly (vs `skip` which hides).

**Acceptance criteria:**
- `pip install -r requirements-dev.txt` includes reportlab
- `python -c "from tests.fixtures.make_pdf import make_synthetic_pdf; b = make_synthetic_pdf('hello'); assert b[:4] == b'%PDF'"`
- pypdf can extract text from the produced bytes round-trip
- CI (when added) installs requirements-dev.txt — documented in README

**Dependencies:** none (parallel with TASK-1)

---

### TASK-4 (Slice B): Endpoint integration test with latency assertion

**File:** `tests/test_bulk_upload_endpoint.py` (new)

**Behavior:**
- Use `fastapi.testclient.TestClient` with an in-memory SQLite override
- Seed: 1 project + 5 competitors
- Build 8 synthetic PDFs (mix of named + ambiguous filenames) using TASK-3 helper
- POST to `/api/knowledge/projects/{id}/bulk-upload-reports`
- Assert:
  - response status 200
  - response time `< 15s` wall-clock (with `allow_llm=False`)
  - `matched_count + unmatched_count + failed_count == 8`
  - manifest contains `synthesizing: True/False` flag
  - no in-flight DB transactions left dangling

**Acceptance criteria:**
- Test passes locally
- This test would have FAILED before the fix (pin via baseline run if possible)

**Dependencies:** TASK-3

---

### TASK-5 (Slice C): Bulk endpoint — **AMENDED for must-fix #2, #3, #4 + should-fix #5, #7**

**Files:** `webapp/api/routes/knowledge.py`

**Changes (numbered to map to review items):**

**A. API surface (should-fix #5).** Use a Literal enum, not a bool:
```python
classification_strategy: Literal["fast", "thorough"] = Query("fast")
```
Default `"fast"` = no LLM. `"thorough"` = filename → body_text → LLM (uses Groq quota). Future-proof for adding `"ocr"` etc.

**B. Pre-extraction sanity gate (must-fix #4 — magic-byte check):**
```python
if blob[:4] != b"%PDF":
    failed.append({"filename": f.filename, "error": "not_a_pdf_magic_bytes"})
    del blob
    continue
```
Fails fast on `.pdf` files that are actually HTML/spam. Cheap (4 bytes).

**C. Thread-pool architecture using `as_completed()` (must-fix #2):**

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _worker(filename: str, blob: bytes) -> dict:
    """Pure: read blob → extract text → classify. No DB. Frees blob before return."""
    text, meta = extract_text_from_pdf_bytes(blob)
    cr = classify(filename, text, competitors, allow_llm=(strategy == "thorough"))
    return {"filename": filename, "text": text, "meta": meta, "classified": cr}

# Submit all then drain via as_completed:
with ThreadPoolExecutor(max_workers=3) as pool:
    futures = {pool.submit(_worker, f.filename, await_blob): f.filename
               for f in files
               if (await_blob := await _read_with_caps(f)) is not None}
    deadline = time.monotonic() + 25.0
    for fut in as_completed(futures, timeout=None):
        if time.monotonic() > deadline:
            # CRITICAL: cancel remaining futures, mark them deferred (not failed)
            for pending in futures:
                if not pending.done():
                    pending.cancel()
                    deferred.append({"filename": futures[pending], "reason": "batch_timeout_cancelled"})
            break
        result = fut.result()
        # DB write here on main thread — session-safe.
        artifact = KnowledgeArtifact(...); db.add(artifact); db.commit()
        ...
```

Realistic latency model (must-fix #2 — should-fix #7 corrected):
- pypdf is GIL-held on text decoding; GIL released on raw IO only
- Empirical speedup: **~1.5×, not 3×**. Target updated: 30 PDFs × ~1.5s avg / 1.5x = ~30s total — too tight for 15s p50 goal
- **Revised goal:** 15 PDFs in <15s p50 with `fast` strategy, OR 30 PDFs in <30s p99
- For >15 PDFs the user gets the `deferred` bucket UX (must-fix #3)

**D. Deferred bucket (must-fix #3):**
```python
return {
    "matched": [...],
    "unmatched": [...],
    "failed": [...],          # extraction errors, not_a_pdf, etc.
    "deferred": [...],        # NEW — cancelled by deadline; UI prompts re-submit
    "synthesizing": True,
    ...
}
```
Frontend (TASK-6) shows deferred list with a "Re-upload these N" button.

**E. Failure-mode handlers (must-fix #4):**
- **Per-future try/except** — one bad PDF doesn't poison the batch:
  ```python
  try:
      result = fut.result()
  except Exception as exc:
      failed.append({"filename": futures[fut], "error": f"worker_exception: {exc}"})
      continue
  ```
- **Magic-byte check** — covered above (B)
- **Groq-down during background synthesis** — `_synth_one_detached` writes a stub `business_history` artifact with `"error": "synthesis_failed_retry"` field instead of silently swallowing
- **DB pool sizing** — current pool is `pool_size=5, max_overflow=10` (db.py:49) → 15 concurrent. Worker pool=3 + synth pool=3 + main=1 = 7 max. Documented as headroom-OK; no change needed

**F. content_md cap reconciliation (should-fix #9):** `content_md=text[:200_000]` for raw-artifact storage stays (allows users to read full text in the UI). Synthesis caps at 60K via `MAX_TEXT_CHARS` in `business_history.py`. Comment added at the artifact-write site explaining the asymmetry.

**Acceptance criteria:**
- TASK-4 integration test passes with new code path
- Existing 19 classifier tests still pass after `allow_llm=False` default flip
- New endpoint test: 1 malformed-bytes file → `failed: not_a_pdf_magic_bytes` (no crash)
- New endpoint test: 20-file batch with deadline patched to 1s → ≥1 `deferred` entry, no `failed: batch_timeout`
- Manual smoke (Slice D verifies on deploy)

**Dependencies:** TASK-2, TASK-4

---

### TASK-6 (Slice C): Frontend — handle new `body_text_count` match_method

**File:** `webapp/web/app/projects/[id]/industry-pulse/page.tsx`

**Changes:**
- Manifest UI already renders `match_method` as a string — confirm it pretty-prints `body_text_count` cleanly (lowercase + `_` → space)
- Add a small UX hint: "Files matched via body-text count are deterministic — no LLM was called"
- Frontend types updated for new match_method literal

**Acceptance criteria:**
- `npx tsc --noEmit` passes
- Manual smoke shows the new label rendering

**Dependencies:** TASK-5

---

### Checkpoint 1 — `code-reviewer` agent on Slices A–C

**After TASK-6, before TASK-7:** spawn `code-review-and-quality` skill / agent with the diff. Specifically ask for:
- Concurrency safety (thread pool + DB session)
- Counter-examples to the body-text dominance ratio (could 3× still mis-attribute on a comparison report?)
- Memory profile (does we still risk OOM with 30 PDFs × thread pool?)
- API surface — is `use_llm_disambiguation` flag the right shape?

**Block on:** any "must-fix" items from the review.

---

### TASK-7 (Slice D): Version bump + commit + push + deploy

**Files:** `VERSION`, `webapp/api/main.py`, `webapp/web/package.json`, `README.md`, `CHANGELOG.md`

**Behavior:**
- Bump to `0.21.4` (patch — bug fix, no API removal)
- Commit message: explain the fix + test discipline applied
- Tag `v0.21.4`
- `railway up --detach`

**Acceptance criteria:**
- `git status` clean
- Live API reports `0.21.0` ... `0.21.4` after deploy completes

**Dependencies:** Checkpoint 1 cleared

---

### TASK-8 (Slice D): Deployed-endpoint smoke test

**Behavior:**
- Build 5 synthetic PDFs locally
- POST to live `https://prism-api-production-18bf.up.railway.app/api/knowledge/projects/{id}/bulk-upload-reports`
- Assert: 200 OK, response time < 15s

**Acceptance criteria:**
- Wall-clock under 15s
- All 5 PDFs accounted for (matched + unmatched + failed = 5)
- No CD8 / 502

**Dependencies:** TASK-7

---

### Checkpoint 2 — `/post-task-eval` with `feature_endpoint` task type

Run the upgraded post-task-eval. New checks specifically for this task type:
1. **Plan-trail check** — this `tasks/plan.md` file satisfies it ✓
2. **Endpoint-latency smoke** — TASK-8 satisfies it ✓
3. **Integration test exists** — TASK-4 satisfies it ✓
4. **Failure-mode coverage** — TASK-5 includes batch_timeout fallback + 30-file cap ✓
5. Plus the standard python_module / typescript_module / git_operation / deployment checks

If any FAIL: stop, do not say done.

---

## 5. Dependency graph

```
TASK-1 ──┐
         ├──> TASK-2 ──> TASK-5 ──> TASK-6 ──┐
TASK-3 ──┘                                   │
         └──> TASK-4 ──────────────^         │
                                              │
                                              ↓
                                     Checkpoint 1 (code-reviewer)
                                              │
                                              ↓
                                          TASK-7 ──> TASK-8
                                                       │
                                                       ↓
                                              Checkpoint 2 (post-task-eval)
```

Slice A (TASK-1, TASK-2) and Slice B-prep (TASK-3) can run in parallel.

---

## 6. Risks & open questions

1. **`reportlab` for synthetic PDFs** — is it installed? If not, integration test will need a different fixture approach. *Mitigation:* TASK-3 will check at module import time and skip gracefully; we can add `reportlab` to dev-deps if missing.
2. **Body-text match false positives** — A "Generative AI Industry Report 2025" may mention "OpenAI" 80×, "Anthropic" 40×, "Google" 30×. Top is 2× runner-up → returns None (correct: this is an industry report, not an OpenAI 10-K). Verified by the 3× dominance ratio. *Test in TASK-1.*
3. **DB session in thread pool** — current code uses a single session; thread pool workers cannot share. *Mitigation:* extract+classify in workers (no DB), then sync the DB writes on the main thread.
4. **Soft-deadline truncation** — if user uploads 30 files and we hit 25s after processing 18, the remaining 12 land as "failed: batch_timeout". User has to retry with a smaller batch. *Acceptable per workspace's "transparent failure" preference.*

---

## 7. Out-of-scope (note for future work)

- True streaming uploads (chunked transfer encoding) — requires API contract change
- Persistent storage of source PDFs — needs durable FS or S3
- LLM disambiguation as a *separate* "deepen classification" endpoint the user opts into per file
- Re-processing existing artifacts under the new classifier

---

## 7b. Code-review log

**Reviewer:** general-purpose agent acting as senior staff engineer (independent context, no shared state).
**Date:** 2026-04-30.
**Verdict:** Approve-with-changes (4 must-fix, 5 should-fix, 4 out-of-scope).

### Must-fix items addressed in this amendment:
1. **Body-text dominance + structural co-signal** → TASK-1 contract updated; co-signal gate added; new `test_classify_industry_report_returns_none` test required.
2. **Thread-pool architecture** → TASK-5 spec rewritten with `as_completed()` drain pattern; latency model corrected from "3×" to "1.5×"; goal revised to 15 PDFs in <15s OR 30 in <30s p99.
3. **`failed: batch_timeout` → `deferred` bucket** → TASK-5 manifest contract gains `deferred[]` array; TASK-6 frontend shows "Re-upload these N" CTA.
4. **Failure-mode handlers** → TASK-5 added: magic-byte check, per-future try/except, Groq-down stub artifact, DB pool sizing documented.

### Should-fix items addressed:
5. `use_llm_disambiguation: bool` → `classification_strategy: Literal["fast", "thorough"]` in TASK-5
6. `reportlab` silent-skip → pinned dev dep in TASK-3, `pytest.xfail` not skip
7. Latency-budget math in §1 — corrected (see TASK-5 amendment)
8. `allow_llm` default flipped to `False` in TASK-2 (safer)
9. `content_md[:200_000]` vs `MAX_TEXT_CHARS=60_000` cap divergence — documented in TASK-5(F)

### Out-of-scope (deferred to v0.22.x or beyond):
- Persistent file stash + true background queue
- OCR for scanned PDFs
- Streaming/chunked uploads
- Re-classifying historical artifacts under new matcher

### Re-review trigger
If implementation diverges materially from this amended spec (e.g. different concurrency primitive, different co-signal heuristic), re-spawn the code-reviewer on the diff before pushing.

---

## 8. Done = all of:

- [ ] All 8 tasks complete with passing acceptance criteria
- [ ] `code-reviewer` checkpoint cleared
- [ ] Live deploy at v0.21.4 reachable
- [ ] Endpoint smoke test passes against deployed URL
- [ ] `/post-task-eval` with `feature_endpoint` type passes all 4 new checks + the standard checks
- [ ] CHANGELOG entry honest about the prior failures and what changed
- [ ] LESSONS.md updated (`/project-chronicle`) with the SDLC-discipline tradeoff: "we got 3 user-visible 502s before adopting full SDLC; here's what's different now"
