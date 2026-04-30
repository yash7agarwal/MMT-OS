# Plan — v0.21.5: per-file progress UI for bulk upload

**Owner:** AI agent following SDLC rule
**Trigger:** "during the upload process it's not clear whether processing really is happening, how much is left, how it's being organized"
**Approach:** Option #2 from the 4-way comparison — per-file client-side iteration. Reuses the existing `/competitors/{id}/upload-report` endpoint. No new server complexity.

---

## 1. Problem

Bulk upload posts the entire folder in one request. Even with v0.21.4's 0.92s response for 5 PDFs, the user sees a spinner with no per-file feedback. For 30 PDFs (the worst case under the cap), the user has 6–25s of opaque waiting. Three specific gaps:

1. **Is processing happening?** — spinner alone doesn't prove the server isn't dead
2. **How much is left?** — no count
3. **How is it being organized?** — no live mapping of "file → matched competitor"

## 2. Approach

**Client-side iteration.** Frontend loops through the picked files, calls a small per-file endpoint for each, accumulates results into a manifest as they land, renders progress live. Keeps the existing batch endpoint for backward compat.

**New backend endpoint:** `POST /api/knowledge/projects/{project_id}/classify-one-report`
- Single multipart `file` upload
- Body: extract → classify → save raw artifact → return ClassifiedReport (no synthesis)
- ~150–500ms per file (PDF size dependent)
- Synthesis is NOT triggered per file — that would slow down each call. Instead, after all files land client-side, frontend calls existing `/industry-pulse` (which auto-triggers synthesis on stale profiles).

**Frontend flow:**
1. User picks folder
2. Filter to PDFs (already done)
3. Render progress card: `0 of N files processed`
4. For each file (sequential):
   - POST to `/classify-one-report`
   - On result → push to local manifest, increment counter, re-render
   - On error → push to `failed[]`, continue
5. After loop done → render full manifest (same UI as v0.21.4) + auto-refresh Industry Pulse (existing path triggers profile synthesis)

## 3. Tasks

### TASK-1: Backend `/classify-one-report` endpoint

**File:** `webapp/api/routes/knowledge.py`

```python
@router.post("/projects/{project_id}/classify-one-report")
async def classify_one_report(
    project_id: int,
    file: UploadFile = File(...),
    classification_strategy: str = Query("fast"),
    db: Session = Depends(get_db),
):
    """Single-PDF version of bulk-upload-reports — no synthesis triggered.
    Used by the frontend's per-file iteration loop so the user sees live
    progress instead of a 6-25s opaque wait."""
```

**Behavior:** Identical to a single-file pass through `bulk_upload_reports` minus the thread pool, the deferred bucket, and synthesis kickoff. Returns one of: matched / unmatched / failed. Reuses the same `classify()` + magic-byte check + extraction + DB-write logic.

**Acceptance criteria:**
- Single-file response in <2s for 1-page PDFs (TestClient assertion)
- Returns same record shape as one entry in `bulk_upload_reports.matched/unmatched/failed`
- Magic-byte check + 50MB cap + extraction error handling all preserved
- Adds artifact to DB; does NOT kick off synthesis

**Tests:**
- Existing `test_bulk_upload_endpoint.py` patterns extended OR new `test_classify_one_endpoint.py` with 4 tests (matched, unmatched, magic-byte fail, OpenAI body-text-only signal)

### TASK-2: Frontend per-file iteration

**Files:** `webapp/web/lib/api.ts`, `webapp/web/app/projects/[id]/industry-pulse/page.tsx`

**API client:**
```typescript
classifyOneReport: async (projectId: number, file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`/api/knowledge/projects/${projectId}/classify-one-report`, {
    method: 'POST', body: fd,
  })
  if (!res.ok) throw new Error(...)
  return res.json()  // single classified record
}
```

**UI flow:**
- Replace the existing `handleBulkUpload` to iterate sequentially
- Add `progress` state: `{ done: number; total: number; current?: string; results: any[] }`
- Render progress card while running:
  ```
  Processing 12 of 30 · openai-10K-2024.pdf
  ████████░░░░░░░░░░░  40%
  Last matched: Anthropic Q3-2024 → Anthropic ✓
  ```
- After loop: render the same manifest UI (matched/unmatched/failed sections)
- Trigger Industry Pulse refresh when done (existing path)

**Acceptance criteria:**
- Visible progress for each file as it lands
- "Last matched" shows the most recent successful classification
- Failed files don't halt the loop
- Cancel button — sets a flag that the loop checks; stops at next iteration
- Final manifest matches the v0.21.4 contract (matched/unmatched/failed)

### TASK-3: Tests

- `tests/test_classify_one_endpoint.py` — 4 integration tests via TestClient + reportlab synthetic PDFs

### TASK-4: Code review

Spawn `code-reviewer` agent on the diff before push. Specific question: does the cancel-button flag actually work given React async state updates? (potential `setState` race with `ref` for cancel signal — should use `useRef`).

### TASK-5: Ship

- Bump VERSION + CHANGELOG + main.py + package.json + README to 0.21.5
- Commit + tag + push + railway up
- Post-deploy live smoke test against deployed Railway URL

### TASK-6: post-task-eval

- `feature_endpoint` task type checks
- All standard checks

## 4. Tradeoffs

| Decision | Gained | Lost | Net |
|---|---|---|---|
| Client-side iteration vs SSE | Simpler, no streaming infra | Lose atomicity (closed browser → partial upload) | Worth it — partial uploads are recoverable |
| Reuse existing `/upload-report` vs new endpoint | Less code | The existing one ALSO triggers synthesis inline (heavy) | Need new lightweight endpoint |
| Skip synthesis per file, batch at end | Each call is fast | One synthesis call per project at the end (10-30s) | Acceptable; runs in background daemon thread |
| Sequential vs parallel client-side | Simpler UX | Slower wall-clock for 30 PDFs | Sequential gives clean progress; ~6s for 30 PDFs is fine |

## 5. Out of scope

- Streaming/SSE-based progress
- Job-queue + polling architecture (would be v0.22.x infra work)
- Cancel-and-rollback semantics (cancel just stops; landed files stay)

## 6. Done = all of:

- [ ] `/classify-one-report` endpoint shipped + tests
- [ ] Frontend iterates with live progress + cancel button
- [ ] Code-reviewer agent on diff → APPROVE
- [ ] v0.21.5 deployed; live smoke test passes (~6s for 5 PDFs sequentially)
- [ ] post-task-eval `feature_endpoint` checks all pass
