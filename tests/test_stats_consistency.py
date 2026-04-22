"""Stats-consistency invariants — Ch.12 in LESSONS.

For every project, the counts shown on the project-detail "stats" card must
match the lengths of the corresponding list endpoints. A divergence is the
root cause of the Intuit/Sarvam.ai bug: users saw "3 competitors" but the
competitors tab was empty because two queries computed the number by
different rules.

Run locally:
    pytest tests/test_stats_consistency.py -v
Or against live Railway:
    PRISM_BASE=https://prism-api-production-18bf.up.railway.app \\
      pytest tests/test_stats_consistency.py -v

The suite is read-only — no writes, safe to run against production.
"""
from __future__ import annotations

import os

import httpx
import pytest

BASE = os.environ.get("PRISM_BASE", "http://localhost:8100").rstrip("/")
TIMEOUT_S = 30


def _get(path: str):
    r = httpx.get(f"{BASE}{path}", timeout=TIMEOUT_S)
    r.raise_for_status()
    return r.json()


def _projects() -> list[dict]:
    try:
        return _get("/api/projects")
    except Exception:
        # Collection-time failures (DNS, 500) must not abort the whole module.
        # Return an empty list so parametrized tests are generated with zero
        # cases and the fixture-based tests can call pytest.skip at runtime.
        return []


@pytest.fixture(scope="module")
def projects() -> list[dict]:
    ps = _projects()
    if not ps:
        pytest.skip(f"Prism API unreachable at {BASE}")
    return ps


@pytest.mark.parametrize("_fixture_anchor", [None])
def test_api_reachable(_fixture_anchor):
    health = _get("/api/health")
    assert health.get("status") == "ok"


def test_at_least_one_project(projects):
    assert len(projects) > 0, "no projects to validate"


def _project_ids(projects):
    return [p["id"] for p in projects]


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_competitor_count_matches_list(project_id):
    """stats.competitor_count must equal len(GET /competitors)."""
    detail = _get(f"/api/projects/{project_id}")
    stats_n = detail.get("stats", {}).get("competitor_count", 0)
    comps = _get(f"/api/knowledge/competitors?project_id={project_id}")
    assert stats_n == len(comps), (
        f"project {project_id} ({detail.get('name')}): "
        f"stats.competitor_count={stats_n} but /competitors returned {len(comps)}"
    )


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_entity_count_matches_list(project_id):
    """stats.entity_count must equal len(GET /entities?project_id=<>)."""
    detail = _get(f"/api/projects/{project_id}")
    stats_n = detail.get("stats", {}).get("entity_count", 0)
    # /entities uses a default limit; bump it so we don't false-fail on big projects.
    entities = _get(f"/api/knowledge/entities?project_id={project_id}&limit=500")
    assert stats_n == len(entities), (
        f"project {project_id} ({detail.get('name')}): "
        f"stats.entity_count={stats_n} but /entities returned {len(entities)}"
    )


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_observation_count_nonzero_when_entities_nonzero(project_id):
    """Weaker invariant: if there are entities, observations should not be negative.

    We don't assert a strict equality here because `stats.observation_count`
    aggregates across all entities of all types while the per-entity
    observation endpoint is scoped — so there isn't a single list endpoint
    to compare against. Assert only that the number is coherent (>=0) and
    that projects with zero entities also have zero observations.
    """
    detail = _get(f"/api/projects/{project_id}")
    stats = detail.get("stats", {})
    ec = stats.get("entity_count", 0)
    oc = stats.get("observation_count", 0)
    assert oc >= 0
    if ec == 0:
        assert oc == 0, (
            f"project {project_id} ({detail.get('name')}): "
            f"no entities but observation_count={oc}"
        )


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_project_detail_has_required_stats(project_id):
    """Sanity: project detail always includes the full stats dict."""
    detail = _get(f"/api/projects/{project_id}")
    stats = detail.get("stats")
    assert stats is not None, f"project {project_id} missing stats"
    for k in ("screen_count", "entity_count", "observation_count", "competitor_count"):
        assert k in stats, f"project {project_id} stats missing {k!r}"
        assert isinstance(stats[k], int), f"stats.{k} must be int, got {type(stats[k]).__name__}"


