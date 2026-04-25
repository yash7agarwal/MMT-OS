"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, PlainSerializer, model_validator


def _serialize_utc(value: UTCDatetime) -> str:
    # DB stores naive UTC (datetime.utcnow); attach UTC so clients parse correctly.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


UTCDatetime = Annotated[datetime, PlainSerializer(_serialize_utc, return_type=str)]


# ---------- Project ----------

class ProjectCreate(BaseModel):
    name: str
    app_package: str | None = None
    description: str | None = None
    enable_intelligence: bool = False
    industry: str | None = None
    competitors_hint: str | None = None


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
    created_at: UTCDatetime


class ProjectStats(BaseModel):
    screen_count: int
    edge_count: int
    plan_count: int
    entity_count: int = 0
    observation_count: int = 0
    competitor_count: int = 0


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
    discovered_at: UTCDatetime
    last_updated: UTCDatetime


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
    figma_file_id: str | None = None  # accepted for API back-compat; design_fidelity moved to Loupe in v0.10.0
    plan_type: str | None = None  # feature_flow | functional_flow | deeplink_utility | edge_cases


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
    created_at: UTCDatetime
    cases: list[TestCaseOut] = []


# ---------- Knowledge Graph (Product OS) ----------


class KnowledgeEntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    entity_type: str
    name: str
    canonical_name: str | None
    description: str | None
    metadata_json: dict | None
    source_agent: str | None
    confidence: float
    first_seen_at: UTCDatetime
    last_updated_at: UTCDatetime
    user_signal: str | None = None
    dismissed_reason: str | None = None


class EntitySignalIn(BaseModel):
    """Request body for POST /api/knowledge/entities/{id}/signal."""
    signal: str  # "kept" | "dismissed" | "starred" | "clear"
    reason: str | None = None  # optional free-text "why" for dismissals


class CrossProjectHypothesisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_project_id: int
    target_project_id: int
    source_entity_id: int
    source_entity_name: str
    source_description: str | None
    rationale: str | None
    similarity_score: float
    status: str
    decided_at: UTCDatetime | None
    created_at: UTCDatetime


class CrossProjectSuggestIn(BaseModel):
    """POST /api/xproj/suggest — propose a hypothesis from source_entity into target_project."""
    source_entity_id: int
    target_project_id: int
    rationale: str | None = None
    similarity_score: float = 0.0


class PRDGenerateIn(BaseModel):
    """POST /api/prd/generate — kick off an async PRD synthesis for a feature."""
    project_id: int
    feature_description: str


class PRDGenerateOut(BaseModel):
    """Response shape for /api/prd/generate. The artifact content populates
    asynchronously; clients poll /api/knowledge/artifacts/{id}."""
    artifact_id: int
    status: str  # "generating" | "done"
    prism_evidence_count: int | None = None
    loupe_evidence_available: bool | None = None
    loupe_runs_matched: int | None = None


class KnowledgeEntityDetail(KnowledgeEntityOut):
    observations: list["KnowledgeObservationOut"] = []
    relations: list["KnowledgeRelationOut"] = []


class KnowledgeRelationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    from_entity_id: int
    to_entity_id: int
    relation_type: str
    metadata_json: dict | None
    source_agent: str | None
    created_at: UTCDatetime


class KnowledgeObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int
    observation_type: str
    content: str
    evidence_json: dict | None
    observed_at: UTCDatetime
    recorded_at: UTCDatetime
    source_url: str | None
    source_agent: str | None


class KnowledgeArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    artifact_type: str
    title: str
    content_md: str
    entity_ids_json: list | None
    generated_by_agent: str | None
    generated_at: UTCDatetime
    is_stale: bool


class KnowledgeScreenshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int | None
    project_id: int
    file_path: str
    thumbnail_path: str | None
    screen_label: str | None
    app_package: str | None
    app_version: str | None
    visual_hash: str | None
    captured_at: UTCDatetime
    captured_by_agent: str | None
    flow_session_id: str | None
    sequence_order: int | None


class WorkItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    agent_type: str
    priority: int
    category: str
    description: str
    status: str
    result_summary: str | None
    created_at: UTCDatetime
    started_at: UTCDatetime | None
    completed_at: UTCDatetime | None


class AgentSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    agent_type: str
    started_at: UTCDatetime
    completed_at: UTCDatetime | None
    items_completed: int
    items_failed: int
    knowledge_added: int
    session_summary: str | None
    quality_score_json: dict | None = None
    status: str = "in_progress"

    @model_validator(mode="after")
    def _derive_status(self):
        # Derived from completed_at + items_failed so the API has a clean
        # in_progress / completed / failed signal without a DB migration.
        # `failed` only when the run finished AND zero items succeeded
        # while at least one failed — matches how a user reads "did this
        # session do anything useful?"
        if self.completed_at is None:
            self.status = "in_progress"
        elif self.items_completed == 0 and self.items_failed > 0:
            self.status = "failed"
        else:
            self.status = "completed"
        return self


class KnowledgeSummary(BaseModel):
    entity_count_by_type: dict[str, int]
    total_observations: int
    total_artifacts: int
    total_screenshots: int
    stale_artifact_count: int


class ProductOSStatus(BaseModel):
    is_running: bool
    agents: dict[str, dict]  # agent_type -> {last_session, work_items_pending, etc}
    knowledge_summary: KnowledgeSummary | None


class QueryRequest(BaseModel):
    question: str
    project_id: int


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict] = []
    screenshots: list[dict] = []
    confidence: float
    data_freshness: str
    follow_up_questions: list[str] = []
