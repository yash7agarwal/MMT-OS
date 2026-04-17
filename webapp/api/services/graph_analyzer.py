"""Pure-Python graph analysis utilities for the Prism screen graph.

Used by the deeplink_utility planner to find structural issues:
- Orphan screens (no incoming edges) — possibly only reachable via deeplink
- Dead-end screens (no outgoing edges) — confirmation/terminal screens or stuck flows
- Dangling leads_to_hints — element-level navigation pointing to non-existent screens
- Unreachable screens — no path from a designated home screen

No LLM calls. All deterministic.

Inputs use the same dict shapes as routes/screens.py — list of plain dicts (not ORM
objects) so this module is easy to test in isolation.
"""
from __future__ import annotations

from collections import defaultdict, deque


def _by_id(screens: list[dict]) -> dict[int, dict]:
    return {s["id"]: s for s in screens}


def _by_name(screens: list[dict]) -> dict[str, dict]:
    return {s["name"]: s for s in screens}


def find_orphan_screens(
    screens: list[dict],
    edges: list[dict],
    home_screen_id: int | None = None,
) -> list[dict]:
    """Return screens that have no incoming edges (and are not the designated home).

    These are typically only reachable via deeplinks or external entry points,
    making them likely dead-link candidates if the deeplink is broken.
    """
    incoming = {s["id"]: 0 for s in screens}
    for e in edges:
        if e["to_screen_id"] in incoming:
            incoming[e["to_screen_id"]] += 1
    return [s for s in screens if incoming[s["id"]] == 0 and s["id"] != home_screen_id]


def find_dead_end_screens(screens: list[dict], edges: list[dict]) -> list[dict]:
    """Return screens with no outgoing edges.

    Some dead-ends are legitimate (booking confirmation, success screens) — the
    planner-level prompt should ask the user to verify each one is intentional.
    """
    outgoing = {s["id"]: 0 for s in screens}
    for e in edges:
        if e["from_screen_id"] in outgoing:
            outgoing[e["from_screen_id"]] += 1
    return [s for s in screens if outgoing[s["id"]] == 0]


def find_dangling_hints(screens: list[dict]) -> list[dict]:
    """Find elements whose `leads_to_hint` references a name that doesn't exist as a screen.

    Returns a list of dicts: [{screen_id, screen_name, element_label, hint, suggestion}]
    The `suggestion` field is the closest matching real screen name (or None).
    """
    name_to_screen = _by_name(screens)
    all_names = set(name_to_screen.keys())
    out: list[dict] = []
    for screen in screens:
        for el in screen.get("elements") or []:
            hint = (el.get("leads_to_hint") or "").strip()
            if not hint:
                continue
            # Hint may contain "or", multiple possibilities, or be vague — accept any token match
            hint_tokens = [t.strip().strip("'\"") for t in hint.replace(",", " or ").split(" or ")]
            if any(t in all_names for t in hint_tokens):
                continue
            # No match — find closest by substring overlap as a suggestion
            suggestion = _closest_name(hint, all_names)
            out.append({
                "screen_id": screen["id"],
                "screen_name": screen["name"],
                "element_label": el.get("label", "(unlabeled)"),
                "hint": hint,
                "suggestion": suggestion,
            })
    return out


def reachability_from(start_id: int, screens: list[dict], edges: list[dict]) -> set[int]:
    """BFS — return the set of screen ids reachable from `start_id` via outgoing edges."""
    adj: dict[int, list[int]] = defaultdict(list)
    for e in edges:
        adj[e["from_screen_id"]].append(e["to_screen_id"])
    seen = {start_id}
    queue = deque([start_id])
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def find_unreachable_screens(
    screens: list[dict],
    edges: list[dict],
    home_screen_id: int,
) -> list[dict]:
    """Return screens that can't be reached from the home screen by following edges."""
    reachable = reachability_from(home_screen_id, screens, edges)
    return [s for s in screens if s["id"] not in reachable]


def _closest_name(hint: str, names: set[str]) -> str | None:
    """Heuristic match — pick the screen name with the most token overlap with the hint."""
    if not names:
        return None
    hint_tokens = set(hint.lower().replace("-", "_").split("_"))
    best, best_score = None, 0
    for n in names:
        n_tokens = set(n.lower().split("_"))
        score = len(hint_tokens & n_tokens)
        if score > best_score:
            best_score, best = score, n
    return best if best_score > 0 else None
