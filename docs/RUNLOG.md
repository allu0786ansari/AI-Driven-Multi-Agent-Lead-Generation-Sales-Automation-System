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

---

## Week 3 - Comments, DMs, qualification, alerts, Sheets mirror (2026-10-14) - branch `week-3`

### What was built

| Area | Files | Notes |
|---|---|---|
| Comment-to-lead | `app/agents/comments.py` | Intent (LLM + rules), one private reply ending with a question, public reply, spam hidden, complaints handed off |
| Instagram / Messenger | `app/agents/capture.py`, `app/handlers/meta.py`, `app/integrations/meta_messaging.py` | DM leads, private replies, Messenger ID linked from the private reply, 24h DM window, 7-day private-reply window |
| One flow for all channels | `app/agents/engage.py` | Opt-out, takeover, "call me" handoff, media handoff, product reply + next question |
| Qualify Agent | `app/qualify/*` | Details from LLM + rules, scoring rubric, hot/warm/cold, next question, summary; rules editable per business |
| Post/ad -> product links | `app/catalog/links.py` | "How much is this?" under a linked post/ad gets that product's price |
| Alerts | `app/alerts/*` | Hot lead / handoff / customer replied; log mode, email, Telegram; never sent twice |
| Lead mirror | `app/sheets/mirror.py` | CSV (opens in Excel) or Google Sheet via service account; rewritten only on change |
| API | `app/api/leads.py` | `GET/PUT /api/settings/qualification`, `GET/PUT /api/settings/content-products`, `GET /api/notifications`; lead detail shows score, rationale, details |
| Database | `alembic/versions/0003_*` | `business_settings`, `notifications`, `leads.facebook_user_id` |
| Docs | spec Rev 5, proposal Rev 4, DECISIONS D-015..D-021, meta_setup sections 7-9 | |

### What now happens

- **Instagram comment "price?"** → lead (Instagram ID = their DM ID) → one private DM with prices, ending with a question → public reply "We've sent you a message" → when they answer in DMs the same lead continues.
- **Facebook comment** → same, through Messenger; their Messenger ID is learned from the private reply so the conversation stays one lead.
- **Spam comment** → hidden. **Compliment/emoji** → ignored. **Complaint** → "sorry, a person will message you" + handed off + alert.
- **Every message** updates product, quantity, budget, timeline, city, intent → score 0-100 → hot (>= 70) / warm (>= 40) / cold. Each reply ends with the next unanswered question.
- **Lead turns hot** → alert once. **"Call me" / voice note / complaint** → handed to a person + alert. **Customer writes during a takeover** → alert, AI stays quiet.
- **Lead mirror** → `backend/data/leads_mirror.csv` refreshed every 60 s when something changed.

### Test results (sandbox, before publishing)

- `pytest`: **245 passed** on Python 3.13.16 and 3.11.17 (186 from Weeks 1-2 + 59 new).
  - test_webhooks 35, test_comments_dms 29, test_system 25, test_rag 23, test_send_gate 18, test_qualify 17, test_capture 15, test_flows 15, test_guardrails 15, test_worker 11, test_auth 10, test_llm 10, test_alerts_sheets 9, test_catalog 9, test_flows_week3 4
  - 4 earlier tests were updated because their expectation changed by design: Instagram is now supported (send gate reason), every event type now has a handler (2 worker tests), the Instagram comment in the events API test is now processed.
- `ruff check` / `ruff format --check`: clean.
- **Migration on real data**: Week 2 sandbox database (7 leads, 14 messages, 32 events, 8 vectors) upgraded `0002 -> 0003` without loss.
- **Live run** (`simulate all` with the new scenarios), checked in the database:
  - IG hot conversation (`priya_k`): comment → private + public reply → 2 DMs → score 100, **hot**, alert logged. Summary: "Wants: Air Fryers · Qty 2 · Budget ₹15,000 · Timeline: now · City: Chennai".
  - WhatsApp hot conversation: score 100, hot, alert; summary with the exact 6L model.
  - Spam comment hidden; Facebook complaint: apology private reply + handoff alert; "Can someone call me please?": short reply + handoff alert; Messenger ID linked from the private reply.
  - CSV mirror: 17 leads, 22 columns, UTF-8 BOM present.
