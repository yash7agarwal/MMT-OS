"""LLM-as-search for competitor discovery (v0.19.0).

User insight that drove this:
  "If I ask Claude or GPT 'who are the competitors of company XYZ',
   they answer from training data — why does Prism need a Tavily call?"

For well-known entities, modern LLMs (Groq Llama 3.3, Claude, GPT) already
encode the answer to "who are X's competitors?" — including global category
leaders and indirect substitutes. The traditional search → fetch → synthesize
pipeline is search-cost-heavy and search-drift-prone (e.g. "Platinum
Industries" leaking platinum-metal results). For the discovery step
specifically, asking the LLM directly is faster, cheaper, and often more
accurate than search-grounded synthesis.

This module provides ONE function:
  llm_competitor_discovery(project_name, project_description, portfolio_summary)
    → CompetitorDiscovery(direct_local, direct_global, indirect)

Each result includes a URL the LLM emitted. We verify each URL with a single
HEAD request (5s timeout, no API key needed). Verified URLs become first-
class source citations; unverified ones are kept with a label so the user
knows the citation is training-data-only.

Search providers (Tavily / Exa / Brave / DDG) are reserved for the work
items that genuinely need them: dated quantified claims, recent news,
regulatory updates. The cascade in tools/web_research stays intact —
this module just bypasses it for the discovery step.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Literal

import httpx

logger = logging.getLogger(__name__)


@dataclass
class CompetitorRef:
    name: str
    differentiator: str  # one-line: what makes them distinct
    url: str | None  # LLM-emitted homepage / wiki URL (may be None or unverified)
    url_status: Literal["verified", "unverified", "none"] = "none"
    category: Literal["direct_local", "direct_global", "indirect"] = "direct_local"


@dataclass
class CompetitorDiscovery:
    direct_local: list[CompetitorRef] = field(default_factory=list)
    direct_global: list[CompetitorRef] = field(default_factory=list)
    indirect: list[CompetitorRef] = field(default_factory=list)
    raw_response: str = ""  # for audit / debugging

    @property
    def all(self) -> list[CompetitorRef]:
        return self.direct_local + self.direct_global + self.indirect


# ---------------------------------------------------------------------------
# URL HEAD verification (cheap — no API, ~5s per URL, parallelizable)
# ---------------------------------------------------------------------------

_USER_AGENT = (
    "Mozilla/5.0 (compatible; PrismBot/0.19; +https://prism-ros.vercel.app)"
)


def _verify_url(url: str, timeout: float = 5.0) -> bool:
    """Single HEAD request; True iff response is a success-class status (2xx
    or 3xx — many sites 301-redirect from bare http to https or to www)."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            r = client.head(url)
            # Some servers reject HEAD; fall back to GET on 405/501.
            if r.status_code in (405, 501):
                r = client.get(url)
            return 200 <= r.status_code < 400
    except Exception as exc:
        logger.debug(f"[llm_search] URL verify failed for {url}: {exc}")
        return False


def _verify_all_urls(refs: list[CompetitorRef]) -> None:
    """Mutate refs in place: set url_status to 'verified' / 'unverified' / 'none'.

    Fans out HEAD requests in a thread pool (max 8 in flight) so verifying
    20 URLs takes ~5s total instead of 100s sequential.
    """
    targets = [r for r in refs if r.url]
    if not targets:
        for r in refs:
            r.url_status = "none"
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(zip(
            [r.url for r in targets],
            pool.map(_verify_url, [r.url for r in targets]),
        ))
    for r in refs:
        if not r.url:
            r.url_status = "none"
        else:
            r.url_status = "verified" if results.get(r.url) else "unverified"


# ---------------------------------------------------------------------------
# Prompt + parsing
# ---------------------------------------------------------------------------

