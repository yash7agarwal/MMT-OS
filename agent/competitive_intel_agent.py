"""Competitive Intelligence Agent — discovers and tracks competitors autonomously.

Responsibilities:
- Identify competitors based on project industry
- Research competitor features, pricing, positioning
- Track competitor app changes over time
- Map competitor app UX flows (when device available)
- Generate competitive analysis reports
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


class CompetitiveIntelAgent(AutonomousAgent):
    """Autonomous agent that discovers and tracks competitive intelligence."""

    def __init__(self, project_id: int, db: Session, device=None):
        super().__init__("competitive_intel", project_id, db, device)
        self.web = WebResearcher()

        # Load project info
        project = self.db.query(Project).filter(Project.id == project_id).first()
        self.project_name = project.name if project else "Unknown Project"
        self.project_description = project.description or "" if project else ""

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    def seed_backlog(self) -> list[dict]:
        """Return initial work items for competitive intelligence gathering."""
        return [
            {
                "priority": 10,
                "category": "industry_identification",
                "description": (
                    "Identify and save all direct competitors. "
                    "Do not write an industry essay — just find competitors and save them."
                ),
                "context_json": {
                    "project_name": self.project_name,
                    "project_description": self.project_description,
                },
            },
        ]

    def generate_next_work(self) -> list[dict]:
        """Use Claude to reason about knowledge gaps and suggest next work items."""
        summary = self.knowledge.get_knowledge_summary()

        # Gather existing entities
        competitors = self.knowledge.find_entities(entity_type="company")
        competitor_names = [c["name"] for c in competitors]

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

        # Check which competitors have profiles vs just names
        profiled = set()
        for comp in competitors:
            obs = self.knowledge.get_observations(comp["id"], limit=3)
            if len(obs) >= 3:
                profiled.add(comp["name"])

        unprofiled = [n for n in competitor_names if n not in profiled and n != self.project_name]

        prompt = f"""You are a competitive intelligence analyst deciding what to research next.

Project: {self.project_name}
Known competitors: {json.dumps(competitor_names)}
Competitors WITH deep profiles: {json.dumps(list(profiled))}
Competitors WITHOUT profiles yet: {json.dumps(unprofiled)}

Already done:
{chr(10).join(completed_descriptions[-5:]) or "(none)"}

Pick 2-3 SPECIFIC next tasks. Prioritize:
1. Deep-dive profiles on unprofiled competitors (most valuable)
2. Feature comparisons on specific features (e.g. "loyalty programs", "cancellation flows")
3. Refreshing stale profiles only if >7 days old