- **Found in the live run and fixed** (regression tests added for each):
  1. "How much is this?" under an ad got only a generic reply → post/ad → product links (D-018).
  2. A "nothing within the stated budget" note appeared when the message named no product at all.
  3. Instagram comments were time-stamped when processed, not when Meta delivered them, so they sorted after later DMs.
  4. (Found by the tests before the run) the reply asked "Which product are you interested in?" right after the customer named one, and "ok thanks" from a hot lead caused an unnecessary handoff → next question now considers the product search, and product-less messages use the earlier interest (D-021).

### Run it on Windows

**1. Get Week 3** (after merging the Week 3 PR, or `git checkout week-3`)
```powershell
git checkout main
git pull
pip install -r requirements-dev.txt     # adds the cryptography package (Google Sheets signing)
```

**2. Settings** - copy the `# ---- Week 3 ----` block from `.env.example` to the end
of your `.env`. Defaults are safe: alerts only logged, mirror written as CSV.

**3. Upgrade and test**
```powershell
python -m alembic upgrade head      # Running upgrade 0002 -> 0003
python -m pytest                    # 245 passed
```

**4. Try it**
```powershell
python -m uvicorn app.main:app --reload
# second window:
python -m scripts.simulate ig-hot
python -m scripts.simulate wa-hot
python -m scripts.simulate fb-complaint
python -m scripts.simulate ig-spam
python -m scripts.simulate human
```
Then in http://127.0.0.1:8000/docs (Authorize first):
- `GET /api/leads` - the hot leads show `status: hot` and a summary
- `GET /api/notifications` - hot-lead and handoff alerts
- `GET /api/settings/qualification` - change questions/weights with `PUT`
- open `backend\data\leads_mirror.csv` in Excel (close it again before the next refresh)

**5. Link a post or ad to a product** - in `/docs`, `PUT /api/settings/content-products`
with `{"links": {"17900000000000042": "AF-6L-WIN"}}`, then
```powershell
python -m scripts.simulate ig-comment --media 17900000000000042
```
`GET /api/leads` shows the commenter interested in the 6L air fryer, and the
private reply (in the lead detail) lists its price. With real Instagram, use the
media ID shown in the lead's source detail.

**6. Real alerts** - follow `docs/meta_setup.md` section 8 (Telegram takes 2
minutes), set `ALERTS_MODE=live`, restart the server, then
```powershell
python -m scripts.simulate human --lead-phone 919811199999
```
A "Needs a person" alert arrives in Telegram/email. Use a new `--lead-phone`
each time (a lead already handed to a person does not alert again for the
same reason).

**7. Real Instagram/Messenger** (Meta app ready) - `docs/meta_setup.md` section 7,
`META_API_MODE=live`, comment "price?" on your own post from another account.

**8. Google Sheet instead of CSV** - `docs/meta_setup.md` section 9.

### Troubleshooting

| Symptom | Fix |
|---|---|
| Comment got the generic "What would you like to know" reply | Post not linked to a product (see step 5), or no product words in the comment |
| Lead stays cold although the customer gave details | Check `GET /api/leads/{id}` → `qualify_slots` and `score_rationale`; adjust weights with `PUT /api/settings/qualification` |
| `leads_mirror.csv` not updating | It is open in Excel (Windows locks it); close it, it refreshes within a minute |
| No Telegram alert | `ALERTS_MODE=live`, `ALERT_CHANNELS` includes `telegram`, you messaged the bot once; errors appear in `GET /api/notifications` |
| Live Instagram send fails with a permissions error | Check `instagram_manage_messages` / comment permissions and "Connected tools" in the Instagram app |

### Your actions this week
- Steps 1-4 on your PC; tell me if anything fails.
- Decide what makes a lead "hot" for your business; I can set the defaults for you if you tell me (e.g. "budget above ₹5,000 and buying this week").
- Set up Telegram alerts (section 8) - it is the quickest way to see the system working for you.

### Next: Week 4
React dashboard: lead inbox and detail, take over / release, manual messages
through the Send Gate, live notifications, settings pages (qualification,
post/ad links).

---

