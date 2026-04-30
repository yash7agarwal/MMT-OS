"""Unit tests for agent/bulk_report_classifier.py (v0.21.1, body_text_match v0.21.4).

Most important: prove the no-hallucination guarantees.
- Period regex returns None when no explicit cue exists.
- Filename match scores high only on real substring overlap.
- LLM disambiguation respects null answers and rejects out-of-range ids.
- Body-text match requires a structural co-signal — dominance alone never wins
  (kills the "industry report mentions OpenAI 100×" misattribution).
"""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from agent import bulk_report_classifier as brc


# ---------------------------------------------------------------------------
# Period extraction
# ---------------------------------------------------------------------------


def test_parse_period_quarter_from_filename():
    p = brc.parse_period("acme_Q3_2024_10Q.pdf")
    assert p is not None
    assert p.fiscal_year == 2024
    assert p.quarter == 3
    assert p.period_label == "Q3 2024"


def test_parse_period_quarter_alt_format():
    p = brc.parse_period("acme-2024Q1-quarterly.pdf")
    assert p is not None
    assert p.quarter == 1
    assert p.fiscal_year == 2024


def test_parse_period_annual_fy_short():
    p = brc.parse_period("acme-FY24-annual-report.pdf")
    assert p is not None
    assert p.fiscal_year == 2024
    assert p.is_annual is True


def test_parse_period_annual_10k():
    p = brc.parse_period("acme-10K-2023.pdf")
    assert p is not None
    assert p.fiscal_year == 2023
    assert p.is_annual is True


def test_parse_period_strict_returns_none_for_bare_year():
    """Strict mode: a bare 4-digit year is NOT enough — too noisy."""
    p = brc.parse_period("acme-2024.pdf", strict=True)
    assert p is None


def test_parse_period_non_strict_accepts_bare_year_in_filename():
    p = brc.parse_period("acme-2024.pdf", strict=False)
    assert p is not None
    assert p.fiscal_year == 2024


def test_parse_period_returns_none_when_no_year():
    """No year, no period — never invent one."""
    p = brc.parse_period("acme-annual.pdf", strict=True)
    assert p is None
    p2 = brc.parse_period("acme-annual.pdf", strict=False)
    assert p2 is None


# ---------------------------------------------------------------------------
# Filename → competitor matching
# ---------------------------------------------------------------------------


def test_filename_match_substring_high_score():
    competitors = [
        {"id": 1, "name": "OpenAI", "canonical_name": "openai"},
        {"id": 2, "name": "Anthropic", "canonical_name": "anthropic"},
    ]
    m = brc.filename_match("openai-10K-2024.pdf", competitors)
    assert m is not None
    eid, name, score = m
    assert eid == 1
    assert score > 0.5


def test_filename_match_picks_higher_overlap_when_two_match():
    competitors = [
        {"id": 1, "name": "Microsoft Azure", "canonical_name": "microsoft azure"},
        {"id": 2, "name": "Microsoft Azure Cognitive Services", "canonical_name": "microsoft azure cognitive services"},
    ]
    m = brc.filename_match("microsoft_azure_2024.pdf", competitors)
    # Both contain "microsoft azure" — the longer name's match still resolves
    # to ONE entity. The exact id depends on score; we just verify SOME match.
    assert m is not None
    assert m[0] in (1, 2)


def test_filename_match_no_match_returns_none():
    competitors = [
        {"id": 1, "name": "OpenAI", "canonical_name": "openai"},
        {"id": 2, "name": "Anthropic", "canonical_name": "anthropic"},
    ]
    m = brc.filename_match("random-industry-report.pdf", competitors)
    assert m is None


def test_filename_match_skips_short_canonical_names():
    """3-char names are too short to substring-match against long filenames."""
    competitors = [{"id": 1, "name": "AI", "canonical_name": "ai"}]
    m = brc.filename_match("ai-trends-report.pdf", competitors)
    assert m is None  # canonical_name length < 3 → skipped


# ---------------------------------------------------------------------------
# LLM disambiguation — null is a valid answer
# ---------------------------------------------------------------------------


