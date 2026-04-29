"""Unit tests for agent/business_history.py (v0.21.0)."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from agent import business_history as bh


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def test_parse_strict():
    assert bh._parse_json('{"a": 1}') == {"a": 1}


def test_parse_fenced():
    assert bh._parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_with_preamble():
    assert bh._parse_json('here is the answer:\n{"a": 1}\nhope this helps') == {"a": 1}


# ---------------------------------------------------------------------------
# PDF extraction — generate a tiny synthetic PDF in memory
# ---------------------------------------------------------------------------


def _make_synthetic_pdf(text: str = "Hello world. " * 200) -> bytes:
    """Build a minimal one-page PDF using pypdf primitives. Used so the
    extractor tests don't need a fixture file."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject, DictionaryObject, FloatObject, IndirectObject, NameObject,
        NumberObject, TextStringObject,
    )
    # Easier path: use pypdf's built-in add_blank_page + write text via low-level
    # ContentStream is complex. Instead we just write text to a real PDF using
    # a Form XObject... too heavy. Use a much simpler approach: write text via
    # reportlab if available, else skip these tests.
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import LETTER
    except ImportError:
        pytest.skip("reportlab not installed; skipping PDF generation tests")

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    # Wrap to multiple pages of text
    y = 750
    for line in text.split(". "):
        c.drawString(72, y, line)
        y -= 12
        if y < 72:
            c.showPage()
            y = 750
    c.save()
    return buf.getvalue()


def test_extract_text_handles_blank_bytes():
    text, meta = bh.extract_text_from_pdf_bytes(b"")
    assert text == ""
    assert meta["extraction_error"]


def test_extract_text_invalid_pdf_returns_error():
    text, meta = bh.extract_text_from_pdf_bytes(b"not a pdf")
    assert text == ""
    assert "PDF parse failed" in (meta.get("extraction_error") or "")


# ---------------------------------------------------------------------------
# synthesize_business_profile — mocks the LLM so we test the orchestration
# ---------------------------------------------------------------------------


def test_synthesize_empty_sources_returns_empty_profile():
    profile = bh.synthesize_business_profile("Acme", "Beta", "test", sources=[])
    assert profile.competitor == "Acme"
    assert profile.market_thesis == ""


def test_synthesize_happy_path():
    fake_response = json.dumps({
        "market_thesis": "Acme dominates because of distribution moat.",
        "business_model": "Take-rate marketplace at 12% per transaction.",
        "margin_profile": "62% gross, 18% operating in FY2025.",
        "performance": "Revenue grew 34% YoY in Q3 2025.",
        "contrarian_insights": [
            "Their ARR includes 28% one-time impl fees that won't repeat.",
            "Buyer concentration: top 5 customers are 40% of revenue.",
        ],
        "nuances": ["Reports SaaS metrics but operating model is consulting-heavy."],
        "risks_and_red_flags": ["Going-concern note in 10-K reissued in Q4."],
    })
    with patch.object(bh, "_call_llm", return_value=fake_response):
        profile = bh.synthesize_business_profile(
            "Acme", "Beta", "Beta is a B2B SaaS",
            sources=[{"title": "Acme 10-K 2025", "text": "annual report text " * 1000, "year": "2025"}],
        )
    assert "Take-rate" in profile.business_model
    assert len(profile.contrarian_insights) == 2
    assert "10-K 2025" in profile.sources[0]


def test_synthesize_drops_empty_text_blocks():
    """Sources with empty text should not crash and not contribute."""
    with patch.object(bh, "_call_llm", return_value=""):
        profile = bh.synthesize_business_profile(
            "Acme", "Beta", "test",
            sources=[
                {"title": "empty doc", "text": "", "year": ""},
                {"title": "another empty", "text": "  ", "year": ""},
            ],
        )
    assert profile.competitor == "Acme"
    assert profile.sources == []


def test_synthesize_handles_malformed_llm_json():
    with patch.object(bh, "_call_llm", return_value="this is not json at all"):
        profile = bh.synthesize_business_profile(
            "Acme", "Beta", "test",
            sources=[{"title": "src", "text": "real text " * 500, "year": ""}],
        )
    # Should not crash; returns empty profile with sources captured.
    assert profile.competitor == "Acme"
    assert profile.market_thesis == ""
    assert profile.sources == ["src"]


def test_to_markdown_renders_all_sections_when_present():
    profile = bh.BusinessProfile(
        competitor="Acme",
        market_thesis="Thesis text",
        business_model="Model text",
        margin_profile="Margin text",
        performance="Perf text",
        contrarian_insights=["one", "two"],
        nuances=["nuance one"],
        risks_and_red_flags=["risk one"],
        sources=["doc"],
    )
    md = profile.to_markdown()
    assert "# Business History · Acme" in md
    assert "## Market thesis" in md
    assert "## Contrarian insights" in md
    assert "- one" in md


def test_to_markdown_skips_empty_sections():
    profile = bh.BusinessProfile(competitor="Acme", market_thesis="only this")
    md = profile.to_markdown()
    assert "## Market thesis" in md
    assert "## Business model" not in md  # empty, skipped
    assert "## Risks" not in md
