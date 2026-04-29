"""Unit tests for agent/sec_edgar.py (v0.21.1).

EDGAR is a live external API. We mock httpx so tests run offline.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent import sec_edgar as edgar


def _mock_response(status: int, payload):
    r = MagicMock()
    r.status_code = status
    r.text = payload if isinstance(payload, str) else ""
    r.json.return_value = payload if isinstance(payload, dict) else {}
    r.raise_for_status = MagicMock(return_value=None) if status < 400 else MagicMock(side_effect=Exception("http error"))
    return r


@pytest.fixture(autouse=True)
def clear_cik_cache():
    edgar._CIK_CACHE.clear()
    yield
    edgar._CIK_CACHE.clear()


def test_lookup_cik_exact_substring_match():
    tickers = {
        "0": {"cik_str": 1018724, "ticker": "AMZN", "title": "AMAZON COM INC"},
        "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    }
    with patch("agent.sec_edgar.httpx.get", return_value=_mock_response(200, tickers)):
        out = edgar.lookup_cik("Amazon")
    assert out is not None
    assert out[0] == "0001018724"


def test_lookup_cik_returns_none_on_no_match():
    tickers = {"0": {"cik_str": 1, "ticker": "X", "title": "Some random co"}}
    with patch("agent.sec_edgar.httpx.get", return_value=_mock_response(200, tickers)):
        out = edgar.lookup_cik("ZzzNonExistentCorp")
    assert out is None


def test_lookup_cik_caches_misses():
    """Second lookup for an unknown name should not re-hit the API."""
    tickers = {"0": {"cik_str": 1, "ticker": "X", "title": "Foo"}}
    call_count = {"n": 0}

    def stub(*a, **kw):
        call_count["n"] += 1
        return _mock_response(200, tickers)

    with patch("agent.sec_edgar.httpx.get", side_effect=stub):
        edgar.lookup_cik("Unknown")
        edgar.lookup_cik("Unknown")
    assert call_count["n"] == 1


def test_fetch_latest_annual_report_no_cik_returns_none():
    tickers = {"0": {"cik_str": 1, "ticker": "X", "title": "Foo"}}
    with patch("agent.sec_edgar.httpx.get", return_value=_mock_response(200, tickers)):
        result = edgar.fetch_latest_annual_report("DefinitelyNotListed Corp")
    assert result is None


def test_fetch_latest_annual_report_no_10k_in_recent():
    """CIK found, but no 10-K/20-F in recent filings → None."""
    tickers = {"0": {"cik_str": 12345, "ticker": "ACME", "title": "Acme Corp"}}
    submissions = {
        "filings": {
            "recent": {
                "form": ["8-K", "10-Q"],
                "accessionNumber": ["acc-1", "acc-2"],
                "primaryDocument": ["doc1", "doc2"],
                "filingDate": ["2025-01-01", "2025-02-01"],
            }
        }
    }

    responses = [_mock_response(200, tickers), _mock_response(200, submissions)]
    call_idx = {"i": 0}

    def stub(*a, **kw):
        r = responses[call_idx["i"]]
        call_idx["i"] += 1
        return r

    with patch("agent.sec_edgar.httpx.get", side_effect=stub):
        result = edgar.fetch_latest_annual_report("Acme")
    assert result is None


def test_normalize_strips_punctuation():
    assert edgar._normalize("Amazon.com, Inc.") == "amazon com  inc"