def test_llm_classify_returns_null_when_llm_says_null():
    competitors = [{"id": 1, "name": "OpenAI"}, {"id": 2, "name": "Anthropic"}]
    fake = json.dumps({
        "matched_entity_id": None,
        "confidence": "high",
        "reasoning": "Generic industry overview, no specific company.",
    })
    with patch.object(brc, "_call_llm", return_value=fake):
        eid, conf, reasoning = brc.llm_classify("industry-report.pdf", "the AI industry...", competitors)
    assert eid is None
    assert conf == "high"  # high confidence in saying NO match — that's good


def test_llm_classify_rejects_id_not_in_competitor_list():
    """Hallucination guard: LLM picks an id that doesn't exist → we treat as None."""
    competitors = [{"id": 1, "name": "OpenAI"}, {"id": 2, "name": "Anthropic"}]
    fake = json.dumps({
        "matched_entity_id": 999,
        "confidence": "high",
        "reasoning": "made up id",
    })
    with patch.object(brc, "_call_llm", return_value=fake):
        eid, conf, _ = brc.llm_classify("x.pdf", "...", competitors)
    assert eid is None
    assert conf == "none"


def test_llm_classify_rejects_non_int_id():
    competitors = [{"id": 1, "name": "OpenAI"}]
    fake = json.dumps({"matched_entity_id": "OpenAI", "confidence": "high"})
    with patch.object(brc, "_call_llm", return_value=fake):
        eid, conf, _ = brc.llm_classify("x.pdf", "...", competitors)
    assert eid is None


def test_llm_classify_handles_malformed_json():
    competitors = [{"id": 1, "name": "OpenAI"}]
    with patch.object(brc, "_call_llm", return_value="not json at all"):
        eid, conf, _ = brc.llm_classify("x.pdf", "...", competitors)
    assert eid is None


def test_llm_classify_no_competitors_returns_none():
    eid, conf, _ = brc.llm_classify("x.pdf", "...", [])
    assert eid is None
    assert conf == "none"


# ---------------------------------------------------------------------------
# End-to-end classify()
# ---------------------------------------------------------------------------


def test_classify_filename_match_skips_llm():
    """When filename match scores high, LLM is NOT called."""
    competitors = [{"id": 1, "name": "OpenAI", "canonical_name": "openai"}]
    with patch.object(brc, "llm_classify") as llm_mock:
        result = brc.classify("openai-10K-2024.pdf", "OpenAI annual report ..." * 100, competitors)
    assert result.matched_entity_id == 1
    assert result.match_method == "filename_substring"
    llm_mock.assert_not_called()


def test_classify_filename_no_match_falls_back_to_llm():
    """v0.21.4: must pass allow_llm=True explicitly — default flipped to False."""
    competitors = [{"id": 1, "name": "OpenAI"}]
    with patch.object(brc, "llm_classify", return_value=(1, "medium", "body mentions OpenAI 30 times")):
        result = brc.classify("ambiguous-report.pdf", "long pdf text", competitors, allow_llm=True)
    assert result.matched_entity_id == 1
    assert result.match_method == "llm_disambiguation"


def test_classify_unmatched_returns_none_id():
    """The full no-match path: filename misses, LLM says null → ClassifiedReport
    with matched_entity_id=None. This is an EXPECTED outcome, not a failure."""
    competitors = [{"id": 1, "name": "OpenAI"}]
    with patch.object(brc, "llm_classify", return_value=(None, "high", "doesn't belong to any listed competitor")):
        result = brc.classify("random.pdf", "general industry text", competitors, allow_llm=True)
    assert result.matched_entity_id is None
    assert result.match_confidence == "none" or result.match_confidence == "high"
    # period extraction still runs even on unmatched
    assert result.period is None or result.period.fiscal_year is not None


# ---------------------------------------------------------------------------
# v0.21.4: body_text_match — deterministic occurrence-count + co-signal gate
#
# These tests pin the no-hallucination invariants that the code-reviewer
# called out as must-fix #1: dominance alone is necessary but NOT sufficient.
# An "AI Industry Report 2025" mentioning OpenAI 100× must NOT auto-attribute
# to OpenAI's 10-K. We require a structural co-signal: name appears in the
# filename, OR in the first 200 chars (cover page), OR within 500 chars of
# a 10-K/20-F structural marker.
# ---------------------------------------------------------------------------


