"""Unit tests for agent/quality_guard.py (v0.22.0).

Pins the no-compromise content quality invariants:
- Verbatim duplicates merge into existing rows (don't insert second)
- ≥85% Jaccard 3-gram similarity counts as duplicate
- Hard rejects: empty, <30 chars, fluff regex, placeholder strings
- quality_score in [0, 1] composed of length, specificity, source, lens-tags, non-fluff
"""
from __future__ import annotations

import pytest

from agent import quality_guard as qg


# ---------------------------------------------------------------------------
# Normalize
# ---------------------------------------------------------------------------


def test_normalize_lowercases_and_strips_punctuation():
    assert qg.normalize_text("Hello, World!") == "hello world"


def test_normalize_collapses_whitespace():
    assert qg.normalize_text("hello    \n\n  world") == "hello world"


def test_normalize_drops_short_stopwords():
    # "to", "of" are <=2 chars — dropped to reduce common-word noise
    out = qg.normalize_text("Acme to acquire Beta of California")
    assert out == "acme acquire beta california"


def test_normalize_preserves_numeric_tokens_regardless_of_length():
    """v0.22.0 must-fix #1: digit-bearing tokens must SURVIVE normalization
    even when ≤2 chars. Otherwise '$4B' and '$5B' collapse to identical
    strings and observations differing only in dollar amount silently merge.
    """
    out_4b = qg.normalize_text("Acme reported revenue of $4B in 2024")
    out_5b = qg.normalize_text("Acme reported revenue of $5B in 2024")
    assert out_4b != out_5b, "numeric magnitude must NOT collapse"
    assert "4b" in out_4b
    assert "5b" in out_5b


def test_jaccard_distinguishes_different_dollar_amounts():
    """Direct integration check: two observations differing only on $amount
    must NOT cross the 0.85 dedupe threshold."""
    a = "Acme reported revenue of $4B in Q3 2024 with 30% growth"
    b = "Acme reported revenue of $5B in Q3 2024 with 30% growth"
    sim = qg.jaccard_3gram_similarity(a, b)
    assert sim < 0.85, f"$4B vs $5B should not cross dedupe threshold, got {sim}"


# ---------------------------------------------------------------------------
# Jaccard similarity
# ---------------------------------------------------------------------------


def test_jaccard_identical_strings():
    s = "the quick brown fox jumped over the lazy dog"
    assert qg.jaccard_3gram_similarity(s, s) == 1.0


def test_jaccard_completely_different():
    assert qg.jaccard_3gram_similarity(
        "alpha beta gamma delta epsilon",
        "one two three four five six",
    ) == 0.0


def test_jaccard_symmetric():
    a = "Acme expanded into Asia in 2024"
    b = "in 2024 Acme expanded into Asia"
    assert qg.jaccard_3gram_similarity(a, b) == qg.jaccard_3gram_similarity(b, a)


def test_jaccard_paraphrase_has_partial_similarity():
    # Word-3-gram Jaccard misses pure paraphrase but catches reordering.
    a = "Acme acquired Beta for two billion dollars in 2024"
    b = "Acme acquired Beta for two billion dollars in 2024 in cash"
    score = qg.jaccard_3gram_similarity(a, b)
    assert score > 0.5
    assert score < 1.0


# ---------------------------------------------------------------------------
# is_duplicate_observation — uses real DB
# ---------------------------------------------------------------------------


