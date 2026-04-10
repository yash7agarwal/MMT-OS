"""Flow inferrer — given all analyzed screens for a project, propose edges between them.

The user uploads screenshots in any order. This service uses Claude to reason about
which screens are connected, identify the home/entry screen, and detect branches
(e.g., "By Night" vs "By Hour" funnels).

Reuses utils.claude_client.ask (text-only) — much cheaper than vision since we already
extracted screen metadata in the per-screen analysis pass.
"""
from __future__ import annotations

import json
import logging
import re

from utils.claude_client import DEFAULT_MODEL, ask

logger = logging.getLogger(__name__)


_INFERENCE_PROMPT = """\
You are reverse-engineering a mobile app's navigation graph from a set of screen summaries.

A product manager has uploaded screenshots of {n_screens} screens from their app, in no particular order.
For each screen, you have its analyzed metadata (name, purpose, interactive elements with leads_to_hint guesses).

Your job: propose the navigation graph.

SCREENS:
{screens_json}

Output a JSON object with this exact structure:
{{
  "home_screen_id": <id of the screen that looks like the app's main entry / landing>,
  "proposed_edges": [
    {{
      "from_screen_id": <int>,
      "to_screen_id": <int>,
      "trigger": "<which element on `from` leads to `to`, e.g. 'tap Hotels tile'>",
      "confidence": <0.0 to 1.0>,
      "reasoning": "<one sentence — why you think these are connected>"
    }}
  ],
  "branches": [
    {{
      "name": "<short label, e.g. 'Hotel booking type'>",
      "screen_ids": [<id1>, <id2>],
      "reasoning": "<why these are alternative branches of the same flow>"
    }}
  ]
}}

Rules:
- Use TWO signals to propose edges:
  1. `elements[].leads_to_hint` on the SOURCE screen — what tapping each element likely leads to
  2. `context_hints` on the TARGET screen — what the analyzer thinks the predecessor was
- When BOTH signals point to the same connection, set confidence ≥0.85 (high enough for auto-creation)
- Match by screen NAME, not display_name
- Use confidence 0.7-0.85 when only one signal supports the edge
- Use <0.7 when the connection is plausible but not directly evidenced
- Identify branches when two screens have similar purposes but distinct content (e.g. one filtered view vs another)
- Do NOT invent edges. If you can't connect two screens, leave them disconnected.
- Respond with ONLY the JSON object, no markdown, no prose"""


def infer_flow(screens: list[dict]) -> dict:
    """Infer the navigation graph from a list of analyzed screens.

    Args:
        screens: list of dicts each with {id, name, display_name, purpose, elements}

    Returns:
        {home_screen_id, proposed_edges: [...], branches: [...]}
    """
    if not screens:
        return {"home_screen_id": None, "proposed_edges": [], "branches": []}

    # Build a compact summary to send to Claude — strip screenshot paths, etc.
    # Include context_hints — this is the screen analyzer's guess about where
    # the screen came from (e.g., "Has back arrow + hotel name in title bar —
    # likely came from a hotel listing"). It's a strong signal for inferrer.
    compact = [
        {
            "id": s["id"],
            "name": s["name"],
            "display_name": s.get("display_name"),
            "purpose": s.get("purpose"),
            "context_hints": s.get("context_hints"),
            "elements": [
                {
                    "label": e.get("label"),
                    "type": e.get("type"),
                    "leads_to_hint": e.get("leads_to_hint"),
                }
                for e in (s.get("elements") or [])
            ],
        }
        for s in screens
    ]

    prompt = _INFERENCE_PROMPT.format(
        n_screens=len(screens),
        screens_json=json.dumps(compact, indent=2),
    )

    try:
        raw = ask(prompt=prompt, model=DEFAULT_MODEL, max_tokens=4096)
        return _parse_json(raw)
    except Exception as exc:
        logger.warning(f"[FlowInferrer] Failed: {exc}")
        return {
            "home_screen_id": None,
            "proposed_edges": [],
            "branches": [],
            "error": str(exc),
        }


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise
