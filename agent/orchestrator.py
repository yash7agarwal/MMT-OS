"""
agent/orchestrator.py — UAT run orchestrator

Thin coordinator that:
1. Installs builds
2. Spawns FlowExplorerAgent to map the feature
3. Generates test scenarios from the feature description + screen map
4. Spawns ScenarioRunnerAgents in parallel (max 5 at a time)
5. Runs VariantDetector to group accounts
6. Collects all results
7. Saves a structured run summary for Phase 3 evaluation

This agent holds only summaries — raw evidence stays in .tmp/evidence/
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_LOB_CONFIG_PATH = Path(__file__).parent.parent / "config" / "lob_config.json"


def _load_lob_config() -> dict:
    try:
        return json.loads(_LOB_CONFIG_PATH.read_text())
    except Exception:
        return {}


def _resolve_lob(feature_description: str, lob_config: dict) -> tuple[str | None, dict | None]:
    """Return (lob_name, lob_entry) for the best-matching LOB, or (None, None)."""
    feature_lower = feature_description.lower()
    best_lob = None
    best_score = 0
    for lob_name, entry in lob_config.items():
        score = sum(1 for kw in entry.get("keywords", []) if kw in feature_lower)
        if score > best_score:
            best_score = score
            best_lob = lob_name
    if best_lob and best_score > 0:
        return best_lob, lob_config[best_lob]
    return None, None

from agent.flow_explorer_agent import FlowExplorerAgent
from agent.health_monitor import HealthMonitor
from agent.scenario_runner_agent import ScenarioRunnerAgent
from agent.variant_detector import VariantDetector
from agent.diff_agent import DiffAgent
from agent.evaluator_agent import EvaluatorAgent
from agent.report_writer_agent import ReportWriterAgent
from agent.use_case_registry import UseCaseRegistry
from tools.android_device import AndroidDevice
from tools.apk_manager import install_apk, launch_app, get_apk_version
from tools.emulator_manager import EmulatorManager
from tools.report_generator import save_json_export, to_jira_issues, to_slack_summary
from utils.claude_client import ask
from utils.config import get

logger = logging.getLogger(__name__)

# Prompt used to generate test scenarios from feature + screen graph
_SCENARIO_GEN_SYSTEM = """\
You are a senior QA architect designing UAT scenarios for a MakeMyTrip Android app feature.
Generate a comprehensive scenario suite covering all important user flows.
Return ONLY valid JSON — no markdown fences, no prose."""

_SCENARIO_GEN_PROMPT = """\
Feature under test:
{feature_description}

Acceptance criteria:
{acceptance_criteria}

Screen graph summary (screens discovered during exploration):
{screen_summary}

Generate between 5 and 8 test scenarios as a JSON array.
Each scenario must follow this exact structure:
{{
  "name": "<short unique scenario name>",
  "category": "<one of: happy_path | edge_case | state_variant | regression>",
  "steps": ["<step 1>", "<step 2>", ...],
  "expected_outcome": "<clear description of what success looks like>",
  "severity": "<one of: critical | high | medium | low>"
}}

