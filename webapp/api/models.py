"""SQLAlchemy ORM models for AppUAT.

Five tables:
- Project: an app being mapped (e.g., "MakeMyTrip")
- Screen: a captured/uploaded mobile screen with Claude-extracted metadata
- Edge: a directed transition between two screens
- TestPlan: a UAT plan generated from a feature description
- TestCase: an individual test case within a plan
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
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
