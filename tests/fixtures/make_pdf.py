"""Synthetic PDF generation for endpoint integration tests (v0.21.4).

The bulk-upload endpoint can only be honestly tested with REAL PDFs —
mocking pypdf hides the latency that caused 3× user-visible CD8 failures.
This helper builds a real PDF in memory using reportlab so tests can:

  - Exercise the full extract + classify + DB-write path
  - Assert wall-clock budget (`time.perf_counter()`)
  - Verify the manifest contract (matched / unmatched / failed / deferred)

reportlab is pinned in `requirements-dev.txt`. If it's missing, callers
should `pytest.xfail` (NOT skip — silent skips are how we ended up with
3× 502s before this was caught in code review).
"""
from __future__ import annotations

import io


def make_synthetic_pdf(text: str, pages: int = 1, page_text_repeat: int = 1) -> bytes:
    """Build a real PDF in memory containing `text` repeated across `pages` pages.

    Returns raw PDF bytes (starts with `%PDF-`). Round-trips with pypdf.

    Args:
        text: body text written on each page. Use this to embed competitor
            names so body_text_match has something to count.
        pages: number of pages. Defaults to 1; bump for "long 10-K" tests.
        page_text_repeat: write `text` this many times per page. Useful for
            inflating occurrence counts without building separate fixtures.
    """
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover — pinned in requirements-dev.txt
        raise RuntimeError(
            "reportlab not installed. It's pinned in requirements-dev.txt — "
            "run `pip install -r requirements-dev.txt` before running tests."
        ) from exc

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER

    for _ in range(max(1, pages)):
        y = height - 72  # start 1 inch below top
        # Wrap long text into 80-char lines
        block = text * max(1, page_text_repeat)
        for i in range(0, len(block), 80):
            line = block[i:i + 80]
            c.drawString(72, y, line)
            y -= 12
            if y < 72:
                c.showPage()
                y = height - 72
        c.showPage()

    c.save()
    return buf.getvalue()
