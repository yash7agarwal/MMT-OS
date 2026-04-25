"""Website grounding — anchors research on the company's actual portfolio.

Why this exists:
The Platinum Industries UAT exposed a class of bug the architecture had no
defense against: the user TYPED "plastic additives manufacturer" in the
project description AND provided platinumindustriesltd.com as the URL,
but the agent never opened the URL. So the query planner generated
queries based purely on the keyword "Platinum Industries" — which collides
with platinum-the-metal commodity reporting, news about platinum mining
in South Africa, and so on. The result was 50+ "effect" entities about
mining deficits, fuel cells, and Helmholtz researchers — all derived
from search drift.

A "simple Claude/ChatGPT query" the user noted as a comparison works
because Claude's web tooling reads the URL FIRST and grounds everything
on what the company actually does. This module gives the agent the same
discipline: fetch the homepage once per session, ask Claude to extract a
structured portfolio summary (products, services, target market, what it
ISN'T), and feed THAT into every downstream prompt.

Cached at process scope via lru_cache — within a session, multiple
research stages reuse the same summary. Across restarts the cache rebuilds
on first use; no DB migration needed.
"""
from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)


_PROMPT = """You are reading the homepage of a company called {project_name}.

HOMEPAGE CONTENT:
{page_content}

Extract a STRICT, FACTUAL portfolio summary in this exact shape:

PRODUCTS / SERVICES (the actual things this company sells):
- <one specific item per line, no marketing fluff>

INDUSTRY:
<one sentence — what industry are they in, specifically? Not generic.>

TARGET CUSTOMERS:
<one sentence — who buys from them?>

GEOGRAPHIC FOCUS:
<one sentence — primary markets>

WHAT THIS COMPANY IS NOT:
- <crucial: list 3–5 things the COMPANY NAME might be confused with but is NOT.>
- <e.g. for "Platinum Industries Ltd" (a plastic-additives manufacturer) →
  NOT a platinum-metal mining company; NOT a precious-metals investment fund;
  NOT a jewelry retailer; NOT related to the platinum commodity market.>

LIKELY COMPETITORS (from the homepage's positioning, not your guess):
- <one specific company name per line if mentioned; otherwise "(none mentioned on homepage)">

Output the summary in plaintext exactly as structured above. Do NOT add commentary.
If the page content is empty/blocked/irrelevant, return the literal string "GROUNDING_FAILED".
"""


@lru_cache(maxsize=32)
def _cached_summary(url: str, project_name: str) -> str | None:
    """Cached one-shot fetch + Claude extraction. lru_cache key is (url, project_name).

    Returns None on any failure — callers should treat that as "no grounding
    available" and fall back to the user-typed description.
    """
    if not url:
        return None

    # Normalize: bare domain → https
    fetch_url = url
    if not fetch_url.startswith(("http://", "https://")):
        fetch_url = f"https://{fetch_url}"

    try:
        from tools.web_research import WebResearcher
        web = WebResearcher()
        page = web.fetch_page(fetch_url, max_length=4000)
        content = (page.get("content") or "").strip()
        if len(content) < 200:
            logger.warning(f"[website_grounding] {fetch_url} returned <200 chars; skipping")
            return None

        from utils.claude_client import ask
        summary = ask(
            _PROMPT.format(project_name=project_name, page_content=content),
            max_tokens=800,
            system="You are a precise factual analyst. Return only the structured summary, never invent facts not on the page.",
        )
        if not summary or "GROUNDING_FAILED" in summary:
            logger.warning(f"[website_grounding] grounding failed for {fetch_url}")
            return None
        return summary.strip()
    except Exception as exc:
        logger.warning(f"[website_grounding] failed for {fetch_url}: {exc}")
        return None


def fetch_portfolio_summary(app_package: str | None, project_name: str) -> str | None:
    """Public entrypoint — returns a portfolio summary or None.

    `app_package` is the project's URL or domain (we accept both with-protocol
    and bare-domain forms). `project_name` is included in the cache key so a
    rename triggers a fresh fetch.
    """
    if not app_package:
        return None
    return _cached_summary(app_package.strip(), project_name.strip())
