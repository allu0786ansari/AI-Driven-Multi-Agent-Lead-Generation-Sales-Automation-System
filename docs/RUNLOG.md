# Run Log

Week-by-week record of what was built, how to run it on Windows, and the
test results at the time the week was published. Newest week at the bottom.

---

## Week 0 - Documents (2026-10-14)

- Proposal and technical spec, Revision 2, added under `docs/`.
- No code yet. Development starts in Week 1 on branch `week-1`.

---

## Week 1 - Foundations (2026-10-14) - branch `week-1`

### What was built

| Area | Files | Notes |
|---|---|---|
| App + settings | `backend/app/main.py`, `app/config.py`, `.env.example` | FastAPI app, settings from `backend/.env`, CORS for the Week 4 dashboard |
| Logging | `app/logging_config.py` | JSON or plain logs, trace ID per request and per event, phone/email masking |
| Database | `app/db/models.py`, `alembic/versions/0001_initial_schema.py` | All spec Section 4 tables + `users`; `user_id` on every table; SQLite now, Postgres later |
| Login | `app/auth/*` | bcrypt passwords, JWT; `POST /api/auth/login`, `GET /api/auth/me` |
| Webhooks | `app/webhooks/*` | Signature check, verification handshake, split body into events, store, de-duplicate, wake worker |
| Worker + router | `app/worker/processor.py`, `app/router/dispatcher.py` | DB-backed queue, retries with backoff, deterministic routing (no handlers yet - agents arrive in Week 2) |
| LLM | `app/llm/*` | `LLMClient` interface, Ollama client (JSON-schema output), fake client, structured output with one retry |
| Catalog | `app/catalog/loader.py`, `data/catalog.sample.json` | Validates and imports the catalog; re-import updates by `sku` |
| Tools | `scripts/create_user.py`, `load_catalog.py`, `simulate.py`, `llm_smoke.py` | Create login, import catalog, send signed fake Meta events, check the LLM |
| API | `app/api/system.py` | `GET /api/health`, `GET /api/events` |
| Docs | `docs/meta_setup.md`, `DECISIONS.md` D-001..D-006, spec Rev 3 | Template and opt-in drafts, Meta webhook settings |

### Test results (sandbox, before publishing)

- `pytest`: **100 passed** on Python 3.13.16 and on Python 3.11.17 (Linux).
  - test_webhooks 35, test_system 25, test_worker 11, test_auth 10, test_llm 10, test_catalog 9
- `ruff check` and `ruff format --check`: clean.
- Live run: migrations, user creation, catalog import (8 products), server
  started, handshake returned the challenge, wrong verify token gave 403.
  `python -m scripts.simulate all`:

```
whatsapp message     -> 200 stored 1        status sent/delivered/read -> 200 stored 1 each
ctwa ad message      -> 200 stored 1        lead form            -> 200 stored 1
whatsapp voice note  -> 200 stored 1        facebook comment     -> 200 stored 1
instagram comment    -> 200 stored 1        messenger message    -> 200 stored 1
instagram DM         -> 200 stored 1        instagram echo       -> 200 stored 1
facebook reaction    -> 200 skipped 1       duplicate (2nd)      -> 200 duplicates 1
bad signature        -> 401 Invalid signature
```
  `/api/events` then showed all 13 stored events processed by the live
  worker as `ignored` (expected: no agent handlers until Week 2).
- Bug found and fixed in the live run: log masking hid timestamps and parts
  of trace IDs (D-006); regression tests added.
- Not verifiable in the sandbox: real Meta/WhatsApp delivery, real Ollama,
  Windows itself. Steps 6-7 below verify these on your machine.

### Run it on Windows

**Prerequisites:** Python 3.11, 3.12 or 3.13 from python.org (tick "Add
python.exe to PATH" in the installer) and Git for Windows. All commands are
for PowerShell.

**1. Get the code**
```powershell
git clone https://github.com/allu0786ansari/AI-Driven-Multi-Agent-Lead-Generation-Sales-Automation-System.git
cd AI-Driven-Multi-Agent-Lead-Generation-Sales-Automation-System
git checkout week-1          # or stay on main once the Week 1 PR is merged
cd backend
```

**2. Create the virtual environment and install**
```powershell
py -3.12 -m venv .venv        # use -3.11 or -3.13 if that is what you installed
.\.venv\Scripts\Activate.ps1
# If PowerShell blocks the script:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

**3. Configure**
```powershell
copy .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # copy the output
notepad .env
```
In `.env` set `JWT_SECRET` to the generated value. For now set
`META_APP_SECRET` and `META_VERIFY_TOKEN` to any values you like (replace
them with the real Meta values in step 7). Keep `LLM_PROVIDER=fake` until
Ollama is installed.

**4. Database, login, catalog**
```powershell
python -m alembic upgrade head
python -m scripts.create_user --email you@example.com --business-name "Your Store" --product-category "Kitchen appliances"
python -m scripts.load_catalog
```
Expected: `Running upgrade -> 0001, initial schema`, `Created user ...`,
`Imported: 8 created, 0 updated, 0 unchanged.`

**5. Run the tests**
```powershell
python -m pytest
```
Expected: `100 passed`.

**6. Start the server and send simulated Meta events**
```powershell
python -m uvicorn app.main:app --reload
```
Open a second PowerShell window:
```powershell
cd <repo>\backend
.\.venv\Scripts\Activate.ps1
python -m scripts.simulate all
```
Expected: the same lines as in "Test results" above, ending with
`bad signature -> 401`. Then open http://127.0.0.1:8000/docs, click
**Authorize**, log in with your email and password, and run `GET /api/events`:
13 events, all `ignored`.

**7. (When ready) Ollama and real Meta webhooks**
- Ollama: see `docs/meta_setup.md` section 6, then
  `python -m scripts.llm_smoke` and `GET /api/health?deep=true` (`"llm": "ok"`).
- Public HTTPS for Meta: install ngrok, run `ngrok config add-authtoken <token>`,
  claim your free static domain in the ngrok dashboard, then
  `ngrok http --url=<your-domain>.ngrok-free.app 8000`.
  Put the real `META_APP_SECRET` and your `META_VERIFY_TOKEN` in `.env`,
  restart the server, and configure the three callback URLs from
  `docs/meta_setup.md` section 3. Meta's "Verify and save" must succeed, and
  test events from the App Dashboard must appear in `GET /api/events`.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `Database tables not found` | Run `python -m alembic upgrade head` from the `backend` folder |
| Simulator gets `401 Invalid signature` | Server and simulator read the same `.env`; restart the server after editing `.env` |
| `Cannot reach http://127.0.0.1:8000` | Start the server first (step 6), or use `--url` if you changed the port |
| Port 8000 already in use | `python -m uvicorn app.main:app --port 8001` and `simulate --url http://127.0.0.1:8001` |
| Meta "Verify and save" fails | `META_VERIFY_TOKEN` in `.env` must match the dashboard exactly; ngrok must be running |

### Your actions this week (Day-1 track)
See `docs/meta_setup.md`: business verification, WhatsApp number and display
name, template submission, lead-form opt-in wording. Tell me which customer
languages to support and your real catalog format before Week 2.

### Next: Week 2
RAG over the catalog (embedding model chosen on an evaluation slice),
price/stock guardrails, Send Gate, CTWA instant reply, lead form -> Graph API
fetch -> template send, WhatsApp status handling.
