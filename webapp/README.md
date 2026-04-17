# Prism — Generic UAT Web App

A web app any product manager can use to map an Android/iOS app's screens, infer the navigation flow, and (eventually) generate UAT test plans.

## Architecture

```
┌─ Browser (localhost:3000)            Next.js 14 + Tailwind
│  - Project list
│  - Bulk screenshot upload (drag-drop multi-file)
│  - Claude vision analysis per screen
│  - Flow inference (Claude proposes edges between screens)
│  - Inline editing of screen names/purposes
│
└─ FastAPI (localhost:8000)            SQLite + SQLAlchemy
   - /api/projects        Project CRUD
   - /api/screens/bulk    Multi-file upload + parallel Claude vision
   - /api/infer-flow      Cross-screen flow inference (Claude reasoning)
   - /api/edges           Manual + accepted-from-inference
   - Reuses existing utils.claude_client.ask_vision/ask
```

## Key UX decisions

1. **Bulk upload first** — PMs grab a bunch of screenshots from their phone (or download them) and dump them all at once. Order doesn't matter.
2. **Claude infers the flow** — after upload, the user clicks "Infer flow" and Claude reasons about which screens connect to which, identifies branches (e.g., "By Night vs By Hour"), and proposes the home screen.
3. **Review then accept** — proposed edges show with confidence scores and reasoning. The user accepts edges one at a time or in bulk.
4. **Editable names** — Claude generates initial screen names but the PM can rename anything inline.

## Run it

### One-time setup

```bash
# From repo root
.venv/bin/pip install sqlalchemy aiosqlite websockets python-multipart

# Frontend deps (Node 20+ required)
cd webapp/web && npm install
```

### Start backend

```bash
# From repo root
.venv/bin/python3 -m uvicorn webapp.api.main:app --reload --port 8000
```

API at http://localhost:8000, OpenAPI docs at http://localhost:8000/docs

### Start frontend

```bash
cd webapp/web && npm run dev
```

UI at http://localhost:3000

## What's in Phase 1 (this version)

✅ Project CRUD
✅ Bulk screenshot upload (drag-drop, multiple files at once)
✅ Per-screen Claude analysis (name, purpose, interactive elements)
✅ Flow inference — Claude proposes edges + branches + home screen
✅ Manual edge acceptance/rejection
✅ Inline screen renaming

## What's next (later phases)

- Live device capture via local agent (Phase 2)
- Interactive graph visualization with react-flow (Phase 3)
- Test plan generator from feature description + voice input (Phase 4)
- Bridge to existing CLI for UAT execution (Phase 5)
