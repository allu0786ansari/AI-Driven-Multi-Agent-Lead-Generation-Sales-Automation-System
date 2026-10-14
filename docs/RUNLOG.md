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

---

## Week 2 - RAG, Send Gate, instant replies (2026-10-14) - branch `week-2`

### What was built

| Area | Files | Notes |
|---|---|---|
| Product search (RAG) | `app/rag/*` | Embeddings (built-in hashing or Ollama), vectors stored in the database, auto re-embed of changed products, hybrid ranking, budget filters ("under 5000", "5k tak") |
| Search quality check | `scripts/eval_retrieval.py`, `data/eval/retrieval_eval.sample.jsonl` | Top-1 / top-3 / MRR per language for any embedding model |
| Guardrails | `app/agents/guardrails.py` | Price/stock/name placeholders filled from the catalog; checks every number and claim in a reply |
| Replies | `app/agents/responder.py`, `app/agents/engage.py` | Grounded product answers; catalog-only fallback; opt-out words; voice-note/photo handoff |
| Capture Agent | `app/agents/capture.py` | Phone normalization, lead matching, CTWA ad attribution, lead-form fields and opt-in with consent record |
| Send Gate + outbox | `app/messaging/*` | All window/consent/takeover/cost rules; messages sent after the agent's transaction commits; retries; never double-sends |
| Meta clients | `app/integrations/*` | WhatsApp Cloud API sender and lead fetch, each with a fake for testing (`META_API_MODE`) |
| Handlers | `app/handlers/*` | WhatsApp message, WhatsApp delivery status, lead form |
| API | `app/api/leads.py` | `GET /api/leads`, `GET /api/leads/{id}`, `GET /api/products/search` |
| Database | `alembic/versions/0002_*` | Outbox fields, `ctwa_message_at`, `result_note`, `product_embeddings` |
| Docs | spec Rev 4, proposal Rev 3, DECISIONS D-007..D-014 | |

### What now happens, end to end

- **Customer taps a Click-to-WhatsApp ad and asks about a product** → lead created with the ad ID → product search → reply with real prices → sent free (72h free entry window opened).
- **Lead form with the WhatsApp box ticked** → form answers fetched from Meta → lead + consent record → `lead_form_welcome` template sent (marketing, ~₹0.86).
- **Lead form without the box ticked** → lead saved, nothing sent.
- **Any WhatsApp question** → grounded reply inside the 24h window (service rate ~₹0.12).
- **"STOP"** → opted out, one confirmation, silence afterwards.
- **Voice note / photo** → "a team member will reply" + handed to a person; the AI stays silent for that lead.
- **Delivery receipts** → message moves to delivered/read/failed; free messages get cost 0.

Instagram/Facebook comments and DMs are still stored but not answered (Week 3).

### Test results (sandbox, before publishing)

- `pytest`: **186 passed** on Python 3.13.16 and 3.11.17 (100 from Week 1 + 86 new).
  - test_webhooks 35, test_system 25, test_rag 23, test_send_gate 18, test_capture 15, test_flows 15, test_guardrails 15, test_worker 11, test_auth 10, test_llm 10, test_catalog 9
  - 5 Week 1 tests were updated because they assumed no event handlers existed (now WhatsApp messages are handled); their checks are otherwise unchanged.
- `ruff check` / `ruff format --check`: clean.
- **Migration on real data**: the Week 1 sandbox database (1 user, 8 products, 14 events) upgraded `0001 -> 0002` without loss.
- **Search quality** (`python -m scripts.eval_retrieval`, built-in hashing embedder):

```
language   queries   top-1   top-3    MRR
en              10    100%    100%   1.00
hi               4     25%     75%   0.48
hinglish         6    100%    100%   1.00
ta               4     25%     50%   0.46
tanglish         4    100%    100%   1.00
ALL             28     79%     89%   0.85
```
  Devanagari and Tamil script need a multilingual model - see step 5 below.
- **Live run** (`simulate all`): every scenario returned the expected status; conversations checked in the database:
  - CTWA lead `+919876543211`: source `ctwa_ad`, ad ID recorded, reply listed both air fryers with ₹6,799 / ₹4,999, billed `free_entry` ₹0, free window open 72h.
  - Hinglish "induction chulha 3000 tak milega?": budget filter applied, reply offered the ₹2,699 cooktop only.
  - Lead form: welcome template sent with `Form | Air Fryers`, ₹0.8631; no-opt-in form: nothing sent.
  - STOP: opted out + confirmation; voice note: holding reply + handoff; duplicate delivery: one reply.
