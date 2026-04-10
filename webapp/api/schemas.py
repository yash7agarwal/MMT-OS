"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


# ---------- Project ----------

class ProjectCreate(BaseModel):
    name: str
    app_package: str | None = None
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    app_package: str | None = None
    description: str | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    app_package: str | None
    description: str | None
    created_at: datetime


class ProjectStats(BaseModel):
    screen_count: int
    edge_count: int
    plan_count: int


class ProjectDetail(ProjectOut):
    stats: ProjectStats


# ---------- Screen ----------

class ScreenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    display_name: str | None
    purpose: str | None
    screenshot_path: str
    elements: list[Any] | None
    context_hints: str | None = None
    discovered_at: datetime
    last_updated: datetime


class ScreenUpdate(BaseModel):
    name: str | None = None
    display_name: str | None = None
    purpose: str | None = None


class ScreenAnalysisResult(BaseModel):
    """One screen's Claude analysis output."""
    name: str
    display_name: str
    purpose: str
    elements: list[dict]
    context_hints: str | None = None  # Where this screen likely came from


# ---------- Edge ----------

class EdgeCreate(BaseModel):
    from_screen_id: int
    to_screen_id: int
    trigger: str


class EdgeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    from_screen_id: int
    to_screen_id: int
    trigger: str


class InferredEdge(BaseModel):
    """An edge proposed by the flow inference service, pending user approval."""
    from_screen_id: int
    to_screen_id: int
    trigger: str
    confidence: float  # 0-1
    reasoning: str


class FlowInferenceResult(BaseModel):
    proposed_edges: list[InferredEdge]
    home_screen_id: int | None
    branches: list[dict]  # [{"name": "By Night vs By Hour", "screen_ids": [...]}, ...]


# ---------- Test plan ----------

class TestPlanCreate(BaseModel):
    feature_description: str
    voice_transcript: str | None = None


class TestCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    plan_id: int
    title: str
    target_screen_id: int | None
    navigation_path: list | None
    acceptance_criteria: str
    branch_label: str | None
    status: str


class TestCaseUpdate(BaseModel):
    title: str | None = None
    acceptance_criteria: str | None = None
    branch_label: str | None = None
    status: str | None = None


class TestPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    feature_description: str
    voice_transcript: str | None
    status: str
    created_at: datetime
    cases: list[TestCaseOut] = []