## Week 4 - Dashboard (2026-10-15) - branch `week-4`

### What was built

| Area | Files | Notes |
|---|---|---|
| Dashboard | `frontend/` | React 19 + Vite + TypeScript + Tailwind 4; sign-in, Leads, Alerts, Products, Settings, Activity; works on a phone |
| Lead inbox | `frontend/src/pages/Inbox.tsx`, `app/api/leads.py` | Views Needs you / Open / All / Closed with counts, search, channel filter, last message, unread alerts, who is handling it |
| Lead page | `frontend/src/pages/lead/*` | Conversation across channels with the assistant's, your and automatic messages told apart; score and why; what the customer told us; take over / hand back; mark as Contacted / Bought / Lost; assign |
| Replying | `app/leads/service.py`, `app/api/leads.py`, `Composer.tsx` | Free text while the window is open, approved template otherwise, nothing if opted out; through the Send Gate; replying takes over automatically; never sent twice |
| Live updates | `app/api/ws.py`, `frontend/src/lib/live.ts` | `WS /ws/notifications`; new alerts appear as toasts, lists refresh without reloading |
| Alerts page | `app/api/notifications.py` | All alerts, unread first; opening a lead marks its alerts read |
| Products | `app/api/products.py`, `Products.tsx` | Add, edit, delete; "Check what the assistant finds" |
| Settings | `app/api/settings_api.py`, `Settings.tsx` | Questions, score weights, hot/warm thresholds, post/ad links, system health |
| Templates | `app/api/templates.py` | From Meta when live (needs `WHATSAPP_BUSINESS_ACCOUNT_ID`), from `.env` otherwise |
| Serving | `app/web.py` | FastAPI serves the built dashboard at `/` (one address for dashboard, API and webhooks) |
| Database | `alembic/versions/0004_lead_activity.py` | `lead_activity` (status, assign, take over, hand back, manual message) |
| Demo data | `scripts/simulate.py demo` | 8 customers on all channels: hot Instagram lead, WhatsApp ad lead, voice note, Facebook complaint, Messenger, spam |
| Docs | spec Rev 6, proposal Rev 5, DECISIONS D-022..D-028 | |

### Test results (sandbox, before publishing)

- `pytest`: **274 passed** on Python 3.13.16 and 3.11.17 (245 from Weeks 1-3 + 29 new).
  - test_webhooks 35, test_comments_dms 30, test_system 25, test_qualify 24, test_rag 23, test_dashboard_api 21, test_send_gate 18, test_capture 15, test_flows 15, test_guardrails 15, test_worker 11, test_auth 10, test_llm 10, test_alerts_sheets 9, test_catalog 9, test_flows_week3 4
  - `test_dashboard_api` covers the inbox views and counts, reply options, manual WhatsApp and Instagram messages, template rules, take over / hand back, another business's lead (404), alerts read, product editing, templates from Meta, the WebSocket (including a bad token), and dashboard serving (including attempts to read files outside it).
- `ruff check` / `ruff format --check`: clean.
- Dashboard: `npm run typecheck` clean, **30 unit tests passed** (Vitest), production build 367 kB JavaScript (113 kB gzipped).
- **Browser test** (Playwright, Chromium, 1440x900 and a 390x844 phone), **7 passed** against a server loaded with the demo:
  wrong password message; inbox puts hot and handed-over leads first; hot Instagram lead: read, take over, reply, hand back, mark as Bought; a WhatsApp "call me" arrives while the inbox is open and appears live with a toast; add a product and the assistant finds it; change the hot threshold and see it saved; phone: inbox, conversation, details, no sideways scrolling.
- **Migration on real data**: the Week 3 sandbox database (18 leads, 55 messages, 9 alerts, 57 events) upgraded `0003 -> 0004` without loss.
- **Found by reading the browser screenshots and fixed** (regression tests for each, D-028):
  1. Meena's budget message ("Budget 15000. Coimbatore delivery this week?") was answered with a kettle.
  2. "Need 2, one for my mother" was not read as a quantity, so the assistant kept asking how many.
  3. A reply showed above the voice note it answered (sorting by timestamp text).
  4. Instagram DM customers showed as "Unnamed customer".
  5. A lead the assistant handed over said "You're handling this" to everyone.
  6. Narrow-width layout: the "Mark as" menu stretched to full width and cut the customer's name; toasts covered the Send button.
  7. `GET /api/events` did not return `result_note`, which earlier run logs refer to.

