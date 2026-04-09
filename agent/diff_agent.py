"""
agent/diff_agent.py — Build comparison subagent

Compares baseline vs candidate build screenshots for the same flow.
Uses visual diff + Claude analysis to produce structured findings.

Runs as a subagent — receives file paths, returns structured JSON.

Figma mode:
    If figma_file_id and figma_mappings are provided to __init__(),
    DiffAgent delegates to FigmaComparator instead of pixel diff.
    Existing baseline/candidate comparison is fully backward-compatible.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from tools.visual_diff import compare_screenshots, batch_compare
from utils.claude_client import ask

logger = logging.getLogger(__name__)

_DIFF_ANALYSIS_SYSTEM = (
    "You are a senior QA engineer analysing visual changes between two builds of a mobile app. "
    "Respond only in valid JSON. No markdown, no explanation."
)

_DIFF_ANALYSIS_PROMPT = """\
You are evaluating a QA visual diff between two builds of a mobile app screen.

Screen label: {screen_label}
Feature context: {feature_description}
Visual diff result: {diff_percentage}% pixel difference ({assessment})
Different pixels: {different_pixels} out of {total_pixels}

Based on the screen label name and the visual diff statistics above, analyse this change and respond with JSON:
{{
  "change_description": "<what likely changed on this screen, inferred from the label and diff stats>",
  "change_type": "<one of: INTENTIONAL_CHANGE | REGRESSION_CANDIDATE | NO_CHANGE>",
  "severity": "<one of: critical | high | medium | low | none>",
  "reasoning": "<1-2 sentences explaining your classification>"
}}

