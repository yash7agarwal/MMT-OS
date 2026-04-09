"""
agent/use_case_registry.py — Use case registry and coverage validation

Tracks registered test cases per feature, validates scenario coverage before
a UAT run, and enforces a pre-flight gate.

The registry is persisted as JSON at memory/use_cases.json and supports:
  - Registering use cases with metadata (severity, category, acceptance criteria)
  - Coverage validation against generated scenarios (semantic via Claude, or
    keyword fallback when ANTHROPIC_API_KEY is not set)
  - Auto-bootstrap: register scenarios from an initial run
  - Markdown checklist export
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

# Resolve REGISTRY_PATH relative to the project root (parent of agent/)
_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_REGISTRY_PATH = _PROJECT_ROOT / "memory" / "use_cases.json"


# ------------------------------------------------------------------
# Type alias for the coverage report dict
# ------------------------------------------------------------------

class CoverageReport(TypedDict):
    total_registered: int
    covered: int
    uncovered: list[str]
    coverage_pct: float
    critical_uncovered: list[str]
    gate_pass: bool


# ------------------------------------------------------------------
# UseCaseRegistry
# ------------------------------------------------------------------

class UseCaseRegistry:
    """
    Persistent registry of expected use cases for tested features.

    Backed by memory/use_cases.json. All mutating operations save atomically
    (write to a tmp file then rename) to prevent corruption.
    """

    REGISTRY_PATH = str(_DEFAULT_REGISTRY_PATH)

    def __init__(self, registry_path: str | None = None):
        self._path = Path(registry_path or self.REGISTRY_PATH)
        self._data: dict = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        """Load registry from disk. Creates an empty one if the file is missing."""
        if not self._path.exists():
            logger.info(
                f"[UseCaseRegistry] Registry not found at {self._path}. "
                "Starting with empty registry."
            )
            return {"use_cases": [], "last_updated": None}
        try:
            with open(self._path) as f:
                data = json.load(f)
            if "use_cases" not in data:
                data["use_cases"] = []
            logger.info(
                f"[UseCaseRegistry] Loaded {len(data['use_cases'])} use cases from {self._path}"
            )
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"[UseCaseRegistry] Failed to load registry: {e}. Using empty registry.")
            return {"use_cases": [], "last_updated": None}

    def _save(self) -> None:
        """Atomically write registry to disk."""
        self._data["last_updated"] = datetime.now(timezone.utc).isoformat()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary file in the same directory, then rename
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, suffix=".tmp", prefix=".use_cases_"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            # Clean up the tmp file if something went wrong
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_use_case(
        self,
        feature: str,
        name: str,
        description: str,
        category: str,
        severity: str,
        acceptance_criteria: str = "",
    ) -> str:
        """
        Add a use case and persist to disk.

        Returns the auto-generated use_case_id (a URL-safe slug).
        """
        use_case_id = self._make_id(feature, name)

        # Deduplicate: if id already exists, update in place
        for existing in self._data["use_cases"]:
            if existing["id"] == use_case_id:
                logger.info(f"[UseCaseRegistry] Updating existing use case: {use_case_id}")
                existing.update(
                    {
                        "feature": feature,
                        "name": name,
                        "description": description,
                        "category": category,
                        "severity": severity,
                        "acceptance_criteria": acceptance_criteria,
                    }
                )
                self._save()
                return use_case_id

        record = {
            "id": use_case_id,
            "feature": feature,
            "name": name,
            "description": description,
            "category": category,
            "severity": severity,
            "acceptance_criteria": acceptance_criteria,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_run": None,
            "last_result": None,
        }
        self._data["use_cases"].append(record)
        self._save()
        logger.info(f"[UseCaseRegistry] Registered use case: {use_case_id}")
        return use_case_id

    def get_for_feature(self, feature: str) -> list[dict]:
        """Return all registered use cases for a feature (fuzzy match on feature name)."""
        feature_lower = feature.lower()
        return [
            uc for uc in self._data["use_cases"]
            if self._fuzzy_match(feature_lower, uc.get("feature", "").lower())
        ]

    def update_result(self, use_case_id: str, result: str) -> None:
        """Update last_run and last_result for a use case after execution."""
        for uc in self._data["use_cases"]:
            if uc["id"] == use_case_id:
                uc["last_run"] = datetime.now(timezone.utc).isoformat()
                uc["last_result"] = result
                self._save()
                return

    # ------------------------------------------------------------------
    # Coverage validation
    # ------------------------------------------------------------------

    def validate_coverage(self, feature: str, scenarios: list[dict]) -> CoverageReport:
        """
        Check how well the generated scenarios cover registered use cases.

        Uses Claude for semantic matching when ANTHROPIC_API_KEY is available,
        falls back to keyword matching otherwise.

        Gate passes when: coverage >= 80% AND zero critical use cases are uncovered.
        """
        registered = self.get_for_feature(feature)
        if not registered:
            logger.info(
                f"[UseCaseRegistry] No registered use cases for feature '{feature}'. "
                "Coverage gate skipped."
            )
            return CoverageReport(
                total_registered=0,
                covered=0,
                uncovered=[],
                coverage_pct=100.0,
                critical_uncovered=[],
                gate_pass=True,
            )

        # Choose matching strategy
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            covered_ids = self._semantic_coverage_check(registered, scenarios)
        else:
            logger.info(
                "[UseCaseRegistry] ANTHROPIC_API_KEY not set — using keyword coverage fallback."
            )
            covered_ids = self._keyword_coverage_check(registered, scenarios)

        covered_names = [
            uc["name"] for uc in registered if uc["id"] in covered_ids
        ]
        uncovered = [
            uc["name"] for uc in registered if uc["id"] not in covered_ids
        ]
        critical_uncovered = [
            uc["name"]
            for uc in registered
            if uc["id"] not in covered_ids and uc.get("severity") == "critical"
        ]

        total = len(registered)
        covered_count = len(covered_ids)
        coverage_pct = round(covered_count / total * 100, 1) if total else 100.0
        gate_pass = coverage_pct >= 80.0 and len(critical_uncovered) == 0

        report = CoverageReport(
            total_registered=total,
            covered=covered_count,
            uncovered=uncovered,
            coverage_pct=coverage_pct,
            critical_uncovered=critical_uncovered,
            gate_pass=gate_pass,
        )

        logger.info(
            f"[UseCaseRegistry] Coverage for '{feature}': "
            f"{covered_count}/{total} ({coverage_pct}%) | "
            f"gate_pass={gate_pass} | critical_uncovered={len(critical_uncovered)}"
        )
        return report

    # ------------------------------------------------------------------
    # Auto-registration
    # ------------------------------------------------------------------

    def auto_register_from_scenarios(self, feature: str, scenarios: list[dict]) -> int:
        """
        Register scenarios not already in the registry as new use cases.

        Used to bootstrap the registry on the first run. Returns the count of
        newly registered cases.
        """
        existing_names = {
            uc["name"].lower() for uc in self.get_for_feature(feature)
        }
        newly_added = 0

        for scenario in scenarios:
            name = scenario.get("name", "").strip()
            if not name or name.lower() in existing_names:
                continue

            self.add_use_case(
                feature=feature,
                name=name,
                description=scenario.get("expected_outcome", ""),
                category=scenario.get("category", "happy_path"),
                severity=scenario.get("severity", "medium"),
                acceptance_criteria=scenario.get("expected_outcome", ""),
            )
            existing_names.add(name.lower())
            newly_added += 1

        if newly_added:
            logger.info(
                f"[UseCaseRegistry] Auto-registered {newly_added} new use cases "
                f"for feature '{feature}'."
            )
        return newly_added

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_checklist(self, feature: str) -> str:
        """Return a Markdown checklist of all use cases for the feature."""
        use_cases = self.get_for_feature(feature)
        if not use_cases:
            return f"## Use Case Checklist: {feature}\n\n_No use cases registered._\n"

        lines = [f"## Use Case Checklist: {feature}\n"]

        # Group by category
        categories: dict[str, list[dict]] = {}
        for uc in use_cases:
            cat = uc.get("category", "other")
            categories.setdefault(cat, []).append(uc)

        for cat, items in sorted(categories.items()):
            lines.append(f"\n### {cat.replace('_', ' ').title()}\n")
            for uc in items:
                severity_tag = f"[{uc.get('severity', 'medium').upper()}]"
                last_result = uc.get("last_result")
                status = "x" if last_result == "pass" else " "
                lines.append(
                    f"- [{status}] {severity_tag} **{uc['name']}**"
                    + (f" — {uc['description']}" if uc.get("description") else "")
                )
                if uc.get("acceptance_criteria"):
                    lines.append(f"  - _Acceptance_: {uc['acceptance_criteria']}")

        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_id(feature: str, name: str) -> str:
        """Generate a stable, URL-safe slug from feature + name."""
        raw = f"{feature}_{name}".lower()
        slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
        # Truncate to 64 chars max
        return slug[:64]

    @staticmethod
    def _fuzzy_match(query: str, candidate: str) -> bool:
        """
        Simple fuzzy matching: consider a match if one string contains the other,
        or if they share at least half the words.
        """
        if query in candidate or candidate in query:
            return True
        query_words = set(query.split())
        candidate_words = set(candidate.split())
        if not query_words:
            return False
        overlap = len(query_words & candidate_words)
        return overlap / len(query_words) >= 0.5

    def _keyword_coverage_check(
        self, registered: list[dict], scenarios: list[dict]
    ) -> set[str]:
        """
        Keyword-based coverage: a use case is 'covered' if at least one scenario's
        name, steps, or expected_outcome shares meaningful keywords with the use case
        name or description.
        """
        covered: set[str] = set()

        for uc in registered:
            uc_text = " ".join(
                filter(None, [uc.get("name", ""), uc.get("description", "")])
            ).lower()
            uc_keywords = set(re.findall(r"\b[a-z]{3,}\b", uc_text))

            for scenario in scenarios:
                sc_text = " ".join(
                    filter(
                        None,
                        [
                            scenario.get("name", ""),
                            scenario.get("expected_outcome", ""),
                            " ".join(scenario.get("steps", [])),
                        ],
                    )
                ).lower()
                sc_keywords = set(re.findall(r"\b[a-z]{3,}\b", sc_text))

                overlap = uc_keywords & sc_keywords
                # Coverage threshold: at least 2 shared keywords and >= 30% overlap
                if len(overlap) >= 2 and len(overlap) / max(len(uc_keywords), 1) >= 0.3:
                    covered.add(uc["id"])
                    break

        return covered

    def _semantic_coverage_check(
        self, registered: list[dict], scenarios: list[dict]
    ) -> set[str]:
        """
        Use Claude to semantically determine which registered use cases are covered
        by at least one generated scenario. Falls back to keyword matching on error.
        """
        try:
            from utils.claude_client import ask

            # Build a condensed representation of scenarios
            scenario_summaries = "\n".join(
                f"- [{i+1}] {s.get('name', '')}: {s.get('expected_outcome', '')}"
                for i, s in enumerate(scenarios[:30])
            )

            covered: set[str] = set()

            # Check in batches of 10 to stay within token limits
            batch_size = 10
            for i in range(0, len(registered), batch_size):
                batch = registered[i : i + batch_size]
                uc_list = "\n".join(
                    f"- ID={uc['id']} | Name={uc['name']} | Desc={uc.get('description', '')}"
                    for uc in batch
                )

                prompt = (
                    "You are a QA analyst. Given the registered use cases and the generated "
                    "test scenarios below, determine which use cases are semantically covered "
                    "by at least one scenario.\n\n"
                    f"REGISTERED USE CASES:\n{uc_list}\n\n"
                    f"GENERATED SCENARIOS:\n{scenario_summaries}\n\n"
                    "Return a JSON array of IDs of the use cases that ARE covered. "
                    "Example: [\"id_1\", \"id_2\"]. Return ONLY the JSON array, no prose."
                )

                raw = ask(prompt, max_tokens=1024)
                # Parse the response — handle markdown fences
                raw = raw.strip()
                if raw.startswith("```"):
                    raw = re.sub(r"^```[a-z]*\n?", "", raw)
                    raw = re.sub(r"\n?```$", "", raw)
                covered_ids: list[str] = json.loads(raw)
                covered.update(covered_ids)

            return covered

        except Exception as e:
            logger.warning(
                f"[UseCaseRegistry] Semantic coverage check failed: {e}. "
                "Falling back to keyword matching."
            )
            return self._keyword_coverage_check(registered, scenarios)
