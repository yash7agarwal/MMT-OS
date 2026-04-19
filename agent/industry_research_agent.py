"""Industry Research Agent — tracks industry trends, reports, and market dynamics.

Responsibilities:
- Self-identify the industry and its sub-segments
- Discover and follow key industry sources (e.g., Skift for travel, PhocusWire, etc.)
- Track industry reports, market data, trends
- Monitor regulatory changes and new entrants
- Build a comprehensive view: where the industry came from, where it is, where it's going
- Generate periodic "state of industry" reports
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from agent.base_autonomous_agent import AutonomousAgent
from tools.web_research import WebResearcher
from utils.claude_client import ask
from webapp.api.models import KnowledgeEntity, Project, WorkItem

logger = logging.getLogger(__name__)


class IndustryResearchAgent(AutonomousAgent):
    """Autonomous agent that tracks industry trends, reports, and market dynamics."""

    def __init__(self, project_id: int, db: Session):
        super().__init__("industry_research", project_id, db)
        self.web = WebResearcher()

        # Load project info
        project = self.db.query(Project).filter(Project.id == project_id).first()
        self.project_name = project.name if project else "Unknown Project"
        self.project_description = project.description or "" if project else ""

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    def seed_backlog(self) -> list[dict]:
        """Return initial work items for industry research."""
        return [
            {
                "priority": 9,
                "category": "industry_identification",
                "description": (
                    "Identify the industry, sub-segments, key players, and market "
                    "dynamics based on the project description"
                ),
                "context_json": {
                    "project_name": self.project_name,
                    "project_description": self.project_description,
                },
            },
            {
                "priority": 8,
                "category": "source_discovery",
                "description": (
                    "Find and catalog the best industry-specific news sources, "
                    "research firms, blogs, and publications to follow"
                ),
                "context_json": None,
            },
            {
                "priority": 7,
                "category": "stakeholder_mapping",
                "description": (
                    "Map all stakeholders in this industry: regulators, industry "
                    "bodies, major investors, key executives, and influencers"
                ),
                "context_json": None,
            },
            {
                "priority": 6,
                "category": "trend_analysis",
                "description": (
                    "Research current industry trends, market size, growth rates, "
                    "and major shifts"
                ),
                "context_json": None,
            },
            {
                "priority": 5,
                "category": "niche_trend_discovery",
                "description": (
                    "Discover niche and emerging trends in this industry that represent "
                    "underserved segments or future growth areas"
                ),
                "context_json": None,
            },
        ]

    def generate_next_work(self) -> list[dict]:
        """Use Claude to analyze current knowledge and suggest next research items."""
        summary = self.knowledge.get_knowledge_summary()

        # Gather existing industry entities
        industry_entities = self.knowledge.find_entities(entity_type="trend")
        industry_entities += self.knowledge.find_entities(entity_type="regulation")
        industry_entities += self.knowledge.find_entities(entity_type="company")
        entity_names = [e["name"] for e in industry_entities]

        # Gather completed work
        completed = (
            self.db.query(WorkItem)
            .filter(
                WorkItem.agent_type == self.agent_type,
                WorkItem.project_id == self.project_id,
                WorkItem.status == "completed",
            )
            .order_by(WorkItem.completed_at.desc())
            .limit(20)
            .all()
        )
        completed_descriptions = [
            f"[{w.category}] {w.description} -> {w.result_summary or 'done'}"
            for w in completed
        ]

        prompt = f"""You are an industry research analyst planning next research steps.

Project: {self.project_name}
Description: {self.project_description}

Current knowledge state:
{json.dumps(summary, indent=2, default=str)}

Known entities: {json.dumps(entity_names)}

Completed work items:
{chr(10).join(completed_descriptions) or "(none yet)"}

Based on this state, suggest 2-4 high-value next research items. Consider:
- Industry sources that should be checked for new content
- Trends that need deeper investigation or updates
- Regulatory changes that should be monitored
- Market segments that haven't been analyzed yet
- New entrants or emerging players to track
- Whether it's time to generate a comprehensive report

Return a JSON array of work items. Each item must have:
- "priority": int 1-10 (higher = more important)
- "category": one of "article_reading", "trend_tracking", "regulatory_scan", "market_analysis", "new_entrant_scan", "report_generation", "niche_trend_discovery", "trend_quantification", "trend_adoption_mapping"
- "description": what to research
- "context_json": optional dict with entity names or other context

