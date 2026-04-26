"""Server-side chart rendering for the executive report (v0.17.0).

Three charts, each returns PNG bytes that the Jinja2 template embeds
via base64 data URI (so the report is self-contained — no external
asset dependencies in the PDF).

Design:
- matplotlib `Agg` backend (no display) — safe in any thread / docker.
- Palette: zinc + emerald (matches Prism brand). One accent color, not
  a rainbow — readability first.
- 144 DPI for crisp print rendering.
- Charts return None when input data is empty/insufficient — caller
  decides whether to skip the section or render a "(no data)" placeholder.
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

# matplotlib import is lazy — first chart call initializes it
_matplotlib_initialized = False

# Brand palette — zinc background, emerald accent (matches DESIGN.md).
ZINC_950 = "#09090b"
ZINC_800 = "#27272a"
ZINC_500 = "#71717a"
ZINC_300 = "#d4d4d8"
ZINC_100 = "#f4f4f5"
EMERALD_400 = "#34d399"
EMERALD_500 = "#10b981"
EMERALD_700 = "#047857"


def _init_matplotlib():
    """Lazy init — picks the Agg backend so we don't try to open a display."""
    global _matplotlib_initialized
    if _matplotlib_initialized:
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": ["DejaVu Sans"],  # ships with matplotlib; safe everywhere
        "font.size": 9,
        "axes.edgecolor": ZINC_800,
        "axes.labelcolor": ZINC_800,
        "xtick.color": ZINC_500,
        "ytick.color": ZINC_500,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })
    _matplotlib_initialized = True


def _fig_to_png(fig) -> bytes:
    """Render the figure to PNG bytes, then close it."""
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=144, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    return buf.getvalue()


def png_to_data_uri(png: bytes) -> str:
    """Embed PNG bytes in an HTML <img src="..."> data URI."""
    if not png:
        return ""
    return f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"


# ---------------------------------------------------------------------------
# Chart 1: Lens × competitor heatmap
# ---------------------------------------------------------------------------

def render_lens_heatmap(lens_matrix: dict) -> bytes | None:
    """Heatmap of lens (rows) × competitor (cols), cell value = obs count.

    Returns None if matrix is empty (caller can skip the section).
    """
    competitors = lens_matrix.get("competitors") or []
    lenses = lens_matrix.get("lenses") or []
    if not competitors or not lenses:
        return None

    _init_matplotlib()
    import matplotlib.pyplot as plt
    import numpy as np

    # Build value matrix [n_lenses][n_competitors]
    n_l, n_c = len(lenses), len(competitors)
    data = np.zeros((n_l, n_c), dtype=int)
    for j, comp in enumerate(competitors):
        counts = comp.get("lens_counts") or {}
        for i, lens in enumerate(lenses):
            data[i, j] = int(counts.get(lens, 0))

    fig, ax = plt.subplots(figsize=(max(6, n_c * 0.9), max(3.5, n_l * 0.5)))

    # Custom colormap: zinc-100 → emerald-500, white below 1
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list(
        "prism_emerald",
        [(0, ZINC_100), (0.05, "#d1fae5"), (0.5, EMERALD_400), (1.0, EMERALD_700)],
    )

    im = ax.imshow(data, cmap=cmap, aspect="auto", vmin=0, vmax=max(data.max(), 1))

    # Cell text — only when count > 0 (zeros stay invisible in the white area)
    for i in range(n_l):
        for j in range(n_c):
            v = data[i, j]
            if v > 0:
                ax.text(j, i, str(v), ha="center", va="center",
                        color="white" if v >= data.max() * 0.5 else ZINC_950,
                        fontsize=8, fontweight="bold")

    ax.set_xticks(range(n_c))
    ax.set_xticklabels([c.get("name", "?")[:18] for c in competitors],
                       rotation=30, ha="right", fontsize=8)
    ax.set_yticks(range(n_l))
    ax.set_yticklabels([l.replace("_", " ").title() for l in lenses], fontsize=9)
    ax.set_title("Strategic lens coverage by competitor",
                 fontsize=11, color=ZINC_800, pad=12, loc="left")

    return _fig_to_png(fig)


# ---------------------------------------------------------------------------
# Chart 2: Trend timeline (past / present / emerging / future bands)
# ---------------------------------------------------------------------------