Rules:
- Include at least 2 happy_path, 2 edge_case, 1 regression scenario
- Steps should be concrete and actionable (e.g. "Tap the 'Search Hotels' button")
- Cover happy path, error states, boundary conditions, and back-navigation
- Prioritise critical and high severity scenarios first in the list
"""


class Orchestrator:
    """
    Top-level UAT run coordinator.

    Usage:
        orch = Orchestrator(
            candidate_apk="path/to/new.apk",
            feature_description="hotel detail gallery redesign",
            accounts=[{"id": "acc1", "email": "...", "password": "..."}],
            acceptance_criteria="Gallery should show 15 images",
        )
        summary = orch.run()
    """

    def __init__(
        self,
        candidate_apk: str,
        feature_description: str,
        accounts: list[dict],
        baseline_apk: str | None = None,
        acceptance_criteria: str = "",
        run_id: str | None = None,
    ):
        self.candidate_apk = candidate_apk
        self.baseline_apk = baseline_apk
        self.feature_description = feature_description
        self.accounts = accounts
        self.acceptance_criteria = acceptance_criteria

        # Auto-generate a run_id if not provided
        if run_id:
            self.run_id = run_id
        else:
            feature_slug = re.sub(r"[^a-z0-9]+", "_", feature_description.lower())[:30]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.run_id = f"{feature_slug}_{timestamp}"

        # Ensure reports directory exists
        Path("reports").mkdir(parents=True, exist_ok=True)

        logger.info(f"[Orchestrator] Initialized run_id={self.run_id}")

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Execute the full UAT run.

        Returns a run summary dict containing scenario results, variant groups,
        screen graph metadata, and aggregate stats.
        """
        logger.info(f"[Orchestrator] === UAT Run START: {self.run_id} ===")
        start_time = time.time()

        # Step 1 — Connect to device
        logger.info("[Orchestrator] Connecting to device...")
        device = AndroidDevice()

        # Step 2 — Install candidate APK
        logger.info(f"[Orchestrator] Installing candidate APK: {self.candidate_apk}")
        package_name = install_apk(self.candidate_apk, serial=device.serial)
        self.package_name = package_name  # store for health monitoring + summary
        apk_version = get_apk_version(self.candidate_apk)
        logger.info(
            f"[Orchestrator] Installed: {package_name} "
            f"v{apk_version.get('version_name', '?')} "
            f"(code {apk_version.get('version_code', '?')})"
        )

        # Step 3 — Launch app and run FlowExplorerAgent
        logger.info("[Orchestrator] Launching app for flow exploration...")
        launch_app(package_name, serial=device.serial)
        time.sleep(3)  # allow app to settle

        # Pre-run health check — ensure app launched cleanly before exploration
        pre_run_monitor = HealthMonitor(device, package_name, self.run_id)
        pre_run_heal = pre_run_monitor.check_and_heal("pre-run launch")
        if pre_run_heal.healed and pre_run_heal.gap_type != "APP_RUNNING":
            logger.warning(
                f"[Orchestrator] Pre-run recovery performed: "
                f"gap_type={pre_run_heal.gap_type} action={pre_run_heal.recovery_action}"
            )
        elif not pre_run_heal.healed:
            logger.error(
                f"[Orchestrator] Pre-run health check FAILED: "
                f"gap_type={pre_run_heal.gap_type} — continuing anyway, "
                "but run results may be unreliable."
            )

        # LOB routing — inject targeted navigation steps if feature maps to a known LOB
        lob_config = _load_lob_config()
        lob_name, lob_entry = _resolve_lob(self.feature_description, lob_config)
        if lob_entry:
            nav_steps = lob_entry.get("navigation_steps")
            max_depth = lob_entry.get("max_depth", get("agent.flow_explorer_max_depth", 20))
            logger.info(
                f"[Orchestrator] LOB routing: '{lob_name}' matched — "
                f"injecting {len(nav_steps)} navigation steps, max_depth={max_depth}"
            )
        else:
            nav_steps = None
            max_depth = get("agent.flow_explorer_max_depth", 20)
            logger.info("[Orchestrator] No LOB match — open-ended exploration")

        explorer = FlowExplorerAgent(
            device=device,
            feature_description=self.feature_description,
            entry_package=package_name,
            max_depth=max_depth,
            navigation_steps=nav_steps,
        )
        logger.info("[Orchestrator] Running FlowExplorerAgent...")
        screen_graph = explorer.explore()
        logger.info(
            f"[Orchestrator] FlowExplorer complete: "
            f"{screen_graph['total_screens']} screens found"
        )

        # Step 4 — Generate test scenarios
        logger.info("[Orchestrator] Generating test scenarios...")
        scenarios = self._generate_scenarios(
            self.feature_description, screen_graph, self.acceptance_criteria
        )
        logger.info(f"[Orchestrator] Generated {len(scenarios)} scenarios")

        # Step 4b — Pre-flight coverage gate
        coverage_report = self._run_preflight_gate(self.feature_description, scenarios)

        # Step 5 — Variant fingerprinting per account
        # TODO(v1): In this version, accounts are pre-logged-in manually.
        # The user must ensure each account session is active before running.
        # Future: auto-login using account credentials via the login flow.
        logger.info("[Orchestrator] Running variant fingerprinting per account...")
        variant_detector = VariantDetector(run_id=self.run_id)
        fingerprints: list[dict] = []

        for account in self.accounts:
            account_id = account["id"]
            logger.info(f"[Orchestrator] Fingerprinting account: {account_id}")
            # TODO(v1): Auto-login with account["email"] / account["password"] not yet implemented.
            # Assumes app is already showing the logged-in home screen for this account.
            try:
                fp = variant_detector.fingerprint_session(device, account_id)
                fingerprints.append(fp)
            except Exception as e:
                logger.warning(
                    f"[Orchestrator] Fingerprinting failed for {account_id}: {e}"
                )

        # Step 6 — Group accounts by variant
        variant_groups = variant_detector.group_by_variant(fingerprints)
        logger.info(f"[Orchestrator] Variant groups: {variant_groups}")

        # Step 7 — Run scenarios across all accounts
        logger.info("[Orchestrator] Running scenario suite across accounts...")
        all_results = self._run_scenarios_parallel(
            device=device,
            scenarios=scenarios,
            accounts=self.accounts,
            run_id=self.run_id,
        )

        # Step 8 — Classify findings using variant info
        logger.info("[Orchestrator] Classifying findings...")
        classified_results = self._classify_results(all_results, variant_groups, variant_detector)

        # Step 9 — Save run summary
        elapsed = round(time.time() - start_time, 1)
        summary_path = self._save_run_summary(classified_results, variant_groups, screen_graph)

        # Step 10 — Print summary stats
        stats = self._compute_stats(classified_results)
        self._print_summary(stats, elapsed, summary_path)

        run_summary = {
            "run_id": self.run_id,
            "feature": self.feature_description,
            "candidate_apk": self.candidate_apk,
            "baseline_apk": self.baseline_apk,
            "package_name": package_name,
            "apk_version": apk_version,
            "total_accounts": len(self.accounts),
            "total_scenarios": len(scenarios),
            "variant_groups": variant_groups,
            "screen_graph_summary": {
                "total_screens": screen_graph["total_screens"],
                "exploration_summary": screen_graph.get("summary", ""),
            },
            "coverage_report": coverage_report,
            "results": classified_results,
            "stats": stats,
            "elapsed_seconds": elapsed,
            "summary_path": summary_path,
        }

        # Step 11 — Phase 3: evaluate results + generate report
        report_path = self.generate_report(run_summary=run_summary, scenarios=scenarios)
        run_summary["report_path"] = report_path
        print(f"  Report              : {report_path}")

        logger.info(f"[Orchestrator] === UAT Run END: {self.run_id} ===")
        return run_summary

    # ------------------------------------------------------------------
    # Scenario generation
    # ------------------------------------------------------------------

    def _generate_scenarios(
        self,
        feature_description: str,
        screen_graph: dict,
        acceptance_criteria: str,
    ) -> list[dict]:
        """
        Use Claude to generate 10–20 test scenarios from the feature + screen graph.

        Returns a list of scenario dicts:
            {"name": str, "category": str, "steps": [str], "expected_outcome": str, "severity": str}
        """
        # Build a condensed screen summary for the prompt
        screens = screen_graph.get("screens", [])
        screen_lines = []
        for s in screens[:15]:  # cap at 15 screens to avoid prompt overflow
            elements_preview = ", ".join(s.get("key_elements", [])[:5])
            screen_lines.append(
                f"- {s['screen_id']} (depth {s['depth']}): {elements_preview}"
            )
        screen_summary = "\n".join(screen_lines) if screen_lines else "No screens discovered"

        prompt = _SCENARIO_GEN_PROMPT.format(
            feature_description=feature_description,
            acceptance_criteria=acceptance_criteria or "Not specified",
            screen_summary=screen_summary,
        )

        try:
            raw = ask(
                prompt,
                system=_SCENARIO_GEN_SYSTEM,
                max_tokens=8192,
            )
            scenarios = json.loads(raw)
            if not isinstance(scenarios, list):
                raise ValueError("Expected a JSON array of scenarios")

            # Validate and sanitise each scenario
            validated: list[dict] = []
            for s in scenarios:
                if not isinstance(s, dict):
                    continue
                validated.append(
                    {
                        "name": str(s.get("name", "Unnamed scenario")),
                        "category": str(
                            s.get("category", "happy_path")
                        ) if s.get("category") in {
                            "happy_path", "edge_case", "state_variant", "regression"
                        } else "happy_path",
                        "steps": [str(step) for step in s.get("steps", [])],
                        "expected_outcome": str(s.get("expected_outcome", "")),
                        "severity": str(
                            s.get("severity", "medium")
                        ) if s.get("severity") in {
                            "critical", "high", "medium", "low"
                        } else "medium",
                    }
                )
            logger.info(f"[Orchestrator] Parsed {len(validated)} valid scenarios")
            return validated
        except (json.JSONDecodeError, ValueError, Exception) as e:
            logger.error(f"[Orchestrator] Scenario generation failed: {e}")
            # Return a minimal fallback scenario so the run can continue
            return [
                {
                    "name": "Baseline smoke test",
                    "category": "happy_path",
                    "steps": [
                        "Take a screenshot of the current screen",
                        "Verify the feature area is visible",
                    ],
                    "expected_outcome": "Feature area is accessible and renders correctly",
                    "severity": "critical",
                }
            ]

    # ------------------------------------------------------------------
    # Pre-flight coverage gate
    # ------------------------------------------------------------------

    def _run_preflight_gate(
        self, feature: str, scenarios: list[dict]
    ) -> dict:
        """
        Run the pre-flight coverage gate between scenario generation and execution.

        1. Auto-register any new scenarios into the registry (bootstrap).
        2. Validate coverage of registered use cases against generated scenarios.
        3. Log the report. Warn (do not block) on gate failure in v1.

        # TODO(v2): Make gate_fail=True block the run by raising RuntimeError.

        Returns the CoverageReport dict so it can be saved in the run summary.
        """
        try:
            registry = UseCaseRegistry()

            # Bootstrap: register new scenarios that aren't in the registry yet
            new_cases = registry.auto_register_from_scenarios(feature, scenarios)
            if new_cases:
                logger.info(
                    f"[Orchestrator] Pre-flight: bootstrapped {new_cases} new use cases "
                    f"into registry for feature '{feature}'."
                )

            # Validate coverage
            coverage = registry.validate_coverage(feature, scenarios)
            logger.info(
                f"[Orchestrator] Pre-flight coverage report: "
                f"total_registered={coverage['total_registered']} "
                f"covered={coverage['covered']} "
                f"coverage_pct={coverage['coverage_pct']}% "
                f"gate_pass={coverage['gate_pass']}"
            )

            if not coverage["gate_pass"]:
                uncovered_list = ", ".join(coverage["uncovered"][:10])
                critical_list = ", ".join(coverage["critical_uncovered"])
                logger.warning(
                    f"[Orchestrator] PRE-FLIGHT GATE FAILED — coverage {coverage['coverage_pct']}% "
                    f"(threshold 80%). Uncovered: [{uncovered_list}]. "
                    f"Critical uncovered: [{critical_list}]. "
                    "Continuing run (warn-only in v1)."
                    # TODO(v2): raise RuntimeError here to block the run.
                )
            else:
                logger.info("[Orchestrator] Pre-flight gate PASSED.")

            return dict(coverage)

        except Exception as e:
            logger.error(
                f"[Orchestrator] Pre-flight gate raised an unexpected error: {e}. "
                "Skipping coverage check and continuing run."
            )
            return {
                "total_registered": 0,
                "covered": 0,
                "uncovered": [],
                "coverage_pct": 100.0,
                "critical_uncovered": [],
                "gate_pass": True,
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # Figma-first UAT entry point
    # ------------------------------------------------------------------

    @classmethod
    def run_figma_uat(
        cls,
        figma_url: str,
        apk_path: str,
        accounts: list = None,
        run_id: str = None,
        notify=None,
    ) -> dict:
        """
        Figma-first UAT entry point.

        1. Parse Figma file to get journey spec
        2. Install APK
        3. Connect to device
        4. Run FigmaUATRunner
        5. Return FigmaUATReport

        Args:
            figma_url:  Full Figma file URL (figma.com/design/... or file/... or proto/...)
            apk_path:   Path to the candidate APK to install
            accounts:   Optional list of account dicts (unused by Figma UAT but kept for API parity)
            run_id:     Optional run ID; auto-generated if not provided
            notify:     Optional callable(str) for progress messages (e.g. bot send_message)

        Returns:
            FigmaUATReport dict (see FigmaUATRunner.run)
        """
        from agent.figma_journey_parser import FigmaJourneyParser  # noqa: PLC0415
        from agent.figma_uat_runner import FigmaUATRunner          # noqa: PLC0415
        from tools.apk_manager import install_apk                   # noqa: PLC0415

        if run_id is None:
            import uuid as _uuid  # noqa: PLC0415
            run_id = f"figma_{_uuid.uuid4().hex[:8]}"

        def _notify(msg: str) -> None:
            if notify:
                try:
                    notify(msg)
                except Exception as _e:
                    logger.warning(f"[Orchestrator.run_figma_uat] notify() raised: {_e}")
            logger.info(f"[Orchestrator.run_figma_uat] {msg}")

        # Step 1 — Parse Figma
        _notify("Parsing Figma file...")
        file_id = FigmaJourneyParser.file_id_from_url(figma_url)
        parser = FigmaJourneyParser(file_id=file_id)
        journey_spec = parser.parse()

        n_screens = journey_spec.get("total_screens", 0)
        n_persuasion = len(journey_spec.get("persuasion_screens", []))
        n_tc = len(journey_spec.get("test_cases", []))
        _notify(
            f"Parsed Figma: {n_screens} screens found "
            f"({n_persuasion} persuasion elements), {n_tc} test cases generated. "
            "Connecting to device..."
        )

        # Step 2 — Connect to device
        device = AndroidDevice()

        # Step 3 — Install APK
        _notify(f"Installing APK: {Path(apk_path).name}")
        package_name = install_apk(apk_path, serial=device.serial)
        logger.info(f"[Orchestrator.run_figma_uat] Installed package: {package_name}")

        # Launch the app before running
        from tools.apk_manager import launch_app  # noqa: PLC0415
        launch_app(package_name, serial=device.serial)
        import time as _time  # noqa: PLC0415
        _time.sleep(3)

        _notify(
            f"App launched. Running {n_tc} test cases against Figma design..."
        )

        # Step 4 — Run FigmaUATRunner
        runner = FigmaUATRunner(
            device=device,
            journey_spec=journey_spec,
            run_id=run_id,
            package_name=package_name,
        )
        report = runner.run()

        _notify(
            f"Figma UAT complete: {report.get('overall_verdict')} — "
            f"{round(report.get('compliance_rate', 0) * 100, 1)}% compliance"
        )

        return report

    # ------------------------------------------------------------------
    # Cloud entry point
    # ------------------------------------------------------------------

    def run_cold_start(
        self,
        apk_path: str,
        feature: str,
        accounts: list[dict],
        package_name: str | None = None,
        avd_name: str = "mmt_test",
        headless: bool = True,
        **kwargs,
    ) -> dict:
        """
        Cloud/CI entry point: start the emulator, install the APK, then run the
        normal UAT flow.

        Uses EmulatorManager.cold_start_for_cloud() to handle the full device
        bootstrap sequence before delegating to the standard run() method.

        Args:
            apk_path:     Path to the candidate APK.
            feature:      Human-readable feature description for this run.
            accounts:     List of account dicts with 'id', 'email', 'password'.
            package_name: Optional package name (extracted from APK if not given).
            avd_name:     AVD name to start (default: mmt_test).
            headless:     Run emulator without a window — for cloud/CI (default: True).
            **kwargs:     Additional keyword arguments forwarded to Orchestrator.__init__.

        Returns:
            The run summary dict from run().
        """
        logger.info(
            f"[Orchestrator] cold_start_for_cloud: AVD={avd_name}, "
            f"headless={headless}, apk={apk_path}"
        )

        manager = EmulatorManager(avd_name=avd_name, headless=headless)
        cold_start_info = manager.cold_start_for_cloud(
            apk_path=apk_path,
            package_name=package_name,
        )

        logger.info(
            f"[Orchestrator] Cold start complete: serial={cold_start_info['serial']}, "
            f"package={cold_start_info['package_name']}, "
            f"installed_fresh={cold_start_info['installed_fresh']}"
        )

        # Re-initialise the orchestrator with the resolved package and run
        # (The normal run() will re-install + re-launch, which is intentional
        # for consistent state. The cold_start_for_cloud above ensures the
        # device is fully booted and the package manager is ready.)
        orch = Orchestrator(
            candidate_apk=apk_path,
            feature_description=feature,
            accounts=accounts,
            **kwargs,
        )
        run_summary = orch.run()
        run_summary["cold_start_info"] = cold_start_info
        return run_summary

    # ------------------------------------------------------------------
    # Parallel scenario execution
    # ------------------------------------------------------------------

    def _run_scenarios_parallel(
        self,
        device: AndroidDevice,
        scenarios: list[dict],
        accounts: list[dict],
        run_id: str,
    ) -> list[dict]:
        """
        Run all scenarios across all accounts.

        NOTE (v1): With a single physical device we cannot truly parallelise at the
        device level, so we use ThreadPoolExecutor with max_workers=1 and iterate
        accounts × scenarios sequentially. The structure is ready for multi-device
        parallelism in future: increase max_workers and pass separate device instances.

        TODO(future): Accept a list of devices and distribute accounts across them.
        """
        max_workers = get("agent.max_parallel_runners", 5)
        # v1 safety: clamp to 1 for single device
        effective_workers = 1
        logger.info(
            f"[Orchestrator] Running {len(scenarios)} scenarios × {len(accounts)} accounts "
            f"(max_workers={effective_workers}, configured={max_workers})"
        )

        tasks: list[tuple[dict, dict]] = [
            (scenario, account)
            for account in accounts
            for scenario in scenarios
        ]

        results: list[dict] = []

        def _run_one(task: tuple[dict, dict]) -> dict:
            scenario, account = task
            runner = ScenarioRunnerAgent(
                device=device,
                scenario=scenario,
                account_id=account["id"],
                feature_description=self.feature_description,
                run_id=run_id,
                package_name=getattr(self, "package_name", ""),
            )
            return runner.run()

        with concurrent.futures.ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures = {executor.submit(_run_one, task): task for task in tasks}
            for future in concurrent.futures.as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    logger.info(
                        f"[Orchestrator] Completed: '{result['scenario_name']}' | "
                        f"account={result['account_id']} | status={result['status']}"
                    )
                except Exception as e:
                    scenario, account = task
                    logger.error(
                        f"[Orchestrator] Task failed for scenario='{scenario['name']}' "
                        f"account='{account['id']}': {e}"
                    )
                    results.append(
                        {
                            "scenario_name": scenario["name"],
                            "account_id": account["id"],
                            "status": "failed",
                            "steps_taken": [],
                            "final_outcome": f"Task executor error: {e}",
                            "evidence_pack": {},
                            "error": str(e),
                        }
                    )

        return results

    # ------------------------------------------------------------------
    # Result classification
    # ------------------------------------------------------------------

    def _classify_results(
        self,
        results: list[dict],
        variant_groups: dict,
        variant_detector: VariantDetector,
    ) -> list[dict]:
        """
        Enrich each result with a classification (REGRESSION / VARIANT_DIFFERENCE / INCONCLUSIVE).
        Groups same-named failing scenarios across accounts for accurate classification.
        """
        # Build a map: scenario_name -> list of failing account_ids
        failing_by_scenario: dict[str, list[str]] = {}
        for r in results:
            if r["status"] != "completed":
                sname = r["scenario_name"]
                failing_by_scenario.setdefault(sname, [])
                failing_by_scenario[sname].append(r["account_id"])

        enriched: list[dict] = []
        for r in results:
            classification = None
            if r["status"] != "completed":
                sname = r["scenario_name"]
                failing_accounts = failing_by_scenario.get(sname, [r["account_id"]])
                finding = {
                    "account_id": r["account_id"],
                    "failing_accounts": failing_accounts,
                }
                classification = variant_detector.classify_finding(finding, variant_groups)
            enriched.append({**r, "classification": classification})

        return enriched

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _save_run_summary(
        self,
        results: list[dict],
        variant_groups: dict,
        screen_graph: dict,
    ) -> str:
        """Serialise the full run summary to reports/run_summary_{run_id}.json."""
        report_path = Path("reports") / f"run_summary_{self.run_id}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)

        # Strip large evidence packs from the summary (raw evidence stays in .tmp/)
        slim_results = []
        for r in results:
            slim = {k: v for k, v in r.items() if k != "evidence_pack"}
            slim["evidence_dir"] = r.get("evidence_pack", {}).get("session_dir", "")
            slim_results.append(slim)

        summary = {
            "run_id": self.run_id,
            "feature": self.feature_description,
            "candidate_apk": self.candidate_apk,
            "baseline_apk": self.baseline_apk,
            "acceptance_criteria": self.acceptance_criteria,
            "generated_at": datetime.now().isoformat(),
            "variant_groups": variant_groups,
            "screen_graph": {
                "total_screens": screen_graph["total_screens"],
                "summary": screen_graph.get("summary", ""),
                "screen_ids": [s["screen_id"] for s in screen_graph.get("screens", [])],
            },
            "results": slim_results,
            "stats": self._compute_stats(results),
        }

        with open(report_path, "w") as f:
            json.dump(summary, f, indent=2)

        logger.info(f"[Orchestrator] Run summary saved: {report_path}")
        return str(report_path)

    @staticmethod
    def _compute_stats(results: list[dict]) -> dict:
        total = len(results)
        completed = sum(1 for r in results if r["status"] == "completed")
        failed = sum(1 for r in results if r["status"] == "failed")
        blocked = sum(1 for r in results if r["status"] == "blocked")
        regressions = sum(
            1 for r in results if r.get("classification") == "REGRESSION"
        )
        variant_diffs = sum(
            1 for r in results if r.get("classification") == "VARIANT_DIFFERENCE"
        )
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "blocked": blocked,
            "pass_rate": round(completed / total * 100, 1) if total else 0.0,
            "regressions": regressions,
            "variant_differences": variant_diffs,
        }

    @staticmethod
    def _print_summary(stats: dict, elapsed: float, summary_path: str) -> None:
        print("\n" + "=" * 60)
        print("UAT RUN SUMMARY")
        print("=" * 60)
        print(f"  Total scenario runs : {stats['total']}")
        print(f"  Passed              : {stats['completed']}  ({stats['pass_rate']}%)")
        print(f"  Failed              : {stats['failed']}")
        print(f"  Blocked             : {stats['blocked']}")
        print(f"  Regressions found   : {stats['regressions']}")
        print(f"  Variant differences : {stats['variant_differences']}")
        print(f"  Elapsed time        : {elapsed}s")
        print(f"  Report              : {summary_path}")
        print("=" * 60 + "\n")

    # ------------------------------------------------------------------
    # Phase 3 — Report generation
    # ------------------------------------------------------------------

    def generate_report(
        self,
        run_summary: dict | None = None,
        scenarios: list[dict] | None = None,
    ) -> str:
        """
        Run Phase 3: evaluate results, compare builds, and write the UAT report.

        Can be called standalone (loading run_summary from disk) or directly
        from run() with the in-memory run_summary.

        Returns the path to the saved Markdown report.
        """
        # Load run_summary from disk if not provided
        if run_summary is None:
            summary_path = Path("reports") / f"run_summary_{self.run_id}.json"
            if not summary_path.exists():
                raise FileNotFoundError(
                    f"Run summary not found: {summary_path}. "
                    "Run the UAT run first before generating a report."
                )
            with open(summary_path) as f:
                run_summary = json.load(f)
            logger.info(f"[Orchestrator] Loaded run_summary from {summary_path}")

        feature = run_summary.get("feature", self.feature_description)
        variant_groups = run_summary.get("variant_groups", {})
        all_results = run_summary.get("results", [])

        # Rebuild scenarios list from results if not provided
        if scenarios is None:
            seen: dict[str, dict] = {}
            for r in all_results:
                sname = r.get("scenario_name", "")
                if sname not in seen:
                    seen[sname] = {
                        "name": sname,
                        "category": r.get("category", "happy_path"),
                        "expected_outcome": r.get("expected_outcome", ""),
                        "severity": r.get("severity", "medium"),
                    }
            scenarios = list(seen.values())

        # --- EvaluatorAgent ---
        logger.info("[Orchestrator] Running EvaluatorAgent...")
        evaluator = EvaluatorAgent(variant_groups=variant_groups)
        evaluation = evaluator.evaluate_run(scenarios=scenarios, all_results=all_results)
        logger.info(
            f"[Orchestrator] Evaluation complete: "
            f"verdict={evaluation['overall_verdict']} "
            f"passed={evaluation['passed']} failed={evaluation['failed']}"
        )

        # --- DiffAgent (only if both baseline and candidate dirs are available) ---
        diff_analysis: dict = {}
        baseline_apk = run_summary.get("baseline_apk") or self.baseline_apk
        candidate_apk = run_summary.get("candidate_apk") or self.candidate_apk

        if baseline_apk and candidate_apk:
            # Derive screenshot directories from APK paths (convention: .tmp/evidence/{run_id}/)
            evidence_root = Path(get("uat.evidence_dir", ".tmp/evidence")) / self.run_id
            baseline_dir = str(evidence_root / "baseline_screenshots")
            candidate_dir = str(evidence_root / "candidate_screenshots")

            if Path(baseline_dir).is_dir() and Path(candidate_dir).is_dir():
                logger.info("[Orchestrator] Running DiffAgent...")
                diff_agent = DiffAgent(
                    baseline_dir=baseline_dir,
                    candidate_dir=candidate_dir,
                    feature_description=feature,
                    run_id=self.run_id,
                )
                diff_analysis = diff_agent.analyze()
                logger.info(
                    f"[Orchestrator] DiffAgent complete: {diff_analysis.get('summary', '')}"
                )
            else:
                logger.info(
                    "[Orchestrator] Baseline/candidate screenshot dirs not found — "
                    "skipping DiffAgent."
                )
        else:
            logger.info(
                "[Orchestrator] No baseline APK provided — skipping DiffAgent."
            )

        # --- ReportWriterAgent ---
        logger.info("[Orchestrator] Running ReportWriterAgent...")
        report_writer = ReportWriterAgent(run_id=self.run_id, reports_dir="reports")
        report_path = report_writer.write_report(
            run_summary=run_summary,
            evaluation=evaluation,
            diff_analysis=diff_analysis,
            variant_groups=variant_groups,
        )
        logger.info(f"[Orchestrator] Report written: {report_path}")

        # --- Save JSON export ---
        json_export_path = save_json_export(
            run_summary=run_summary,
            evaluation=evaluation,
            diff_analysis=diff_analysis,
            run_id=self.run_id,
            reports_dir="reports",
        )
        logger.info(f"[Orchestrator] JSON export saved: {json_export_path}")

        # --- Slack summary (logged, not sent in v1) ---
        slack_msg = to_slack_summary(
            evaluation=evaluation,
            run_id=self.run_id,
            report_path=report_path,
        )
        logger.info(f"[Orchestrator] Slack summary:\n{slack_msg}")

        # --- Jira issues (logged, not created in v1) ---
        jira_issues = to_jira_issues(evaluation=evaluation, run_id=self.run_id)
        if jira_issues:
            logger.info(
                f"[Orchestrator] {len(jira_issues)} Jira issue(s) ready "
                f"(use MCP createJiraIssue to file them)"
            )

        return report_path
