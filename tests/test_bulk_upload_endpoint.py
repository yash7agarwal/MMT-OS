"""Endpoint integration test for bulk-upload (v0.21.4).

This is the test that would have caught 3× user-visible 502s earlier:
it hits the real endpoint with real synthetic PDFs and asserts wall-clock
latency. Pure unit tests with mocked LLMs hid the failure mode because
they finished in milliseconds.

Per the workspace SDLC rule (`feedback_sdlc_enforcement.md`), feature_endpoint
tasks MUST include integration coverage — not just unit. The upgraded
`/post-task-eval` enforces this.
"""
from __future__ import annotations

import io
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from webapp.api.db import Base, get_db
from webapp.api.main import app
from webapp.api.models import KnowledgeEntity, Project

try:
    from tests.fixtures.make_pdf import make_synthetic_pdf
    HAS_REPORTLAB = True
except (ImportError, RuntimeError):
    HAS_REPORTLAB = False


pytestmark = pytest.mark.xfail(
    not HAS_REPORTLAB,
    reason="reportlab not installed — pinned in requirements-dev.txt; install before running tests",
    strict=False,
)


@pytest.fixture
def client(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test_bulk.db"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # Seed: 1 project + 5 competitors
        proj = Project(name="Test Project", description="Bulk upload test")
        db.add(proj)
        db.commit()
        db.refresh(proj)
        names = ["OpenAI", "Anthropic", "Google", "Cohere", "Mistral"]
        for n in names:
            db.add(KnowledgeEntity(
                project_id=proj.id,
                entity_type="company",
                name=n,
                canonical_name=n.lower(),
                source_agent="test",
                confidence=1.0,
            ))
        db.commit()
        project_id = proj.id
    finally:
        db.close()

    def override_get_db():
        d = SessionLocal()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), project_id
    app.dependency_overrides.clear()


def _make_pdf_for(competitor_name: str, in_filename: bool = True) -> tuple[str, bytes]:
    """Build a synthetic PDF that the classifier should match to `competitor_name`.

    `in_filename=True` puts the name in the filename (filename_match wins).
    `in_filename=False` puts it only in the cover page + body (body_text_match wins).
    """
    body = (
        f"{competitor_name} Inc. — Annual Report 2024\n\n"  # cover page (first 200 chars)
        + (f"{competitor_name} " * 50)
        + " operating revenue and growth trajectory."
    )
    if in_filename:
        fn = f"{competitor_name.lower()}-10K-2024.pdf"
    else:
        fn = "Y2024.pdf"  # generic, like real-world Booking.com PDFs
    return fn, make_synthetic_pdf(body, pages=1, page_text_repeat=2)


def test_bulk_upload_under_15s_with_8_pdfs(client):
    """Core latency assertion: 8 PDFs through the endpoint must complete <15s.

    Pre-fix code (with inline LLM classification) would blow this budget on
    Railway and produce CD8 502s. Even on a local TestClient with no network
    overhead, classify-then-write should fit comfortably under 5s for 8 files;
    we set 15s to give room for slow CI runners.
    """
    c, project_id = client
    files = [
        _make_pdf_for("OpenAI", in_filename=True),
        _make_pdf_for("Anthropic", in_filename=True),
        _make_pdf_for("Google", in_filename=True),
        _make_pdf_for("Cohere", in_filename=False),  # body-only signal
        _make_pdf_for("Mistral", in_filename=False),
        ("noise.pdf", make_synthetic_pdf("Generic industry overview no companies named " * 50)),
        ("openai-Q3-2024.pdf", _make_pdf_for("OpenAI", in_filename=True)[1]),
        ("anthropic-FY24.pdf", _make_pdf_for("Anthropic", in_filename=True)[1]),
    ]
    multipart = [("files", (fn, io.BytesIO(blob), "application/pdf")) for fn, blob in files]

    start = time.perf_counter()
    r = c.post(
        f"/api/knowledge/projects/{project_id}/bulk-upload-reports",
        files=multipart,
    )
    elapsed = time.perf_counter() - start

    assert r.status_code == 200, f"got {r.status_code}: {r.text[:200]}"
    body = r.json()
    total = body["matched_count"] + body["unmatched_count"] + body["failed_count"] + body.get("deferred_count", 0)
    assert total == 8, f"expected 8 results, got {total}: {body}"
    # Most files have name in filename → should match deterministically.
    assert body["matched_count"] >= 5, f"expected ≥5 matched, got {body['matched_count']}"
    assert elapsed < 15.0, f"bulk upload took {elapsed:.2f}s — exceeds 15s budget"


