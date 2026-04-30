"""Integration tests for `/classify-one-report` (v0.21.5).

Single-PDF endpoint used by the frontend's per-file iteration loop.
No synthesis triggered — pure extract + classify + save artifact.
Frontend iterates many files sequentially to render live progress.
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
    reason="reportlab not installed — pinned in requirements-dev.txt",
    strict=False,
)


@pytest.fixture
def client(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test_classify_one.db"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        proj = Project(name="Test Proj", description="t")
        db.add(proj)
        db.commit()
        db.refresh(proj)
        for n in ["OpenAI", "Anthropic", "Google"]:
            db.add(KnowledgeEntity(
                project_id=proj.id, entity_type="company",
                name=n, canonical_name=n.lower(),
                source_agent="test", confidence=1.0,
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


def test_classify_one_filename_match(client):
    """Filename has competitor name → should match deterministically."""
    c, project_id = client
    pdf = make_synthetic_pdf("OpenAI annual report 2024 " * 50)
    r = c.post(
        f"/api/knowledge/projects/{project_id}/classify-one-report",
        files=[("file", ("openai-10K-2024.pdf", io.BytesIO(pdf), "application/pdf"))],
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body["status"] in ("matched", "unmatched")
    assert body["matched_entity_name"] == "OpenAI"
    assert body["match_method"] == "filename_substring"
    assert "artifact_id" in body


def test_classify_one_body_text_match(client):
    """Filename ambiguous, but body has cover-page co-signal + dominance → body_text match."""
    c, project_id = client
    body_text = "OpenAI Inc. — Annual Report 2024. " + ("OpenAI " * 80) + " growth"
    pdf = make_synthetic_pdf(body_text, page_text_repeat=2)
    r = c.post(
        f"/api/knowledge/projects/{project_id}/classify-one-report",
        files=[("file", ("Y2024.pdf", io.BytesIO(pdf), "application/pdf"))],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["matched_entity_name"] == "OpenAI"
    assert body["match_method"] == "body_text_count"


def test_classify_one_industry_report_unmatched(client):
    """Industry report mentioning all competitors equally — no co-signal → unmatched."""
    c, project_id = client
    head = "STATE OF AI INDUSTRY 2025 — Industry Outlook " + "X" * 200
    body = head + (" OpenAI " * 30) + (" Anthropic " * 25) + (" Google " * 20)
    pdf = make_synthetic_pdf(body)
    r = c.post(
        f"/api/knowledge/projects/{project_id}/classify-one-report",
        files=[("file", ("ai-trends.pdf", io.BytesIO(pdf), "application/pdf"))],
    )
    assert r.status_code == 200
    assert r.json()["status"] == "unmatched"


def test_classify_one_magic_byte_rejection(client):
    """`.pdf` extension on non-PDF bytes — magic-byte check fails fast."""
    c, project_id = client
    fake = b"<html><body>not a pdf</body></html>"
    r = c.post(
        f"/api/knowledge/projects/{project_id}/classify-one-report",
        files=[("file", ("fake.pdf", io.BytesIO(fake), "application/pdf"))],
    )
    assert r.status_code == 422
    assert "not_a_pdf_magic_bytes" in r.text or "magic" in r.text.lower()


def test_classify_one_under_2s(client):
    """Per-file latency budget: single PDF must process in <2s on TestClient."""
    c, project_id = client
    pdf = make_synthetic_pdf("OpenAI Inc. annual report " * 100)
    start = time.perf_counter()
    r = c.post(
        f"/api/knowledge/projects/{project_id}/classify-one-report",
        files=[("file", ("openai-2024.pdf", io.BytesIO(pdf), "application/pdf"))],
    )
    elapsed = time.perf_counter() - start
    assert r.status_code == 200
    assert elapsed < 2.0, f"single-file classify took {elapsed:.2f}s — exceeds 2s budget"