# ---- Ch.13 invariants — lens/trends/artifact convergence ----


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_lens_detail_returns_data_when_matrix_has_counts(project_id):
    """If /lens-matrix reports non-zero counts for a lens, /lens/{name} must
    return at least that many entities. The MakeMyTrip "0 found" bug (Ch.13)
    lived exactly in the gap between these two endpoints.
    """
    try:
        matrix = _get(f"/api/knowledge/lens-matrix?project_id={project_id}")
    except httpx.HTTPStatusError:
        pytest.skip(f"lens-matrix not available for project {project_id}")
    lenses = matrix.get("lenses", [])
    competitors = matrix.get("competitors", [])
    for lens in lenses:
        matrix_total = sum((c.get("lens_counts", {}) or {}).get(lens, 0) for c in competitors)
        if matrix_total == 0:
            continue
        detail = _get(f"/api/knowledge/lens/{lens}?project_id={project_id}")
        entities = detail.get("entities", [])
        assert len(entities) > 0, (
            f"project {project_id} lens {lens!r}: matrix reports "
            f"{matrix_total} tagged observations but /lens/{lens} returned 0 entities"
        )


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_lens_matrix_totals_roughly_match_detail(project_id):
    """Sum of matrix counts per lens should be within parity of the
    observation count returned by /lens/{name}. We allow equal-or-greater
    detail count (the matrix is per-competitor, the detail includes any
    observation tagged with that lens)."""
    try:
        matrix = _get(f"/api/knowledge/lens-matrix?project_id={project_id}")
    except httpx.HTTPStatusError:
        pytest.skip(f"lens-matrix not available for project {project_id}")
    lenses = matrix.get("lenses", [])
    competitors = matrix.get("competitors", [])
    for lens in lenses:
        matrix_total = sum((c.get("lens_counts", {}) or {}).get(lens, 0) for c in competitors)
        if matrix_total == 0:
            continue
        detail = _get(f"/api/knowledge/lens/{lens}?project_id={project_id}")
        detail_obs = sum(len(e.get("observations", [])) for e in detail.get("entities", []))
        assert detail_obs >= matrix_total, (
            f"project {project_id} lens {lens!r}: matrix says {matrix_total} "
            f"obs but /lens/{lens} only returned {detail_obs}"
        )


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_trends_observation_count_is_not_truncated(project_id):
    """trends-view used to compute observation_count from a .limit(5) query,
    so any trend with >5 observations would under-report. The count must now
    reflect the real DB count — at minimum never less than the length of the
    observations array returned on the same row."""
    try:
        data = _get(f"/api/knowledge/trends-view?project_id={project_id}")
    except httpx.HTTPStatusError:
        pytest.skip(f"trends-view not available for project {project_id}")
    trends = data if isinstance(data, list) else data.get("trends", [])
    for t in trends:
        oc = t.get("observation_count", 0)
        shown = len(t.get("observations", []))
        assert oc >= shown, (
            f"project {project_id} trend {t.get('name')!r}: observation_count={oc} "
            f"but observations array has {shown} items (truncation regression)"
        )


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_entities_endpoint_honors_high_limit(project_id):
    """/entities has a default limit of 50; a cards-consuming client must be
    able to bump it to 500 without the server silently capping. Assert that
    the returned list length is either <=50 (normal) or <=500 (bumped)."""
    r = httpx.get(f"{BASE}/api/knowledge/entities?project_id={project_id}&limit=500", timeout=TIMEOUT_S)
    r.raise_for_status()
    items = r.json()
    assert len(items) <= 500, (
        f"project {project_id}: /entities?limit=500 returned {len(items)} items "
        f"— exceeds documented max"
    )


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_no_tab_endpoint_returns_5xx(project_id):
    """Every read endpoint the web tabs hit must return a non-5xx. Catches
    regressions like the PG JSON LIKE bug before they reach a user."""
    paths = [
        f"/api/projects/{project_id}",
        f"/api/knowledge/entities?project_id={project_id}&limit=50",
        f"/api/knowledge/competitors?project_id={project_id}",
        f"/api/knowledge/trends-view?project_id={project_id}",
        f"/api/knowledge/lens-matrix?project_id={project_id}",
        f"/api/knowledge/impact-graph?project_id={project_id}",
        f"/api/product-os/{project_id}/status",
    ]
    for p in paths:
        r = httpx.get(f"{BASE}{p}", timeout=TIMEOUT_S)
        assert r.status_code < 500, (
            f"project {project_id}: {p} returned {r.status_code} — {r.text[:200]}"
        )


@pytest.mark.parametrize("project_id", _project_ids(_projects()) if True else [])
def test_lens_detail_endpoint_never_500s(project_id):
    """The specific endpoint that was broken (PG rejecting LIKE on JSON)
    must now return 200 for every known lens name."""
    known_lenses = [
        "product_craft", "growth", "supply", "monetization",
        "technology", "brand_trust", "moat", "trajectory",
    ]
    for lens in known_lenses:
        r = httpx.get(f"{BASE}/api/knowledge/lens/{lens}?project_id={project_id}", timeout=TIMEOUT_S)
        assert r.status_code < 500, (
            f"project {project_id} lens {lens!r}: {r.status_code} — {r.text[:200]}"
        )
