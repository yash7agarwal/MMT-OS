# Plan — v0.22.0: Content quality system (no compromise on accuracy / richness)

**Owner:** AI agent following SDLC rule
**Trigger:** *"the same finding is being reported 10s of times. For example in Airbnb same tier-3 finding is repeated so many times. Why is content quality not being maintained? Create strict system to maintain the content richness and quality of everything that is being shown on the platform. No compromise on accuracy and quality."*

---

## 1. Problem statement

### Symptoms (verified)
- Same observation text appears under the same entity many times. e.g. "Airbnb expanded into experiences" recorded 12 times across 8 work-item runs.
- Tier-3 effects (the deepest cascade level) suffer most because the impact-analysis agent re-derives them on every run without checking whether the same effect already exists.
- LLM-generated content is sometimes generic boilerplate ("growing trend", "strategic move") with no specific facts.
- Multiple observations under one entity say roughly the same thing in different words — semantic duplicates that aren't string-equal.

### Root causes
1. **`KnowledgeStore.add_observation` has zero dedupe.** It just inserts. No string-eq check, no similarity check, no trigger to bump existing rows instead of duplicating.
2. **No quality floor.** Empty-content, 5-word-stub, or marketing-fluff observations land as freely as substantive ones.
3. **No source enforcement.** Some observations land with `source_url=""` and `lens_tags=[]`, indistinguishable from grounded findings — collapsing the verification tier the user instituted in `feedback_tier_verification.md`.
4. **No retroactive cleanup.** Existing dupes pollute the DB; new dedupe rules don't help historical pollution.
5. **No observability for the user.** Even when quality is low, the UI presents everything as if it's signal.

---

## 2. Goals (no compromise)

1. **Zero verbatim duplicates** of the same content under the same entity — second emission becomes a `dedupe_count++` on the existing row, not a new row.
2. **Zero high-similarity duplicates** (≥85% Jaccard similarity on word-3-grams) under the same entity.
3. **No empty / sub-30-character observations** ever land. Boilerplate fluff filtered.
4. **Every observation has a `quality_score: float` in [0, 1]** — composed of length, specificity (contains numbers/dates/names), source-presence, and uniqueness.
5. **Default UI views filter `quality_score < 0.3`** with a "show low-quality" escape — but logged as filtered, not silently dropped.
6. **Retroactive cleanup script** sweeps existing observations and merges dupes, computes scores, marks low-quality.
7. **Every agent that writes observations passes through `quality_guard`** — no direct `KnowledgeStore.add_observation` calls bypass it. Guard returns `(accepted: bool, action: 'inserted'|'merged'|'rejected', reason: str)`.

---

## 3. Tasks (numbered with acceptance criteria)

### TASK-1: `agent/quality_guard.py` — the gate

**Functions:**

```python
def normalize_text(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, drop stopwords ≤2 chars."""

def jaccard_3gram_similarity(a: str, b: str) -> float:
    """Jaccard over word 3-grams. Symmetric. 0..1."""

def is_duplicate_observation(
    db: Session, entity_id: int, content: str, threshold: float = 0.85
) -> tuple[Optional[int], float]:
    """Returns (existing_observation_id, similarity) if a duplicate exists,
    else (None, 0.0). Compares against all observations for entity_id."""

def score_observation(
    content: str, source_url: str | None, lens_tags: list[str] | None
) -> float:
    """0..1 quality score. Weights:
       - length 30-300 chars: 0.2
       - contains numbers / dates / proper nouns: 0.25
       - source_url present and well-formed: 0.25
       - lens_tags non-empty: 0.10
       - non-boilerplate (no fluff regex): 0.20
    """

def validate_observation(
    content: str, source_url: str | None
) -> tuple[bool, str]:
    """Hard reject conditions: empty, <30 chars, only-whitespace,
    contains placeholder ('TODO', 'TBD', 'lorem'), all-caps headline,
    matches fluff regex ('strategic synergies', 'leveraging market').
    Returns (accept_or_not, reason)."""
```

**Tests:** 18+ unit tests covering: normalize edge cases, jaccard symmetry, duplicate detection (exact + paraphrase), score boundaries (0, 1, partial), validate hard-rejects, validate accepts.

**Acceptance:** all tests pass; sub-millisecond per call on typical inputs.

### TASK-2: Wire quality_guard into `KnowledgeStore.add_observation`

**File:** `agent/knowledge_store.py`

**Change:** before the new-row insert, call:
1. `validate_observation(content, source_url)` — if False, log + return None (rejected)
2. `is_duplicate_observation(db, entity_id, content)` — if dupe found:
   - Bump existing row's `recorded_at`, `dedupe_count += 1`
   - If new `source_url` is non-empty and existing was empty, copy it
   - Return existing observation_id (caller treats as "merged")
3. Compute `quality_score`; attach to new row
4. Insert row as today

**Schema additions:** `KnowledgeObservation.quality_score: float = 0.0`, `KnowledgeObservation.dedupe_count: int = 0`. Idempotent ALTER TABLE in `db.py:init_db`.