### Run it on Windows

**1. Get Week 4** (after merging the Week 4 PR, or `git checkout week-4`)
```powershell
git checkout main
git pull
cd backend
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt     # no new Python packages this week
```

**2. Install Node.js** (once) - the 24 LTS installer from https://nodejs.org, or
```powershell
winget install OpenJS.NodeJS.LTS
```
Close and reopen PowerShell, then `node --version` should show v22.12 or newer.

**3. Settings** - copy the `# ---- Week 4 ----` block from `.env.example` to the
end of your `.env`, and change `DASHBOARD_URL` to `http://127.0.0.1:8000`
(remove the `/docs` from Week 3) so alert messages link to the lead's page.

**4. Upgrade and test the backend**
```powershell
python -m alembic upgrade head      # Running upgrade 0003 -> 0004, lead activity
python -m pytest                    # 274 passed
```

**5. Build the dashboard** (again after every update)
```powershell
cd ..\frontend
npm ci                              # first time ~1 minute
npm test                            # 30 passed
npm run build                       # writes frontend\dist
cd ..\backend
```

**6. Use it**
```powershell
python -m uvicorn app.main:app --reload
# second window (backend folder, venv active):
python -m scripts.simulate demo
```
Open http://127.0.0.1:8000 and sign in with the account you created in Week 1
(`scripts.create_user`). Try:
- **Leads → Needs you**: meena.cooks (hot, 100), Rahul Sharma (complaint), Lakshmi R (voice note).
- Open **meena.cooks**: the score and why on the right; **Take over**, write a reply, **Send**; then **Hand back to assistant**; **Mark as… → Bought** moves her to Closed.
- Open **Arjun Mehta** (WhatsApp ad): free text is allowed and the window countdown shows; **Send an approved template instead** shows the template form.
- Keep the inbox open and run `python -m scripts.simulate human --lead-phone 919811155555` in the second window: a "Needs a person" toast appears without reloading (use a new number each time).
- **Products**: add or edit a product, then type a customer message under "Check what the assistant finds".
- **Settings**: change the questions, weights or "Hot from", link a post to a product.
- **Activity**: every webhook event and what happened to it.
- On your phone (same Wi-Fi): start the server with `--host 0.0.0.0`, allow it in the Windows firewall prompt, open `http://<your PC's IP>:8000`. Through your ngrok domain the dashboard works from anywhere (sign-in required).

**7. Optional: work on the dashboard with instant reload**
```powershell
cd ..\frontend
npm run dev                          # http://127.0.0.1:5173, uses the backend on :8000
```

**8. Optional: run the browser tests** - they change data, so use a separate
test database:
```powershell
# window 1 (backend folder)
$env:DATABASE_URL = "sqlite:///data/e2e.db"
python -m alembic upgrade head
python -m scripts.create_user --email owner@store.test --business-name "Sharma Kitchen Store" --password sandbox-pass-123
python -m scripts.load_catalog
python -m uvicorn app.main:app
# window 2 (backend folder)
$env:DATABASE_URL = "sqlite:///data/e2e.db"
python -m scripts.simulate demo
# window 3 (frontend folder)
npx playwright install chromium      # once
$env:E2E_META_APP_SECRET = "<META_APP_SECRET from your backend\.env>"
npm run e2e                          # 7 passed; screenshots in frontend\e2e\screenshots
```
Delete `backend\data\e2e.db` afterwards. Open a new window (or
`Remove-Item Env:DATABASE_URL`) before using your real database again.

### Troubleshooting