def render_trend_timeline(trends: list[dict]) -> bytes | None:
    """Horizontal stacked bars, one per timeline band.

    Each band = count of trends categorized into past/present/emerging/future.
    """
    if not trends:
        return None

    _init_matplotlib()
    import matplotlib.pyplot as plt

    bands = ["past", "present", "emerging", "future"]
    counts = {b: 0 for b in bands}
    for t in trends:
        tl = (t.get("timeline") or "present").lower()
        if tl in counts:
            counts[tl] += 1

    if sum(counts.values()) == 0:
        return None

    fig, ax = plt.subplots(figsize=(8, 2.4))
    colors = [ZINC_500, ZINC_300, EMERALD_400, EMERALD_700]

    for i, band in enumerate(bands):
        v = counts[band]
        ax.barh(0, v, left=sum(counts[b] for b in bands[:i]),
                color=colors[i], height=0.6, label=f"{band} ({v})")

    ax.set_yticks([])
    ax.set_xlabel("trend count")
    ax.set_title("Trend timeline distribution",
                 fontsize=11, color=ZINC_800, pad=10, loc="left")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15),
              ncol=4, frameon=False, fontsize=9)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(0, max(sum(counts.values()), 1))

    return _fig_to_png(fig)


# ---------------------------------------------------------------------------
# Chart 3: Impact cascade tree
# ---------------------------------------------------------------------------

def render_impact_cascade(impact_graph: dict) -> bytes | None:
    """Three-tier cascade: trends (left) → effects (mid) → companies (right).

    Edges drawn between tiers showing causality. Compresses to top-N per tier
    if the graph is dense (>15 nodes).
    """
    nodes = impact_graph.get("nodes") or []
    edges = impact_graph.get("edges") or []
    if not nodes or not edges:
        return None

    _init_matplotlib()
    import matplotlib.pyplot as plt

    by_id = {n.get("id"): n for n in nodes}
    trends = [n for n in nodes if (n.get("type") or "").startswith("trend")]
    effects = [n for n in nodes if (n.get("type") or "") == "effect"]
    companies = [n for n in nodes if (n.get("type") or "") in ("company", "app")]

    # Cap each tier for readability
    MAX_PER_TIER = 8
    trends = trends[:MAX_PER_TIER]
    effects = effects[:MAX_PER_TIER]
    companies = companies[:MAX_PER_TIER]
    keep_ids = {n["id"] for n in trends + effects + companies}

    if not (trends and effects):
        return None

    fig, ax = plt.subplots(figsize=(11, max(4, max(len(trends), len(effects), len(companies)) * 0.5)))
    ax.set_xlim(0, 3)
    ax.set_ylim(0, max(len(trends), len(effects), len(companies)))
    ax.invert_yaxis()
    ax.axis("off")

    def _layout(items: list[dict], x: float):
        coords = {}
        for i, n in enumerate(items):
            y = i + 0.5
            coords[n["id"]] = (x, y)
            ax.text(x, y, (n.get("name") or "?")[:32],
                    ha="center", va="center",
                    fontsize=8, color=ZINC_950,
                    bbox=dict(facecolor=ZINC_100, edgecolor=ZINC_300, boxstyle="round,pad=0.3", linewidth=0.8))
        return coords

    trend_pos = _layout(trends, 0.4)
    effect_pos = _layout(effects, 1.5)
    company_pos = _layout(companies, 2.6)

    pos = {**trend_pos, **effect_pos, **company_pos}
    for e in edges:
        src, dst = e.get("from"), e.get("to")
        if src in pos and dst in pos and src in keep_ids and dst in keep_ids:
            x1, y1 = pos[src]
            x2, y2 = pos[dst]
            ax.plot([x1 + 0.05, x2 - 0.05], [y1, y2],
                    color=ZINC_300, linewidth=0.8, zorder=0)

    # Tier headers
    ax.text(0.4, -0.5, "Trends", fontsize=10, fontweight="bold",
            ha="center", color=ZINC_800)
    ax.text(1.5, -0.5, "Effects", fontsize=10, fontweight="bold",
            ha="center", color=ZINC_800)
    ax.text(2.6, -0.5, "Companies", fontsize=10, fontweight="bold",
            ha="center", color=ZINC_800)

    return _fig_to_png(fig)
