"""Screen analyzer — extracts metadata from a single screenshot via Claude vision.

Outputs: name, display_name, purpose, elements, context_hints (where the screen
likely came from in the navigation flow).

Reuses utils.claude_client.ask_vision (already in the codebase).
"""
from __future__ import annotations

import json
import logging
import re

from utils.claude_client import FAST_MODEL, ask_vision

logger = logging.getLogger(__name__)


_ANALYSIS_PROMPT = """\
You are analyzing a mobile app screenshot to map an app's structure for UAT.

Output a JSON object with these fields:
{
  "name": "<short snake_case identifier, stable across runs, e.g. 'hotels_landing_by_night'>",
  "display_name": "<human-readable, e.g. 'Hotels Landing — By Night'>",
  "purpose": "<one sentence: what is this screen for>",
  "elements": [
    {
      "label": "<visible text or icon name>",
      "type": "button|tab|tile|input|card|nav|link|other",
      "x_pct": <0.0-1.0>,
      "y_pct": <0.0-1.0>,
      "leads_to_hint": "<best guess of where tapping this leads, e.g. 'hotels_listing' or null>"
    }
  ],
  "context_hints": "<what does this screen tell us about its predecessor? e.g. 'Has back arrow + Hotel name in title bar — likely came from a hotel listing page'>"
}

Rules:
- Only include INTERACTIVE elements (tappable buttons, tabs, tiles, inputs)
- Use NORMALIZED 0-1 coordinates as fractions of image dimensions
- Pick a stable `name` — same screen across runs should produce the same name
- Be conservative: if you're not sure if something is tappable, omit it
- Respond with ONLY the JSON object, no markdown fences, no prose"""


def _sniff_media_type(image_bytes: bytes) -> str:
    """Detect image media type from the first few bytes (magic numbers)."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    # Default to PNG (most common for screenshots)
    return "image/png"


def analyze_screen(image_bytes: bytes) -> dict:
    """Analyze a single screenshot. Returns dict with name, display_name, purpose, elements, context_hints.

    On failure, returns a minimal stub so upload doesn't fail entirely.
    """
    media_type = _sniff_media_type(image_bytes)
    try:
        raw = ask_vision(
            prompt=_ANALYSIS_PROMPT,
            image_bytes=image_bytes,
            media_type=media_type,
            model=FAST_MODEL,
            max_tokens=1500,
        )
        return _parse_json(raw)
    except Exception as exc:
        logger.warning(f"[ScreenAnalyzer] Failed: {exc}")
        return {
            "name": "unknown_screen",
            "display_name": "Unknown Screen",
            "purpose": f"Analysis failed: {exc}",
            "elements": [],
            "context_hints": None,
        }


def _parse_json(raw: str) -> dict:
    """Parse Claude's JSON response, tolerating markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract first JSON object from text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise
