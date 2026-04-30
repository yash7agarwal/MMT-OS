"""Content quality guard — strict gate on every observation written to the
knowledge graph (v0.22.0).

User pain that drove this:
  "the same finding is being reported 10s of times … no compromise on
   accuracy and quality."

What this module guarantees:
  1. Verbatim duplicates merge into the existing row (caller bumps
     `dedupe_count` instead of inserting a second).
  2. ≥85% Jaccard 3-gram similarity counts as duplicate (catches reordering,
     punctuation tweaks, near-paraphrase).
  3. Hard rejects: empty, <30 chars, placeholder strings ("TODO", "TBD"),
     marketing fluff ("strategic synergies", "leveraging market").
  4. Every accepted observation gets a `quality_score` in [0, 1] composed of
     length, specificity (numbers/dates/proper nouns), source presence,
     lens-tag presence, and non-fluff signal.

NOT in scope (deferred to v0.22.x):
  - Embedding-based semantic dedupe (catches pure paraphrase).
  - Per-source authority weighting.

Performance: jaccard is sub-millisecond for typical 100-word inputs.
`is_duplicate_observation` runs O(N) in observations on the same entity;
real entities have ~50–200 observations so this is fine on the hot path.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunable thresholds
# ---------------------------------------------------------------------------

DEFAULT_DUPLICATE_THRESHOLD = 0.85   # Jaccard 3-gram
MIN_CONTENT_CHARS = 30               # hard reject below this
MAX_REASONABLE_LEN = 5000            # observations longer than this get capped score

# Marketing fluff phrases — observations containing any of these are rejected
# unless they ALSO contain a specific datapoint (numbers / dates / names).
_FLUFF_PATTERNS = [
    re.compile(r"\bstrategic\s+synergies?\b", re.IGNORECASE),
    re.compile(r"\bleveraging\s+(?:market|opportunit|capabilit)", re.IGNORECASE),
    re.compile(r"\bworld[\s\-]class\s+(?:platform|solution|team)", re.IGNORECASE),
    re.compile(r"\bdriving\s+(?:innovation|growth|value|transformation)", re.IGNORECASE),
    re.compile(r"\bbest[\s\-]in[\s\-]class\b", re.IGNORECASE),
    re.compile(r"\bcutting[\s\-]edge\b", re.IGNORECASE),
    re.compile(r"\bnext[\s\-]generation\s+(?:platform|technology)", re.IGNORECASE),
    re.compile(r"\bend[\s\-]to[\s\-]end\s+solution", re.IGNORECASE),
    re.compile(r"\bsynergize\b", re.IGNORECASE),
]

# Placeholder / scaffolding tokens — instant reject
_PLACEHOLDER_PATTERNS = [
    re.compile(r"\bTODO\b"),
    re.compile(r"\bTBD\b"),
    re.compile(r"\bXXX\b"),
    re.compile(r"\bN/A\b", re.IGNORECASE),
    re.compile(r"\blorem\s+ipsum\b", re.IGNORECASE),
    re.compile(r"<placeholder>", re.IGNORECASE),
]

# Specificity signals — presence boosts score
_NUMBER_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)?(?:[KMB%]|x|bn|mn)?\b")
_DATE_PATTERN = re.compile(
    r"\b(?:Q[1-4]\s*\d{4}|FY\d{2,4}|\d{4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2}?,?\s*\d{2,4})\b",
    re.IGNORECASE,
)
_PROPER_NOUN_PATTERN = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)*\b")


# ---------------------------------------------------------------------------
# Text normalization + similarity
# ---------------------------------------------------------------------------


def normalize_text(s: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace, drop short common
    words BUT preserve any token containing a digit.

    v0.22.0 review must-fix #1: previously we dropped any token ≤2 chars.
    That stripped "$4B" → "4B" → dropped, "$5B" → dropped, so two filings
    differing ONLY on the dollar amount normalized to identical text and
    were silently merged. We now keep digit-bearing tokens regardless of
    length so numeric magnitudes survive normalization.
    """
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    tokens = [
        t for t in s.split()
        if len(t) > 2 or any(ch.isdigit() for ch in t)
    ]
    return " ".join(tokens)


def _word_3grams(s: str) -> set[tuple[str, str, str]]:
    tokens = s.split()
    if len(tokens) < 3:
        return {(t, "", "") for t in tokens}  # fall back on unigrams for short text
    return {(tokens[i], tokens[i + 1], tokens[i + 2]) for i in range(len(tokens) - 2)}


