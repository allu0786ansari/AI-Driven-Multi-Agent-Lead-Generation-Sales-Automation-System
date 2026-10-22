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
| [docs/meta_setup.md](docs/meta_setup.md) | Meta/WhatsApp setup checklist, webhook settings, template and opt-in drafts, feedback to Meta |
| [docs/docker.md](docs/docker.md) | Running it in Docker: first start, moving existing data, backups, Ollama, ngrok, troubleshooting |
| [docs/demo_script.md](docs/demo_script.md) | Stakeholder demo: a simulated rehearsal and the live version |
| [docs/limitations_and_next_phase.md](docs/limitations_and_next_phase.md) | Known limitations, measured figures, costs and the next-phase plan |

## Repository layout

```
backend/     FastAPI service (webhooks, agents, API, worker)
frontend/    React dashboard (Week 4), served by the backend at /
docs/        Proposal, spec, runlog, decisions, Meta setup, Docker guide, demo script
Dockerfile, docker-compose*.yml   Container build and start (docs/docker.md)
```

## Workflow

Each week is built on its own branch (`week-1`, `week-2`, ...) and opened as a
pull request into `main`. The full test suite from all previous weeks must pass
before a week is published. To continue the project in a new Claude
conversation, point it at this repo and ask it to read `docs/RUNLOG.md` and
`docs/DECISIONS.md` first.

## Running it

**With Docker** (Docker Desktop on Windows, or Docker on Linux) - details in [docs/docker.md](docs/docker.md):

```powershell
copy backend\.env.example backend\.env    # then set JWT_SECRET, INITIAL_USER_EMAIL, INITIAL_USER_PASSWORD
docker compose up -d --build
```

**Without Docker** - backend: Python 3.11-3.13; dashboard: Node.js 22.12 or newer (24 LTS recommended).

```powershell
cd frontend; npm ci; npm run build; cd ..\backend
python -m uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000 (dashboard) or http://127.0.0.1:8000/docs (API).
`python -m scripts.simulate demo` fills the dashboard with sample conversations.

Useful commands (backend folder):

| Command | What it does |
|---|---|
| `python -m scripts.simulate --list` | Send test webhooks (signed like Meta's); `demo` fills the dashboard |
| `python -m scripts.nurture` | Who gets a follow-up next, and why not the others; `run --days-ahead 3` for a demo |
| `python -m scripts.evaluate` | Check the agents against the prototype's quality targets ([data/eval](backend/data/eval/README.md)) |
| `python -m scripts.eval_retrieval` | Compare embedding models for product search |
| `python -m scripts.load_catalog` | Import or update the product catalog |
| `python -m scripts.backup` | Back up the database now; `list`; `restore <file> --yes` (server stopped) |
| `python -m scripts.create_user --email ...` | Add a sign-in account |

In Docker, put `docker compose exec app` in front of each command.

Full setup on Windows, week by week: [docs/RUNLOG.md](docs/RUNLOG.md).
