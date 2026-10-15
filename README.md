# AI-Driven Multi-Agent Lead Generation & Sales Automation System

Prototype that captures leads from Instagram/Facebook ads, comments and
WhatsApp, answers product questions from a real catalog, qualifies and scores
leads, alerts the salesperson about hot leads, and nurtures inactive ones.

## Documents

| File | What it is |
|------|------------|
| [docs/project_proposal_ai_lead_gen_agent.txt](docs/project_proposal_ai_lead_gen_agent.txt) | Business proposal: goals, scope, architecture, costs, phases |
| [docs/technical_implementation_spec.txt](docs/technical_implementation_spec.txt) | Technical spec: API contract, data schema, build plan |
| [docs/RUNLOG.md](docs/RUNLOG.md) | Week-by-week build log: what was built, how to run it on Windows, test results |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Every change made during development that differs from the proposal/spec, and why |
| [docs/meta_setup.md](docs/meta_setup.md) | Meta/WhatsApp setup checklist, webhook settings, template and opt-in drafts |

## Repository layout

```
backend/     FastAPI service (webhooks, agents, API, worker)
frontend/    React dashboard (Week 4), served by the backend at /
docs/        Proposal, spec, runlog, decisions, Meta setup drafts
```

## Workflow

Each week is built on its own branch (`week-1`, `week-2`, ...) and opened as a
pull request into `main`. The full test suite from all previous weeks must pass
before a week is published. To continue the project in a new Claude
conversation, point it at this repo and ask it to read `docs/RUNLOG.md` and
`docs/DECISIONS.md` first.

## Running it

Backend: Python 3.11-3.13. Dashboard: Node.js 22.12 or newer (24 LTS recommended).

```powershell
cd frontend; npm ci; npm run build; cd ..\backend
python -m uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000 (dashboard) or http://127.0.0.1:8000/docs (API).
`python -m scripts.simulate demo` fills the dashboard with sample conversations.

Full setup on Windows, week by week: [docs/RUNLOG.md](docs/RUNLOG.md).
