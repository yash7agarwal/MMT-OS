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
    figma_file_id: str | None = None  # if set, generate design-fidelity cases
    plan_type: str | None = None  # feature_flow | design_fidelity | functional_flow | deeplink_utility | edge_cases


class SuiteCreate(BaseModel):
    """Request body for POST /api/projects/{id}/plans/suite — generates ALL plan types."""
    feature_description: str
    figma_file_id: str | None = None
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
    plan_type: str = "feature_flow"
    created_at: datetime
    cases: list[TestCaseOut] = []


# ---------- UAT Run ----------

class UatRunCreate(BaseModel):
    """Request body for POST /api/projects/{id}/uat/runs"""
    apk_path: str | None = None  # optional — skip install if None
    figma_file_id: str
    feature_description: str | None = None
    skip_install: bool = False


class UatFrameResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    run_id: int
    figma_frame_name: str
    figma_node_id: str
    figma_image_path: str | None
    app_screenshot_path: str | None
    diff_image_path: str | None
    match_score: float | None
    verdict: str
    issues: list[Any] | None
    navigation_steps: int
    elapsed_s: float | None


class UatRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    apk_path: str | None
    apk_version: str | None
    package_name: str | None
    figma_file_id: str | None
    feature_description: str | None
    status: str
    total_frames: int
    matched: int
    mismatched: int
    unreachable: int
    overall_match_score: float | None
    report_md_path: str | None
    error: str | None
    started_at: datetime
    completed_at: datetime | None
    frame_results: list[UatFrameResultOut] = []


class UatRunSummary(BaseModel):
    """Lightweight list-view summary — omits frame_results to keep the list endpoint fast."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    apk_version: str | None
    figma_file_id: str | None
    feature_description: str | None
    status: str
    total_frames: int
    matched: int
    mismatched: int
    unreachable: int
    overall_match_score: float | None
    started_at: datetime
    completed_at: datetime | None


# ---------- Figma import ----------

class FigmaImportCreate(BaseModel):
    figma_file_id: str


class FigmaFrameOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    import_id: int
    node_id: str
    name: str
    page_name: str | None
    frame_type: str
    image_path: str | None
    width: float | None
    height: float | None
    x: float | None
    y: float | None
    text_content: list[Any] | None
    colors: list[Any] | None
    fonts: list[Any] | None


class FigmaImportSummary(BaseModel):
    """Lightweight list view — omits the frames list."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    figma_file_id: str
    file_name: str | None
    status: str
    total_frames: int
    error: str | None
    imported_at: datetime
    completed_at: datetime | None


class FigmaImportOut(FigmaImportSummary):
    """Full detail — includes all frames."""
    frames: list[FigmaFrameOut] = []
