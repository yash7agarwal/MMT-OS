"""Knowledge graph interface for autonomous agents.

Provides a clean API for agents to read/write entities, relations,
observations, artifacts, screenshots, and embeddings.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from webapp.api.models import (
    KnowledgeArtifact,
    KnowledgeEmbedding,
    KnowledgeEntity,
    KnowledgeObservation,
    KnowledgeRelation,
    KnowledgeScreenshot,
)

logger = logging.getLogger(__name__)


class KnowledgeStore:
    """Interface for agents to interact with the shared knowledge graph."""

    def __init__(self, db: Session, agent_type: str, project_id: int):
        self.db = db
        self.agent_type = agent_type
        self.project_id = project_id

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    def upsert_entity(
        self,
        entity_type: str,
        name: str,
        description: str | None = None,
        metadata: dict | None = None,
        confidence: float = 1.0,
    ) -> int:
        """Create or update a knowledge entity. Returns the entity id."""
        canonical = name.lower().strip()
        existing = (
            self.db.query(KnowledgeEntity)
            .filter(
                KnowledgeEntity.project_id == self.project_id,
                KnowledgeEntity.entity_type == entity_type,
                KnowledgeEntity.canonical_name == canonical,
            )
            .first()
        )

        if existing:
            if description is not None:
                existing.description = description
            if metadata is not None:
                merged = existing.metadata_json or {}
                merged.update(metadata)
                existing.metadata_json = merged
            existing.confidence = confidence
            existing.last_updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(existing)
            logger.debug(f"Updated entity {existing.id}: {name}")
            return existing.id

        entity = KnowledgeEntity(
            project_id=self.project_id,
            entity_type=entity_type,
            name=name,
            canonical_name=canonical,
            description=description,
            metadata_json=metadata,
            source_agent=self.agent_type,
            confidence=confidence,
        )
        self.db.add(entity)
        self.db.commit()
        self.db.refresh(entity)
        logger.debug(f"Created entity {entity.id}: {name}")
        return entity.id

    def get_entity(self, entity_id: int) -> dict | None:
        """Return entity as dict, or None if not found."""
        entity = self.db.get(KnowledgeEntity, entity_id)
        if entity is None:
            return None
        return {
            "id": entity.id,
            "project_id": entity.project_id,
            "entity_type": entity.entity_type,
            "name": entity.name,
            "canonical_name": entity.canonical_name,
            "description": entity.description,
            "metadata_json": entity.metadata_json,
            "source_agent": entity.source_agent,
            "confidence": entity.confidence,
            "first_seen_at": entity.first_seen_at.isoformat() if entity.first_seen_at else None,
            "last_updated_at": entity.last_updated_at.isoformat() if entity.last_updated_at else None,
        }

    def find_entities(
        self,
        entity_type: str | None = None,
        name_like: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Find entities by type and/or name pattern."""
        q = self.db.query(KnowledgeEntity).filter(
            KnowledgeEntity.project_id == self.project_id,
        )
        if entity_type is not None:
            q = q.filter(KnowledgeEntity.entity_type == entity_type)
        if name_like is not None:
            q = q.filter(KnowledgeEntity.name.ilike(f"%{name_like}%"))
        q = q.limit(limit)

        return [
            {
                "id": e.id,
                "entity_type": e.entity_type,
                "name": e.name,
                "canonical_name": e.canonical_name,
                "description": e.description,
                "metadata_json": e.metadata_json,
                "source_agent": e.source_agent,
                "confidence": e.confidence,
                "first_seen_at": e.first_seen_at.isoformat() if e.first_seen_at else None,
                "last_updated_at": e.last_updated_at.isoformat() if e.last_updated_at else None,
            }
            for e in q.all()
        ]

    # ------------------------------------------------------------------
    # Relations
    # ------------------------------------------------------------------

    def add_relation(
        self,
        from_id: int,
        to_id: int,
        relation_type: str,
        metadata: dict | None = None,
    ) -> int:
        """Add a directed relation between two entities. Avoids duplicates. Returns relation id."""
        existing = (
            self.db.query(KnowledgeRelation)
            .filter(
                KnowledgeRelation.from_entity_id == from_id,
                KnowledgeRelation.to_entity_id == to_id,
                KnowledgeRelation.relation_type == relation_type,
            )
            .first()
        )
        if existing:
            return existing.id

        rel = KnowledgeRelation(
            from_entity_id=from_id,
            to_entity_id=to_id,
            relation_type=relation_type,
            metadata_json=metadata,
            source_agent=self.agent_type,
        )
        self.db.add(rel)
        self.db.commit()
        self.db.refresh(rel)
        logger.debug(f"Added relation {rel.id}: {from_id} --{relation_type}--> {to_id}")
        return rel.id

    def get_related(
        self,
        entity_id: int,
        relation_type: str | None = None,
        direction: str = "both",
    ) -> list[dict]:
        """Get entities related to the given entity.

        Args:
            entity_id: The entity to find relations for.
            relation_type: Optional filter by relation type.
            direction: "from" (outgoing), "to" (incoming), or "both".

        Returns:
            List of dicts with entity_id, entity_name, entity_type,
            relation_type, and relation_metadata.
        """
        results: list[dict] = []

        if direction in ("from", "both"):
            q = (
                self.db.query(KnowledgeRelation, KnowledgeEntity)
                .join(KnowledgeEntity, KnowledgeRelation.to_entity_id == KnowledgeEntity.id)
                .filter(KnowledgeRelation.from_entity_id == entity_id)
            )
            if relation_type is not None:
                q = q.filter(KnowledgeRelation.relation_type == relation_type)
            for rel, ent in q.all():
                results.append({
                    "entity_id": ent.id,
                    "entity_name": ent.name,
                    "entity_type": ent.entity_type,
                    "relation_type": rel.relation_type,
                    "relation_metadata": rel.metadata_json,
                })

        if direction in ("to", "both"):
            q = (
                self.db.query(KnowledgeRelation, KnowledgeEntity)
                .join(KnowledgeEntity, KnowledgeRelation.from_entity_id == KnowledgeEntity.id)
                .filter(KnowledgeRelation.to_entity_id == entity_id)
            )
            if relation_type is not None:
                q = q.filter(KnowledgeRelation.relation_type == relation_type)
            for rel, ent in q.all():
                results.append({
                    "entity_id": ent.id,
                    "entity_name": ent.name,
                    "entity_type": ent.entity_type,
                    "relation_type": rel.relation_type,
                    "relation_metadata": rel.metadata_json,
                })

        return results

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    def add_observation(
        self,
        entity_id: int,
        obs_type: str,
        content: str,
        evidence: dict | None = None,
        source_url: str | None = None,
        observed_at: datetime | None = None,
    ) -> int:
        """Record an observation about an entity. Returns observation id."""
        obs = KnowledgeObservation(
            entity_id=entity_id,
            observation_type=obs_type,
            content=content,
            evidence_json=evidence,
            source_url=source_url,
            observed_at=observed_at or datetime.utcnow(),
            source_agent=self.agent_type,
        )
        self.db.add(obs)
        self.db.commit()
        self.db.refresh(obs)
        logger.debug(f"Added observation {obs.id} for entity {entity_id}")
        return obs.id

    def get_observations(
        self,
        entity_id: int,
        obs_type: str | None = None,
        since: datetime | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Get observations for an entity, ordered by observed_at desc."""
        q = self.db.query(KnowledgeObservation).filter(
            KnowledgeObservation.entity_id == entity_id,
        )
        if obs_type is not None:
            q = q.filter(KnowledgeObservation.observation_type == obs_type)
        if since is not None:
            q = q.filter(KnowledgeObservation.observed_at >= since)
        q = q.order_by(KnowledgeObservation.observed_at.desc()).limit(limit)

        return [
            {
                "id": o.id,
                "entity_id": o.entity_id,
                "observation_type": o.observation_type,
                "content": o.content,
                "evidence_json": o.evidence_json,
                "source_url": o.source_url,
                "observed_at": o.observed_at.isoformat() if o.observed_at else None,
                "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
                "source_agent": o.source_agent,
            }
            for o in q.all()
        ]

    def get_latest_observation(self, entity_id: int, obs_type: str) -> dict | None:
        """Get the most recent observation of a given type for an entity."""
        obs = (
            self.db.query(KnowledgeObservation)
            .filter(
                KnowledgeObservation.entity_id == entity_id,
                KnowledgeObservation.observation_type == obs_type,
            )
            .order_by(KnowledgeObservation.observed_at.desc())
            .first()
        )
        if obs is None:
            return None
        return {
            "id": obs.id,
            "entity_id": obs.entity_id,
            "observation_type": obs.observation_type,
            "content": obs.content,
            "evidence_json": obs.evidence_json,
            "source_url": obs.source_url,
            "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
            "recorded_at": obs.recorded_at.isoformat() if obs.recorded_at else None,
            "source_agent": obs.source_agent,
        }

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def save_artifact(
        self,
        artifact_type: str,
        title: str,
        content_md: str,
        entity_ids: list[int] | None = None,
    ) -> int:
        """Create a knowledge artifact. Returns artifact id."""
        artifact = KnowledgeArtifact(
            project_id=self.project_id,
            artifact_type=artifact_type,
            title=title,
            content_md=content_md,
            entity_ids_json=entity_ids,
            generated_by_agent=self.agent_type,
        )
        self.db.add(artifact)
        self.db.commit()
        self.db.refresh(artifact)
        logger.debug(f"Saved artifact {artifact.id}: {title}")
        return artifact.id

    def list_artifacts(
        self,
        artifact_type: str | None = None,
        stale_only: bool = False,
    ) -> list[dict]:
        """List artifacts for this project."""
        q = self.db.query(KnowledgeArtifact).filter(
            KnowledgeArtifact.project_id == self.project_id,
        )
        if artifact_type is not None:
            q = q.filter(KnowledgeArtifact.artifact_type == artifact_type)
        if stale_only:
            q = q.filter(KnowledgeArtifact.is_stale == True)  # noqa: E712

        return [
            {
                "id": a.id,
                "artifact_type": a.artifact_type,
                "title": a.title,
                "content_md": a.content_md,
                "entity_ids_json": a.entity_ids_json,
                "generated_by_agent": a.generated_by_agent,
                "generated_at": a.generated_at.isoformat() if a.generated_at else None,
                "is_stale": a.is_stale,
            }
            for a in q.all()
        ]

    def mark_stale(self, artifact_id: int) -> None:
        """Mark an artifact as stale."""
        artifact = self.db.get(KnowledgeArtifact, artifact_id)
        if artifact is not None:
            artifact.is_stale = True
            self.db.commit()
            logger.debug(f"Marked artifact {artifact_id} as stale")

    # ------------------------------------------------------------------
    # Screenshots
    # ------------------------------------------------------------------

    def save_screenshot(
        self,
        file_path: str,
        entity_id: int | None = None,
        label: str | None = None,
        app_package: str | None = None,
        app_version: str | None = None,
        ui_elements: dict | None = None,
        visual_hash: str | None = None,
        flow_session_id: str | None = None,
        sequence_order: int | None = None,
    ) -> int:
        """Save a screenshot record. Returns screenshot id."""
        screenshot = KnowledgeScreenshot(
            project_id=self.project_id,
            entity_id=entity_id,
            file_path=file_path,
            screen_label=label,
            app_package=app_package,
            app_version=app_version,
            ui_elements_json=ui_elements,
            visual_hash=visual_hash,
            captured_by_agent=self.agent_type,
            flow_session_id=flow_session_id,
            sequence_order=sequence_order,
        )
        self.db.add(screenshot)
        self.db.commit()
        self.db.refresh(screenshot)
        logger.debug(f"Saved screenshot {screenshot.id}: {file_path}")
        return screenshot.id

    def find_screenshots(
        self,
        entity_id: int | None = None,
        flow_session_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Find screenshots by entity or flow session."""
        q = self.db.query(KnowledgeScreenshot).filter(
            KnowledgeScreenshot.project_id == self.project_id,
        )
        if entity_id is not None:
            q = q.filter(KnowledgeScreenshot.entity_id == entity_id)
        if flow_session_id is not None:
            q = q.filter(KnowledgeScreenshot.flow_session_id == flow_session_id)
        q = q.order_by(KnowledgeScreenshot.sequence_order.asc().nullslast()).limit(limit)

        return [
            {
                "id": s.id,
                "entity_id": s.entity_id,
                "file_path": s.file_path,
                "screen_label": s.screen_label,
                "app_package": s.app_package,
                "app_version": s.app_version,
                "ui_elements_json": s.ui_elements_json,
                "visual_hash": s.visual_hash,
                "captured_at": s.captured_at.isoformat() if s.captured_at else None,
                "captured_by_agent": s.captured_by_agent,
                "flow_session_id": s.flow_session_id,
                "sequence_order": s.sequence_order,
            }
            for s in q.all()
        ]

    def has_visual_hash(self, visual_hash: str) -> bool:
        """Check if a screenshot with this visual hash already exists for this project."""
        return (
            self.db.query(KnowledgeScreenshot)
            .filter(
                KnowledgeScreenshot.project_id == self.project_id,
                KnowledgeScreenshot.visual_hash == visual_hash,
            )
            .first()
            is not None
        )

    # ------------------------------------------------------------------
    # Semantic Search (placeholder)
    # ------------------------------------------------------------------

    def embed_and_store(
        self,
        text: str,
        entity_id: int | None = None,
        observation_id: int | None = None,
        artifact_id: int | None = None,
    ) -> int:
        """Store text for future semantic search. Embedding is placeholder (None) for now."""
        embedding = KnowledgeEmbedding(
            entity_id=entity_id,
            observation_id=observation_id,
            artifact_id=artifact_id,
            text_chunk=text,
            embedding_blob=None,
            embedding_model=None,
        )
        self.db.add(embedding)
        self.db.commit()
        self.db.refresh(embedding)
        logger.debug(f"Stored embedding placeholder {embedding.id}")
        return embedding.id

    def semantic_search(self, query: str, top_k: int = 10) -> list[dict]:
        """Placeholder semantic search using LIKE matching on text_chunk.

        Will be replaced with real vector search once embeddings are generated.
        """
        results = (
            self.db.query(KnowledgeEmbedding)
            .filter(KnowledgeEmbedding.text_chunk.ilike(f"%{query}%"))
            .limit(top_k)
            .all()
        )
        return [
            {
                "id": r.id,
                "text_chunk": r.text_chunk,
                "entity_id": r.entity_id,
                "observation_id": r.observation_id,
                "artifact_id": r.artifact_id,
                "score": 1.0,  # placeholder score
            }
            for r in results
        ]

    # ------------------------------------------------------------------
    # Knowledge Summary
    # ------------------------------------------------------------------

    def get_knowledge_summary(self) -> dict:
        """Return a summary of the knowledge graph for this project."""
        # Entity counts by type
        type_counts = (
            self.db.query(KnowledgeEntity.entity_type, func.count(KnowledgeEntity.id))
            .filter(KnowledgeEntity.project_id == self.project_id)
            .group_by(KnowledgeEntity.entity_type)
            .all()
        )
        entity_count_by_type = {t: c for t, c in type_counts}

        # Total observations
        total_observations = (
            self.db.query(func.count(KnowledgeObservation.id))
            .join(KnowledgeEntity, KnowledgeObservation.entity_id == KnowledgeEntity.id)
            .filter(KnowledgeEntity.project_id == self.project_id)
            .scalar()
        ) or 0

        # Total artifacts
        total_artifacts = (
            self.db.query(func.count(KnowledgeArtifact.id))
            .filter(KnowledgeArtifact.project_id == self.project_id)
            .scalar()
        ) or 0

        # Stale artifact count
        stale_artifact_count = (
            self.db.query(func.count(KnowledgeArtifact.id))
            .filter(
                KnowledgeArtifact.project_id == self.project_id,
                KnowledgeArtifact.is_stale == True,  # noqa: E712
            )
            .scalar()
        ) or 0

        # Total screenshots
        total_screenshots = (
            self.db.query(func.count(KnowledgeScreenshot.id))
            .filter(KnowledgeScreenshot.project_id == self.project_id)
            .scalar()
        ) or 0

        # Recent observations (last 5)
        recent_obs = (
            self.db.query(KnowledgeObservation)
            .join(KnowledgeEntity, KnowledgeObservation.entity_id == KnowledgeEntity.id)
            .filter(KnowledgeEntity.project_id == self.project_id)
            .order_by(KnowledgeObservation.observed_at.desc())
            .limit(5)
            .all()
        )
        recent_observations = [
            {
                "id": o.id,
                "entity_id": o.entity_id,
                "observation_type": o.observation_type,
                "content": o.content[:200],
                "observed_at": o.observed_at.isoformat() if o.observed_at else None,
            }
            for o in recent_obs
        ]

        return {
            "entity_count_by_type": entity_count_by_type,
            "total_observations": total_observations,
            "total_artifacts": total_artifacts,
            "total_screenshots": total_screenshots,
            "recent_observations": recent_observations,
            "stale_artifact_count": stale_artifact_count,
        }
