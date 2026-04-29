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
            {
                "priority": 7,
                "category": "contrarian_discovery",
                "description": (
                    "Identify INDIRECT and contrarian competitors — companies that "
                    "don't look like direct competitors but compete for the same customer need. "
                    "Think substitutes, adjacent categories, and disruptors."
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
2. Financial deep-dives on profiled competitors that lack financial data
3. Contrarian/indirect competitor discovery if not done yet
4. Feature comparisons on specific features
5. Refreshing stale profiles only if >7 days old

Return ONLY a JSON array. Each item:
{{"priority": 7-9, "category": "competitor_profile"|"financial_deep_dive"|"contrarian_discovery"|"feature_comparison"|"competitor_refresh", "description": "specific task", "context_json": {{"competitor_name": "X"}} or {{"feature_name": "Y", "competitors": ["A","B"]}}}}"""

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
                        "lenses": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Analytical lenses this finding relates to. Choose from: product_craft, growth, supply, monetization, technology, brand_trust, moat, trajectory",
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
            f'6. UX innovations (specific flows that are better/worse than ours)\n'
            f'7. FINANCIALS: Revenue, PAT, market cap, YoY growth from annual reports/filings\n'
            f'8. INDIRECT COMPETITORS: Think contrarian — who competes for the same need '
            f'through substitutes, adjacent categories, or platform plays?\n\n'
            f'WORKFLOW RULES:\n'
            f'- Search, read 2-3 pages max, extract specific findings, save, move on\n'
            f'- Do NOT rabbit-hole into 10+ searches on one topic\n'
            f'- Each save_finding must have a source_url — no unsourced assertions\n'
            f'- Every save_finding MUST include a "lenses" array with 1-3 lens tags from:\n'
            f'  product_craft, growth, supply, monetization, technology, brand_trust, moat, trajectory\n'
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
        """Execute a work item — uses efficient research (1-2 LLM calls) when possible."""
        self._current_result: dict = {
            "status": "completed",
            "summary": "Work item processed",
            "entities_created": 0,
            "observations_added": 0,
        }

        context = item.context_json or {}

        # v0.19.0: LLM-as-search for the discovery work-item categories.
        # User feedback: "if I ask Claude or GPT 'who are the competitors of
        # company XYZ' they answer from training data — why does Prism need
        # a Tavily call?" — right. For named-entity discovery, training data
        # is more accurate than search drift, search-cost-free, and survives
        # search-provider quota exhaustion. Search remains the path for
        # dated/quantified/regulatory work items below.
        if item.category in ("industry_identification", "contrarian_discovery"):
            return self._llm_discover_competitors(item)

        # Use efficient researcher for competitor profiles (1-2 LLM calls instead of 10-15)
        if item.category in ("competitor_profile", "financial_deep_dive"):
            competitor_name = context.get("competitor_name") or item.description.split("for ")[-1].split(",")[0].strip()
            logger.info(f"[{self.agent_type}] Efficient research for: {competitor_name}")

            # Self-extraction guard — refuse to "research a competitor"
            # whose name is the project itself. Caught early so we don't
            # spin up an LLM call + DB write for a guaranteed-bad result.
            from agent.extraction_guard import validate_extraction
            guard = validate_extraction(competitor_name, "company", self.project_name)
            if not guard.ok:
                logger.warning(f"[competitive_intel] dropped competitor work item: {guard.reason}")
                self._current_result = {
                    "status": "completed",
                    "summary": f"Rejected: {guard.reason}",
                    "entities_created": 0,
                    "observations_added": 0,
                }
                return self._current_result

            from agent.efficient_researcher import research_competitor
            result = research_competitor(competitor_name, self.project_name, self.project_description)

            # Resolve the target company entity ONCE for this work item. The
            # lookup must filter by entity_type='company' — without it, a sibling
            # App entity (e.g. "Blinkit App") created by ux_intel would be
            # picked first, and all profile observations would silently leak
            # onto the wrong entity. That bug is why `/competitors` reported
            # many competitors at 10% confidence while their profile work
            # items claimed "Profiled X: N findings" with N>0.
            company_matches = self.knowledge.find_entities(
                entity_type="company", name_like=competitor_name,
            )
            if company_matches:
                target_company_id = company_matches[0]["id"]
            else:
                target_company_id = self.knowledge.upsert_entity(
                    "company", competitor_name, f"Competitor of {self.project_name}",
                )

            # Save findings to knowledge graph
            for finding in result.get("findings", []):
                self.knowledge.add_observation(
                    entity_id=target_company_id,
                    obs_type=finding.get("type", "general"),
                    content=finding.get("content", ""),
                    source_url=finding.get("source_url"),
                    lens_tags=finding.get("lenses"),
                )

            # Save profile report as artifact
            if result.get("profile_md"):
                self.knowledge.save_artifact(
                    artifact_type="competitor_profile",
                    title=f"{competitor_name} — Competitive Profile",
                    content_md=result["profile_md"],
                    entity_ids=[target_company_id],
                )

            self._current_result = {
                "status": "completed",
                "summary": f"Profiled {competitor_name}: {result.get('observations_added', 0)} findings",
                "entities_created": result.get("entities_created", 0),
                "observations_added": result.get("observations_added", 0),
            }
            return self._current_result

        # For all other categories, use efficient research with Groq (free)
        # instead of the expensive tool-use loop
        logger.info(f"[{self.agent_type}] Efficient research for: {item.category}")

        from agent.efficient_researcher import _get_synthesizer
        from tools.web_research import WebResearcher
        synthesize = _get_synthesizer()
        web = WebResearcher()

        # Build search queries from the work item description
        search_terms = item.description[:100].replace("'", "").replace('"', '')
        queries = [
            f"{self.project_name} {search_terms}",
            f"{search_terms} 2025 2026",
        ]

        # Gather raw data
        raw_data = []
        for q in queries:
            results = web.search(q, max_results=3)
            for r in results[:2]:
                page = web.fetch_page(r.get("url", ""), max_length=3000)
                if page.get("content") and len(page["content"]) > 100:
                    raw_data.append(f"Source: {r.get('url','')}\n{page['content'][:2000]}")

        if raw_data:
            raw_text = "\n\n---\n\n".join(raw_data[:5])
            prompt = self._build_work_prompt(item.category, item.description, context)

            response = synthesize(
                f"{prompt}\n\nRAW DATA:\n{raw_text}\n\n"
                f"Extract 3-5 specific findings. Return JSON:\n"
                f'{{"findings": [{{"type": "general", "content": "...", "lenses": ["growth"], "source_url": "..."}}]}}',
                max_tokens=2000,
                system="Extract specific findings from raw data. Return valid JSON only.",
            )

            try:
                import json as _json
                text = response.strip()
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip()
                result_data = _json.loads(text)

                for finding in result_data.get("findings", []):
                    entities = self.knowledge.find_entities(name_like=finding.get("entity_name", self.project_name))
                    entity_id = entities[0]["id"] if entities else self.knowledge.upsert_entity(
                        "company", self.project_name, self.project_description
                    )
                    self.knowledge.add_observation(
                        entity_id=entity_id,
                        obs_type=finding.get("type", "general"),
                        content=finding.get("content", ""),
                        source_url=finding.get("source_url"),
                        lens_tags=finding.get("lenses"),
                    )
                    self._current_result["observations_added"] = self._current_result.get("observations_added", 0) + 1

                self._current_result["status"] = "completed"
                self._current_result["summary"] = f"Researched {item.category}: {self._current_result.get('observations_added', 0)} findings"
            except Exception as e:
                logger.warning(f"[{self.agent_type}] Synthesis parse failed: {e}")
                self._current_result["status"] = "completed"
                self._current_result["summary"] = f"Researched {item.category} (synthesis partial)"
        else:
            self._current_result["status"] = "completed"
            self._current_result["summary"] = "No relevant data found in search"

        return self._current_result

    # ------------------------------------------------------------------
    # Tool implementations (private)
    # ------------------------------------------------------------------

    def _llm_discover_competitors(self, item) -> dict:
        """v0.19.0: LLM-as-search path for industry_identification + contrarian_discovery.

        Asks Groq (free, fast) for the competitor list directly from training
        knowledge — no search-provider call. Each emitted URL gets a HEAD
        check; verified URLs become first-class observations, unverified
        ones are saved with a label so the report knows the citation is
        training-data-only.

        Bypasses the search-quota cliff that produced "0 findings overnight"
        when Tavily/Exa hit their limits. Falls through to None-return on
        complete failure; caller (this method's caller is execute_work_item)
        treats that as a failed work item.
        """
        from agent.llm_search import llm_competitor_discovery
        from agent.extraction_guard import validate_extraction

        # Pull portfolio summary (v0.16.2) when available — strongest grounding.
        portfolio = None
        try:
            from agent.research_brief import build_brief
            brief = build_brief(self.db, self.project_id)
            portfolio = brief.portfolio_summary
        except Exception as exc:
            logger.info(f"[competitive_intel:llm_discover] portfolio fetch skipped: {exc}")

        discovery = llm_competitor_discovery(
            project_name=self.project_name,
            project_description=self.project_description,
            portfolio_summary=portfolio,
        )

        # Filter to the categories matching this work item — `contrarian_discovery`
        # is specifically about indirect; `industry_identification` covers
        # both direct buckets.
        if item.category == "contrarian_discovery":
            refs = discovery.indirect
        else:
            refs = discovery.direct_local + discovery.direct_global

        saved = 0
        rejected = 0
        for ref in refs:
            guard = validate_extraction(ref.name, "company", self.project_name)
            if not guard.ok:
                logger.info(f"[competitive_intel:llm_discover] dropped: {guard.reason}")
                rejected += 1
                continue

            metadata = {"discovered_via": "llm_training_knowledge", "category": ref.category}
            if ref.url:
                metadata["url"] = ref.url
                metadata["url_status"] = ref.url_status
            company_id = self.knowledge.upsert_entity(
                entity_type="company",
                name=ref.name,
                description=ref.differentiator or f"{ref.category} competitor of {self.project_name}",
                metadata=metadata,
            )
            # Link via competes_with if a project entity exists; otherwise create one.
            project_entities = self.knowledge.find_entities(
                entity_type="project", name_like=self.project_name
            )
            if project_entities:
                project_eid = project_entities[0]["id"]
            else:
                project_eid = self.knowledge.upsert_entity(
                    entity_type="project",
                    name=self.project_name,
                    description=self.project_description,
                )
            self.knowledge.add_relation(
                from_id=project_eid, to_id=company_id, relation_type="competes_with",
            )

            # If URL verified, add an observation pointing at it. If not verified,
            # add a training-data-cited observation so the report has provenance.
            obs_content = ref.differentiator or f"{ref.category} competitor"
            if ref.url and ref.url_status == "verified":
                self.knowledge.add_observation(
                    entity_id=company_id,
                    obs_type="general",
                    content=obs_content,
                    source_url=ref.url,
                    lens_tags=["growth"],
                )
            else:
                self.knowledge.add_observation(
                    entity_id=company_id,
                    obs_type="general",
                    content=f"{obs_content} (training-data citation, URL unverified)",
                    source_url=ref.url or "",
                    lens_tags=["growth"],
                )
            saved += 1

        self._current_result = {
            "status": "completed",
            "summary": (
                f"LLM-discovered {saved} {item.category} competitors "
                f"(rejected {rejected} by guard). "
                f"local={len(discovery.direct_local)} global={len(discovery.direct_global)} "
                f"indirect={len(discovery.indirect)}"
            ),
            "entities_created": saved,
            "observations_added": saved,
        }
        return self._current_result

    def _tool_save_competitor(self, inp: dict) -> str:
        """Save a competitor company entity and optionally its app."""
        # v0.18.3: guard at the tool boundary so the LLM cannot end-run
        # the validation by emitting placeholders like "Competitor 1".
        from agent.extraction_guard import validate_extraction
        guard = validate_extraction(inp.get("name", ""), "company", self.project_name)
        if not guard.ok:
            logger.warning(f"[competitive_intel:save_competitor] dropped: {guard.reason}")
            return json.dumps({"status": "rejected", "reason": guard.reason})

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
                f"You have ONE job: identify the competitors of {self.project_name} "
                f"and save each one with a REAL company name. Do NOT write an industry essay.\n\n"
                f"Product: {self.project_description}\n\n"
                f"Search BOTH local and global angles to surface a mix:\n"
                f"  a) '{self.project_name} competitors' — local/direct rivals\n"
                f"  b) '{self.project_name} alternatives 2025' — alternatives users consider\n"
                f"  c) Category-leader queries — search for the GLOBAL leaders in this category. "
                f"     E.g. for an Indian LLM platform, that's OpenAI, Anthropic, Google Gemini, "
                f"     Mistral, Cohere — not just Indian players. For a fintech startup in India, "
                f"     also include Stripe, Square, PayPal alongside Razorpay. Customers compare "
                f"     against the global category, not just the local one.\n"
                f"  d) INDIRECT/substitute queries — what alternative ways do customers solve the "
                f"     same job? (For an OTA: airline direct booking, Google Flights. For an LLM "
                f"     platform: building in-house, off-the-shelf SaaS, etc.) Save these too.\n\n"
                f"For each real competitor found, call save_competitor with a one-sentence "
                f"description of what makes them different from us.\n\n"
                f"HARD RULES (synthesizer hallucination guard):\n"
                f"- NEVER save a placeholder name like 'Competitor 1', 'Company A', 'Player X', "
                f"  or anything containing 'from the N findings'. The guard rejects these on write; "
                f"  if you can't find a real name, save FEWER entities.\n"
                f"- NEVER save {self.project_name} itself as a competitor.\n"
                f"- Save 5-10 competitors, mix of (local direct + global category leaders + 1-2 indirect/substitutes). "
                f"  Quality over quantity — a real-named competitor with one-line description beats 5 placeholders.\n\n"
                f"Final step: save ONE finding about market position (with source_url), then "
                f"call finish_work."
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
                f"Research these areas (one save_finding per area):\n\n"
                f"1. FINANCIALS: Search '{competitor_name} revenue annual report FY2025 FY2026'. "
                f"Find: revenue, PAT/net income, YoY growth rate, market cap (if listed), "
                f"key financial ratios. Check investor presentations, earnings calls, "
                f"annual reports. Lenses: [monetization, growth]\n\n"
                f"2. SCALE & GEOGRAPHY: How big are they? Employee count, user base, "
                f"countries/cities served, new market expansions. What's their geographic "
                f"focus and where are they expanding? Lenses: [growth, supply]\n\n"
                f"3. PRODUCT & FEATURES: What specific features do they have that "
                f"{self.project_name} does NOT? App rating, recent update notes, "
                f"key product differentiators. Lenses: [product_craft, technology]\n\n"
                f"4. MONETIZATION: Commission rates, subscription tiers, convenience fees, "
                f"ARPU if available. How does their pricing compare? Lenses: [monetization]\n\n"
                f"5. STRATEGIC MOVES: Recent acquisitions, partnerships, fundraising, "
                f"leadership changes, hiring patterns. What's the single biggest threat "
                f"to {self.project_name}? Lenses: [trajectory, moat]\n\n"
                f"6. STOCK & SENTIMENT (if public): Stock price trend, analyst consensus, "
                f"institutional investor moves, short interest. "
                f"Search '{competitor_name} stock analysis 2025 2026'. Lenses: [trajectory]\n\n"
                f"After saving findings, generate a competitor_profile report "
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

        elif category == "contrarian_discovery":
            known = [e["name"] for e in self.knowledge.find_entities(entity_type="company")]
            return (
                f"Think like a contrarian strategist. Find INDIRECT competitors of "
                f"{self.project_name} that most people wouldn't consider competitors.\n\n"
                f"Product: {self.project_description}\n"
                f"Already known (direct): {', '.join(known) if known else 'none'}\n\n"
                f"Think about:\n"
                f"1. SUBSTITUTES: What alternatives solve the same customer need differently? "
                f"(e.g., for Lenskart: local optical stores, LASIK surgery, online generic "
                f"eyewear from Amazon)\n"
                f"2. ADJACENT CATEGORIES: Who competes for the same wallet share or time? "
                f"(e.g., for travel OTA: Google Flights, airline direct booking, travel "
                f"influencers on YouTube, corporate travel managers)\n"
                f"3. DISRUPTORS: Who could make {self.project_name}'s model obsolete? "
                f"(e.g., AI trip planners, super apps, government portals like IRCTC)\n"
                f"4. PLATFORM THREATS: Which platforms could absorb this functionality? "
                f"(e.g., Google, Apple Maps, WhatsApp, payment apps)\n\n"
                f"For each indirect competitor found, call save_competitor with "
                f"description explaining WHY they're a threat despite not being obvious. "
                f"Tag them with metadata: {{\"competitor_type\": \"indirect\"}}.\n"
                f"Target: 4-6 non-obvious competitors. Call finish_work when done."
            )

        elif category == "financial_deep_dive":
            competitor_name = context.get("competitor_name", description)
            return (
                f"Financial deep-dive on {competitor_name}.\n\n"
                f"Search for annual reports, investor presentations, earnings calls:\n"
                f"1. '{competitor_name} annual report 2025 2026 revenue'\n"
                f"2. '{competitor_name} investor presentation earnings'\n"
                f"3. '{competitor_name} stock analysis market cap'\n\n"
                f"Extract and save:\n"
                f"- Revenue (absolute + YoY growth %)\n"
                f"- PAT / Net income\n"
                f"- Market cap (if listed)\n"
                f"- User/customer count\n"
                f"- Key business metrics (GMV, take rate, ARPU)\n"
                f"- Geographic revenue split\n"
                f"- Segment-wise revenue\n\n"
                f"Save each data point as a finding with observation_type='metric' "
                f"and lenses=['monetization', 'growth']. Source URL is mandatory."
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
