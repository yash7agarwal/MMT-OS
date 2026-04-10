"""Test plan generator — turns a feature description + screen graph into a UAT plan.

Reuses utils.claude_client.ask (text-only) since the screen metadata is already
in the database (no need to send images again).

Output: list of test cases, each with title, target_screen_id, navigation_path,
acceptance_criteria, and branch_label.
"""
from __future__ import annotations

import json
import logging
import re

from utils.claude_client import DEFAULT_MODEL, ask

logger = logging.getLogger(__name__)


_PLANNER_PROMPT = """\
You are a senior product manager planning UAT for a newly launched feature.
Your job: generate a comprehensive list of test cases that cover ALL branches the feature touches.

FEATURE DESCRIPTION:
{feature_description}

APP MAP:
- Screens: {screens_json}
- Edges: {edges_json}

Generate test cases that:
- Cover the HAPPY PATH first
- Cover ALL alternate funnels/branches that touch this feature (if the feature is on a Hotels details page \
and Hotels has By Night and By Hour tabs, generate cases for BOTH paths)
- Include edge cases (empty states, errors, slow networks, missing data)
- Reference SPECIFIC screens by their `name` (snake_case identifier from the app map)
- Build a navigation_path as a list of {{from_screen, to_screen, trigger}} steps that walks from the home screen to the target

Output ONLY a JSON object with this structure (no markdown, no prose):
{{
  "cases": [
    {{
      "title": "<concise test case title — what's being verified>",
      "target_screen_name": "<screen name from app map>",
      "navigation_path": [
        {{"from_screen": "<name>", "to_screen": "<name>", "trigger": "<tap X / fill Y / etc>"}}
      ],
      "acceptance_criteria": "<single sentence describing what must be true on the target screen for PASS>",
      "branch_label": "<short label categorizing this case, e.g. 'By Night', 'By Hour', 'Empty state', 'Slow network'>"
    }}
  ]
}}

Rules:
- Generate 3-10 cases (more if the feature has many branches, fewer if it's narrow)
- target_screen_name MUST exist in the app map. Do not invent screens.
- If the feature description mentions specific elements (e.g. "Book Now button"), include cases that verify them
- branch_label should group related cases together
- Be specific in acceptance_criteria — "shows hotel name, photos, and price" not "looks correct"
"""


def generate_test_plan(
    feature_description: str,
    screens: list[dict],
    edges: list[dict],
) -> list[dict]:
    """Generate a list of test cases for a feature.

    Args:
        feature_description: Plain English description of the feature being tested
        screens: list of {id, name, display_name, purpose}
        edges: list of {from_screen_id, to_screen_id, trigger}

    Returns:
        list of test case dicts (may be empty if generation fails)
    """
    if not screens:
        return []

    screens_compact = [
        {"id": s["id"], "name": s["name"], "display_name": s.get("display_name"), "purpose": s.get("purpose")}
        for s in screens
    ]
    name_by_id = {s["id"]: s["name"] for s in screens}
    edges_compact = [
        {
            "from_screen": name_by_id.get(e["from_screen_id"], "?"),
            "to_screen": name_by_id.get(e["to_screen_id"], "?"),
            "trigger": e["trigger"],
        }
        for e in edges
    ]

    prompt = _PLANNER_PROMPT.format(
        feature_description=feature_description,
        screens_json=json.dumps(screens_compact, indent=2),
        edges_json=json.dumps(edges_compact, indent=2),
    )

    try:
        raw = ask(prompt=prompt, model=DEFAULT_MODEL, max_tokens=4096)
        parsed = _parse_json(raw)
        return parsed.get("cases", [])
    except Exception as exc:
        logger.warning(f"[TestPlanner] Failed: {exc}")
        return []


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