Return ONLY a JSON array. Each item:
{{"priority": 7-9, "category": "competitor_profile"|"feature_comparison"|"competitor_refresh", "description": "specific task", "context_json": {{"competitor_name": "X"}} or {{"feature_name": "Y", "competitors": ["A","B"]}}}}"""

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
        for name in competitor_names[:3]:
            fallback.append({
                "priority": 7,
                "category": "competitor_profile",
                "description": f"Create a detailed profile for {name}",
                "context_json": {"competitor_name": name},
            })
        if not fallback:
            fallback.append({
                "priority": 8,
                "category": "competitor_discovery",
                "description": (
                    "Search for additional competitors using alternative queries"
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
                "name": "search_app_store",
                "description": "Search for apps on Google Play Store.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query for apps.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of apps to return.",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "get_app_info",
                "description": "Get detailed info about a specific app from the Play Store.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "package_name": {
                            "type": "string",
                            "description": "The app's package name (e.g. com.example.app).",
                        },
                    },
                    "required": ["package_name"],
                },
            },
            {
                "name": "save_competitor",
                "description": "Save a discovered competitor to the knowledge graph.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Competitor company name.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Brief description of the competitor.",
                        },
                        "app_package": {
                            "type": "string",
                            "description": "Android app package name, if applicable.",
                        },
                        "website": {
                            "type": "string",
                            "description": "Competitor's website URL.",
                        },
                        "metadata": {
                            "type": "object",
                            "description": "Additional metadata about the competitor.",
                        },
                    },
                    "required": ["name", "description"],
                },
            },
            {
                "name": "save_finding",
                "description": "Save a research finding or observation about an entity.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "entity_name": {
                            "type": "string",
                            "description": "Name of the entity this finding is about.",
                        },
                        "observation_type": {
                            "type": "string",
                            "description": "Type of observation.",
                            "enum": [
                                "feature_change",
                                "pricing_update",
                                "ux_change",
                                "metric",
                                "news",
                                "general",
                            ],
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
                    },
                    "required": ["entity_name", "observation_type", "content"],
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
                            "description": "Optional entity type filter (e.g. company, app).",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "generate_report",
                "description": "Generate a competitive analysis report or artifact.",
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
                            "enum": ["competitor_profile", "feature_comparison"],
                        },
                        "content_markdown": {
                            "type": "string",
                            "description": "Full report content in Markdown.",
                        },
                        "related_entity_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Names of entities this report covers.",
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
            f'You are a senior competitive intelligence analyst embedded in the '
            f'product team at {self.project_name}. You report directly to the PM.\n\n'
            f'Product: {self.project_description}\n\n'
            f'YOUR MANDATE: Produce intelligence that changes product decisions. '
            f'Generic industry facts are worthless — the PM already knows the market size. '
            f'What they need:\n\n'
            f'SPECIFIC over broad:\n'
            f'- BAD: "Booking.com is a large OTA" — everyone knows this\n'
            f'- GOOD: "Booking.com added free cancellation badges to search results on '
            f'March 15, increasing conversion 12% per their Q1 call"\n\n'
            f'ACTIONABLE over informational:\n'
            f'- BAD: "The travel market is growing at 8% CAGR"\n'
            f'- GOOD: "EaseMyTrip launched zero-commission hotel listings, undercutting '
            f'MMT on price by 5-15% for budget properties — this threatens the '
            f'Tier-2/3 growth segment"\n\n'
            f'EVIDENCE-BACKED over asserted:\n'
            f'- Always include the source URL\n'
            f'- Quote specific numbers, dates, feature names\n'
            f'- If you cannot verify something, do not save it\n\n'
            f'WHAT TO RESEARCH:\n'
            f'1. Exact feature differences (what can competitor X do that we cannot?)\n'
            f'2. Recent launches and changes (what shipped in the last 90 days?)\n'
            f'3. Pricing and monetization moves (commission changes, subscription tiers)\n'
            f'4. App store signals (rating trends, recent review complaints, update notes)\n'
            f'5. Strategic moves (acquisitions, partnerships, new markets, hiring patterns)\n'
            f'6. UX innovations (specific flows that are better/worse than ours)\n\n'
            f'WORKFLOW RULES:\n'
            f'- Search, read 2-3 pages max, extract specific findings, save, move on\n'
            f'- Do NOT rabbit-hole into 10+ searches on one topic\n'
            f'- Each save_finding must have a source_url — no unsourced assertions\n'
            f'- Call finish_work within 8-10 tool calls — be efficient\n'
            f'- Prefer recent sources (2025-2026). Skip anything older than 2023.\n\n'
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

            elif tool_name == "search_app_store":
                results = self.web.search_play_store(
                    tool_input["query"],
                    max_results=tool_input.get("max_results", 5),
                )
                return json.dumps(results, default=str)

            elif tool_name == "get_app_info":
                result = self.web.get_app_details(tool_input["package_name"])
                return json.dumps(result, default=str)

            elif tool_name == "save_competitor":
                return self._tool_save_competitor(tool_input)

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
        """Execute a work item by building a targeted prompt and running the tool loop."""
        self._current_result: dict = {
            "status": "completed",
            "summary": "Work item processed",
            "entities_created": 0,
            "observations_added": 0,
        }

        # Build a category-specific prompt
        context = item.context_json or {}
        prompt = self._build_work_prompt(item.category, item.description, context)

        logger.info(
            "[%s] Running tool loop for %s: %s",
            self.agent_type,
            item.category,
            item.description[:80],
        )

        result = self.run_tool_loop(prompt, max_iterations=15)

        # If the tool loop ended without finish_work being called,
        # still return a valid result based on what was accomplished
        if self._current_result["summary"] == "Work item processed":
            self._current_result["summary"] = (
                result.get("final_response", "")[:200] or
                f"Tool loop ended after {result.get('iterations', 0)} iterations"
            )
            self._current_result["status"] = (
                "completed" if result.get("status") == "completed" else "failed"
            )

        return self._current_result

    # ------------------------------------------------------------------
    # Tool implementations (private)
    # ------------------------------------------------------------------

    def _tool_save_competitor(self, inp: dict) -> str:
        """Save a competitor company entity and optionally its app."""
        metadata = inp.get("metadata") or {}
        if inp.get("website"):
            metadata["website"] = inp["website"]

        company_id = self.knowledge.upsert_entity(
            entity_type="company",
            name=inp["name"],
            description=inp["description"],
            metadata=metadata,
        )

        # Link competitor to project via competes_with relation
        project_entities = self.knowledge.find_entities(
            entity_type="project", name_like=self.project_name
        )
        if project_entities:
            project_entity_id = project_entities[0]["id"]
        else:
            # Create a project entity if one doesn't exist
            project_entity_id = self.knowledge.upsert_entity(
                entity_type="project",
                name=self.project_name,
                description=self.project_description,
            )
        self.knowledge.add_relation(
            from_id=project_entity_id,
            to_id=company_id,
            relation_type="competes_with",
        )

        # If app package provided, create an app entity and link it
        if inp.get("app_package"):
            app_id = self.knowledge.upsert_entity(
                entity_type="app",
                name=f"{inp['name']} App",
                description=f"Android app for {inp['name']}",
                metadata={"package_name": inp["app_package"]},
            )
            self.knowledge.add_relation(
                from_id=company_id,
                to_id=app_id,
                relation_type="has_app",
            )

        self._current_result["entities_created"] = (
            self._current_result.get("entities_created", 0) + 1
        )

        return json.dumps({
            "status": "saved",
            "company_entity_id": company_id,
            "name": inp["name"],
        })

    def _tool_save_finding(self, inp: dict) -> str:
        """Save an observation about an entity."""
        # Find the entity by name
        entities = self.knowledge.find_entities(name_like=inp["entity_name"])
        if not entities:
            # Create a generic entity if not found
            entity_id = self.knowledge.upsert_entity(
                entity_type="company",
                name=inp["entity_name"],
                description="Auto-created from finding",
            )
        else:
            entity_id = entities[0]["id"]

        obs_id = self.knowledge.add_observation(
            entity_id=entity_id,
            obs_type=inp["observation_type"],
            content=inp["content"],
            evidence=inp.get("evidence"),
            source_url=inp.get("source_url"),
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
        """Generate and save a competitive analysis report."""
        # Resolve entity IDs from names
        entity_ids: list[int] = []
        for name in inp.get("related_entity_names", []):
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
                f"You have ONE job: identify the direct competitors of {self.project_name} "
                f"and save each one. Do NOT write an industry essay.\n\n"
                f"Product: {self.project_description}\n\n"
                f"Steps:\n"
                f"1. web_search for '{self.project_name} competitors' and "
                f"'{self.project_name} alternatives 2025'\n"
                f"2. For each real competitor found, call save_competitor with a "
                f"one-sentence description of what makes them different from us\n"
                f"3. Save ONE finding about our company's market position with a source URL\n"
                f"4. Call finish_work\n\n"
                f"Target: 5-8 competitors identified and saved. Do NOT research each one "
                f"deeply yet — just identify and save. Deep profiles come later."
            )

        elif category == "competitor_discovery":
            industry_entities = self.knowledge.find_entities(entity_type="company")
            known = [e["name"] for e in industry_entities]
            return (
                f"Find competitors of {self.project_name} we might have missed.\n\n"
                f"Already known: {', '.join(known) if known else 'none'}\n\n"
                f"Steps:\n"
                f"1. Search app stores for travel/booking apps in India\n"
                f"2. Search for '{self.project_name} alternatives' on comparison sites\n"
                f"3. For any NEW competitor not in our list, call save_competitor\n"
                f"4. Call finish_work\n\n"
                f"Skip companies we already know about. Focus on finding gaps."
            )

        elif category == "competitor_profile":
            competitor_name = context.get("competitor_name", description)
            return (
                f"Deep-dive on {competitor_name}. Find SPECIFIC, ACTIONABLE intelligence.\n\n"
                f"Research exactly these 5 things (one save_finding each):\n"
                f"1. RECENT MOVES: What did they launch/change in the last 90 days? "
                f"Search '{competitor_name} new features 2025 2026' and their blog/newsroom\n"
                f"2. APP SIGNALS: Search their Play Store listing. What's their rating? "
                f"What do recent 1-star reviews complain about? What's in recent update notes?\n"
                f"3. PRICING: How do they monetize? Commission rates, subscription tiers, "
                f"convenience fees. Search '{competitor_name} pricing fees commission'\n"
                f"4. FEATURE DIFF: What specific feature do they have that {self.project_name} "
                f"does NOT? Be specific — name the feature and what it does.\n"
                f"5. STRATEGIC THREAT: What is the single biggest threat this competitor "
                f"poses to {self.project_name} in the next 12 months?\n\n"
                f"After saving all 5 findings, generate a competitor_profile report "
                f"and call finish_work. Every finding MUST have a source_url."
            )

        elif category == "competitor_refresh":
            competitor_name = context.get("competitor_name", description)
            return (
                f"Check what's new with {competitor_name} since our last update.\n\n"
                f"Search for:\n"
                f"1. '{competitor_name} 2026' OR '{competitor_name} launch 2025'\n"
                f"2. Check their Play Store listing for new update notes\n"
                f"3. Search news for any acquisitions, partnerships, or pivots\n\n"
                f"Only save genuinely NEW findings. If nothing new, call finish_work "
                f"with summary 'No significant updates found'. Do not rehash old info."
            )

        elif category == "feature_comparison":
            feature = context.get("feature_name", "key features")
            competitors = context.get("competitors", [])
            return (
                f"Compare {feature} across {self.project_name} and competitors: "
                f"{', '.join(competitors) if competitors else 'all known competitors'}.\n\n"
                f"For each company, find the SPECIFIC implementation:\n"
                f"- Does this feature exist? Y/N\n"
                f"- How does it work differently?\n"
                f"- What's the user-facing difference?\n\n"
                f"Save a feature_comparison report with a markdown table. "
                f"Every claim must cite a source."
            )

        else:
            return (
                f"{description}\n\n"
                f"Be specific. Save findings with source URLs. "
                f"Call finish_work within 8 tool calls."
            )