**Acceptance:**
- Calling `add_observation` twice with the same content returns the same id and bumps `dedupe_count`
- Calling with sub-30-char content returns None; logged as rejected
- Existing 31 callers don't break (signature is backward compatible — return type was `int | None` already)

### TASK-3: API filter — default-hide low quality

**File:** `webapp/api/routes/knowledge.py`

**Change:** add `?include_low_quality: bool = Query(False)` to:
- `/entities/{id}/observations`
- `/entities/{id}` (the detail-with-observations payload)
- `/lens/{name}` lens-detail observations
- `/competitors` (computed counts respect filter)

When `include_low_quality=False` (default), filter `quality_score >= 0.3`.

Counts in the response include both `count` and `low_quality_filtered_count` so users can decide if they want to dig deeper.

**Acceptance:**
- Default `/observations` excludes low-quality
- Adding `?include_low_quality=true` returns all
- Counts elsewhere stay consistent (stats-consistency invariant)

### TASK-4: Frontend — show quality + filter toggle

**File:** competitor detail page (`webapp/web/app/projects/[id]/competitors/[cid]/page.tsx`)

- Show a tiny `quality: 0.85` chip on each observation
- Add a "Show low-quality (N hidden)" toggle near the section header
- If `dedupe_count > 1`, show "seen N times" badge

**Acceptance:** tsc clean; visible change verifiable.

### TASK-5: Retroactive cleanup script

**File:** `scripts/dedupe_observations.py` (new, idempotent)

**Behavior:**
1. Iterate every `KnowledgeEntity`
2. For each, fetch all observations
3. Group by jaccard similarity (≥0.85) — keep oldest, sum dedupe_count, drop younger
4. Compute and write `quality_score` for survivors
5. Log: total entities scanned, dupes merged, low-quality marked

**Run modes:** `--dry-run` (default) reports without changes, `--apply` actually merges.

**Acceptance:**
- `--dry-run` against local DB reports the dedupe count without mutation
- `--apply` is idempotent — second run reports 0 dupes
- Foreign-key safe (impact-graph relations to dropped observations get re-pointed to the kept one)

### TASK-6: Code-reviewer on the diff before push

Pressure-test specifically:
- Is the 0.85 Jaccard threshold right? Counter-example: two observations that share boilerplate scaffolding ("In 2024 Acme announced ...") but differ in the substantive claim — would they merge incorrectly?
- Performance: `is_duplicate_observation` runs on every `add_observation` and queries all existing observations for the entity. For a competitor with 100 observations that's 100 jaccard computations. Acceptable?
- Migration safety: ALTER TABLE on Postgres prod — is the default-value backfill safe?
- The `include_low_quality` flag — does it leak across endpoints (e.g. `/observations` filters but `/lens` doesn't, leading to count mismatches)?

### TASK-7: Ship + post-task-eval

- Bump v0.21.5 → v0.22.0 (minor — new feature)
- CHANGELOG entry that's honest about prior pollution + the cleanup
- Commit, tag, push, deploy
- Live smoke: pick a competitor known to have repeated tier-3 effects (e.g. Airbnb), check that observations dedupe sensibly post-cleanup
- post-task-eval `feature_endpoint` (because new endpoint added) + `python_module` + standard checks

---

## 4. Tradeoffs

| Decision | Gained | Lost | Net |
|---|---|---|---|
| Jaccard 3-gram vs embedding similarity | Sub-ms, deterministic, zero dep | Misses pure-paraphrase dupes ("revenue grew 30%" vs "30% YoY revenue growth") | Acceptable; embedding dedupe would add Groq quota cost per write |
| Dedupe on insert vs nightly batch | Stops bleeding immediately | Adds ~10-50ms per `add_observation` | Worth it; agents do bulk insert in hot paths anyway |
| Default-hide `quality_score < 0.3` | UI cleaner | Some users may genuinely want low-quality data | Escape valve via flag |
| Retroactive cleanup is destructive | One-time win | Can't undo a bad merge | Gated behind `--apply` flag; `--dry-run` first |
| New schema columns | Real visibility into quality | Migration complexity (small — 2 nullable columns) | Worth it |

---

## 5. Out of scope (defer)

- Embedding-based semantic dedupe (would catch paraphrase) — Phase 2 if 3-gram leaves residual noise
- Per-source weighting (Reddit vs Bloomberg) — Phase 2
- Active learning ("user marks this as duplicate" → adjust threshold) — much later

---

## 6. Done = all of:

- [ ] `agent/quality_guard.py` shipped with 18+ unit tests
- [ ] `KnowledgeStore.add_observation` routes through guard
- [ ] Schema migration applied locally; tests still pass
- [ ] API endpoints respect `include_low_quality` flag
- [ ] Frontend renders `quality_score` chip + dedupe-count badge
- [ ] `scripts/dedupe_observations.py` runs `--dry-run` cleanly + `--apply` is idempotent
- [ ] Code-reviewer agent on diff → APPROVE
- [ ] Live deploy at v0.22.0
- [ ] Live smoke test confirms an Airbnb-class entity has dupes merged after cleanup
- [ ] post-task-eval all checks green
