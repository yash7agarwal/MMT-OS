"""Tests for agent/llm_search (v0.19.0).

The function is integration-heavy (LLM call + HTTP verifications) but a
few critical contracts are unit-testable without hitting the network:
- _parse_response handles strict JSON, fenced JSON, and JSON-with-prose
- _verify_url returns True/False without raising on bad input
- empty / malformed LLM responses degrade to empty CompetitorDiscovery
- placeholder names emitted by a misbehaving LLM are still caught
  upstream by extraction_guard (covered in test_extraction_guard)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.llm_search import (
    CompetitorDiscovery,
    CompetitorRef,
    _parse_response,
    _verify_url,
    llm_competitor_discovery,
)


# ---- _parse_response ----

def test_parse_response_strict_json():
    raw = '{"direct_local": [{"name": "Acme", "differentiator": "x", "url": ""}]}'
    out = _parse_response(raw)
    assert out["direct_local"][0]["name"] == "Acme"


def test_parse_response_with_markdown_fence():
    raw = '```json\n{"direct_global": [{"name": "Beta", "differentiator": "g", "url": "https://b.example"}]}\n```'
    out = _parse_response(raw)
    assert out["direct_global"][0]["name"] == "Beta"


def test_parse_response_with_prose_around_json():
    raw = "Here are the competitors:\n\n{\"indirect\": [{\"name\": \"Gamma\", \"differentiator\": \"d\", \"url\": \"\"}]}\n\nLet me know."
    out = _parse_response(raw)
    assert out["indirect"][0]["name"] == "Gamma"


def test_parse_response_garbage_returns_empty():
    assert _parse_response("not JSON at all") == {}
    assert _parse_response("") == {}


# ---- _verify_url ----

def test_verify_url_rejects_empty_or_non_http():
    assert _verify_url("") is False
    assert _verify_url("ftp://x.example") is False
    assert _verify_url("not a url") is False


# ---- end-to-end with mocked LLM ----

def test_llm_discovery_with_mocked_groq():
    """Full happy path: Groq returns structured JSON; verifier fakes URL checks."""
    fake_groq_response = """{
      "direct_local": [
        {"name": "LocalCo", "differentiator": "Indian rival", "url": "https://localco.example"}
      ],
      "direct_global": [
        {"name": "GlobalCorp", "differentiator": "category leader", "url": "https://global.example"}
      ],
      "indirect": [
        {"name": "AltCo", "differentiator": "alternative way", "url": ""}
      ]
    }"""
    with patch("utils.groq_client.is_available", return_value=True), \
         patch("utils.groq_client.synthesize", return_value=fake_groq_response), \
         patch("agent.llm_search._verify_url", return_value=True):
        d = llm_competitor_discovery(
            project_name="TestCo",
            project_description="A test company",
        )
    assert len(d.direct_local) == 1
    assert d.direct_local[0].name == "LocalCo"
    assert d.direct_local[0].category == "direct_local"
    assert d.direct_local[0].url_status == "verified"

    assert len(d.direct_global) == 1
    assert d.direct_global[0].name == "GlobalCorp"

    assert len(d.indirect) == 1
    assert d.indirect[0].name == "AltCo"
    # No URL → status='none', not 'unverified'
    assert d.indirect[0].url is None
    assert d.indirect[0].url_status == "none"


def test_llm_discovery_with_failing_url_verification():
    """When verifier returns False, status='unverified' (not dropped)."""
    fake_response = '{"direct_local":[{"name":"X","differentiator":"y","url":"https://nope.example"}],"direct_global":[],"indirect":[]}'
    with patch("utils.groq_client.is_available", return_value=True), \
         patch("utils.groq_client.synthesize", return_value=fake_response), \
         patch("agent.llm_search._verify_url", return_value=False):
        d = llm_competitor_discovery("TestCo", "desc")
    # Entity is still kept — url_status flags the citation as training-data only
    assert len(d.direct_local) == 1
    assert d.direct_local[0].url_status == "unverified"


def test_llm_discovery_empty_response_degrades_gracefully():
    with patch("utils.groq_client.is_available", return_value=True), \
         patch("utils.groq_client.synthesize", return_value=""), \
         patch("utils.claude_client.ask", return_value=""):
        d = llm_competitor_discovery("TestCo", "desc")
    assert d.direct_local == []
    assert d.direct_global == []
    assert d.indirect == []


def test_llm_discovery_skips_unnamed_entries():
    """Defensive: empty `name` in a list item is silently dropped, not raised."""
    fake = '{"direct_local":[{"name":"","differentiator":"","url":""},{"name":"RealCo","differentiator":"d","url":""}],"direct_global":[],"indirect":[]}'
    with patch("utils.groq_client.is_available", return_value=True), \
         patch("utils.groq_client.synthesize", return_value=fake), \
         patch("agent.llm_search._verify_url", return_value=True):
        d = llm_competitor_discovery("TestCo", "desc")
    # Only RealCo survives
    assert len(d.direct_local) == 1
    assert d.direct_local[0].name == "RealCo"


def test_llm_discovery_handles_non_list_categories():
    """If LLM returns a category as a non-list (drift), don't raise."""
    fake = '{"direct_local":"this should be a list","direct_global":[{"name":"OK","differentiator":"d","url":""}],"indirect":[]}'
    with patch("utils.groq_client.is_available", return_value=True), \
         patch("utils.groq_client.synthesize", return_value=fake), \
         patch("agent.llm_search._verify_url", return_value=True):
        d = llm_competitor_discovery("TestCo", "desc")
    assert d.direct_local == []
    assert len(d.direct_global) == 1
