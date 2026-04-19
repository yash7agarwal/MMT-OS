"""SQLAlchemy ORM models for Prism.

Five tables:
- Project: an app being mapped (e.g., "MakeMyTrip")
- Screen: a captured/uploaded mobile screen with Claude-extracted metadata
- Edge: a directed transition between two screens
- TestPlan: a UAT plan generated from a feature description
- TestCase: an individual test case within a plan
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from webapp.api.db import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    app_package: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    screens: Mapped[list["Screen"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    edges: Mapped[list["Edge"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    plans: Mapped[list["TestPlan"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Screen(Base):
    __tablename__ = "screens"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_path: Mapped[str] = mapped_column(String(500), nullable=False)
    elements: Mapped[list | None] = mapped_column(JSON, nullable=True)
    context_hints: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    project: Mapped[Project] = relationship(back_populates="screens")


class Edge(Base):
    __tablename__ = "edges"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    from_screen_id: Mapped[int] = mapped_column(ForeignKey("screens.id", ondelete="CASCADE"))
    to_screen_id: Mapped[int] = mapped_column(ForeignKey("screens.id", ondelete="CASCADE"))
    trigger: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped[Project] = relationship(back_populates="edges")


class TestPlan(Base):
    __tablename__ = "test_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    feature_description: Mapped[str] = mapped_column(Text, nullable=False)
    voice_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved
    # plan_type: feature_flow | design_fidelity | functional_flow | deeplink_utility | edge_cases
    plan_type: Mapped[str] = mapped_column(String(50), default="feature_flow")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped[Project] = relationship(back_populates="plans")
    cases: Mapped[list["TestCase"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class TestCase(Base):
    __tablename__ = "test_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("test_plans.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    target_screen_id: Mapped[int | None] = mapped_column(
        ForeignKey("screens.id", ondelete="SET NULL"), nullable=True
    )
    navigation_path: Mapped[list | None] = mapped_column(JSON, nullable=True)
    acceptance_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    branch_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | approved | removed

    plan: Mapped[TestPlan] = relationship(back_populates="cases")


# ---------------------------------------------------------------------------
# Knowledge Graph + Competitive Intelligence models
# ---------------------------------------------------------------------------


class KnowledgeEntity(Base):
    """A node in the knowledge graph — a company, app, feature, flow, etc."""
    __tablename__ = "knowledge_entities"
    __table_args__ = (
        # Atomic dedup: app treats canonical_name (lowercased name) as the
        # dedup key. Enforce it at the DB level so races can't create dupes.
        UniqueConstraint(
            "project_id", "canonical_name",
            name="uq_knowledge_entities_project_canonical",
        ),
        # Hot path: list_entities filters by (project_id, entity_type).
        Index("ix_knowledge_entities_project_type", "project_id", "entity_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    canonical_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_agent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    observations: Mapped[list["KnowledgeObservation"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )
    relations_from: Mapped[list["KnowledgeRelation"]] = relationship(
        foreign_keys="KnowledgeRelation.from_entity_id", cascade="all, delete-orphan"
    )
    screenshots: Mapped[list["KnowledgeScreenshot"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )


class KnowledgeRelation(Base):
    """A directed edge between two knowledge entities."""
    __tablename__ = "knowledge_relations"
    __table_args__ = (
        Index("ix_knowledge_relations_from", "from_entity_id"),
        Index("ix_knowledge_relations_to", "to_entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    from_entity_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE")
    )
    to_entity_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE")
    )
    relation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_agent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class KnowledgeObservation(Base):
    """A time-stamped observation about a knowledge entity."""
    __tablename__ = "knowledge_observations"
    __table_args__ = (
        Index("ix_knowledge_observations_entity_type", "entity_id", "observation_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE")
    )
    observation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_agent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_observations.id", ondelete="SET NULL"), nullable=True
    )
    # Analytical lenses: e.g. ["monetization", "growth", "product_craft"]
    lens_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)

    entity: Mapped[KnowledgeEntity] = relationship(back_populates="observations")


class KnowledgeArtifact(Base):
    """A generated artifact such as a competitor profile or trend report."""
    __tablename__ = "knowledge_artifacts"
    __table_args__ = (
        Index("ix_knowledge_artifacts_project_type", "project_id", "artifact_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    entity_ids_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    generated_by_agent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)


class KnowledgeScreenshot(Base):
    """A screenshot captured during competitive intel or flow mapping."""
    __tablename__ = "knowledge_screenshots"
    __table_args__ = (
        Index("ix_knowledge_screenshots_project", "project_id"),
        Index("ix_knowledge_screenshots_entity", "entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    thumbnail_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    screen_label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    app_package: Mapped[str | None] = mapped_column(String(200), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ui_elements_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    visual_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    captured_by_agent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    flow_session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sequence_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    entity: Mapped[KnowledgeEntity | None] = relationship(back_populates="screenshots")


class WorkItem(Base):
    """A unit of work for an intelligence agent."""
    __tablename__ = "work_items"
    __table_args__ = (
        # Hot path: agent pending-work query filters by (agent_type, project_id, status).
        Index("ix_work_items_agent_project_status", "agent_type", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    parent_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="SET NULL"), nullable=True
    )
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentSession(Base):
    """Tracks a single agent execution session."""
    __tablename__ = "agent_sessions"
    __table_args__ = (
        Index("ix_agent_sessions_project_started", "project_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    items_completed: Mapped[int] = mapped_column(Integer, default=0)
    items_failed: Mapped[int] = mapped_column(Integer, default=0)
    knowledge_added: Mapped[int] = mapped_column(Integer, default=0)
    token_usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    session_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeEmbedding(Base):
    """Vector embedding for semantic search over knowledge."""
    __tablename__ = "knowledge_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=True
    )
    observation_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_observations.id", ondelete="CASCADE"), nullable=True
    )
    artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_artifacts.id", ondelete="CASCADE"), nullable=True
    )
    text_chunk: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