def test_bulk_upload_rejects_non_pdf_magic_bytes(client):
    """A `.pdf` file that's actually HTML must be rejected by magic-byte check.

    Pins must-fix #4 from code review: pre-extraction sanity gate prevents
    pypdf from being invoked on garbage and absorbing latency on bad input.
    """
    c, project_id = client
    fake_pdf = b"<html><body>not a pdf</body></html>"
    real_pdf = make_synthetic_pdf("OpenAI annual report " * 30)

    multipart = [
        ("files", ("fake.pdf", io.BytesIO(fake_pdf), "application/pdf")),
        ("files", ("openai-real.pdf", io.BytesIO(real_pdf), "application/pdf")),
    ]
    r = c.post(
        f"/api/knowledge/projects/{project_id}/bulk-upload-reports",
        files=multipart,
    )
    assert r.status_code == 200
    body = r.json()
    failed_filenames = [f["filename"] for f in body.get("failed", [])]
    assert "fake.pdf" in failed_filenames, f"magic-byte check should reject fake.pdf, got: {body}"
    # And the real one should still process.
    matched_filenames = [m["filename"] for m in body.get("matched", [])]
    assert "openai-real.pdf" in matched_filenames or any("openai" in m["filename"].lower() for m in body.get("matched", []))


def test_bulk_upload_industry_report_lands_unmatched(client):
    """A generic industry report (no competitor co-signal) must NOT auto-match.

    Pins must-fix #1: an "AI Industry Report" mentioning OpenAI 100× and no
    structural co-signal (no name in filename / cover page / SEC marker)
    must land in `unmatched`, not `matched`. This is the failure mode the
    code reviewer caught.
    """
    c, project_id = client
    # Cover page is industry-framed, no company name in first 200 chars,
    # no SEC marker, filename also generic.
    head = "STATE OF AI 2025 — Industry Outlook. " + "X" * 200
    body = head + (" OpenAI " * 100) + (" Anthropic " * 30)
    pdf = make_synthetic_pdf(body, pages=1)
    multipart = [("files", ("ai-industry-report-2025.pdf", io.BytesIO(pdf), "application/pdf"))]

    r = c.post(
        f"/api/knowledge/projects/{project_id}/bulk-upload-reports",
        files=multipart,
    )
    assert r.status_code == 200
    body_resp = r.json()
    assert body_resp["matched_count"] == 0, (
        f"industry report must not auto-attribute, got: {body_resp.get('matched')}"
    )
    assert body_resp["unmatched_count"] == 1


def test_bulk_upload_returns_synthesizing_flag(client):
    """Manifest contract: when synthesis is kicked off, `synthesizing: True`."""
    c, project_id = client
    real_pdf = make_synthetic_pdf("OpenAI Inc. annual report " * 50)
    multipart = [("files", ("openai-10K.pdf", io.BytesIO(real_pdf), "application/pdf"))]
    r = c.post(
        f"/api/knowledge/projects/{project_id}/bulk-upload-reports",
        files=multipart,
    )
    assert r.status_code == 200
    body = r.json()
    # When `auto_synthesize=True` (default) and a file matches, synthesis kicks off.
    assert "synthesizing" in body
    if body["matched_count"] > 0:
        assert body["synthesizing"] is True
        assert body.get("synthesizing_count", 0) >= 1