| Symptom | Fix |
|---|---|
| http://127.0.0.1:8000 says "The dashboard is not built yet" | Step 5 (`npm ci`, `npm run build` in `frontend`) |
| `npm ci` warns about an unsupported engine, or the build fails on Node | Install Node 24 LTS (step 2) and reopen PowerShell |
| `npm` is not recognized | Reopen PowerShell after installing Node; check "Add to PATH" in the installer |
| Bottom-left says "Offline - refreshing every 30 s" | The server restarted or the connection dropped; it reconnects by itself. Through ngrok, WebSockets work on the same domain |
| Send button replaced by "Needs a template" | The WhatsApp 24-hour window is closed; use "Send an approved template instead" |
| No templates to choose | Template names come from `TEMPLATE_*` in `.env`; with `META_API_MODE=live` set `WHATSAPP_BUSINESS_ACCOUNT_ID` to list the approved ones from Meta |
| Alert links open the API docs | `DASHBOARD_URL` still ends with `/docs` (step 3) |
| Dashboard looks old after an update | Run `npm run build` again, then Ctrl+F5 |
| Sign-in fails with the right password | Is it the account from `scripts.create_user` in the database the server uses? (`DATABASE_URL` left set from step 8?) |

### Your actions this week
- Steps 1-6 on your PC; tell me if anything fails or looks wrong (a screenshot helps).
- Use the dashboard as the salesperson would for a day of demo messages: what is missing, what is confusing, what should be first?
- Look up your WhatsApp Business Account ID (Meta Business Settings) for the template list.

### Next: Week 5
Nurture follow-ups (opted-in leads only, capped), opt-out across channels and
languages, erase a lead's personal data, analytics and costs page, and the
full evaluation run.

---

## Week 5 - Follow-ups, consent, erasure, analytics, evaluation (2026-10-18) - branch `week-5`

### What was built

| Area | Files | Notes |
|---|---|---|
| Follow-ups (Nurture Agent) | `app/nurture/*`, `app/worker/processor.py` | Approved templates for customers who agreed to offers and went quiet: day 2 / 5 / 10, business hours only, stop when the customer writes, at most 6 per customer, never twice |
| Consent | `app/agents/consent.py`, `app/agents/engage.py`, `app/messaging/send_gate.py` | Opt-out in English, Hindi, Hinglish, Tamil, Tanglish and the template's stop button; START opts back in; the assistant asks once for WhatsApp opt-in; customers who opted out are still answered when they write |
| Complaints in DMs | `app/qualify/slots.py`, `app/agents/engage.py` | "It stopped working", "my order hasn't arrived": apology, a person takes over, alert |
| Erase personal data | `app/leads/erase.py`, `DELETE /api/leads/{id}/personal-data` | Removes identity and message text everywhere; keeps an anonymous row for the numbers |
| Analytics | `app/analytics/service.py`, `frontend/src/pages/Analytics.tsx` | Leads per day, from lead to sale, sources, first-reply speed, follow-ups and consent, hot-label agreement, costs |
| Settings | `frontend/src/pages/Settings.tsx`, `/api/settings/nurture`, `/api/nurture/*` | Follow-up sequence, offer text, limits, sending hours, what is planned next, "Check now" |
| Lead page | `frontend/src/pages/lead/*` | Next follow-up or why not, what the customer agreed to, "Is this lead really hot?", erase |
| Evaluation | `scripts/evaluate.py`, `data/eval/*` | Replays labelled messages through the real agents and checks the proposal's targets; report in `data/eval/reports/` |
| Command line | `scripts/nurture.py` | See planned follow-ups; send what would be due in N days (demo) |
| Database | `alembic/versions/0005_*` | `leads.opt_in_asked_at`, `leads.erased_at` |
| Docs | spec Rev 7, proposal Rev 6, DECISIONS D-029..D-035, meta_setup sections 4-5 | |

### What now happens

- **Customer goes quiet** (agreed to offers): 2 days later, between 10:00 and 19:00, they get the
  "check-in" template; 10 days later the "last reminder" (the "offer" one too, once you set an
  offer in Settings). Any message from them stops it; it starts again if they go quiet again.
- **WhatsApp ad / DM customer** answers the qualifying questions -> the reply ends with
  "Can we send you offers and reminders on WhatsApp? Reply YES or NO." -> "Haan" is recorded as
  opt-in, with that wording.
- **"band karo" / "STOP" / Stop button** -> no more follow-ups or templates. If they ask
  something later, they get an answer. "START" -> subscribed again.
- **"The mixer stopped working"** in a DM -> "Sorry to hear that! A member of our team will look
  into it..." + handed to you + alert.
