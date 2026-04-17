"""Query Engine — natural language interface to the Product OS knowledge graph.

Takes a PM's question, classifies intent, retrieves relevant knowledge,
and synthesizes an evidence-backed answer.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from agent.knowledge_store import KnowledgeStore
from utils.claude_client import ask, ask_fast
from webapp.api.models import (
    KnowledgeArtifact,
    KnowledgeEntity,
    KnowledgeObservation,
    KnowledgeScreenshot,
)

logger = logging.getLogger(__name__)

VALID_INTENTS = {
    "competitor_comparison",
    "flow_lookup",
    "industry_trend",
    "feature_analysis",
    "general",
}


class QueryEngine:
    """Natural language query interface to the product knowledge graph."""

    def __init__(self, project_id: int, db: Session):
        self.project_id = project_id
        self.db = db
        self.knowledge = KnowledgeStore(db, "query_engine", project_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(self, question: str) -> dict:
        """Answer a PM's natural language question using the knowledge graph.

        Returns a dict with answer, sources, screenshots, confidence,
        data_freshness, and follow_up_questions.
        """
        logger.info(f"Processing query: {question}")

        intent = self._classify_intent(question)
        logger.info(f"Classified intent: {intent}")

        context = self._retrieve(question, intent)
        logger.info(
            f"Retrieved {len(context['entities'])} entities, "
            f"{len(context['observations'])} observations, "
            f"{len(context['artifacts'])} artifacts, "
            f"{len(context['screenshots'])} screenshots"
        )

        result = self._synthesize(question, intent, context)
        return result

    # ------------------------------------------------------------------
    # Intent Classification
    # ------------------------------------------------------------------

    def _classify_intent(self, question: str) -> str:
        """Classify the question into a retrieval intent category."""
        prompt = (
            "Classify this product intelligence question into one category:\n"
            "- competitor_comparison: comparing features/approaches across companies\n"
            "- flow_lookup: asking about a specific UX flow or user journey\n"
            "- industry_trend: asking about industry trends, market data, regulations\n"
            "- feature_analysis: asking about a specific feature across products\n"
            "- general: anything else\n\n"
            f"Question: {question}\n\n"
            "Respond with ONLY the category name, nothing else."
        )
        try:
            response = ask_fast(prompt).strip().lower()
            if response in VALID_INTENTS:
                return response
            logger.warning(f"Unrecognized intent '{response}', defaulting to general")
            return "general"
        except Exception as e:
            logger.error(f"Intent classification failed: {e}")
            return "general"

    # ------------------------------------------------------------------
    # Knowledge Retrieval
    # ------------------------------------------------------------------

    def _retrieve(self, question: str, intent: str) -> dict:
        """Retrieve relevant knowledge based on question and intent."""
        entities: list[dict] = []
        observations: list[dict] = []
        artifacts: list[dict] = []
        screenshots: list[dict] = []

        if intent == "competitor_comparison":
            entities = self.knowledge.find_entities(entity_type="company", limit=10)
            for ent in entities:
                observations.extend(
                    self.knowledge.get_observations(ent["id"], limit=20)
                )
                screenshots.extend(
                    self.knowledge.find_screenshots(entity_id=ent["id"], limit=10)
                )
            artifacts = self.knowledge.list_artifacts(artifact_type="competitor_profile")
            artifacts.extend(
                self.knowledge.list_artifacts(artifact_type="feature_comparison")
            )

        elif intent == "flow_lookup":
            # Extract keywords from question for name matching
            keywords = self._extract_keywords(question)
            entities = self.knowledge.find_entities(entity_type="flow", limit=10)
            for kw in keywords:
                matched = self.knowledge.find_entities(
                    entity_type="flow", name_like=kw, limit=5
                )
                for m in matched:
                    if m["id"] not in {e["id"] for e in entities}:
                        entities.append(m)
            for ent in entities[:10]:
                observations.extend(
                    self.knowledge.get_observations(ent["id"], limit=20)
                )
                screenshots.extend(
                    self.knowledge.find_screenshots(entity_id=ent["id"], limit=10)
                )
            artifacts = self.knowledge.list_artifacts(artifact_type="flow_map")
            artifacts.extend(
                self.knowledge.list_artifacts(artifact_type="ux_journey")
            )

        elif intent == "industry_trend":
            entities = self.knowledge.find_entities(entity_type="trend", limit=10)
            for ent in entities:
                for obs_type in ("news", "regulatory", "metric"):
                    observations.extend(
                        self.knowledge.get_observations(
                            ent["id"], obs_type=obs_type, limit=7
                        )
                    )
            artifacts = self.knowledge.list_artifacts(artifact_type="trend_report")
            artifacts.extend(
                self.knowledge.list_artifacts(artifact_type="industry_overview")
            )

        elif intent == "feature_analysis":
            entities = self.knowledge.find_entities(entity_type="feature", limit=10)
            for ent in entities:
                observations.extend(
                    self.knowledge.get_observations(ent["id"], limit=20)
                )
            # Also pull in competitor entities that may have feature relations
            competitor_entities = self.knowledge.find_entities(
                entity_type="company", limit=10
            )
            entities.extend(competitor_entities)
            artifacts = self.knowledge.list_artifacts(
                artifact_type="feature_comparison"
            )

        else:  # general
            semantic_results = self.knowledge.semantic_search(question, top_k=10)
            # Resolve entities from semantic hits
            seen_entity_ids: set[int] = set()
            for hit in semantic_results:
                if hit.get("entity_id") and hit["entity_id"] not in seen_entity_ids:
                    ent = self.knowledge.get_entity(hit["entity_id"])
                    if ent:
                        entities.append(ent)
                        seen_entity_ids.add(hit["entity_id"])
            # Keyword-based entity search as fallback
            keywords = self._extract_keywords(question)
            for kw in keywords[:3]:
                for ent in self.knowledge.find_entities(name_like=kw, limit=5):
                    if ent["id"] not in seen_entity_ids:
                        entities.append(ent)
                        seen_entity_ids.add(ent["id"])
            # Get observations for discovered entities
            for ent in entities[:10]:
                observations.extend(
                    self.knowledge.get_observations(ent["id"], limit=20)
                )

        # Apply limits
        entities = entities[:10]
        observations = observations[:20]
        artifacts = artifacts[:5]
        screenshots = screenshots[:10]

        return {
            "entities": entities,
            "observations": observations,
            "artifacts": artifacts,
            "screenshots": screenshots,
        }

    # ------------------------------------------------------------------
    # Answer Synthesis
    # ------------------------------------------------------------------

    def _synthesize(self, question: str, intent: str, context: dict) -> dict:
        """Synthesize a final answer from retrieved knowledge."""
        # Format context sections for the prompt
        entities_text = self._format_entities(context["entities"])
        observations_text = self._format_observations(context["observations"])
        artifacts_text = self._format_artifacts(context["artifacts"])
        screenshots_text = self._format_screenshots(context["screenshots"])

        prompt = (
            "You are a Product Intelligence Analyst. Answer the PM's question "
            "using ONLY the knowledge base context below. Be specific, "
            "evidence-backed, and actionable.\n\n"
            "## Knowledge Context\n\n"
            f"### Entities\n{entities_text}\n\n"
            f"### Recent Observations\n{observations_text}\n\n"
            f"### Reports\n{artifacts_text}\n\n"
            f"### Screenshots Available\n{screenshots_text}\n\n"
            f"## Question\n{question}\n\n"
            "## Instructions\n"
            "1. Answer the question directly and concisely\n"
            "2. Cite specific entities and observations as evidence\n"
            "3. If information is incomplete or stale, say so explicitly\n"
            "4. Rate your confidence (0-1) based on how well the knowledge "
            "covers the question\n"
            "5. Suggest 2-3 follow-up questions the PM might want to ask\n\n"
            "Respond in this JSON format:\n"
            "{\n"
            '    "answer": "your markdown answer",\n'
            '    "confidence": 0.0-1.0,\n'
            '    "data_freshness": "description of how recent the data is",\n'
            '    "follow_up_questions": ["q1", "q2", "q3"]\n'
            "}"
        )

        try:
            raw_response = ask(prompt)
            parsed = self._parse_json_response(raw_response)
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            parsed = {
                "answer": "Sorry, I was unable to synthesize an answer at this time.",
                "confidence": 0.0,
                "data_freshness": self._calculate_freshness(context),
                "follow_up_questions": [],
            }

        # Build source references from context entities
        sources = [
            {
                "entity_id": ent["id"],
                "type": ent["entity_type"],
                "name": ent["name"],
            }
            for ent in context["entities"]
        ]

        # Build screenshot references from context
        screenshot_refs = [
            {
                "id": s["id"],
                "path": s["file_path"],
                "label": s.get("screen_label", ""),
            }
            for s in context["screenshots"]
        ]

        # Override freshness with calculated value if not provided by Claude
        if not parsed.get("data_freshness"):
            parsed["data_freshness"] = self._calculate_freshness(context)

        return {
            "answer": parsed.get("answer", ""),
            "sources": sources,
            "screenshots": screenshot_refs,
            "confidence": parsed.get("confidence", 0.5),
            "data_freshness": parsed.get("data_freshness", "Unknown"),
            "follow_up_questions": parsed.get("follow_up_questions", []),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _calculate_freshness(self, context: dict) -> str:
        """Compute a human-readable freshness string from context observations."""
        if not context["observations"]:
            return "No data available"

        timestamps: list[datetime] = []
        for obs in context["observations"]:
            ts = obs.get("observed_at")
            if ts:
                if isinstance(ts, str):
                    try:
                        timestamps.append(datetime.fromisoformat(ts))
                    except ValueError:
                        continue
                elif isinstance(ts, datetime):
                    timestamps.append(ts)

        if not timestamps:
            return "No data available"

        most_recent = max(timestamps)
        delta = datetime.utcnow() - most_recent
        total_seconds = int(delta.total_seconds())

        if total_seconds < 60:
            return "Last updated just now"
        elif total_seconds < 3600:
            minutes = total_seconds // 60
            unit = "minute" if minutes == 1 else "minutes"
            return f"Last updated {minutes} {unit} ago"
        elif total_seconds < 86400:
            hours = total_seconds // 3600
            unit = "hour" if hours == 1 else "hours"
            return f"Last updated {hours} {unit} ago"
        else:
            days = total_seconds // 86400
            unit = "day" if days == 1 else "days"
            return f"Last updated {days} {unit} ago"

    def _parse_json_response(self, raw: str) -> dict:
        """Parse JSON from Claude's response, handling markdown fences."""
        text = raw.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # remove opening fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse Claude response as JSON, using raw text")
            return {
                "answer": raw.strip(),
                "confidence": 0.5,
                "data_freshness": "",
                "follow_up_questions": [],
            }

    def _extract_keywords(self, question: str) -> list[str]:
        """Extract significant keywords from a question for search."""
        stop_words = {
            "a", "an", "the", "is", "are", "was", "were", "what", "how",
            "why", "when", "where", "who", "which", "do", "does", "did",
            "can", "could", "should", "would", "will", "shall", "may",
            "might", "has", "have", "had", "be", "been", "being", "to",
            "of", "in", "for", "on", "with", "at", "by", "from", "about",
            "into", "through", "during", "before", "after", "above",
            "below", "between", "and", "or", "but", "not", "no", "if",
            "then", "than", "so", "as", "it", "its", "this", "that",
            "these", "those", "my", "our", "your", "their", "me", "us",
            "them", "i", "we", "you", "he", "she", "they",
        }
        words = question.lower().split()
        return [w.strip("?.,!") for w in words if w.strip("?.,!") not in stop_words]

    def _format_entities(self, entities: list[dict]) -> str:
        if not entities:
            return "No relevant entities found."
        lines = []
        for e in entities:
            desc = e.get("description") or "No description"
            lines.append(f"- **{e['name']}** ({e['entity_type']}): {desc}")
        return "\n".join(lines)

    def _format_observations(self, observations: list[dict]) -> str:
        if not observations:
            return "No observations available."
        lines = []
        for o in observations:
            date = o.get("observed_at", "unknown date")
            source = o.get("source_url", "")
            source_str = f" | source: {source}" if source else ""
            lines.append(
                f"- [{date}] ({o.get('observation_type', 'note')}) "
                f"{o['content']}{source_str}"
            )
        return "\n".join(lines)

    def _format_artifacts(self, artifacts: list[dict]) -> str:
        if not artifacts:
            return "No reports available."
        lines = []
        for a in artifacts:
            summary = (a.get("content_md") or "")[:200]
            lines.append(f"- **{a['title']}** ({a['artifact_type']}): {summary}...")
        return "\n".join(lines)

    def _format_screenshots(self, screenshots: list[dict]) -> str:
        if not screenshots:
            return "No screenshots available."
        lines = []
        for s in screenshots:
            label = s.get("screen_label") or s.get("file_path", "unlabeled")
            lines.append(f"- {label} ({s['file_path']})")
        return "\n".join(lines)