- **Bugs found in the live run and fixed:**
  1. Simulator sent the CTWA test from the same number as the plain WhatsApp test, hiding the ad attribution - now uses its own number.
  2. Simulated delivery receipts claimed every message was billed as "service", which relabelled the marketing template - receipts no longer include billing info unless asked; regression test added.

### Run it on Windows

Start from your Week 1 setup (`backend` folder, virtual environment active).

**1. Get Week 2**
```powershell
git checkout main
git pull                      # after merging the Week 2 PR; or: git checkout week-2
pip install -r requirements-dev.txt
```

**2. Add the new settings**
Open `.env.example`, copy the block under `# ---- Week 2 ----` to the end of
your `.env`, and set `BUSINESS_NAME`. Keep `META_API_MODE=fake` for now:
messages are recorded in the database, nothing goes to WhatsApp.

**3. Upgrade the database and build the search index**
```powershell
python -m alembic upgrade head          # Running upgrade 0001 -> 0002
python -m scripts.load_catalog          # ... Search index (hashing-v1-512): 8 embedded
python -m pytest                        # 186 passed
```

**4. Try the flows**
```powershell
python -m uvicorn app.main:app --reload
# second window:
python -m scripts.simulate all
python -m scripts.simulate whatsapp --phone 919811112222 --text "induction chulha 3000 tak milega?"
```
Then in http://127.0.0.1:8000/docs (Authorize first): `GET /api/leads`, open a
lead with `GET /api/leads/{lead_id}` to see the conversation, and try
`GET /api/products/search` with `q = air fryer under 6000`.

**5. Choose the embedding model (needs Ollama, about 1.2 GB download)**
```powershell
ollama pull bge-m3
python -m scripts.eval_retrieval --provider ollama --model bge-m3 --show-misses
ollama pull nomic-embed-text
python -m scripts.eval_retrieval --provider ollama --model nomic-embed-text
```
Pick the model with the best `ALL` and per-language top-3, then in `.env`:
`EMBEDDING_PROVIDER=ollama`, `EMBEDDING_MODEL=<model>`, and run
`python -m scripts.load_catalog` again. Send me the two result tables.
Better still: replace the queries in `data/eval/retrieval_eval.sample.jsonl`
with 30-50 real customer messages first.

**6. Real AI replies (needs Ollama chat model from Week 1)**
`LLM_PROVIDER=ollama` in `.env`, restart the server, run
`python -m scripts.simulate whatsapp --text "6 litre air fryer kitne ka hai?"`.
The reply should now be written by the model (still with catalog prices). If
the model's reply fails the checks, you will see the catalog-only reply and
the reason in `GET /api/events` (`result_note`).

**7. Real WhatsApp (when your Meta app and number are ready)**
In `.env`: `META_API_MODE=live`, `WHATSAPP_ACCESS_TOKEN`,
`WHATSAPP_PHONE_NUMBER_ID`, `META_PAGE_ACCESS_TOKEN`, and your real
`META_APP_SECRET`. With ngrok running (Week 1 step 7), message your business
number from your own phone: you should get the reply within seconds. Lead
forms need the `lead_form_welcome` template approved first.

### Troubleshooting

| Symptom | Fix |
|---|---|
| Every reply is "Here are the options that match: ..." | Expected with `LLM_PROVIDER=fake`. With Ollama, check `result_note` in `GET /api/events` for the reason |
| Reply says "A member of our team will reply" and the AI then stays silent | No product matched and the LLM failed, or the customer sent media / asked for a person. The lead has `is_human_takeover = true`; Week 4 adds the "release" button |
| `eval_retrieval` says "Embedding failed" | Start Ollama and `ollama pull <model>` |
| Live mode: message `failed` with code 131047 | More than 24h since the customer's last message - free-form not allowed; a template is needed |
| Live mode: lead form event `failed` | Check `META_PAGE_ACCESS_TOKEN` has `leads_retrieval`; see the error in `GET /api/events` |

### Your actions this week
- Run steps 1-4, and step 5 if Ollama is installed; send me the eval tables.
- Confirm customer languages and, if possible, 30-50 real past customer messages (they make step 5 meaningful).
- Template approval and business verification from `docs/meta_setup.md` (still pending on Meta's side).

### Next: Week 3
Comment-to-lead (one private reply per comment), Instagram/Messenger DM
conversations, Qualify Agent (slot extraction + scoring rubric), hot-lead
alerts by email/Telegram, Google Sheets mirror.
