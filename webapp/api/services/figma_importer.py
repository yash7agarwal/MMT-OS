"""Figma importer — one-shot fetch + local persistence.

Does a SINGLE fetch of a Figma file:
1. Parse the file structure via FigmaJourneyParser (hits /v1/files once)
2. Save raw JSON response to disk
3. For each frame: extract structured metadata (dimensions, text, colors, fonts),
   download the full-res image from Figma's CDN, persist a FigmaFrame row
4. Mark the FigmaImport row as status=ready

After a successful import, every downstream consumer (UAT runner, planners,
comparator) sources data from the DB + local disk — zero additional Figma API
calls until the user explicitly re-imports.

Reuses:
- agent.figma_journey_parser.FigmaJourneyParser.parse(enrich=False)
- httpx for image downloads (Figma's image URLs are AWS S3, not rate-limited)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

# Ensure repo root is importable
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from webapp.api import models

logger = logging.getLogger(__name__)

_IMPORTS_DIR = _REPO_ROOT / "webapp" / "data" / "figma_imports"
_IMPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def import_figma_file(
    project_id: int,
    figma_file_id: str,
    db: Session,
) -> models.FigmaImport:
    """Fetch a Figma file once and persist everything locally.

    Returns the FigmaImport row with status=ready on success or status=failed
    on any error (with the error message populated).

    This function is synchronous — it takes ~30-60s for a typical file with
    5-10 frames and returns only when the import is fully complete.
    """
    project = db.get(models.Project, project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")

    figma_token = (
        os.environ.get("FIGMA_ACCESS_TOKEN") or os.environ.get("FIGMA_API_TOKEN")
    )
    if not figma_token:
        raise RuntimeError(
            "FIGMA_ACCESS_TOKEN not set in .env. Generate a personal access "
            "token at figma.com/settings → Security → Personal access tokens."
        )

    # Create the row up-front so the caller can poll its status
    imp = models.FigmaImport(
        project_id=project_id,
        figma_file_id=figma_file_id,
        status="fetching",
    )
    db.add(imp)
    db.commit()
    db.refresh(imp)
    import_id = imp.id
    import_dir = _IMPORTS_DIR / str(import_id)
    frames_dir = import_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    try:
        from agent.figma_journey_parser import FigmaJourneyParser

        # Step 1: Parse the Figma file (hits /v1/files + /v1/images ONCE)
        logger.info(f"[FigmaImporter#{import_id}] Parsing file {figma_file_id}...")
        parser = FigmaJourneyParser(figma_file_id, token=figma_token)
        journey = parser.parse(enrich=False)

        imp.file_name = journey.get("file_name")
        raw_path = import_dir / "raw.json"
        raw_path.write_text(json.dumps(journey, indent=2, default=str))
        imp.raw_json_path = str(raw_path)
        db.commit()

        # Step 2: Persist one FigmaFrame row per frame + download image
        all_frames = journey.get("all_screens", [])
        logger.info(f"[FigmaImporter#{import_id}] {len(all_frames)} frames to persist")

        # Figma image URLs are S3-hosted and can 403 after expiry; keep a short
        # client session + reasonable timeouts
        with httpx.Client(timeout=60) as http:
            for frame in all_frames:
                node_id = frame.get("node_id", "")
                if not node_id:
                    continue

                # Pull structured design data from the raw node tree
                text_content = frame.get("text_content") or []
                meta = _extract_frame_metadata(frame)

                row = models.FigmaFrame(
                    import_id=import_id,
                    node_id=node_id,
                    name=frame.get("name", "Unnamed"),
                    page_name=frame.get("page_name"),
                    frame_type=frame.get("type", "other"),
                    width=meta.get("width"),
                    height=meta.get("height"),
                    x=meta.get("x"),
                    y=meta.get("y"),
                    text_content=text_content,
                    colors=meta.get("colors"),
                    fonts=meta.get("fonts"),
                )

                # Download full-res image to disk (S3 URLs — not counted against Figma quota)
                image_url = frame.get("image_url") or ""
                if image_url:
                    safe_node = node_id.replace(":", "_").replace("/", "_")
                    img_path = frames_dir / f"{safe_node}.png"
                    try:
                        r = http.get(image_url)
                        r.raise_for_status()
                        img_path.write_bytes(r.content)
                        row.image_path = str(img_path)
                    except Exception as exc:
                        logger.warning(
                            f"[FigmaImporter#{import_id}] Image download failed for "
                            f"{node_id}: {exc}"
                        )

                db.add(row)

        db.commit()

        # Step 3: finalize
        imp.total_frames = len(all_frames)
        imp.status = "ready"
        imp.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(imp)
        logger.info(
            f"[FigmaImporter#{import_id}] COMPLETED — {imp.total_frames} frames persisted"
        )
        return imp

    except Exception as exc:
        logger.exception(f"[FigmaImporter#{import_id}] Import failed")
        imp.status = "failed"
        imp.error = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()[:2000]}"
        imp.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(imp)
        return imp


# ---------------------------------------------------------------------------
# Metadata extraction (pure Python — no LLM, no additional API calls)
# ---------------------------------------------------------------------------


def _extract_frame_metadata(frame: dict) -> dict:
    """Walk a frame dict from FigmaJourneyParser and collect structured data.

    The parser already captures `text_content` and `image_url` per frame.
    Here we pull additional fields from the raw Figma node tree if present:
    absoluteBoundingBox (dimensions), unique hex colors, and unique (family,
    size, weight) font tuples from any text children.

    Returns a dict with: width, height, x, y, colors, fonts
    """
    out: dict[str, Any] = {
        "width": None, "height": None, "x": None, "y": None,
        "colors": [], "fonts": [],
    }

    # The parser currently flattens the tree — it may or may not include the raw
    # node. If it does, walk it. If not, the fields stay None/empty.
    raw_node = frame.get("raw_node") or frame.get("node")
    if raw_node and isinstance(raw_node, dict):
        # Bounding box
        bbox = raw_node.get("absoluteBoundingBox") or {}
        if bbox:
            out["width"] = bbox.get("width")
            out["height"] = bbox.get("height")
            out["x"] = bbox.get("x")
            out["y"] = bbox.get("y")

        # Walk children collecting colors + fonts
        colors: set[str] = set()
        fonts: set[tuple] = set()
        _walk_node(raw_node, colors, fonts)
        out["colors"] = sorted(colors)
        out["fonts"] = [
            {"family": f, "size": s, "weight": w} for (f, s, w) in sorted(fonts)
        ]

    return out


def _walk_node(node: dict, colors: set[str], fonts: set[tuple]) -> None:
    """Recursively collect unique colors and font tuples from a Figma node tree."""
    if not isinstance(node, dict):
        return

    # Fills
    for fill in node.get("fills") or []:
        if fill.get("type") == "SOLID":
            c = fill.get("color") or {}
            hex_color = _rgb_to_hex(c)
            if hex_color:
                colors.add(hex_color)

    # Strokes
    for stroke in node.get("strokes") or []:
        if stroke.get("type") == "SOLID":
            c = stroke.get("color") or {}
            hex_color = _rgb_to_hex(c)
            if hex_color:
                colors.add(hex_color)

    # Text styling
    style = node.get("style") or {}
    if style.get("fontFamily"):
        fonts.add((
            style.get("fontFamily"),
            style.get("fontSize"),
            style.get("fontWeight"),
        ))

    for child in node.get("children") or []:
        _walk_node(child, colors, fonts)


def _rgb_to_hex(c: dict) -> str | None:
    """Convert a Figma {r,g,b,a} color dict (0.0–1.0 floats) to #rrggbb."""
    try:
        r = int(round(c.get("r", 0) * 255))
        g = int(round(c.get("g", 0) * 255))
        b = int(round(c.get("b", 0) * 255))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return None