@pytest.fixture
def db_with_obs(tmp_path):
    """Builds a minimal in-memory SQLite DB with one entity and 3 observations."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from webapp.api.db import Base
    from webapp.api.models import KnowledgeEntity, KnowledgeObservation, Project

    engine = create_engine(f"sqlite:///{tmp_path}/qg.db", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    p = Project(name="x", description="x")
    db.add(p)
    db.commit()
    db.refresh(p)
    e = KnowledgeEntity(
        project_id=p.id, entity_type="company", name="Acme",
        canonical_name="acme", source_agent="t", confidence=1.0,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    obs1 = KnowledgeObservation(
        entity_id=e.id, observation_type="general",
        content="Acme acquired Beta for two billion in 2024",
        source_url="", source_agent="t",
    )
    obs2 = KnowledgeObservation(
        entity_id=e.id, observation_type="general",
        content="Revenue grew 30% YoY in Q3 2024",
        source_url="", source_agent="t",
    )
    db.add_all([obs1, obs2])
    db.commit()
    db.refresh(obs1)
    db.refresh(obs2)
    yield db, e.id, obs1.id, obs2.id
    db.close()


def test_is_duplicate_finds_verbatim_match(db_with_obs):
    db, eid, obs1_id, _ = db_with_obs
    found_id, score = qg.is_duplicate_observation(
        db, eid, "Acme acquired Beta for two billion in 2024"
    )
    assert found_id == obs1_id
    assert score >= 0.85


def test_is_duplicate_finds_case_punctuation_variant(db_with_obs):
    """Case + punctuation differences normalize away; jaccard = 1.0."""
    db, eid, obs1_id, _ = db_with_obs
    # Existing: "Acme acquired Beta for two billion in 2024"
    found_id, score = qg.is_duplicate_observation(
        db, eid, "ACME ACQUIRED, BETA-for-TWO! Billion in 2024."
    )
    assert found_id == obs1_id
    assert score == 1.0  # normalize() collapses punctuation + case


def test_is_duplicate_reorder_below_default_threshold(db_with_obs):
    """Pure reordering at start-end ('in 2024 X' vs 'X in 2024') doesn't
    meet the 0.85 default — by design. Word-3-grams favor flow over bag-of-
    words. Real-world dupes are mostly verbatim from the same LLM emission;
    aggressive reorder dedupe risks false positives. Caller can lower the
    threshold if they want bag-of-words behavior."""
    db, eid, obs1_id, _ = db_with_obs
    found_default, _ = qg.is_duplicate_observation(
        db, eid, "in 2024 Acme acquired Beta for two billion"
    )
    assert found_default is None  # default threshold rejects the reorder
    # But at threshold=0.5 (looser bag-overlap), it does match
    found_loose, score_loose = qg.is_duplicate_observation(
        db, eid, "in 2024 Acme acquired Beta for two billion", threshold=0.5
    )
    assert found_loose == obs1_id
    assert score_loose >= 0.5


def test_is_duplicate_returns_none_on_distinct(db_with_obs):
    db, eid, _, _ = db_with_obs
    found_id, score = qg.is_duplicate_observation(
        db, eid, "Microsoft cut 5000 jobs in restructuring announced today"
    )
    assert found_id is None
    assert score < 0.5


def test_is_duplicate_respects_threshold(db_with_obs):
    """Below threshold → no match even if some words overlap."""
    db, eid, _, _ = db_with_obs
    found_id, _ = qg.is_duplicate_observation(
        db, eid, "Acme launched a new product line", threshold=0.85
    )
    assert found_id is None


# ---------------------------------------------------------------------------
# validate_observation — hard reject conditions
# ---------------------------------------------------------------------------


def test_validate_rejects_empty():
    ok, _ = qg.validate_observation("", "")
    assert ok is False


def test_validate_rejects_too_short():
    ok, _ = qg.validate_observation("growth", "")
    assert ok is False


def test_validate_rejects_placeholder():
    ok, _ = qg.validate_observation("TODO: write up the Q3 results when published", "")
    assert ok is False


def test_validate_rejects_fluff():
    ok, _ = qg.validate_observation(
        "Strategic synergies and leveraging market opportunities to drive growth", ""
    )
    assert ok is False


def test_validate_accepts_substantive():
    ok, _ = qg.validate_observation(
        "Acme acquired Beta for $2.1B in March 2024, paying 1.4x revenue.",
        "https://example.com/news/acme-beta",
    )
    assert ok is True


# ---------------------------------------------------------------------------
# score_observation — composite quality score
# ---------------------------------------------------------------------------


def test_score_high_for_specific_sourced_observation():
    s = qg.score_observation(
        content="Acme reported $4.2B revenue in Q3 2024, up 32% YoY, with operating margin of 18%.",
        source_url="https://www.sec.gov/some-filing",
        lens_tags=["growth", "business_model"],
    )
    assert s >= 0.8


def test_score_low_for_short_unsourced():
    s = qg.score_observation(
        content="Acme is doing well right now overall.",  # >30 chars but no specifics
        source_url=None,
        lens_tags=None,
    )
    assert s <= 0.4


def test_score_zero_for_empty():
    assert qg.score_observation("", None, None) == 0.0


def test_score_in_unit_range():
    """Whatever input — score is always in [0, 1]."""
    cases = [
        ("", None, None),
        ("a", None, None),
        ("the quick brown fox", "", None),
        ("Acme grew revenue 30% in Q3 2024 across 14 markets", "https://example.com", ["growth"]),
        ("X" * 10_000, "https://example.com", ["growth"]),
    ]
    for content, url, lenses in cases:
        s = qg.score_observation(content, url, lenses)
        assert 0.0 <= s <= 1.0


# ---------------------------------------------------------------------------
# Integration: KnowledgeStore.add_observation actually routes through the guard
# ---------------------------------------------------------------------------


@pytest.fixture
def knowledge_store_fresh(tmp_path):
    """Fresh KnowledgeStore + project + entity for end-to-end dedupe tests."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from webapp.api.db import Base
    from webapp.api.models import KnowledgeEntity, Project
    from agent.knowledge_store import KnowledgeStore

    engine = create_engine(f"sqlite:///{tmp_path}/ks.db", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    p = Project(name="x", description="x")
    db.add(p)
    db.commit()
    db.refresh(p)
    e = KnowledgeEntity(
        project_id=p.id, entity_type="company", name="Airbnb",
        canonical_name="airbnb", source_agent="t", confidence=1.0,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    store = KnowledgeStore(db, "test_agent", p.id)
    yield store, db, e.id
    db.close()


def test_add_observation_dedupes_verbatim_repeat(knowledge_store_fresh):
    """The user's actual pain: Airbnb tier-3 finding reported 12 times.
    Second insert of the same content must merge, not duplicate."""
    from webapp.api.models import KnowledgeObservation
    store, db, eid = knowledge_store_fresh
    content = "Airbnb expanded into experiences, growing host signups 40% in tier-3 markets in Q2 2024"

    id1 = store.add_observation(eid, "feature_change", content, source_url="https://example.com/a")
    assert id1 is not None

    id2 = store.add_observation(eid, "feature_change", content, source_url="https://example.com/a")
    assert id2 == id1, "second emission must merge into existing row, not insert"

    # Verify only ONE observation row, with dedupe_count = 1 (one merge applied)
    rows = db.query(KnowledgeObservation).filter(KnowledgeObservation.entity_id == eid).all()
    assert len(rows) == 1
    assert rows[0].dedupe_count == 1


def test_add_observation_rejects_fluff(knowledge_store_fresh):
    """No-compromise: marketing fluff without specific datapoint = rejected."""
    from webapp.api.models import KnowledgeObservation
    store, db, eid = knowledge_store_fresh
    result = store.add_observation(
        eid, "general",
        "Airbnb is leveraging market opportunities to drive innovation forward",
    )
    assert result is None, "fluff must be rejected"
    rows = db.query(KnowledgeObservation).filter(KnowledgeObservation.entity_id == eid).all()
    assert len(rows) == 0


def test_add_observation_attaches_quality_score(knowledge_store_fresh):
    """Every accepted observation gets quality_score populated."""
    from webapp.api.models import KnowledgeObservation
    store, db, eid = knowledge_store_fresh
    obs_id = store.add_observation(
        eid, "metric",
        "Airbnb reported $11.1B revenue in 2024, up 11% YoY, with operating margin of 23.5%",
        source_url="https://www.sec.gov/Archives/edgar/data/abnb",
        lens_tags=["growth", "business_model"],
    )
    assert obs_id is not None
    row = db.query(KnowledgeObservation).filter(KnowledgeObservation.id == obs_id).first()
    assert row.quality_score >= 0.7, f"high-quality observation should score ≥0.7, got {row.quality_score}"


def test_add_observation_upgrades_source_url_on_merge(knowledge_store_fresh):
    """When a merge happens AND new emission has a source_url but existing
    didn't, copy it over — best of both."""
    from webapp.api.models import KnowledgeObservation
    store, db, eid = knowledge_store_fresh
    content = "Airbnb's host signups in tier-3 markets grew 40% in Q2 2024"

    id1 = store.add_observation(eid, "metric", content, source_url=None)
    assert id1 is not None

    id2 = store.add_observation(eid, "metric", content, source_url="https://reuters.com/airbnb-q2-2024")
    assert id2 == id1

    row = db.query(KnowledgeObservation).filter(KnowledgeObservation.id == id1).first()
    assert row.source_url == "https://reuters.com/airbnb-q2-2024"
    assert row.dedupe_count == 1


def test_add_observation_upgrades_quality_score_on_merge(knowledge_store_fresh):
    """v0.22.0 must-fix #3 (review): merge path must take max(existing, new)
    quality score so a high-quality emission upgrades a previously low-quality
    row instead of leaving it stuck below the 0.3 default-hide threshold."""
    from webapp.api.models import KnowledgeObservation
    store, db, eid = knowledge_store_fresh
    # First emission: minimal — only the bare claim, no URL, no lens tags
    content = "Airbnb expanded host signups across India"
    id1 = store.add_observation(eid, "general", content, source_url=None)
    assert id1 is not None
    row = db.query(KnowledgeObservation).filter(KnowledgeObservation.id == id1).first()
    initial_score = row.quality_score   # capture as primitive before mutation
    initial_url = row.source_url

    # Second emission: same content + source URL + lens_tags → must merge
    # AND upgrade the quality_score on the existing row.
    id2 = store.add_observation(
        eid, "general", content,
        source_url="https://www.sec.gov/Archives/edgar/data/abnb",
        lens_tags=["growth", "business_model"],
    )
    assert id2 == id1, "merge into same row"
    db.refresh(row)
    assert row.quality_score > initial_score, (
        f"merged emission must upgrade quality_score (was {initial_score}, now {row.quality_score})"
    )
    # Source URL should also be upgraded (existing was empty).
    assert row.source_url == "https://www.sec.gov/Archives/edgar/data/abnb"
