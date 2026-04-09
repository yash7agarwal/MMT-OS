"""
agent/figma_comparator.py — Figma design validator

Compares actual app screenshots against Figma design frames.
Used when no baseline APK exists — validates against design spec instead.

Figma API reference:
    GET https://api.figma.com/v1/images/{file_id}?ids={node_id}&format=png&scale=2

Claude vision is used to identify visual discrepancies between the live app
screenshot and the Figma reference frame. PIL pixel-diff is used as a fallback
when Claude is unavailable.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional deps — degrade gracefully
# ---------------------------------------------------------------------------
try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False
    logger.warning("[figma] 'requests' not installed — Figma API calls will fail")

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

_FIGMA_API_BASE = "https://api.figma.com/v1"


# ---------------------------------------------------------------------------
# Helper: PIL pixel diff (fallback)
# ---------------------------------------------------------------------------

def _pixel_diff_score(img_a: "Image.Image", img_b: "Image.Image") -> float:
    """Return a 0-1 similarity score based on pixel-level diff (1 = identical)."""
    from PIL import ImageChops  # noqa: PLC0415

    img_a = img_a.convert("RGB")
    img_b = img_b.convert("RGB")

    # Resize to same dimensions
    if img_a.size != img_b.size:
        img_b = img_b.resize(img_a.size, Image.LANCZOS)

    diff = ImageChops.difference(img_a, img_b)
    diff_gray = diff.convert("L")
    pixels = list(diff_gray.getdata())
    different = sum(1 for p in pixels if p > 10)
    total = len(pixels)
    return 1.0 - (different / total) if total > 0 else 0.0


def _save_side_by_side_diff(
    img_a: "Image.Image",
    img_b: "Image.Image",
    output_path: str,
) -> None:
    """Save a side-by-side composite of two images to output_path."""
    if not _PIL_AVAILABLE:
        return

    img_a = img_a.convert("RGB")
    img_b = img_b.convert("RGB")

    # Resize b to match a height
    if img_a.height != img_b.height:
        ratio = img_a.height / img_b.height
        new_w = int(img_b.width * ratio)
        img_b = img_b.resize((new_w, img_a.height), Image.LANCZOS)

    combined = Image.new("RGB", (img_a.width + img_b.width + 10, img_a.height), (200, 200, 200))
    combined.paste(img_a, (0, 0))
    combined.paste(img_b, (img_a.width + 10, 0))

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    combined.save(output_path)


# ---------------------------------------------------------------------------
# FigmaComparator
# ---------------------------------------------------------------------------


class FigmaComparator:
    """
    Compares actual app screenshots against Figma design frames.

    Used when no baseline APK exists — validates the live app against
    the design spec instead of a previous build.
    """

    def __init__(
        self,
        figma_file_id: str,
        figma_token: Optional[str] = None,
        run_id: str = "",
    ) -> None:
        self.figma_file_id = figma_file_id
        self.figma_token = figma_token or os.getenv("FIGMA_API_TOKEN") or ""
        self.run_id = run_id

        # Diff images output directory
        self._diff_dir = Path("reports") / "figma_diffs" / (run_id or "default")

        if not self.figma_token:
            logger.warning(
                "[FigmaComparator] No FIGMA_API_TOKEN set — Figma API calls will fail"
            )

    # ------------------------------------------------------------------
    # Figma API
    # ------------------------------------------------------------------

    def fetch_frame_image(self, node_id: str) -> Optional[bytes]:
        """
        Fetch a Figma frame as PNG bytes using the Figma Images API.

        GET https://api.figma.com/v1/images/{file_id}?ids={node_id}&format=png&scale=2

        Returns PNG bytes on success, None on failure.
        """
        if not _REQUESTS_AVAILABLE:
            logger.error("[FigmaComparator] 'requests' library is required for Figma API calls")
            return None

        if not self.figma_token:
            logger.error("[FigmaComparator] FIGMA_API_TOKEN is not set")
            return None

        # Step 1: get the CDN URL for this node
        url = f"{_FIGMA_API_BASE}/images/{self.figma_file_id}"
        params = {"ids": node_id, "format": "png", "scale": "2"}
        headers = {"X-Figma-Token": self.figma_token}

        try:
            resp = _requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error(f"[FigmaComparator] Figma Images API request failed: {exc}")
            return None

        if data.get("err"):
            logger.error(f"[FigmaComparator] Figma API error: {data['err']}")
            return None

        image_url = (data.get("images") or {}).get(node_id)
        if not image_url:
            logger.error(
                f"[FigmaComparator] No image URL returned for node {node_id}. "
                f"Response: {data}"
            )
            return None

        # Step 2: download the actual PNG
        try:
            img_resp = _requests.get(image_url, timeout=60)
            img_resp.raise_for_status()
            return img_resp.content
        except Exception as exc:
            logger.error(f"[FigmaComparator] Failed to download Figma frame image: {exc}")
            return None

    # ------------------------------------------------------------------
    # Single-screen comparison
    # ------------------------------------------------------------------

    def compare_screenshot_to_frame(
        self,
        screenshot_path: str,
        figma_node_id: str,
        screen_name: str,
    ) -> dict:
        """
        Compare a real screenshot to a Figma frame.

        Uses Claude vision to identify differences (both images as base64 content
        blocks).  Falls back to pixel diff if Claude is unavailable or the
        Figma frame cannot be fetched.

        Returns:
            {
                "screen_name": str,
                "figma_node_id": str,
                "match_score": float,   # 0.0 – 1.0
                "issues": [str],        # list of discrepancies
                "verdict": "MATCHES" | "DIFFERS" | "UNKNOWN",
                "diff_image_path": str | None,
            }
        """
        base_result: dict = {
            "screen_name": screen_name,
            "figma_node_id": figma_node_id,
            "match_score": 0.0,
            "issues": [],
            "verdict": "UNKNOWN",
            "diff_image_path": None,
        }

        # 1. Validate screenshot exists
        if not os.path.exists(screenshot_path):
            base_result["issues"].append(f"Screenshot not found: {screenshot_path}")
            return base_result

        # 2. Fetch Figma frame
        figma_bytes = self.fetch_frame_image(figma_node_id)
        if not figma_bytes:
            base_result["issues"].append(
                f"Could not fetch Figma frame for node {figma_node_id}"
            )
            return base_result

        # 3. Save diff image (side-by-side) if PIL available
        diff_image_path: Optional[str] = None
        if _PIL_AVAILABLE:
            diff_image_path = str(
                self._diff_dir / f"figma_diff_{screen_name.replace(' ', '_')}.png"
            )
            try:
                screenshot_img = Image.open(screenshot_path)
                figma_img = Image.open(BytesIO(figma_bytes))
                _save_side_by_side_diff(screenshot_img, figma_img, diff_image_path)
                base_result["diff_image_path"] = diff_image_path
            except Exception as exc:
                logger.warning(f"[FigmaComparator] Could not save diff image: {exc}")
                diff_image_path = None

        # 4. Primary path — Claude vision comparison
        claude_result = self._compare_with_claude(
            screenshot_path=screenshot_path,
            figma_bytes=figma_bytes,
            screen_name=screen_name,
        )

        if claude_result is not None:
            base_result.update(claude_result)
            base_result["diff_image_path"] = diff_image_path
            return base_result

        # 5. Fallback — pixel diff
        logger.info(
            f"[FigmaComparator] Falling back to pixel diff for screen '{screen_name}'"
        )
        if _PIL_AVAILABLE:
            try:
                screenshot_img = Image.open(screenshot_path)
                figma_img = Image.open(BytesIO(figma_bytes))
                score = _pixel_diff_score(screenshot_img, figma_img)
                base_result["match_score"] = round(score, 4)
                if score >= 0.90:
                    base_result["verdict"] = "MATCHES"
                elif score >= 0.70:
                    base_result["verdict"] = "DIFFERS"
                    base_result["issues"].append(
                        f"Pixel diff: {round((1 - score) * 100, 1)}% of pixels differ"
                    )
                else:
                    base_result["verdict"] = "DIFFERS"
                    base_result["issues"].append(
                        f"Pixel diff: {round((1 - score) * 100, 1)}% of pixels differ (major)"
                    )
            except Exception as exc:
                logger.error(f"[FigmaComparator] Pixel diff fallback failed: {exc}")
                base_result["issues"].append(f"Comparison failed: {exc}")
        else:
            base_result["issues"].append(
                "PIL not available — cannot compare without Claude vision"
            )

        base_result["diff_image_path"] = diff_image_path
        return base_result

    # ------------------------------------------------------------------
    # Claude vision helper
    # ------------------------------------------------------------------

    def _compare_with_claude(
        self,
        screenshot_path: str,
        figma_bytes: bytes,
        screen_name: str,
    ) -> Optional[dict]:
        """
        Use Claude vision to compare a screenshot against a Figma frame.

        Sends both images as base64 image content blocks.
        Returns a partial result dict or None if Claude is unavailable.
        """
        try:
            import anthropic  # noqa: PLC0415
            from utils.claude_client import _get_client  # noqa: PLC0415
        except ImportError:
            logger.warning("[FigmaComparator] anthropic/claude_client not importable")
            return None

        # Read and encode the screenshot
        try:
            with open(screenshot_path, "rb") as fh:
                screenshot_b64 = base64.standard_b64encode(fh.read()).decode("utf-8")
        except Exception as exc:
            logger.warning(f"[FigmaComparator] Could not read screenshot: {exc}")
            return None

        figma_b64 = base64.standard_b64encode(figma_bytes).decode("utf-8")

        system_prompt = (
            "You are a senior mobile UI/UX QA engineer. "
            "You will be shown two images: first the Figma design frame, "
            "then the actual app screenshot. "
            "Identify visual discrepancies. Respond only in valid JSON."
        )

        user_message = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Screen name: {screen_name}\n\n"
                        "Image 1 is the Figma design reference. "
                        "Image 2 is the actual app screenshot.\n\n"
                        "Compare them and respond with JSON:\n"
                        "{\n"
                        '  "match_score": <float 0.0-1.0, where 1.0 = pixel-perfect match>,\n'
                        '  "issues": [<list of specific discrepancy strings, empty if none>],\n'
                        '  "verdict": "<MATCHES|DIFFERS|UNKNOWN>",\n'
                        '  "summary": "<1-2 sentence summary>"\n'
                        "}\n\n"
                        "Verdict rules:\n"
                        "- MATCHES: match_score >= 0.85 and no critical layout issues\n"
                        "- DIFFERS: noticeable layout, color, font, or component differences\n"
                        "- UNKNOWN: images cannot be compared (different screens, loading state, etc.)"
                    ),
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": figma_b64,
                    },
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": screenshot_b64,
                    },
                },
            ],
        }

        try:
            client = _get_client()
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=system_prompt,
                messages=[user_message],
            )
            raw = response.content[0].text.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            data = json.loads(raw)
            return {
                "match_score": float(data.get("match_score", 0.0)),
                "issues": data.get("issues", []),
                "verdict": data.get("verdict", "UNKNOWN"),
            }

        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning(f"[FigmaComparator] Claude returned invalid JSON: {exc}")
            return None
        except Exception as exc:
            logger.warning(f"[FigmaComparator] Claude vision call failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # Full design validation run
    # ------------------------------------------------------------------

    def run_design_validation(
        self,
        screenshots: list[dict],
        figma_mappings: list[dict],
    ) -> dict:
        """
        Run full design validation against Figma frames.

        Args:
            screenshots:    [{"path": str, "screen_name": str}]
            figma_mappings: [{"screen_name": str, "figma_node_id": str}]

        Returns:
            {
                "run_id": str,
                "total_screens": int,
                "matches": int,
                "differs": int,
                "unknown": int,
                "results": [compare_screenshot_to_frame results],
                "overall_verdict": "DESIGN_COMPLIANT" | "DESIGN_DIVERGED" | "PARTIAL",
            }
        """
        self._diff_dir.mkdir(parents=True, exist_ok=True)

        # Build a lookup: screen_name -> figma_node_id
        mapping_index: dict[str, str] = {
            m["screen_name"]: m["figma_node_id"]
            for m in figma_mappings
        }

        results: list[dict] = []
        matches = 0
        differs = 0
        unknown = 0

        for screen in screenshots:
            screen_name = screen["screen_name"]
            screenshot_path = screen["path"]
            node_id = mapping_index.get(screen_name)

            if not node_id:
                logger.warning(
                    f"[FigmaComparator] No Figma mapping for screen '{screen_name}' — skipping"
                )
                results.append({
                    "screen_name": screen_name,
                    "figma_node_id": None,
                    "match_score": 0.0,
                    "issues": [f"No Figma mapping defined for screen '{screen_name}'"],
                    "verdict": "UNKNOWN",
                    "diff_image_path": None,
                })
                unknown += 1
                continue

            result = self.compare_screenshot_to_frame(
                screenshot_path=screenshot_path,
                figma_node_id=node_id,
                screen_name=screen_name,
            )
            results.append(result)

            verdict = result.get("verdict", "UNKNOWN")
            if verdict == "MATCHES":
                matches += 1
            elif verdict == "DIFFERS":
                differs += 1
            else:
                unknown += 1

        total = len(results)

        # Determine overall verdict
        if differs == 0 and unknown == 0:
            overall_verdict = "DESIGN_COMPLIANT"
        elif matches == 0:
            overall_verdict = "DESIGN_DIVERGED"
        else:
            overall_verdict = "PARTIAL"

        logger.info(
            f"[FigmaComparator] Design validation complete: "
            f"{matches} matches, {differs} differs, {unknown} unknown "
            f"-> {overall_verdict}"
        )

        return {
            "run_id": self.run_id,
            "total_screens": total,
            "matches": matches,
            "differs": differs,
            "unknown": unknown,
            "results": results,
            "overall_verdict": overall_verdict,
        }