Classification rules:
- NO_CHANGE: diff_percentage is 0% or assessment is 'identical'
- REGRESSION_CANDIDATE: diff is significant (>5%) and the screen label suggests a core flow (search, booking, payment, login)
- INTENTIONAL_CHANGE: diff exists but screen appears to be a cosmetic/UI refresh or non-critical screen
- Use severity 'none' only for NO_CHANGE; use 'critical' only for payment/booking/login regressions
"""


class DiffAgent:
    """
    Compares baseline vs candidate build screenshots for the same feature flow.

    Pairs screenshots by step label, runs visual diff, then uses Claude to
    classify each change as intentional or a regression candidate.

    Figma mode (optional):
        Pass figma_file_id and figma_mappings to __init__() to switch into
        design-validation mode using FigmaComparator instead of pixel diff.
        Baseline/candidate dirs are still accepted but are unused in Figma mode
        unless analyze() is called directly.
    """

    def __init__(
        self,
        baseline_dir: str,
        candidate_dir: str,
        feature_description: str,
        run_id: str,
        figma_file_id: Optional[str] = None,
        figma_mappings: Optional[list[dict]] = None,
    ):
        self.baseline_dir = baseline_dir
        self.candidate_dir = candidate_dir
        self.feature_description = feature_description
        self.run_id = run_id

        # Figma mode config
        self.figma_file_id = figma_file_id
        self.figma_mappings = figma_mappings or []

        # Diff images are saved alongside the candidate screenshots
        self._diff_dir = str(Path(candidate_dir) / "diffs")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self) -> dict:
        """
        Run the full comparison pipeline.

        Returns a structured dict with per-screen comparisons and an overall summary.
        """
        logger.info(
            f"[DiffAgent] Starting analysis: run_id={self.run_id} "
            f"baseline={self.baseline_dir} candidate={self.candidate_dir}"
        )

        # 1. Pair screenshots by step number
        pairs = self._pair_screenshots(self.baseline_dir, self.candidate_dir)

        if not pairs:
            logger.warning("[DiffAgent] No paired screenshots found.")
            return self._empty_result()

        # 2. Run visual diff for all pairs
        Path(self._diff_dir).mkdir(parents=True, exist_ok=True)

        comparisons: list[dict] = []
        for baseline_path, candidate_path, label in pairs:
            diff_image_path = str(Path(self._diff_dir) / f"diff_{label}.png")

            try:
                visual_diff = compare_screenshots(
                    baseline_path,
                    candidate_path,
                    output_diff_path=diff_image_path,
                )
            except Exception as e:
                logger.error(f"[DiffAgent] Visual diff failed for {label}: {e}")
                visual_diff = {
                    "diff_percentage": 0.0,
                    "different_pixels": 0,
                    "total_pixels": 0,
                    "diff_image_path": None,
                    "is_significant": False,
                    "assessment": "error",
                    "error": str(e),
                }

            # 3. Claude analysis of this pair
            claude_result = self._analyze_pair_with_claude(
                baseline_path, candidate_path, visual_diff, self.feature_description
            )

            comparisons.append({
                "screen_label": label,
                "baseline_screenshot": baseline_path,
                "candidate_screenshot": candidate_path,
                "diff_image": visual_diff.get("diff_image_path"),
                "visual_diff": {
                    "diff_percentage": visual_diff["diff_percentage"],
                    "assessment": visual_diff["assessment"],
                    "is_significant": visual_diff["is_significant"],
                    "different_pixels": visual_diff["different_pixels"],
                    "total_pixels": visual_diff["total_pixels"],
                },
                "claude_analysis": claude_result.get("change_description", ""),
                "change_type": claude_result.get("change_type", "NO_CHANGE"),
                "severity": claude_result.get("severity", "none"),
                "reasoning": claude_result.get("reasoning", ""),
            })

        # 4. Build summary
        summary = self._build_summary(comparisons)

        result = {
            "run_id": self.run_id,
            "feature": self.feature_description,
            "baseline_dir": self.baseline_dir,
            "candidate_dir": self.candidate_dir,
            "comparisons": comparisons,
            "summary": summary,
        }

        logger.info(f"[DiffAgent] Analysis complete: {summary}")
        return result

    # ------------------------------------------------------------------
    # Screenshot pairing
    # ------------------------------------------------------------------

    def _pair_screenshots(
        self,
        baseline_dir: str,
        candidate_dir: str,
    ) -> list[tuple[str, str, str]]:
        """
        Match baseline and candidate screenshots by their step label.

        Expects filenames like: step_001_*.png, step_002_*.png, etc.
        Returns a list of (baseline_path, candidate_path, label) tuples.
        Logs a warning for any steps present in one dir but not the other.
        """
        def _index_screenshots(directory: str) -> dict[str, str]:
            """Return {step_label: full_path} for all .png files in directory."""
            index: dict[str, str] = {}
            if not os.path.isdir(directory):
                logger.warning(f"[DiffAgent] Directory not found: {directory}")
                return index

            for fname in sorted(os.listdir(directory)):
                if not fname.lower().endswith(".png"):
                    continue
                # Extract step label: everything before the first non-step suffix
                # e.g. "step_001_home_screen.png" -> "step_001"
                match = re.match(r"(step_\d+)", fname, re.IGNORECASE)
                if match:
                    label = match.group(1).lower()
                    index[label] = os.path.join(directory, fname)
                else:
                    # Use the full stem as a fallback label
                    label = Path(fname).stem
                    index[label] = os.path.join(directory, fname)

            return index

        baseline_index = _index_screenshots(baseline_dir)
        candidate_index = _index_screenshots(candidate_dir)

        all_labels = sorted(set(baseline_index) | set(candidate_index))
        pairs: list[tuple[str, str, str]] = []

        for label in all_labels:
            b_path = baseline_index.get(label)
            c_path = candidate_index.get(label)

            if b_path and c_path:
                pairs.append((b_path, c_path, label))
            elif b_path and not c_path:
                logger.warning(
                    f"[DiffAgent] Step '{label}' present in baseline but missing in candidate"
                )
            else:
                logger.warning(
                    f"[DiffAgent] Step '{label}' present in candidate but missing in baseline"
                )

        logger.info(
            f"[DiffAgent] Paired {len(pairs)} screenshots "
            f"(baseline={len(baseline_index)}, candidate={len(candidate_index)})"
        )
        return pairs

    # ------------------------------------------------------------------
    # Claude analysis
    # ------------------------------------------------------------------

    def _analyze_pair_with_claude(
        self,
        baseline_path: str,
        candidate_path: str,
        visual_diff_result: dict,
        feature_description: str,
    ) -> dict:
        """
        Use Claude to classify the change for a single screenshot pair.

        NOTE: In this version we pass diff statistics and label names only —
        no raw image data. A future version with vision API will pass actual images.

        Returns a dict with keys: change_description, change_type, severity, reasoning.
        """
        screen_label = Path(candidate_path).stem

        # Short-circuit for zero-diff screens to avoid unnecessary API calls
        if visual_diff_result.get("assessment") == "identical":
            return {
                "change_description": "No visual difference detected.",
                "change_type": "NO_CHANGE",
                "severity": "none",
                "reasoning": "Visual diff reports 0% pixel difference.",
            }

        prompt = _DIFF_ANALYSIS_PROMPT.format(
            screen_label=screen_label,
            feature_description=feature_description,
            diff_percentage=visual_diff_result.get("diff_percentage", 0),
            assessment=visual_diff_result.get("assessment", "unknown"),
            different_pixels=visual_diff_result.get("different_pixels", 0),
            total_pixels=visual_diff_result.get("total_pixels", 0),
        )

        try:
            raw = ask(prompt, system=_DIFF_ANALYSIS_SYSTEM, max_tokens=512)
            result = json.loads(raw)
            # Validate required keys with safe defaults
            result.setdefault("change_description", "Unable to analyse change.")
            result.setdefault("change_type", "REGRESSION_CANDIDATE")
            result.setdefault("severity", "medium")
            result.setdefault("reasoning", "")

            # Normalise change_type to allowed values
            allowed_types = {"INTENTIONAL_CHANGE", "REGRESSION_CANDIDATE", "NO_CHANGE"}
            if result["change_type"] not in allowed_types:
                result["change_type"] = "REGRESSION_CANDIDATE"

            return result

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"[DiffAgent] Claude analysis failed for {screen_label}: {e}")
            return {
                "change_description": f"Analysis failed: {e}",
                "change_type": "REGRESSION_CANDIDATE",
                "severity": "medium",
                "reasoning": "Could not determine — treat as regression candidate.",
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_summary(self, comparisons: list[dict]) -> str:
        """Build a one-line summary string from comparison results."""
        total = len(comparisons)
        no_change = sum(1 for c in comparisons if c["change_type"] == "NO_CHANGE")
        intentional = sum(1 for c in comparisons if c["change_type"] == "INTENTIONAL_CHANGE")
        regression = sum(1 for c in comparisons if c["change_type"] == "REGRESSION_CANDIDATE")
        return (
            f"{total} screens compared: "
            f"{regression} regression candidate(s), "
            f"{intentional} intentional change(s), "
            f"{no_change} unchanged"
        )

    def _empty_result(self) -> dict:
        return {
            "run_id": self.run_id,
            "feature": self.feature_description,
            "baseline_dir": self.baseline_dir,
            "candidate_dir": self.candidate_dir,
            "comparisons": [],
            "summary": "No screenshots found to compare.",
        }

    # ------------------------------------------------------------------
    # Figma mode
    # ------------------------------------------------------------------

    def run_figma_validation(
        self,
        figma_file_id: Optional[str] = None,
        figma_mappings: Optional[list[dict]] = None,
    ) -> dict:
        """
        Run design validation against Figma frames instead of a baseline APK.

        Uses the FigmaComparator to fetch Figma frames and compare them against
        screenshots found in self.candidate_dir.

        Args:
            figma_file_id:  Figma file ID (overrides self.figma_file_id if provided).
            figma_mappings: [{"screen_name": str, "figma_node_id": str}]
                            Overrides self.figma_mappings if provided.

        Returns:
            The result dict from FigmaComparator.run_design_validation().
        """
        from agent.figma_comparator import FigmaComparator  # noqa: PLC0415

        effective_file_id = figma_file_id or self.figma_file_id
        effective_mappings = figma_mappings or self.figma_mappings

        if not effective_file_id:
            raise ValueError(
                "figma_file_id is required for Figma validation. "
                "Pass it to run_figma_validation() or DiffAgent.__init__()."
            )

        if not effective_mappings:
            raise ValueError(
                "figma_mappings is required for Figma validation. "
                "Pass it to run_figma_validation() or DiffAgent.__init__()."
            )

        # Collect screenshots from candidate_dir
        screenshots = self._collect_screenshots(self.candidate_dir)
        if not screenshots:
            logger.warning(
                f"[DiffAgent] No screenshots found in candidate_dir: {self.candidate_dir}"
            )

        comparator = FigmaComparator(
            figma_file_id=effective_file_id,
            run_id=self.run_id,
        )

        logger.info(
            f"[DiffAgent] Starting Figma validation: run_id={self.run_id} "
            f"file_id={effective_file_id} screens={len(screenshots)}"
        )

        result = comparator.run_design_validation(
            screenshots=screenshots,
            figma_mappings=effective_mappings,
        )

        # Attach feature context
        result["feature"] = self.feature_description
        return result

    def _collect_screenshots(self, directory: str) -> list[dict]:
        """
        Collect all PNG screenshots from a directory.

        Returns [{"path": str, "screen_name": str}] sorted by filename.
        """
        if not os.path.isdir(directory):
            logger.warning(f"[DiffAgent] Screenshot directory not found: {directory}")
            return []

        screenshots = []
        for fname in sorted(os.listdir(directory)):
            if not fname.lower().endswith(".png"):
                continue
            full_path = os.path.join(directory, fname)
            # Derive a human-readable screen name from the filename stem
            screen_name = Path(fname).stem.replace("_", " ").title()
            screenshots.append({"path": full_path, "screen_name": screen_name})

        return screenshots
