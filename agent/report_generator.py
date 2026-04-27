"""Executive report orchestrator (v0.17.0).

The single chokepoint that takes (db, project_id, options) → ReportArtifacts
(pdf_bytes, xlsx_bytes, manifest_dict). Wires together:
  - report_snapshot.build_snapshot — KG state
  - utils.loupe_client — UAT enrichment (optional, graceful skip)
  - report_synthesis — six LLM-synthesized narrative pieces (cached)
  - report_charts — three matplotlib PNGs
  - report_templates/report.html.j2 + WeasyPrint → PDF
  - report_xlsx → Excel

Caching: the manifest (narrative + content_hash) is persisted as a
KnowledgeArtifact(artifact_type='executive_report'). On subsequent
generate calls with the same content_hash, the LLM step is skipped —
binaries are re-rendered cheaply from the cached narrative. This is
what makes "instant download" UX possible without a binary store.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from agent.knowledge_store import KnowledgeStore
from agent.report_charts import (
    png_to_data_uri,
    render_impact_cascade,
    render_lens_heatmap,
    render_trend_timeline,
)
from agent.report_snapshot import ReportSnapshot, build_snapshot
from agent.report_synthesis import (
    Recommendation,
    competitive_landscape_framing,
    executive_summary,
    lens_insights_batch,
    recommendations,
    regulatory_framing,
    strategic_implications,
)
from agent.report_xlsx import generate_xlsx
from webapp.api.models import KnowledgeArtifact

logger = logging.getLogger(__name__)

ARTIFACT_TYPE = "executive_report"
TEMPLATE_DIR = Path(__file__).parent / "report_templates"


@dataclass
class ReportArtifacts:
    """What `generate_report` returns."""
    pdf_bytes: bytes | None
    xlsx_bytes: bytes | None
    manifest: dict
    content_hash: str
    artifact_id: int  # KnowledgeArtifact row id
    cached: bool      # True when narrative was reused from a prior generation


# ---------------------------------------------------------------------------
# Job progress (in-process; the route handler reads this)
# ---------------------------------------------------------------------------

@dataclass
class JobState:
    job_id: str
    project_id: int
    status: str  # queued | running | done | failed
    progress: str  # human-readable step name
    artifact_id: int | None = None
    error: str | None = None
    started_at: str = ""


_jobs: dict[str, JobState] = {}


def get_job(job_id: str) -> JobState | None:
    return _jobs.get(job_id)


def list_jobs_for_project(project_id: int) -> list[JobState]:
    return [j for j in _jobs.values() if j.project_id == project_id]


def _set_progress(job: JobState | None, msg: str):
    if job is None:
        return
    job.progress = msg
    logger.info(f"[report_generator] {job.job_id}: {msg}")


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def generate_report(
    db: Session,
    project_id: int,
    formats: list[str] | None = None,
    include_loupe: bool = True,
    job: JobState | None = None,
) -> ReportArtifacts:
    """Generate the report. Returns ReportArtifacts (binaries + manifest).

    `formats` defaults to both pdf+xlsx. Passing only one (e.g. ["xlsx"])
    skips the other binary's rendering — useful when the user re-downloads
    a single format.
    """
    formats = formats or ["pdf", "xlsx"]
    _set_progress(job, "Building snapshot…")

    snapshot = build_snapshot(db, project_id)
    content_hash = snapshot.content_hash()

    # Loupe enrichment (optional, graceful skip)
    if include_loupe:
        _set_progress(job, "Enriching with Loupe UAT data…")
        try:
            from utils.loupe_client import fetch_runs_for_project
            runs = fetch_runs_for_project(snapshot.project_name) or []
            snapshot.loupe_runs = runs
        except Exception as exc:
            logger.info(f"[report_generator] Loupe unreachable, skipping: {exc}")
            snapshot.loupe_runs = []

    # Source pool — used by the synthesizer's URL gate
    source_pool = {s["url"] for s in snapshot.source_index if s.get("url")}

    # Cache lookup — most recent artifact for this project, this type
    cached_manifest = _find_cached_manifest(db, project_id, content_hash)
    if cached_manifest:
        _set_progress(job, "Reusing cached narrative (content unchanged)…")
        narrative = cached_manifest.get("narrative") or {}
        recs = [
            Recommendation(
                title=r["title"], body=r["body"],
                evidence_urls=r.get("evidence_urls", []),
            )
            for r in cached_manifest.get("recommendations") or []
        ]
        cached = True
    else:
        _set_progress(job, "Synthesizing executive summary…")
        narrative = {
            "executive_summary": executive_summary(_to_dict(snapshot), source_pool),
        }
        _set_progress(job, "Framing competitive landscape…")
        narrative["competitive_framing"] = competitive_landscape_framing(_to_dict(snapshot), source_pool)
        _set_progress(job, "Distilling per-lens insights…")
        narrative["lens_insights"] = lens_insights_batch(_to_dict(snapshot), source_pool)
        _set_progress(job, "Framing regulatory landscape…")
        narrative["regulatory_framing"] = regulatory_framing(_to_dict(snapshot), source_pool)
        _set_progress(job, "Tracing strategic implications…")
        narrative["strategic_implications"] = strategic_implications(_to_dict(snapshot), source_pool)
        _set_progress(job, "Drafting recommendations…")
        recs = recommendations(_to_dict(snapshot), narrative, source_pool)
        cached = False

    # Render charts
    _set_progress(job, "Rendering charts…")
    charts = {
        "lens_heatmap": png_to_data_uri(render_lens_heatmap(snapshot.lens_matrix) or b""),
        "trend_timeline": png_to_data_uri(render_trend_timeline(snapshot.trends) or b""),
        "impact_cascade": png_to_data_uri(render_impact_cascade(snapshot.impact_graph) or b""),
    }

    # PDF
    pdf_bytes = None
    if "pdf" in formats:
        _set_progress(job, "Rendering PDF…")
        pdf_bytes = _render_pdf(snapshot, narrative, charts, recs)

    # XLSX
    xlsx_bytes = None
    if "xlsx" in formats:
        _set_progress(job, "Rendering Excel…")
        xlsx_bytes = generate_xlsx(snapshot)

    # Persist manifest
    manifest = {
        "version": "0.17.0",
        "project_id": project_id,
        "project_name": snapshot.project_name,
        "content_hash": content_hash,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "narrative": narrative,
        "recommendations": [
            {"title": r.title, "body": r.body, "evidence_urls": r.evidence_urls}
            for r in recs
        ],
        "stats": snapshot.stats,
        "loupe_run_count": len(snapshot.loupe_runs),
    }
    artifact_id = _persist_manifest(db, project_id, snapshot.project_name, manifest)

    if job:
        job.status = "done"
        job.artifact_id = artifact_id
        job.progress = "Complete"

    return ReportArtifacts(
        pdf_bytes=pdf_bytes,
        xlsx_bytes=xlsx_bytes,
        manifest=manifest,
        content_hash=content_hash,
        artifact_id=artifact_id,
        cached=cached,
    )


def render_from_manifest(
    db: Session,
    artifact_id: int,
    fmt: str = "pdf",
) -> bytes:
    """Re-render binary from a previously-persisted manifest.

    Used by the download endpoint: cheap (no LLM) regeneration of PDF/xlsx
    from the cached narrative + a fresh snapshot. If the project KG has
    drifted since the manifest was created (content_hash diverged), we
    re-render anyway with the cached narrative — the binary will reflect
    current data tables but use the prior narrative. Fully consistent
    output requires generate_report() rather than this fast path.
    """
    artifact = db.get(KnowledgeArtifact, artifact_id)
    if artifact is None:
        raise ValueError(f"Artifact {artifact_id} not found")
    if artifact.artifact_type != ARTIFACT_TYPE:
        raise ValueError(f"Artifact {artifact_id} is not a report (got {artifact.artifact_type!r})")

    try:
        manifest = json.loads(artifact.content_md or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Artifact {artifact_id} manifest is corrupt: {exc}")

    snapshot = build_snapshot(db, artifact.project_id)
    narrative = manifest.get("narrative", {})
    recs = [
        Recommendation(title=r["title"], body=r["body"], evidence_urls=r.get("evidence_urls", []))
        for r in manifest.get("recommendations", [])
    ]
    charts = {
        "lens_heatmap": png_to_data_uri(render_lens_heatmap(snapshot.lens_matrix) or b""),
        "trend_timeline": png_to_data_uri(render_trend_timeline(snapshot.trends) or b""),
        "impact_cascade": png_to_data_uri(render_impact_cascade(snapshot.impact_graph) or b""),
    }

    if fmt == "pdf":
        return _render_pdf(snapshot, narrative, charts, recs)
    elif fmt == "xlsx":
        return generate_xlsx(snapshot)
    else:
        raise ValueError(f"Unknown format {fmt!r}")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _to_dict(snapshot: ReportSnapshot) -> dict:
    """Snapshot → plain dict for synthesis prompt feeding."""
    from dataclasses import asdict
    return asdict(snapshot)


def _render_pdf(
    snapshot: ReportSnapshot,
    narrative: dict,
    charts: dict,
    recs: list[Recommendation],
) -> bytes:
    """Render the Jinja2 template to HTML, then HTML → PDF via WeasyPrint.

    WeasyPrint is imported lazily so module import doesn't fail on machines
    that don't have libpango (local dev without `brew install pango`).
    The PDF rendering path runs on Railway via the Dockerfile apt-installs.
    """
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("report.html.j2")
    css_path = TEMPLATE_DIR / "report.css"
    css = css_path.read_text() if css_path.exists() else ""

    html = template.render(
        ctx={
            "snapshot": _snapshot_for_template(snapshot),
            "narrative": narrative,
            "charts": charts,
            "recommendations": recs,
            "css": css,
            "generated_at_human": datetime.utcnow().strftime("%B %d, %Y · %H:%M UTC"),
            "version": "v0.17.0",
        }
    )

    # Lazy import — works on Railway (apt: libpango), fails-loud locally
    # if pango is missing (with a clear message).
    try:
        from weasyprint import HTML
    except OSError as exc:
        raise RuntimeError(
            f"WeasyPrint cannot load Pango. Install via "
            f"`apt-get install libpango-1.0-0 libpangoft2-1.0-0` (Linux) "
            f"or `brew install pango` (macOS). Original error: {exc}"
        )

    return HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf()


def _snapshot_for_template(snapshot: ReportSnapshot) -> dict:
    """Add `content_hash` to the snapshot dict (templates can't call methods)."""
    from dataclasses import asdict
    d = asdict(snapshot)
    d["content_hash"] = snapshot.content_hash()
    return d


def _find_cached_manifest(
    db: Session, project_id: int, content_hash: str
) -> dict | None:
    """Look up the most recent executive_report artifact whose manifest's
    content_hash matches. None if no cache hit."""
    rows = (
        db.query(KnowledgeArtifact)
        .filter(
            KnowledgeArtifact.project_id == project_id,
            KnowledgeArtifact.artifact_type == ARTIFACT_TYPE,
        )
        .order_by(KnowledgeArtifact.generated_at.desc())
        .limit(5)
        .all()
    )
    for row in rows:
        try:
            m = json.loads(row.content_md or "{}")
            if m.get("content_hash") == content_hash:
                return m
        except json.JSONDecodeError:
            continue
    return None


def _persist_manifest(
    db: Session, project_id: int, project_name: str, manifest: dict
) -> int:
    """Save manifest as a KnowledgeArtifact row and return its id.

    KnowledgeStore signature is (db, agent_type, project_id) — the report
    generator's "agent_type" is the report-system itself. Caught when
    persistence failed at the end of the first successful Groq-driven run.
    """
    ks = KnowledgeStore(db, "report_generator", project_id)
    title = f"Executive Report — {project_name} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    return ks.save_artifact(
        artifact_type=ARTIFACT_TYPE,
        title=title,
        content_md=json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        entity_ids=[],
    )