Return ONLY the JSON array, no other text."""

        try:
            response = ask(prompt, max_tokens=2048)
            # Extract JSON from response
            text = response.strip()
            # Handle markdown code blocks
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            items = json.loads(text)
            if isinstance(items, list) and len(items) > 0:
                return items
        except (json.JSONDecodeError, IndexError, Exception) as exc:
            logger.warning("Failed to parse Claude's work suggestions: %s", exc)

        # Fallback: generate sensible defaults
        fallback: list[dict] = []
        if entity_names:
            fallback.append({
                "priority": 7,
                "category": "trend_tracking",
                "description": (
                    "Check for updates on known industry trends and recent developments"
                ),
                "context_json": {"known_entities": entity_names[:5]},
            })
            fallback.append({
                "priority": 6,
                "category": "regulatory_scan",
                "description": (
                    "Search for new regulations or policy changes affecting the industry"
                ),
                "context_json": None,
            })
        else:
            fallback.append({
                "priority": 8,
                "category": "trend_analysis",
                "description": (
                    "Research current industry trends using alternative search queries"
                ),
                "context_json": None,
            })
        return fallback

    def get_tools(self) -> list[dict]:
        """Return Anthropic-format tool schemas for the tool-use loop."""
        return [
            {
                "name": "web_search",
                "description": "Search the web for information.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results to return.",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "fetch_page",
                "description": "Fetch and read a web page's content.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL to fetch.",
                        },
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "save_source",
                "description": (
                    "Save an industry source or publication for future reference."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name of the source/publication.",
                        },
                        "url": {
                            "type": "string",
                            "description": "URL of the source.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Brief description of the source.",
                        },
                        "focus_area": {
                            "type": "string",
                            "description": (
                                "What area this source focuses on "
                                "(e.g. travel tech, airline industry, hospitality)."
                            ),
                        },
                    },
                    "required": ["name", "url", "description", "focus_area"],
                },
            },
            {
                "name": "save_finding",
                "description": "Save a research finding or observation about a topic.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "The topic this finding is about.",
                        },
                        "observation_type": {
                            "type": "string",
                            "description": "Type of observation.",
                            "enum": ["news", "regulatory", "metric", "general"],
                        },
                        "content": {
                            "type": "string",
                            "description": "The finding content.",
                        },
                        "source_url": {
                            "type": "string",
                            "description": "URL where this information was found.",
                        },
                        "evidence": {
                            "type": "object",
                            "description": "Supporting evidence or data points.",
                        },
                        "lenses": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Analytical lenses this finding relates to. Choose from: product_craft, growth, supply, monetization, technology, brand_trust, moat, trajectory",
                        },
                    },
                    "required": ["topic", "observation_type", "content"],
                },
            },
            {
                "name": "query_knowledge",
                "description": "Query what we already know about a topic or entity.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query.",
                        },
                        "entity_type": {
                            "type": "string",
                            "description": (
                                "Optional entity type filter "
                                "(e.g. trend, regulation, company, article)."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "generate_report",
                "description": "Generate an industry report artifact.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Report title.",
                        },
                        "report_type": {
                            "type": "string",
                            "description": "Type of report.",
                            "enum": ["trend_report", "industry_overview"],
                        },
                        "content_markdown": {
                            "type": "string",
                            "description": "Full report content in Markdown.",
                        },
                        "related_topics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Names of topics this report covers.",
                        },
                    },
                    "required": ["title", "report_type", "content_markdown"],
                },
            },
            {
                "name": "finish_work",
                "description": (
                    "Signal that the current work item is complete. "
                    "Call this when you have finished all research for the current task."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Summary of what was accomplished.",
                        },
                        "entities_created": {
                            "type": "integer",
                            "description": "Number of new entities created.",
                        },
                        "observations_added": {
                            "type": "integer",
                            "description": "Number of observations added.",
                        },
                    },
                    "required": ["summary", "entities_created", "observations_added"],
                },
            },
        ]

    def get_system_prompt(self) -> str:
        """Return the system prompt for the Claude tool-use loop."""
        summary = self.knowledge.get_knowledge_summary()
        return (
            f'You are a senior industry analyst embedded in the product team at '
            f'{self.project_name}. You report directly to the PM.\n\n'
            f'Product: {self.project_description}\n\n'
            f'YOUR MANDATE: Surface intelligence that impacts product roadmap decisions. '
            f'The PM does NOT need a textbook overview of the industry. They need:\n\n'
            f'SIGNALS over summaries:\n'
            f'- BAD: "The travel industry is adopting AI" — too obvious\n'
            f'- GOOD: "Skift reports that 34% of hotel bookings now involve AI-assisted '
            f'search, up from 12% in 2024. Booking.com credits their AI trip planner '
            f'for a 15% lift in cross-sell revenue (Q4 2025 earnings call)"\n\n'
            f'DISRUPTIONS over trends:\n'
            f'- BAD: "Mobile bookings are growing"\n'
            f'- GOOD: "Google is testing direct hotel booking in search results (no OTA '
            f'redirect), spotted in India market Dec 2025. If rolled out, this '
            f'disintermediates all OTAs on their highest-intent traffic."\n\n'
            f'WHAT TO TRACK:\n'
            f'1. Regulatory changes that affect our business model (GST, data localization)\n'
            f'2. Platform moves by Google/Apple that change distribution\n'
            f'3. New business models emerging (subscription travel, BNPL for travel)\n'
            f'4. Specific data points from analyst reports (Skift, PhocusWire, CAPA)\n'
            f'5. Funding rounds and acquisitions that signal market shifts\n'
            f'6. NICHE TRENDS: Emerging consumer segments (women travelers, pet travel, solo, accessibility)\n'
            f'7. TREND QUANTIFICATION: Search volumes, market sizes, growth rates for each trend\n'
            f'8. TREND ADOPTION: Which companies are addressing which trends, and how well\n\n'
            f'RULES:\n'
            f'- Every finding MUST have a source_url\n'
            f'- Every save_finding MUST include a "lenses" array with 1-3 lens tags from:\n'
            f'  product_craft, growth, supply, monetization, technology, brand_trust, moat, trajectory\n'
            f'- Prefer sources from 2025-2026. Skip pre-2024 data.\n'
            f'- Search, read 2-3 pages, extract specific findings, save, move on\n'
            f'- Call finish_work within 8-10 tool calls\n'
            f'- When saving a trend finding, include timeline (past/present/emerging/future) and\n'
            f'  category (consumer_behavior/technology/regulation/demographics/market_structure) in the content\n'
            f'- Quantify trends wherever possible: market size, growth rate, search volume\n\n'
            f'Current knowledge state:\n'
            f'{json.dumps(summary, indent=2, default=str)}'
        )

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Dispatch tool calls to their implementations."""
        try:
            if tool_name == "web_search":
                results = self.web.search(
                    tool_input["query"],
                    max_results=tool_input.get("max_results", 10),
                )
                return json.dumps(results, default=str)

            elif tool_name == "fetch_page":
                result = self.web.fetch_page(tool_input["url"])
                return json.dumps(result, default=str)

            elif tool_name == "save_source":
                return self._tool_save_source(tool_input)

            elif tool_name == "save_finding":
                return self._tool_save_finding(tool_input)

            elif tool_name == "query_knowledge":
                return self._tool_query_knowledge(tool_input)

            elif tool_name == "generate_report":
                return self._tool_generate_report(tool_input)

            elif tool_name == "finish_work":
                return self._tool_finish_work(tool_input)

            else:
                return f"ERROR: Unknown tool '{tool_name}'"

        except Exception as exc:
            logger.error("Tool %s failed: %s", tool_name, exc, exc_info=True)
            return f"ERROR: {exc}"

    def execute_work_item(self, item: WorkItem) -> dict:
        """Execute using efficient research (Groq free) — no tool-use loop."""
        self._current_result: dict = {
            "status": "completed",
            "summary": "Work item processed",
            "entities_created": 0,
            "observations_added": 0,
        }

        context = item.context_json or {}

        # Use efficient researcher for trend discovery. Phase-1 research pipeline:
        # build a typed brief → plan queries with Haiku → retrieve → synthesize
        # → validate source_urls → write. Quality metrics accumulate on
        # self._current_result["quality"] for base_autonomous_agent to persist.
        if item.category in ("trend_analysis", "niche_trend_discovery"):
            from agent.efficient_researcher import research_industry_trends
            from agent.query_planner import get_or_generate_plan
            from agent.research_brief import build_brief
            from agent.synthesis_validator import validate_candidates

            brief = build_brief(self.db, self.project_id)
            plan = get_or_generate_plan(self.db, brief)
            result = research_industry_trends(brief, plan)

            candidates = [
                {**t, "canonical_name": (t.get("name") or "").lower().strip()}
                for t in result.get("trends", [])
            ]
            kept, report = validate_candidates(candidates, result.get("retrieval_bundle", []))

            quality = self._current_result.setdefault("quality", {})
            quality.setdefault("validator", []).append(report.as_dict())
            quality["inferred_industry"] = plan.inferred_industry
            quality["plan_cached"] = plan.cached
            quality["plan_queries"] = len(plan.queries)

            pre_existing_trends = {
                e["canonical_name"] for e in self.knowledge.find_entities(entity_type="trend")
                if e.get("canonical_name")
            }
            novel_count = 0
            for trend in kept:
                meta = {"timeline": trend.get("timeline", "present"), "category": trend.get("category", "general")}
                meta.update(trend.get("quantification") or {})
                canonical = trend.get("canonical_name") or (trend.get("name") or "").lower().strip()
                if canonical and canonical not in pre_existing_trends:
                    novel_count += 1
                eid = self.knowledge.upsert_entity(
                    "trend", trend["name"], trend.get("description", ""), metadata=meta,
                )
                self.knowledge.add_observation(
                    eid, "general", trend.get("description", ""),
                    source_url=trend["source_url"],  # validator guarantees this exists
                    lens_tags=["growth"],
                )
                self._current_result["entities_created"] = self._current_result.get("entities_created", 0) + 1

            quality["retrieval_yield"] = (
                sum(1 for _ in result.get("retrieval_bundle", [])) / max(len(plan.queries), 1)
            )
            quality["novelty_yield"] = novel_count / max(len(kept), 1) if kept else 0.0

            self._current_result["status"] = "completed"
            self._current_result["summary"] = (
                f"[{plan.inferred_industry}] {len(kept)} trends kept, "
                f"{report.total_in - report.total_out} dropped, "
                f"{novel_count} novel; plan_cached={plan.cached}"
            )
            return self._current_result

        # For all other categories, use search + synthesize (1 LLM call via Groq)
        logger.info(f"[{self.agent_type}] Efficient research for: {item.category}")

        from agent.efficient_researcher import _get_synthesizer
        from tools.web_research import WebResearcher
        synthesize = _get_synthesizer()
        web = WebResearcher()

        prompt = self._build_work_prompt(item.category, item.description, context)
        search_terms = item.description[:80]
        queries = [f"{self.project_name} industry {search_terms}", f"{search_terms} 2025 2026"]

        raw_data = []
        for q in queries:
            for r in web.search(q, max_results=3)[:2]:
                page = web.fetch_page(r.get("url", ""), max_length=3000)
                if page.get("content") and len(page["content"]) > 100:
                    raw_data.append(f"Source: {r.get('url','')}\n{page['content'][:2000]}")

        if raw_data:
            response = synthesize(
                f"{prompt}\n\nRAW DATA:\n" + "\n---\n".join(raw_data[:5]) +
                f'\n\nExtract findings. Return JSON: {{"findings": [{{"type": "general", "content": "...", "lenses": ["growth"], "source_url": "..."}}]}}',
                max_tokens=2000,
                system="Extract specific industry findings. Return valid JSON only.",
            )
            try:
                text = response.strip()
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"): text = text[4:]
                    text = text.strip()
                for f in json.loads(text).get("findings", []):
                    eid = self.knowledge.upsert_entity("trend", f.get("content", "")[:60], f.get("content", ""))
                    self.knowledge.add_observation(eid, f.get("type", "general"), f.get("content", ""), source_url=f.get("source_url"), lens_tags=f.get("lenses"))
                    self._current_result["observations_added"] = self._current_result.get("observations_added", 0) + 1
            except Exception as e:
                logger.warning(f"[{self.agent_type}] Parse failed: {e}")

        self._current_result["status"] = "completed"
        self._current_result["summary"] = f"Researched {item.category}: {self._current_result.get('observations_added', 0)} findings"
        return self._current_result

    # ------------------------------------------------------------------
    # Tool implementations (private)
    # ------------------------------------------------------------------

    def _tool_save_source(self, inp: dict) -> str:
        """Save an industry source/publication as a knowledge entity."""
        metadata = {
            "url": inp["url"],
            "focus_area": inp["focus_area"],
        }

        entity_id = self.knowledge.upsert_entity(
            entity_type="article",
            name=inp["name"],
            description=inp["description"],
            metadata=metadata,
        )

        self._current_result["entities_created"] = (
            self._current_result.get("entities_created", 0) + 1
        )

        return json.dumps({
            "status": "saved",
            "entity_id": entity_id,
            "name": inp["name"],
        })

    def _tool_save_finding(self, inp: dict) -> str:
        """Save a research finding as an observation on a topic entity."""
        # Map observation types to entity types
        entity_type_map = {
            "news": "trend",
            "regulatory": "regulation",
            "metric": "trend",
            "general": "trend",
        }
        obs_type = inp["observation_type"]
        target_entity_type = entity_type_map.get(obs_type, "trend")

        # Find or create the topic entity
        entities = self.knowledge.find_entities(name_like=inp["topic"])
        if not entities:
            entity_id = self.knowledge.upsert_entity(
                entity_type=target_entity_type,
                name=inp["topic"],
                description=f"Industry topic: {inp['topic']}",
            )
        else:
            entity_id = entities[0]["id"]

        obs_id = self.knowledge.add_observation(
            entity_id=entity_id,
            obs_type=obs_type,
            content=inp["content"],
            evidence=inp.get("evidence"),
            source_url=inp.get("source_url"),
            lens_tags=inp.get("lenses"),
        )

        self._current_result["observations_added"] = (
            self._current_result.get("observations_added", 0) + 1
        )

        return json.dumps({
            "status": "saved",
            "observation_id": obs_id,
            "entity_id": entity_id,
        })

    def _tool_query_knowledge(self, inp: dict) -> str:
        """Query the knowledge graph for existing information."""
        results: dict[str, Any] = {}

        # Search entities
        entities = self.knowledge.find_entities(
            entity_type=inp.get("entity_type"),
            name_like=inp["query"],
        )
        results["entities"] = entities

        # Semantic search for related observations
        semantic = self.knowledge.semantic_search(inp["query"], top_k=5)
        results["semantic_matches"] = semantic

        # If we found entities, include their recent observations
        if entities:
            for ent in entities[:3]:
                obs = self.knowledge.get_observations(ent["id"], limit=5)
                ent["recent_observations"] = obs

        return json.dumps(results, default=str)

    def _tool_generate_report(self, inp: dict) -> str:
        """Generate and save an industry report artifact."""
        # Resolve entity IDs from topic names
        entity_ids: list[int] = []
        for name in inp.get("related_topics", []):
            found = self.knowledge.find_entities(name_like=name)
            if found:
                entity_ids.append(found[0]["id"])

        artifact_id = self.knowledge.save_artifact(
            artifact_type=inp["report_type"],
            title=inp["title"],
            content_md=inp["content_markdown"],
            entity_ids=entity_ids or None,
        )

        return json.dumps({
            "status": "saved",
            "artifact_id": artifact_id,
            "title": inp["title"],
        })

    def _tool_finish_work(self, inp: dict) -> str:
        """Mark the current work item as complete."""
        self._current_result = {
            "status": "completed",
            "summary": inp["summary"],
            "entities_created": inp.get("entities_created", 0),
            "observations_added": inp.get("observations_added", 0),
        }
        return json.dumps({
            "status": "completed",
            "summary": inp["summary"],
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_work_prompt(
        self, category: str, description: str, context: dict
    ) -> str:
        """Build a targeted prompt based on work item category."""
        if category == "industry_identification":
            return (
                f"Identify the industry, sub-segments, key players, and market "
                f"dynamics for {self.project_name}. {self.project_description}. "
                f"Use web_search to research the industry landscape. Save findings "
                f"about the industry structure, major segments, and key players. "
                f"Create entities for each major segment and trend you discover."
            )

        elif category == "source_discovery":
            industry_entities = self.knowledge.find_entities(entity_type="trend")
            industry = (
                industry_entities[0]["name"]
                if industry_entities
                else "the project's industry"
            )
            return (
                f"Find the best industry-specific news sources, research firms, "
                f"blogs, and publications for {industry}. Search for authoritative "
                f"publications, analyst firms, trade associations, and influential "
                f"blogs. For each source found, use save_source to catalog it with "
                f"its focus area and URL."
            )

        elif category == "stakeholder_mapping":
            return (
                f"Map all key stakeholders in the industry for {self.project_name}: "
                f"regulators, industry bodies, major investors, key executives, "
                f"and influencers. Search the web for industry associations, "
                f"regulatory agencies, and prominent leaders. Save each as a "
                f"finding with their role and influence."
            )

        elif category == "trend_analysis":
            return (
                f"Research current industry trends for {self.project_name}. "
                f"Look for market size data, growth rates, technology disruptions, "
                f"consumer behavior shifts, and major strategic moves. Save each "
                f"trend as a finding with supporting data and source URLs."
            )

        elif category == "article_reading":
            url = context.get("url", "")
            source = context.get("source_name", "the source")
            return (
                f"Read and summarize the latest content from {source}. "
                f"{'Fetch the page at ' + url + '. ' if url else ''}"
                f"Extract key findings, data points, and insights. Save each "
                f"significant finding with save_finding. {description}"
            )

        elif category == "regulatory_scan":
            return (
                f"Search for new regulations, policy changes, or government "
                f"actions affecting the industry for {self.project_name}. "
                f"Look for recent regulatory announcements, compliance changes, "
                f"and policy proposals. Save each finding as a regulatory "
                f"observation with source URLs."
            )

        elif category == "report_generation":
            return (
                f"Generate a comprehensive state-of-industry report for "
                f"{self.project_name}. First, use query_knowledge to gather all "
                f"existing findings. Then synthesize them into a well-structured "
                f"industry overview or trend report using generate_report. "
                f"Include market size, key trends, regulatory landscape, major "
                f"players, and outlook."
            )

        elif category == "niche_trend_discovery":
            known_trends = self.knowledge.find_entities(entity_type="trend")
            known_names = [t["name"] for t in known_trends]
            return (
                f"Discover NICHE and EMERGING trends in {self.project_name}'s domain "
                f"that most companies haven't fully addressed yet. Infer the domain "
                f"from the project description — do NOT assume a specific industry.\n\n"
                f"Project description: {self.project_description or '(none)'}\n"
                f"Already known trends: {', '.join(known_names) if known_names else 'none'}\n\n"
                f"Think about underserved segments and future shifts *specific to this "
                f"subject's actual industry* across these lens families:\n"
                f"- DEMOGRAPHIC shifts (underserved user cohorts within this domain)\n"
                f"- BEHAVIORAL changes (new JTBD or usage patterns in this domain)\n"
                f"- TECHNOLOGY-driven (new capabilities changing this domain's unit economics)\n"
                f"- REGULATORY (policy or compliance shifts affecting this domain)\n"
                f"- PLATFORM / INFRASTRUCTURE (distribution, payment, identity shifts)\n"
                f"- ECONOMIC (business-model pivots, pricing innovations in this domain)\n\n"
                f"Forbidden: trends from OTHER industries. If this subject is not a travel "
                f"company, do not emit travel trends. Cross-industry leakage is a known bug.\n\n"
                f"For each trend discovered:\n"
                f"1. Use save_finding with observation_type='general'. Include in the content:\n"
                f"   - What the trend is and why it matters for THIS subject's domain\n"
                f"   - Timeline (past/present/emerging/future)\n"
                f"   - Category (consumer_behavior/technology/regulation/demographics/market_structure)\n"
                f"2. Quantify: search volume, market size, growth rate — only with cited numbers\n"
                f"3. Tag with relevant lenses\n\n"
                f"Source URL mandatory for every finding.\n"
                f"Target: 5-8 niche trends. Call finish_work when done."
            )

        elif category == "trend_quantification":
            trend_name = context.get("trend_name", description)
            return (
                f"Quantify the trend: {trend_name}\n\n"
                f"Search for data points that measure the real size and growth of this trend:\n"
                f"1. Search volume: '{trend_name} search trends Google Trends'\n"
                f"2. Market size: '{trend_name} market size 2025 2026'\n"
                f"3. Revenue impact: How much revenue does this trend drive for companies?\n"
                f"4. User demand: Survey data, review mentions, app feature requests\n"
                f"5. Traffic volume: Any data on booking volumes or user counts\n\n"
                f"Save each quantification as a finding with observation_type='metric' "
                f"and lenses=['growth']. Source URL mandatory.\n"
                f"Also note which companies are best addressing this trend."
            )

        elif category == "trend_adoption_mapping":
            return (
                f"Map which competitors are addressing which industry trends.\n\n"
                f"For each known trend, evaluate each competitor:\n"
                f"- Does their app/service address this trend? (yes/no/partially)\n"
                f"- How mature is their offering? (strong/emerging/absent)\n"
                f"- Any specific features they've built for this trend?\n\n"
                f"Save findings linking each competitor to each trend they address.\n"
                f"Use lenses ['product_craft', 'growth'] for each finding."
            )

        else:
            # Default: use description directly
            return description
