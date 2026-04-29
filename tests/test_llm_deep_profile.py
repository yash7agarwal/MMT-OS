"""Unit tests for agent/llm_deep_profile.py (v0.20.2).

Mocks the LLM call dispatcher so tests run offline and deterministically.
Covers: prompt-gen JSON parsing, fact extraction, low-confidence drop,
end-to-end happy path, empty-response degradation, malformed JSON.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agent import llm_deep_profile as ldp


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def test_parse_strict_json():
    assert ldp._parse_json_response('{"a": 1}') == {"a": 1}


def test_parse_fenced_json():
    text = '```json\n{"a": 1}\n```'
    assert ldp._parse_json_response(text) == {"a": 1}


def test_parse_prose_wrapped_json():
    text = "Here is your answer:\n{\"a\": 1}\nHope this helps."
    assert ldp._parse_json_response(text) == {"a": 1}


def test_parse_array_response():
    text = "Sure: [1, 2, 3]"
    assert ldp._parse_json_response(text) == [1, 2, 3]


# ---------------------------------------------------------------------------
# generate_probing_prompts
# ---------------------------------------------------------------------------


def test_generate_prompts_happy_path():
    fake = json.dumps({"questions": [
        {"category": "pricing", "question": "What was their last documented price increase?", "rationale": "directly relevant"},
        {"category": "feature", "question": "Which engineering hire signals a product bet?", "rationale": "ditto"},
    ]})
    with patch.object(ldp, "_call_llm", return_value=fake):
        prompts = ldp.generate_probing_prompts("Acme", "Beta", "test", n=10)
    assert len(prompts) == 2
    assert prompts[0].category == "pricing"
    assert "price" in prompts[0].question


def test_generate_prompts_skips_blank_questions():
    fake = json.dumps({"questions": [
        {"category": "pricing", "question": ""},
        {"category": "metric", "question": "concrete metric question?"},
    ]})
    with patch.object(ldp, "_call_llm", return_value=fake):
        prompts = ldp.generate_probing_prompts("Acme", "Beta", "test")
    assert len(prompts) == 1
    assert prompts[0].category == "metric"


def test_generate_prompts_empty_response_returns_empty_list():
    with patch.object(ldp, "_call_llm", return_value=""):
        assert ldp.generate_probing_prompts("Acme", "Beta", "test") == []


def test_generate_prompts_malformed_json_returns_empty_list():
    with patch.object(ldp, "_call_llm", return_value="not json at all"):
        assert ldp.generate_probing_prompts("Acme", "Beta", "test") == []


def test_generate_prompts_caps_at_n():
    questions = [
        {"category": "metric", "question": f"q{i}?"} for i in range(20)
    ]
    fake = json.dumps({"questions": questions})
    with patch.object(ldp, "_call_llm", return_value=fake):
        prompts = ldp.generate_probing_prompts("Acme", "Beta", "test", n=5)
    assert len(prompts) == 5


# ---------------------------------------------------------------------------
# extract_fact
# ---------------------------------------------------------------------------


def test_extract_fact_happy_path():
    fake = json.dumps({
        "answer": "Raised Series C of $200M in Q3 2025.",
        "confidence": "high",
        "date_qualifier": "Q3 2025",
        "source_hint": "https://techcrunch.com/...",
    })
    with patch.object(ldp, "_call_llm", return_value=fake):
        f = ldp.extract_fact(
            "Acme", "Beta", "test",
            ldp.ProbingPrompt(category="growth_signal", question="latest funding?"),
        )
    assert f is not None
    assert f.confidence == "high"
    assert "200M" in f.answer
    assert f.observation_type == "metric"
    assert "growth" in f.lens_tags


def test_extract_fact_invalid_confidence_defaults_to_medium():
    fake = json.dumps({"answer": "Some fact", "confidence": "weird-value"})
    with patch.object(ldp, "_call_llm", return_value=fake):
        f = ldp.extract_fact(
            "Acme", "Beta", "test",
            ldp.ProbingPrompt(category="pricing", question="?"),
        )
    assert f.confidence == "medium"


def test_extract_fact_empty_answer_returns_none():
    fake = json.dumps({"answer": "", "confidence": "high"})
    with patch.object(ldp, "_call_llm", return_value=fake):
        f = ldp.extract_fact(
            "Acme", "Beta", "test",
            ldp.ProbingPrompt(category="pricing", question="?"),
        )
    assert f is None


# ---------------------------------------------------------------------------
# deep_profile_competitor — end-to-end with parallel execution
# ---------------------------------------------------------------------------


def test_deep_profile_drops_low_confidence_by_default():
    questions_response = json.dumps({"questions": [
        {"category": "pricing", "question": "q1?"},
        {"category": "metric", "question": "q2?"},
        {"category": "feature", "question": "q3?"},
    ]})
    fact_responses = [
        json.dumps({"answer": "high-conf fact", "confidence": "high", "date_qualifier": "", "source_hint": ""}),
        json.dumps({"answer": "low fact", "confidence": "low", "date_qualifier": "", "source_hint": ""}),
        json.dumps({"answer": "med fact", "confidence": "medium", "date_qualifier": "", "source_hint": ""}),
    ]
    call_count = [0]

    def fake_call(_prompt: str) -> str:
        i = call_count[0]
        call_count[0] += 1
        if i == 0:
            return questions_response
        return fact_responses[i - 1]

    with patch.object(ldp, "_call_llm", side_effect=fake_call):
        profile = ldp.deep_profile_competitor("Acme", "Beta", "test", n_questions=3, parallel=False)

    assert len(profile.facts) == 2  # low dropped
    assert profile.rejected_low_confidence == 1
    assert profile.competitor == "Acme"


def test_deep_profile_no_prompts_returns_empty():
    with patch.object(ldp, "_call_llm", return_value=""):
        profile = ldp.deep_profile_competitor("Acme", "Beta", "test")
    assert profile.facts == []
    assert profile.rejected_low_confidence == 0