def jaccard_3gram_similarity(a: str, b: str) -> float:
    """Symmetric Jaccard over word 3-grams of normalized text. 0..1."""
    sa = _word_3grams(normalize_text(a))
    sb = _word_3grams(normalize_text(b))
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ---------------------------------------------------------------------------
# Validation — hard reject conditions
# ---------------------------------------------------------------------------


def _has_specific_datapoint(content: str) -> bool:
    """Numbers, dates, OR multiple proper nouns → has a specific claim."""
    if _NUMBER_PATTERN.search(content):
        return True
    if _DATE_PATTERN.search(content):
        return True
    proper_nouns = _PROPER_NOUN_PATTERN.findall(content)
    return len(set(proper_nouns)) >= 2


def validate_observation(content: str, source_url: str | None) -> tuple[bool, str]:
    """Hard reject conditions. Returns (accept, reason)."""
    if not content or not content.strip():
        return False, "empty"
    stripped = content.strip()
    if len(stripped) < MIN_CONTENT_CHARS:
        return False, f"too short (<{MIN_CONTENT_CHARS} chars)"

    for pat in _PLACEHOLDER_PATTERNS:
        if pat.search(stripped):
            return False, f"contains placeholder ({pat.pattern})"

    # Fluff is rejected unless it ALSO has a specific datapoint
    fluff_match = next((p for p in _FLUFF_PATTERNS if p.search(stripped)), None)
    if fluff_match and not _has_specific_datapoint(stripped):
        return False, f"marketing fluff without specific datapoint ({fluff_match.pattern})"

    return True, "accepted"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_observation(
    content: str,
    source_url: str | None,
    lens_tags: list[str] | None,
) -> float:
    """Composite quality score in [0, 1]. Weighted:
       - length (sweet spot 60-300 chars):     0.20
       - specific datapoint (number/date/PN):  0.25
       - source URL well-formed:               0.25
       - lens_tags non-empty:                  0.10
       - non-fluff:                            0.20
    """
    if not content or not content.strip():
        return 0.0
    s = content.strip()
    score = 0.0

    # Length (gaussian-ish, peaks at 60-300 chars)
    n = len(s)
    if n < 30:
        score += 0.0
    elif n < 60:
        score += 0.10
    elif n <= 300:
        score += 0.20
    elif n <= MAX_REASONABLE_LEN:
        score += 0.15
    else:
        score += 0.05

    # Specificity
    if _has_specific_datapoint(s):
        score += 0.25

    # Source URL
    if source_url and isinstance(source_url, str):
        u = source_url.strip()
        if u.startswith("http://") or u.startswith("https://"):
            if "." in u and len(u) > 12:
                score += 0.25

    # Lens tags
    if lens_tags and len([t for t in lens_tags if t]) > 0:
        score += 0.10

    # Non-fluff bonus
    if not any(p.search(s) for p in _FLUFF_PATTERNS):
        score += 0.20

    return min(1.0, max(0.0, score))


# ---------------------------------------------------------------------------
# Duplicate detection — DB-backed
# ---------------------------------------------------------------------------


def is_duplicate_observation(
    db,                       # sqlalchemy Session — keep import-free for testability
    entity_id: int,
    content: str,
    threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
) -> tuple[Optional[int], float]:
    """Find an existing observation with ≥threshold similarity.

    Returns (existing_observation_id, similarity) on hit, else (None, 0.0).
    Compares against ALL observations on `entity_id`. O(N) per call where N
    is observations on that entity (typically 50-200; sub-millisecond per
    comparison so this is fine).
    """
    from webapp.api.models import KnowledgeObservation

    rows = (
        db.query(KnowledgeObservation.id, KnowledgeObservation.content)
        .filter(KnowledgeObservation.entity_id == entity_id)
        .all()
    )
    if not rows:
        return None, 0.0

    norm_query = normalize_text(content)
    query_grams = _word_3grams(norm_query)
    if not query_grams:
        return None, 0.0

    best_id, best_score = None, 0.0
    for obs_id, obs_content in rows:
        if not obs_content:
            continue
        candidate_grams = _word_3grams(normalize_text(obs_content))
        if not candidate_grams:
            continue
        intersection = len(query_grams & candidate_grams)
        if intersection == 0:
            continue
        sim = intersection / len(query_grams | candidate_grams)
        if sim > best_score:
            best_id, best_score = obs_id, sim

    if best_score >= threshold:
        return best_id, best_score
    return None, 0.0