- **Customer asks you to delete their data** -> lead page, "Erase personal data".

### Test results (sandbox, before publishing)

- `pytest`: **381 passed** on Python 3.13.16 and 3.11.17 (274 from Weeks 1-4 + 107 new).
  - test_consent_nurture 80, test_webhooks 35, test_comments_dms 30, test_system 25, test_qualify 24, test_rag 23, test_week5_quality 21, test_dashboard_api 21, test_send_gate 18, test_capture 15, test_flows 15, test_guardrails 15, test_worker 11, test_auth 10, test_llm 10, test_alerts_sheets 9, test_catalog 9, test_erase_analytics 6, test_flows_week3 4
  - 1 earlier test changed by design: after STOP, a later question from the customer is now answered (D-030).
  - Mutation checks: removing the follow-up takeover rule, the "no templates after opt-out" rule, or the raw-event scrubbing each makes a test fail.
- `ruff check` / `ruff format --check`: clean.
- Dashboard: typecheck clean, **35 unit tests passed**, production build 391 kB JavaScript (120 kB gzipped).
- **Browser test** (Playwright, desktop and phone), **10 passed**. This includes 3 new tests:
  - Follow-ups: the planned list, then "Check now". The follow-up shows in Arjun's conversation as the template text he received, and his opt-in shows on the lead page.
  - Analytics: the chart read with the keyboard, and the period switch.
  - Erasing a customer's data.
- **Migration on real data**: the Week 4 sandbox database (18 leads, 55 messages, 9 alerts, 57 events, 4 consent records) upgraded `0004 -> 0005` without loss.
- **Live run** (`simulate demo`, then `scripts.nurture run --days-ahead 3`):
  - Arjun was asked once, said "Haan", and the opt-in was recorded as `ctwa_message`.
  - 2 follow-ups were planned and then delivered by the running server: Arjun's cost ₹0 (inside the free 72-hour ad window), the lead-form customer's ₹0.86.
  - No server errors.
- **Evaluation** (`python -m scripts.evaluate`, keyword rules only, no model; the sandbox cannot run Ollama):

  | Check | Sample set, 82 cases | Held-out, 24 cases, first run | Target |
  |---|---|---|---|
  | Comment: right action | 36/36 (100%) | 6/8 (75%) | >= 90% |
  | Message: right product, or handed to a person | 39/41 (95.1%) | 13/14 (92.9%) | >= 95% |
  | No wrong price or invented offer | 41/41 | 14/14 | all |
  | Opt-out recognised, no false opt-outs | 7/7 | 1/3 (no false ones) | all |

  The sample set was used to find and fix problems, and the held-out set was written afterwards
  and run once. Its first-run column is the honest picture of the keyword rules on new messages.
  After the fixes in D-035 both sets pass, but the held-out set is no longer unseen.
  The two sample misses are Hindi- and Tamil-script product questions. The built-in search
  cannot read those scripts, so they are handed to a person, which is safe. The multilingual
  embedding model fixes this (D-008).
- **Found and fixed** (regression tests for each; details in D-035):
  1. Complaints and order problems in DMs got a product list instead of a person.
  2. "2 mixer" was not read as a quantity.
  3. "Beautiful colour" was read as a question.
  4. "Stop sending these offers" and "Unsubscribe me" were not opt-outs.
  5. A lead-form customer who wrote back was counted as "never answered".
  6. The planned follow-ups list in Settings was up to 10 minutes stale.
  7. The opt-in message asked two questions at once.
  8. Follow-ups showed as raw template values in the inbox.
  9. Old leads would have received follow-ups right after the upgrade; customers quiet for more than 30 days now get none.
  10. Small layout issues: erased leads, the phone Analytics page, "0 s".

### Run it on Windows

**1. Get Week 5** (after merging the Week 5 PR, or `git checkout week-5`)
```powershell
git checkout main
git pull
cd backend
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt     # adds tzdata (time zones on Windows)
```

**2. Settings** - copy the `# ---- Week 5 ----` block from `.env.example` to the
end of your `.env` (business time zone, how often follow-ups are checked).