_PROMPT = """Project: {project_name}
Description: {project_description}
{portfolio_block}

List the competitors of {project_name} from your training knowledge.

OUTPUT — strict JSON exactly this shape:
{{
  "direct_local": [
    {{"name": "...", "differentiator": "one-line distinct angle", "url": "https://homepage-or-wiki-url-if-known-else-empty-string"}}
  ],
  "direct_global": [
    {{"name": "global category leader", "differentiator": "...", "url": "..."}}
  ],
  "indirect": [
    {{"name": "alternative-solving company", "differentiator": "what alternative job they solve", "url": "..."}}
  ]
}}

Rules:
- 3–6 items per category. Real, named companies — never "Competitor 1" / "Company A" / placeholders.
- direct_local: same product or service in the same primary geography as the project. If the project is global, this can be empty.
- direct_global: the recognized leaders in this product category that any informed buyer would compare against, regardless of geography. For an Indian LLM platform, that means OpenAI / Anthropic / Google Gemini / Mistral / Cohere — not just Indian players.
- indirect: alternative ways customers solve the same job. For an OTA, that's airline direct booking / Google Flights / corporate travel managers. For an LLM platform, in-house model fine-tuning or off-the-shelf SaaS.
- url field: include a homepage URL only if you're reasonably confident it exists. If unsure, leave the url field as an empty string. Do not invent URLs.
- Do NOT include {project_name} itself.

Return ONLY the JSON, no prose, no markdown fences.
"""


def _parse_response(raw: str) -> dict:
    """Robust JSON extraction — strips markdown fences, locates the {…} body."""
    text = (raw or "").strip()
    if "```" in text:
        # Pull out the first ```...``` block
        parts = text.split("```")
        for chunk in parts[1::2]:  # odd-indexed are inside-fence
            chunk = chunk.strip()
            if chunk.startswith("json"):
                chunk = chunk[4:].strip()
            if chunk.startswith("{"):
                text = chunk
                break
    # Find the first {...} substring
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(f"[llm_search] JSON parse failed: {exc}; raw: {raw[:300]}")
        return {}


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def llm_competitor_discovery(
    project_name: str,
    project_description: str,
    portfolio_summary: str | None = None,
) -> CompetitorDiscovery:
    """Return competitors from LLM training knowledge, with URLs verified."""
    portfolio_block = (
        f"\nPortfolio (from homepage):\n{portfolio_summary}\n"
        if portfolio_summary else ""
    )
    prompt = _PROMPT.format(
        project_name=project_name,
        project_description=project_description or "(no description)",
        portfolio_block=portfolio_block,
    )

    # Provider: Groq primary (free, fast), Claude fallback. Same chain as
    # report synthesis post-v0.18.1.
    raw = ""
    try:
        from utils import groq_client
        if groq_client.is_available():
            raw = groq_client.synthesize(
                prompt=prompt,
                max_tokens=2000,
                system=(
                    "You are a senior industry analyst. Answer from training "
                    "knowledge. Return only valid JSON. Never use placeholder "
                    "names. Never invent URLs you aren't reasonably confident "
                    "about — leave url empty when uncertain."
                ),
            )
    except Exception as exc:
        logger.warning(f"[llm_search] Groq path failed: {exc}")

    if not raw:
        try:
            from utils.claude_client import ask
            raw = ask(
                prompt=prompt,
                max_tokens=2000,
                system="You are a senior industry analyst. Return only valid JSON.",
            )
        except Exception as exc:
            logger.error(f"[llm_search] Claude fallback also failed: {exc}")
            return CompetitorDiscovery(raw_response=str(exc))

    parsed = _parse_response(raw)
    discovery = CompetitorDiscovery(raw_response=raw)

    for category, attr in [
        ("direct_local", "direct_local"),
        ("direct_global", "direct_global"),
        ("indirect", "indirect"),
    ]:
        items = parsed.get(category) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            if not name:
                continue
            ref = CompetitorRef(
                name=name,
                differentiator=(item.get("differentiator") or "").strip()[:240],
                url=(item.get("url") or "").strip() or None,
                category=category,  # type: ignore
            )
            getattr(discovery, attr).append(ref)

    # Verify URLs in parallel — bounded latency, no API cost.
    _verify_all_urls(discovery.all)

    logger.info(
        f"[llm_search] {project_name}: "
        f"local={len(discovery.direct_local)} global={len(discovery.direct_global)} "
        f"indirect={len(discovery.indirect)} "
        f"verified_urls={sum(1 for r in discovery.all if r.url_status == 'verified')}"
    )
    return discovery