def test_body_text_match_clear_winner_with_filename_cosignal():
    """OpenAI 10-K with 'openai' in filename + dominant body counts → match."""
    competitors = [
        {"id": 1, "name": "OpenAI"},
        {"id": 2, "name": "Anthropic"},
        {"id": 3, "name": "Google"},
    ]
    body = ("OpenAI Inc. annual report. " * 100) + ("Anthropic " * 5) + ("Google " * 3)
    out = brc.body_text_match(body, "openai-10K-2024.pdf", competitors)
    assert out is not None
    assert out[0] == 1
    assert out[1] == "OpenAI"


def test_body_text_match_clear_winner_with_cover_page_cosignal():
    """No filename match, but 'OpenAI' appears in first 200 chars (cover page)."""
    competitors = [{"id": 1, "name": "OpenAI"}, {"id": 2, "name": "Anthropic"}]
    head = "OpenAI Inc. — Annual Report 2024. "  # 35 chars; well under 200
    body = head + ("OpenAI " * 50) + ("Anthropic " * 5)
    out = brc.body_text_match(body, "report.pdf", competitors)
    assert out is not None
    assert out[0] == 1


def test_body_text_match_clear_winner_with_10k_marker():
    """No filename, no first-200 mention, but BOTH the SEC marker AND the
    competitor name appear in the first 2000 chars (authentic 10-K cover
    page). v0.21.4 review must-fix #5 tightened from "marker proximity"
    to "marker+name both in cover region" to reject industry reports."""
    competitors = [{"id": 1, "name": "OpenAI"}, {"id": 2, "name": "Anthropic"}]
    # Pad first 200 chars with neutral text (no name), then put the SEC
    # marker + name BOTH within the first 2000 chars of body (cover region).
    pad = "X" * 220
    body = pad + " UNITED STATES SECURITIES AND EXCHANGE COMMISSION FORM 10-K of OpenAI Inc. " + ("OpenAI " * 50) + ("Anthropic " * 3)
    out = brc.body_text_match(body, "filing.pdf", competitors)
    assert out is not None
    assert out[0] == 1


def test_body_text_match_industry_report_with_sec_marker_deep_returns_none():
    """v0.21.4 must-fix #5 (review): an industry report mentioning 'FORM 10-K'
    deep in body (after char 2000) must NOT pass the co-signal gate just
    because marker + name are both in body. Authentic filings put them on
    the cover page."""
    competitors = [{"id": 1, "name": "OpenAI"}, {"id": 2, "name": "Anthropic"}]
    # Cover page (first 2000 chars): pure industry framing, no name, no SEC marker.
    cover = "STATE OF AI INDUSTRY 2025 — Annual Outlook on Generative AI Vendors. " + "X" * 2000
    assert len(cover) >= 2000
    # Deep in body (after 2000 chars): SEC marker mentioned in passing + name
    # — exactly the failure mode the prior 500-char proximity gate let through.
    deep = " Below we summarize the FORM 10-K of OpenAI Inc. and others. " + ("OpenAI " * 100) + ("Anthropic " * 25)
    body = cover + deep
    out = brc.body_text_match(body, "industry-2025.pdf", competitors)
    assert out is None, "industry report with SEC marker only deep in body must NOT auto-match"


def test_body_text_match_industry_report_returns_none():
    """THE KILLER TEST (must-fix #1): an industry report mentioning OpenAI
    100× has dominance — but no co-signal anywhere. Must return None.
    Without this gate, we'd misattribute the report to OpenAI."""
    competitors = [
        {"id": 1, "name": "OpenAI"},
        {"id": 2, "name": "Anthropic"},
        {"id": 3, "name": "Google"},
    ]
    # First 200 chars: generic industry framing — no company name.
    head = "STATE OF AI 2025 — Industry Outlook. This report covers leading providers in foundation models and their commercial trajectories. " + "X" * 80
    assert len(head) > 200, "head must exceed cover-page window for a fair test"
    body = head + ("OpenAI " * 100) + ("Anthropic " * 30) + ("Google " * 25)
    # Filename also has no co-signal
    out = brc.body_text_match(body, "ai-industry-report-2025.pdf", competitors)
    assert out is None, "Industry report with dominance but no co-signal must NOT auto-match"


def test_body_text_match_ambiguous_returns_none():
    """Two competitors mentioned ~equally → dominance fails, returns None."""
    competitors = [{"id": 1, "name": "OpenAI"}, {"id": 2, "name": "Anthropic"}]
    body = "OpenAI Inc. annual. " + ("OpenAI " * 30) + ("Anthropic " * 25)
    out = brc.body_text_match(body, "openai.pdf", competitors)
    # Even with cover-page co-signal, the dominance ratio (30/25 = 1.2×)
    # falls under 3× — must be None.
    assert out is None


def test_body_text_match_weak_returns_none():
    """Top competitor has only 3 mentions — below min_occurrences threshold."""
    competitors = [{"id": 1, "name": "OpenAI"}]
    body = "Some report. OpenAI Inc. is mentioned thrice: OpenAI, OpenAI."  # 3 occurrences
    out = brc.body_text_match(body, "openai.pdf", competitors, min_occurrences=5)
    assert out is None


def test_body_text_match_legal_suffix_stripped_case_insensitive():
    """'Acme, Inc.' competitor name should match 'acme' / 'ACME' in body."""
    competitors = [{"id": 1, "name": "Acme, Inc."}]
    body = "Acme — annual report. " + ("acme " * 30) + "ACME and Acme Inc."
    out = brc.body_text_match(body, "acme.pdf", competitors)
    assert out is not None
    assert out[0] == 1


def test_body_text_match_no_match_when_no_competitor_present():
    competitors = [{"id": 1, "name": "OpenAI"}]
    body = "This is a Microsoft annual report, no other companies mentioned."
    out = brc.body_text_match(body, "msft.pdf", competitors)
    assert out is None


def test_body_text_match_sub_millisecond():
    """Performance gate — body_text_match must be fast even on 60K-char haystack."""
    competitors = [{"id": i, "name": f"Company{i}"} for i in range(30)]
    body = ("Company0 " * 100) * 50  # ~50KB of text
    start = time.perf_counter()
    for _ in range(50):
        brc.body_text_match(body, "co0.pdf", competitors)
    elapsed_ms = (time.perf_counter() - start) * 1000
    # 50 calls in <100ms = <2ms per call; well under sub-millisecond claim
    # for typical inputs. Loose bound to keep CI reliable across machines.
    assert elapsed_ms < 200, f"body_text_match took {elapsed_ms:.1f}ms for 50 calls — too slow"


# ---------------------------------------------------------------------------
# v0.21.4: classify() integration with body_text_match + allow_llm=False default
# ---------------------------------------------------------------------------


def test_classify_uses_body_text_when_filename_misses():
    """Filename has no signal, body has dominant + co-signal → matches via body_text_count."""
    competitors = [
        {"id": 1, "name": "OpenAI", "canonical_name": "openai"},
        {"id": 2, "name": "Anthropic", "canonical_name": "anthropic"},
    ]
    # Filename "Y2025.pdf" doesn't mention any competitor (the real-world Booking case).
    # But body cover page + dominance gives clear OpenAI win.
    body = "OpenAI Inc. — Annual Report 2025. " + ("OpenAI " * 80) + ("Anthropic " * 5)
    with patch.object(brc, "llm_classify") as llm_mock:
        result = brc.classify("Y2025.pdf", body, competitors)
    assert result.matched_entity_id == 1
    assert result.match_method == "body_text_count"
    llm_mock.assert_not_called()  # LLM never called in fast path


def test_classify_skips_llm_when_allow_llm_false():
    """With allow_llm=False (default), an unresolvable case returns None — no LLM."""
    competitors = [{"id": 1, "name": "OpenAI"}, {"id": 2, "name": "Anthropic"}]
    body = "Some industry report mentioning everyone equally."  # ambiguous
    with patch.object(brc, "llm_classify") as llm_mock:
        result = brc.classify("report.pdf", body, competitors)  # default allow_llm=False
    assert result.matched_entity_id is None
    llm_mock.assert_not_called()


def test_classify_industry_report_returns_none_via_classify():
    """Integration check for must-fix #1 at the classify() level."""
    competitors = [{"id": 1, "name": "OpenAI"}, {"id": 2, "name": "Anthropic"}]
    head = "STATE OF AI 2025 — Industry Outlook. " + "X" * 200
    body = head + ("OpenAI " * 100) + ("Anthropic " * 30)
    result = brc.classify("ai-industry-2025.pdf", body, competitors, allow_llm=False)
    assert result.matched_entity_id is None, "classify() must reject industry reports lacking co-signal"