**3. Upgrade and test**
```powershell
python -m alembic upgrade head      # Running upgrade 0004 -> 0005
python -m pytest                    # 381 passed
cd ..\frontend
npm ci
npm test                            # 35 passed
npm run build
cd ..\backend
```

**4. Try it**
```powershell
python -m uvicorn app.main:app --reload
# second window (backend folder, venv active):
python -m scripts.simulate demo
python -m scripts.nurture                       # who gets a follow-up next, and why not the others
python -m scripts.nurture run --days-ahead 3    # send what would be due in 3 days
```
Open http://127.0.0.1:8000:
- **Leads -> All -> Arjun Mehta**: the assistant asked for opt-in, he said "Haan", and after the
  `run` above his conversation shows the follow-up ("Hi Arjun, just checking in about..."). The
  right panel shows "Agreed (said yes in the chat)" and "1 sent so far".
- **Analytics**: leads per day, from lead to sale, speed, follow-ups, costs. Try 7 / 30 / 90 days.
- **Settings -> Follow-ups**: the sequence, offer text, limits, hours, and what is planned next.
- **meena.cooks** (hot): answer "Is this lead really hot?"; Analytics shows the agreement.
- Opt-out and back in, as Arjun:
  ```powershell
  python -m scripts.simulate whatsapp --phone 919845012345 --text "band karo"
  python -m scripts.simulate whatsapp --phone 919845012345 --text "mixer warranty kitna hai?"   # still answered
  python -m scripts.simulate whatsapp --phone 919845012345 --text "START"
  ```
- A complaint in a DM:
  `python -m scripts.simulate whatsapp --phone 919800000077 --text "the mixer I bought stopped working"`
  -> apology, handed to you, alert.
- **Erase**: open any lead, "Erase personal data" at the bottom of the right panel.

**5. Run the evaluation**
```powershell
python -m scripts.evaluate                                                  # the 82-case sample set
python -m scripts.evaluate --file data/eval/agents_holdout.sample.jsonl     # the 24 held-out cases
```
It uses the model in your `.env`. With `LLM_PROVIDER=ollama` (Ollama running, model pulled) it
takes a few minutes on a CPU. The report is saved in `backend\data\eval\reports\`. Then collect
50-100 of your own customers' real messages, label them as described in
`backend\data\eval\README.md`, and run `--file` on them. That is the result that counts.

**6. Before follow-ups go to real customers** - the four templates in `docs/meta_setup.md`
section 4 must be approved in WhatsApp Manager. Until then keep `META_API_MODE=fake`, or switch
follow-ups off in Settings.

**7. Optional: browser tests** - as in Week 4, step 8 (`npm run e2e`, now 10 tests).

### Troubleshooting

| Symptom | Fix |
|---|---|
| `ZoneInfoNotFoundError` or "unknown time zone" at start | `pip install -r requirements-dev.txt` (installs `tzdata`); check `BUSINESS_TIMEZONE` |
| Settings shows nothing planned | Only customers who agreed to offers get follow-ups; the lead page says why for each lead |
| A follow-up shows "Not delivered" (live mode) | The template is not approved yet, or its name differs; check WhatsApp Manager and the names in Settings |
| A customer gets no opt-in question | It is asked once all qualifying questions are answered, only on WhatsApp, only while follow-ups are on |
| `scripts.evaluate` prints boxes instead of Hindi/Tamil | Use Windows Terminal; the report file is always correct UTF-8 |
| Evaluation is slow with Ollama | Normal on CPU (one LLM call per message); try a smaller model, or run the held-out set first |

### Your actions this week
- Steps 1-4 on your PC; tell me if anything fails or looks wrong.
- Submit the four templates (meta_setup section 4) for approval if you haven't yet.
- Start collecting 50-100 real customer messages for the evaluation (no names or numbers).
- With Ollama installed, run step 5 and send me the report: it decides the model choice.

### Next: Week 6
Buffer and demo preparation: one end-to-end run per entry path (ad, lead form, comment) through
to Bought / Lost, failure drills (duplicate webhooks, model down, send failure, closed window,
opted-out customer), the live demo with a test ad and comment, the final model choice from your
evaluation report, known limitations and the next-phase plan. Stretch: send Bought / Lost back
to Meta (Conversions API).
