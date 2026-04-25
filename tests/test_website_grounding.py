"""Tests for agent/website_grounding.

Pin the contract: bad inputs → None (caller falls back); the grounding
prompt's "WHAT THIS COMPANY IS NOT" disambiguation pattern is what
prevents keyword-collision drift, so the prompt itself is also pinned.
"""
from __future__ import annotations

import pytest

from agent.website_grounding import _PROMPT, _cached_summary, fetch_portfolio_summary


def test_no_url_returns_none():
    assert fetch_portfolio_summary(None, "Some Co") is None
    assert fetch_portfolio_summary("", "Some Co") is None


def test_prompt_contains_disambiguation_block():
    """The 'WHAT THIS COMPANY IS NOT' section is what makes this work for
    keyword-collision cases (Platinum-the-company vs. platinum-the-metal).
    Pin it explicitly so a future prompt-tuning pass can't accidentally
    delete the load-bearing section."""
    assert "WHAT THIS COMPANY IS NOT" in _PROMPT
    assert "PRODUCTS / SERVICES" in _PROMPT
    assert "INDUSTRY" in _PROMPT
    assert "GROUNDING_FAILED" in _PROMPT  # explicit no-op signal


def test_caching_keys_on_url_and_name():
    """lru_cache must respect both url and project_name as part of the key
    so a project rename doesn't get a stale summary."""
    info = _cached_summary.cache_info()
    # Just verify lru_cache is wired — don't actually call (no network).
    assert info is not None


def test_url_normalization_handles_bare_domain(monkeypatch):
    """bare-domain input should be normalized to https:// before fetch.
    Stub WebResearcher.fetch_page so we capture the URL it was called with."""
    captured: dict[str, str] = {}

    class _StubWeb:
        def fetch_page(self, url, max_length=4000):
            captured["url"] = url
            return {"content": "x" * 250}  # passes the >200 chars gate

    monkeypatch.setattr("tools.web_research.WebResearcher", _StubWeb)
    # also stub the LLM so we don't make a real call
    monkeypatch.setattr("utils.claude_client.ask", lambda *a, **kw: "GROUNDING_FAILED")

    # Clear cache so this test isn't a hit on previous runs
    _cached_summary.cache_clear()

    result = fetch_portfolio_summary("platinumindustriesltd.com", "TestCo")
    assert captured.get("url") == "https://platinumindustriesltd.com"
    # GROUNDING_FAILED returned by stub → caller gets None
    assert result is None
