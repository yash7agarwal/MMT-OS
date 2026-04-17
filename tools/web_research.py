"""Web research tools for autonomous agents.

Provides web search, page content extraction, and Play Store app discovery.
Designed to be used as tool implementations within the agent tool-use loop.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import quote_plus, urljoin

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)


class WebResearcher:
    """Web search and content extraction for autonomous agents."""

    def __init__(self) -> None:
        self._tavily_key: str | None = os.getenv("TAVILY_API_KEY")
        self._brave_key: str | None = os.getenv("BRAVE_API_KEY")
        self._client = httpx.Client(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )

    # ------------------------------------------------------------------
    # 1. Web search (multi-provider cascade)
    # ------------------------------------------------------------------

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Run a web search. Tries Tavily → Brave → DuckDuckGo lite."""

        if self._tavily_key:
            try:
                return self._search_tavily(query, max_results)
            except Exception as exc:
                logger.warning("Tavily search failed: %s", exc)

        if self._brave_key:
            try:
                return self._search_brave(query, max_results)
            except Exception as exc:
                logger.warning("Brave search failed: %s", exc)

        try:
            return self._search_ddg(query, max_results)
        except Exception as exc:
            logger.warning("DuckDuckGo search failed: %s", exc)

        logger.warning("All search providers failed for query: %s", query)
        return []

    # -- Tavily --

    def _search_tavily(self, query: str, max_results: int) -> list[dict]:
        resp = self._client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": self._tavily_key,
                "query": query,
                "max_results": max_results,
                "include_raw_content": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            }
            for r in data.get("results", [])
        ]

    # -- Brave --

    def _search_brave(self, query: str, max_results: int) -> list[dict]:
        resp = self._client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": self._brave_key},
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("description", ""),
            }
            for r in data.get("web", {}).get("results", [])
        ]

    # -- DuckDuckGo lite (best-effort fallback) --

    def _search_ddg(self, query: str, max_results: int) -> list[dict]:
        resp = self._client.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": query},
        )
        resp.raise_for_status()
        html = resp.text

        results: list[dict] = []
        # DuckDuckGo lite uses table rows with <a> tags for results
        links = re.findall(
            r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )
        # Snippets follow in subsequent <td> cells
        snippets = re.findall(
            r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
            html,
            re.DOTALL,
        )

        for i, (url, title) in enumerate(links[:max_results]):
            title_clean = re.sub(r"<[^>]+>", "", title).strip()
            snippet = ""
            if i < len(snippets):
                snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()
            if url.startswith("//"):
                url = "https:" + url
            results.append({"title": title_clean, "url": url, "snippet": snippet})

        return results

    # ------------------------------------------------------------------
    # 2. Page content extraction
    # ------------------------------------------------------------------

    def fetch_page(self, url: str, max_length: int = 15000) -> dict:
        """Fetch a web page and extract readable content."""
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            logger.error("Error fetching %s: %s", url, exc)
            return {"title": "", "content": f"Error fetching: {exc}", "url": url, "length": 0}

        # Extract title
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else ""

        # Try trafilatura for quality extraction
        content = None
        try:
            import trafilatura

            content = trafilatura.extract(html, include_links=True, include_tables=True)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("trafilatura extraction failed: %s", exc)

        # Fallback: basic regex extraction
        if not content:
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            content = text

        content = content[:max_length]

        return {"title": title, "content": content, "url": url, "length": len(content)}

    # ------------------------------------------------------------------
    # 3. Play Store app search
    # ------------------------------------------------------------------

    def search_play_store(self, query: str, max_results: int = 5) -> list[dict]:
        """Search Google Play Store for apps."""

        # Try google-play-scraper library first
        try:
            from google_play_scraper import search as gps_search

            raw = gps_search(query, n_results=max_results)
            return [
                {
                    "name": app.get("title", ""),
                    "package": app.get("appId", ""),
                    "rating": app.get("score"),
                    "downloads": app.get("installs"),
                    "url": f"https://play.google.com/store/apps/details?id={app.get('appId', '')}",
                }
                for app in raw
            ]
        except ImportError:
            logger.debug("google-play-scraper not installed, falling back to web search")
        except Exception as exc:
            logger.warning("google-play-scraper search failed: %s", exc)

        # Fallback: web search for Play Store listings
        results = self.search(f"site:play.google.com {query}", max_results=max_results * 2)
        apps: list[dict] = []
        seen_packages: set[str] = set()

        for r in results:
            match = re.search(r"id=([a-zA-Z0-9_.]+)", r["url"])
            if match:
                pkg = match.group(1)
                if pkg not in seen_packages:
                    seen_packages.add(pkg)
                    apps.append({
                        "name": r["title"],
                        "package": pkg,
                        "rating": None,
                        "downloads": None,
                        "url": r["url"],
                    })
            if len(apps) >= max_results:
                break

        return apps

    # ------------------------------------------------------------------
    # 4. Play Store app details
    # ------------------------------------------------------------------

    def get_app_details(self, package_name: str) -> dict:
        """Get detailed info about a Play Store app."""
        url = f"https://play.google.com/store/apps/details?id={package_name}"

        # Try google-play-scraper library first
        try:
            from google_play_scraper import app as gps_app

            info = gps_app(package_name)
            return {
                "name": info.get("title", ""),
                "package": package_name,
                "rating": info.get("score"),
                "downloads": info.get("installs"),
                "description": info.get("description", ""),
                "developer": info.get("developer", ""),
                "version": info.get("version", ""),
                "updated": info.get("updated", ""),
                "url": url,
            }
        except ImportError:
            logger.debug("google-play-scraper not installed, falling back to HTML scrape")
        except Exception as exc:
            logger.warning("google-play-scraper app() failed: %s", exc)

        # Fallback: fetch and parse Play Store page
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
            html = resp.text

            def _meta(prop: str) -> str:
                m = re.search(
                    rf'<meta\s+(?:name|property)="{re.escape(prop)}"\s+content="([^"]*)"',
                    html,
                    re.IGNORECASE,
                )
                return m.group(1) if m else ""

            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
            name = title_match.group(1).strip() if title_match else ""
            # Clean " - Apps on Google Play" suffix
            name = re.sub(r"\s*[-–]\s*Apps on Google Play.*", "", name)

            return {
                "name": name,
                "package": package_name,
                "rating": None,
                "downloads": None,
                "description": _meta("og:description"),
                "developer": "",
                "version": "",
                "updated": "",
                "url": url,
            }
        except Exception as exc:
            logger.error("Error fetching app details for %s: %s", package_name, exc)
            return {
                "name": "",
                "package": package_name,
                "rating": None,
                "downloads": None,
                "description": "",
                "developer": "",
                "version": "",
                "updated": "",
                "url": url,
                "error": str(exc),
            }
