# Decisions Log

Every change made during development that differs from, or adds detail to,
the proposal (`project_proposal_ai_lead_gen_agent.txt`) or the spec
(`technical_implementation_spec.txt`). Each entry says what changed, why, and
which document section was updated.

Format: `D-NNN` - date - week - title, then Context / Decision / Impact /
Docs updated.

---

## D-000 - 2026-10-14 - Week 0 - Revision 2 of proposal and spec adopted

**Context:** Review of Revision 1 found outdated WhatsApp pricing assumptions,
a missing Instagram DM channel, Google Sheets used as the system of record,
and no webhook security.

**Decision:** Adopt Revision 2 of both documents as the baseline for
development.

**Docs updated:** Both documents, Revision 2 (see their revision notes).

---

## D-001 - 2026-10-14 - Week 1 - One webhook URL per Meta object, not per purpose

**Context:** Spec Rev 2 listed `/webhooks/meta/lead-form`,
`/webhooks/meta/comments`, `/webhooks/meta/messages` and
`/webhooks/whatsapp/messages`. In the Meta App Dashboard a callback URL is set
per webhook *object* (Page, Instagram, WhatsApp Business Account), not per
field. Lead forms (`leadgen`), Facebook comments (`feed`) and Messenger
(`messages`) are all fields of the Page object, so Meta would send all three
to one URL. Facebook comments and Instagram comments also come from
different objects, so a single "comments" URL could not work either.

**Decision:** Three URLs, one per object: `/webhooks/meta/page`,
`/webhooks/meta/instagram`, `/webhooks/whatsapp`. The body is split into
events and each event is classified by its content (`event_type`). Every URL
accepts any Meta payload, so a misconfigured subscription still works.

**Impact:** Meta setup is simpler (three URLs to paste). Agents receive
normalized event types and never depend on which URL was used.

**Docs updated:** Spec Rev 3, Section 3.1.

---

## D-002 - 2026-10-14 - Week 1 - Database table as the work queue (no Redis)

**Context:** Spec Rev 2 left the queue open (BackgroundTasks, Redis-based
arq/RQ, or a DB table). RQ does not run on Windows, and Redis adds a service
to install. FastAPI BackgroundTasks lose work if the server restarts.

**Decision:** `inbound_events` table is both the de-duplication record and
the durable queue. An asyncio worker inside the FastAPI process claims due
events, dispatches them through the router, and retries failures with
exponential backoff (5s, 10s, 20s ...) up to `WORKER_MAX_ATTEMPTS`. The
webhook endpoint wakes the worker immediately, so latency stays in
milliseconds. Events stuck in `processing` for 5 minutes are reclaimed.

**Impact:** Nothing extra to install on Windows; no lost events on restart.
At production scale this becomes a message queue (spec Section 6) without
changing the router or agents.

**Docs updated:** Spec Rev 3, Sections 1, 2 and 4.5 (`processed_events`
renamed and extended to `inbound_events`).

---

## D-003 - 2026-10-14 - Week 1 - Small schema additions

**Decision:**
- `leads.messenger_scoped_id` added: Facebook Messenger and Instagram give the
  same person different scoped IDs, so one column could not hold both.
- `products.sku` added and unique per business: the business's own product
  code, used to update products when the catalog is re-imported instead of
  creating duplicates.
- `users.is_active` added so a login can be disabled without deleting data.
- `inbound_events.user_id` is nullable: events that arrive before the
  business user is created are still stored (and shown by `/api/events`).

**Docs updated:** Spec Rev 3, Sections 4.1, 4.2, 4.5, 4.5a.

---

## D-004 - 2026-10-14 - Week 1 - Added /api/health and /api/events

**Context:** Not in the spec. Without a dashboard (Week 4) there was no way to
see whether Meta deliveries arrive and what happened to them.

**Decision:** `GET /api/health` (database, LLM provider, registered handlers;
`?deep=true` also checks Ollama) and `GET /api/events` (authenticated: recent
webhook events, status, attempts, errors, counts by status).

**Docs updated:** Spec Rev 3, Section 3.2.

---

## D-005 - 2026-10-14 - Week 1 - Fakes and a simulator for services the sandbox cannot reach

**Context:** The development sandbox cannot reach Meta, WhatsApp, Google
Sheets or Ollama's model downloads, and has no GPU.

**Decision:** Every external service sits behind an interface with a real
implementation and a fake (Week 1: `LLMClient` with `OllamaClient` and
`FakeLLMClient`; Meta/WhatsApp/Sheets senders follow the same pattern from
Week 2). `app/dev/payloads.py` builds webhook bodies in Meta's documented
formats; `scripts/simulate.py` signs and sends them to a running server.
`LLM_PROVIDER=fake` in `.env.example` lets the app run before Ollama is
installed.

**Impact:** All flows are tested in the sandbox with fakes. Each week's
RUNLOG lists the steps to verify the real services on the Windows machine.

**Docs updated:** None (implementation detail).

---

## D-006 - 2026-10-14 - Week 1 - Log masking rule tightened after sandbox run

**Context:** The first live run showed the phone-number mask also hiding
timestamps (`2026-10-14 14:04:19`) and digit runs inside trace IDs, which
made logs unreadable.

**Decision:** Mask only stand-alone runs of 10-15 digits (optional `+`,
optional single spaces), and in plain-text logs mask only the message, never
the timestamp or trace ID. Emails are masked to the first character.
Regression tests added for each case seen in the run.

**Docs updated:** None (implementation detail; spec Section 11 already
requires masking).

---

## D-007 - 2026-10-14 - Week 2 - Product vectors in the main database, not ChromaDB

**Context:** The spec allowed ChromaDB or pgvector. ChromaDB adds a heavy
dependency set to install on Windows and a second store to keep in sync with
the catalog. pgvector needs Postgres, and the prototype runs on SQLite.

**Decision:** `product_embeddings` table (vector as JSON, model name, text
hash). Retrieval loads the business's vectors and scores them in Python:
0.65 x cosine + 0.35 x keyword overlap, after price/category/stock filters.
Only new or changed products (or a model switch) are re-embedded.

**Impact:** No extra service; one backup covers everything. Scoring a few
hundred products takes milliseconds. For thousands of products, move scoring
to pgvector behind the same `get_product_context()` function.

**Docs updated:** Spec Rev 4 Sections 1, 2, 4.7; proposal Rev 3 Sections 4, 5, 6, 7.

---

## D-008 - 2026-10-14 - Week 2 - Built-in "hashing" embedder by default; model chosen by measurement

**Context:** The sandbox cannot download embedding models, and the user's
customer languages are not confirmed yet.

**Decision:** `EMBEDDING_PROVIDER=hashing` (word + character-trigram hashing,
no download) is the default so search works on any machine. `scripts/eval_retrieval.py`
measures top-1 / top-3 / MRR per language for any Ollama model on a labelled
query file. A 28-query synthetic starter set ships in `data/eval/`.

First result (hashing, sample catalog): English 100%, Hinglish 100%,
Tanglish 100% top-3; Hindi (Devanagari) 75% and Tamil script 50% top-3.
Native-script messages need a multilingual model (bge-m3 is the first
candidate); switch with `EMBEDDING_PROVIDER=ollama`.

**Docs updated:** Spec Rev 4 Section 1; proposal Rev 3.

---

## D-009 - 2026-10-14 - Week 2 - Transactional outbox for all outgoing messages

**Context:** If an agent called the WhatsApp API directly and a later step in
the same event failed, the worker's retry would send the message again.

**Decision:** The Send Gate writes the message as `queued` in the same
database transaction as the agent's other changes. After the transaction
commits, the worker's outbox step sends it (`sending` -> `sent`). Transient
API errors are retried with backoff (10s, 20s; 3 attempts); permanent errors
mark it `failed`. A message stuck in `sending` (server stopped mid-call) is
never re-sent - it is marked `failed` with "outcome unknown" after 10
minutes for a person to check. Each message has a unique idempotency key
(`reply:<inbound message id>`, `leadform:<leadgen id>`).

**Impact:** A customer never receives the same automatic message twice, and
a crash never silently loses one.

**Docs updated:** Spec Rev 4 Sections 2, 3.3, 4.3.

---

## D-010 - 2026-10-14 - Week 2 - Opt-out keywords and media handoff implemented now (basic)

**Context:** Spec puts opt-out verification in Week 5 and handoff alerts in
Week 3. Without them, Week 2 would answer a "STOP" with a product list, and
answer a voice note it cannot understand.

**Decision:** Basic versions now: STOP / UNSUBSCRIBE / CANCEL / BAND / BAND
KARO / "Stop messages" button -> `opted_out`, consent event, one
confirmation. Voice notes, photos and other media -> holding reply and
`is_human_takeover`. These replies use a new sender value `system`, which is
not silenced by a takeover (a salesperson reply is `salesperson`, an AI reply
is `agent`). Week 3 adds the salesperson alert; Week 5 adds multi-language
opt-out words and the nurture checks.

**Docs updated:** Spec Rev 4 Section 4.3 (sender values).

---

## D-011 - 2026-10-14 - Week 2 - Reply guardrails: placeholders, validation, catalog-only fallback

**Decision:** The LLM sees prices (to compare products) but must write
`{{price:SKU}}`, `{{stock:SKU}}`, `{{name:SKU}}`; code fills them. The final
text is validated (money = catalog price, other numbers and percentages must
appear in product data or the customer's message, no unsupported
offer/discount/EMI/free-delivery claims). Any failure - LLM down, invalid or
empty output, unknown SKU, failed validation - produces a deterministic
catalog-only reply. If no product matches either, a holding message is sent
and the lead is handed to a person. The Send Gate re-runs validation as a
second check.

**Impact:** Customers always get a correct answer quickly. With
`LLM_PROVIDER=fake` (sandbox), every reply is the catalog-only version.

**Docs updated:** Spec Rev 4 Section 10.

---

## D-012 - 2026-10-14 - Week 2 - Delivery receipts correct the cost estimate

**Decision:** WhatsApp status webhooks move a message forward only (sent <
delivered < read; `failed` always applies, with Meta's error code). When the
receipt says the message was not billable (free entry point / free customer
service), its estimated cost becomes 0 and the lead's total is corrected. A
receipt for an unknown message ID is retried up to 3 times (it can arrive a
moment before our "sent" record is saved), then ignored.

**Docs updated:** Spec Rev 4 Section 4.3.

---

## D-013 - 2026-10-14 - Week 2 - Read-only lead API and product search now

**Context:** Without them the Week 2 flows can only be checked by reading
the database.

**Decision:** `GET /api/leads`, `GET /api/leads/{id}` (with conversation)
from spec 3.2 brought forward in read-only form; new
`GET /api/products/search?q=` shows the scores the agents see. Week 4 adds
the write endpoints for the dashboard.

**Docs updated:** Spec Rev 4 Section 3.2.

---

## D-014 - 2026-10-14 - Week 2 - Cost estimates are configuration, not code

**Decision:** Per-message INR rates are settings (`WA_RATE_MARKETING_INR=0.8631`,
`WA_RATE_UTILITY_INR=0.12`, `WA_RATE_SERVICE_INR=0.12`). Service replies are
estimated at the utility rate, as Meta bills them since 1 October 2026. The
monthly free service-message allowance some providers report is not
deducted, so estimates err on the high side. Verify the rates against
Meta's current rate card before budgeting.

**Docs updated:** `backend/.env.example`.

---

## D-015 - 2026-10-14 - Week 3 - How Instagram and Messenger messages are sent and linked

**Decision:**
- Private replies, DMs, public comment replies and hiding comments go through
  the Page's Graph API endpoints with the Page token by default (Instagram via
  Facebook Login). `INSTAGRAM_API_BASE_URL=https://graph.instagram.com` switches
  Instagram to the Instagram-Login API (own token and account ID).
- Facebook comments are answered privately through Messenger. A Facebook
  commenter's ID is not their Messenger ID, so the lead stores it in a new
  column `leads.facebook_user_id`; the private reply's response returns the
  Messenger ID (`recipient_id`), which is saved so their later DMs join the
  same lead. Instagram commenter IDs are already the DM IDs.
- No per-message fee on these channels (`pricing_category = free`). DMs are
  allowed within 24h of the lead's last DM on that channel; a comment does not
  open that window. One private reply per comment within 7 days, enforced by the
  idempotency key `private_reply:<comment id>` and the Send Gate.

**Docs updated:** Spec Rev 5 Sections 2, 3.3, 4.1, 9.

---

## D-016 - 2026-10-14 - Week 3 - Comment intent: LLM plus keyword rules

**Decision:** The LLM classifies comments (purchase interest, product question,
complaint, spam, other). Keyword rules (English/Hinglish/Tanglish, incl. "pp",
"evlo", "kitna") are used when no model is configured or the model fails, and
override the model when they clearly see spam links or a complaint. Spam is
hidden best-effort (a failure to hide is logged, not retried). "Other"
(compliments, emojis, tags) creates no lead.

**Docs updated:** Spec Rev 5 Section 10.

---

## D-017 - 2026-10-14 - Week 3 - Qualification in the same LLM call as the reply

**Context:** A separate extraction call per message would double the wait
for a local model.

**Decision:** The reply call also returns `extracted` details (product,
quantity, budget, timeline, city, intent, sentiment, wants_human). Keyword
rules run on every message as well, so qualification works without an LLM;
the product comes from the product search (category when the message names it,
exact product only when it clearly beats the next match). Newer values replace
older ones. Score = configurable points (defaults: product 20, budget 10 +10 if
it covers the product, now/this week 25, this month 15, later 5, quantity 10,
bulk 5, city 10, ready to buy 15, asking about price 8, 2+ messages 5,
unhappy -10), capped at 100; hot >= 70, warm >= 40. The agent never overwrites
a status the salesperson set (contacted/converted/lost). Rules and questions
are stored per business (`business_settings`) and editable through
`GET/PUT /api/settings/qualification` now; the Week 4 dashboard adds a form.

`conversation_summary` is built from the extracted details ("Wants: X · Qty 2
· Budget ₹15,000 · City: Chennai") instead of being LLM-written, so it never
contains invented facts.

**Docs updated:** Spec Rev 5 Sections 3.2, 3.3, 4; proposal Rev 4.

---

## D-018 - 2026-10-14 - Week 3 - Link posts and ads to products (found in the sandbox run)

**Context:** The most common ad comment, "How much is this?", names no
product. In the live run it got only a generic reply.

**Decision:** `PUT /api/settings/content-products` maps a content ID
(Instagram media ID, Facebook post ID, or Click-to-WhatsApp ad ID) to a SKU.
Comments on a linked post, and CTWA chats from a linked ad, are answered about
that product. Unlinked posts are named in the event note so the business can
add them.

**Docs updated:** Spec Rev 5 Section 3.2; proposal Rev 4.

---

## D-019 - 2026-10-14 - Week 3 - Alerts through a notifications outbox; log mode by default

**Decision:** Alerts are rows in `notifications` (idempotent key), written in
the agent's transaction and delivered by the worker afterwards, like
messages. Kinds: hot lead (once when a lead turns hot), handoff (complaint,
voice note/photo, asked for a person, assistant could not answer), and
customer replied (a lead under human takeover wrote again). `ALERTS_MODE=log`
(default) writes them to the log and `GET /api/notifications`; `live` sends
email (SMTP) and/or Telegram, retrying a failed channel without repeating a
successful one. A message asking for a person ("call me", "talk to someone",
"baat karni hai") now hands off immediately with a short reply.

**Docs updated:** Spec Rev 5 Sections 1, 2, 3.2, 4.

---

## D-020 - 2026-10-14 - Week 3 - Sheets mirror: CSV by default, Google via service account

**Context:** Spec Section 8 describes per-user Google OAuth, which belongs to
the later multi-business phase.

**Decision:** `SHEETS_MODE=csv` writes `data/leads_mirror.csv` (UTF-8 with
BOM so Excel shows ₹ and Indian-language names). `SHEETS_MODE=google` writes a
Google Sheet with a service-account key (share the sheet with the service
account's email). The whole table is rewritten at most every
`SHEETS_SYNC_SECONDS`, only when something changed: one API call, no rate-limit
concerns. On Windows a CSV open in Excel is locked; the write is retried on the
next cycle. Per-user OAuth stays in the multi-business phase.

**Docs updated:** Spec Rev 5 Sections 1, 4.6.

---

## D-021 - 2026-10-14 - Week 3 - Messages without a product use the lead's earlier interest

**Context:** In testing, "ok thanks" from a hot lead found no product, so the
assistant handed the lead to a person unnecessarily.

**Decision:** When a message names no product, the search uses what the lead
asked about before. The "nothing within your budget" note is only added when
the budget really excluded matching products (it was shown wrongly in the run).

**Docs updated:** Spec Rev 5 Section 10.

---

## D-022 - 2026-10-15 - Week 4 - Dashboard stack, and FastAPI serves it

**Context:** Spec Section 1 named React (Vite) + Tailwind with shadcn/ui or Ant
Design, and did not say how the dashboard is hosted.

**Decision:** React 19 + Vite + TypeScript + Tailwind CSS 4, TanStack Query,
React Router, lucide icons and one font (Archivo, self-hosted through
fontsource). A small set of components of our own (button, field, select,
toast) instead of shadcn/ui or Ant Design: fewer dependencies, and the
screens look like this product rather than like a component library.
`npm run build` writes `frontend/dist`, and FastAPI serves it at `/`, so
the dashboard, the API and the webhooks share one address and one process
(no CORS settings, one ngrok tunnel). Unknown paths fall back to
`index.html`; `/api`, `/webhooks`, `/ws` and `/docs` are never shadowed;
files outside `dist` cannot be read. Before the first build, `/` shows a page
explaining `npm ci && npm run build`. `npm run dev` (port 5173) proxies `/api`
and `/ws` to the backend for front-end work. Node 22.12 or newer (24 LTS
recommended); package versions are pinned.

**Docs updated:** Spec Rev 6 Sections 1, 2, 3.2, 5; proposal Rev 5.

---

## D-023 - 2026-10-15 - Week 4 - Inbox views, lead activity and read alerts

**Decision:** The inbox opens on **Needs you**: leads a person is handling
(not yet bought/lost) and hot leads. Other views: Open, Closed (Bought or
Lost), All, with counts from `GET /api/leads/counts`. Each row shows the last
message, its channel, unread alerts and who is handling it. "Converted" is
shown as **Bought** in the dashboard (the API value stays `converted`).
A new `lead_activity` table records status changes, assignment, take
over / hand back and manual messages, with who did it; the conversation
shows these between the messages. Opening a lead marks its alerts as read
(`POST /api/notifications/read`). Marking a lead Bought or Lost stops its
nurture schedule. `PATCH /api/leads/{id}` also sets `assigned_to` and the name.

**Docs updated:** Spec Rev 6 Sections 3.2, 4.10.

---

## D-024 - 2026-10-15 - Week 4 - Manual messages: the backend says what is allowed

**Decision:** `GET /api/leads/{id}` returns `reply_options` per channel:
free text while the window is open (WhatsApp 24h, Instagram/Messenger 24h
from the customer's last DM; a comment alone does not open it), an approved
template on WhatsApp otherwise, and nothing if the customer opted out. The
composer only offers what is allowed and shows when the window closes.
`POST /api/leads/{id}/message` goes through the Send Gate like the
assistant's messages (sender `salesperson`); the response is the gate's
decision (`sent`, `needs_template`, `blocked`) with a reason. Sending a
message takes the conversation over automatically, so the assistant does not
answer in parallel. The browser sends a `client_message_id`, so a double
click or a retry never sends twice.

**Docs updated:** Spec Rev 6 Section 3.2.

---

## D-025 - 2026-10-15 - Week 4 - Live updates: a WebSocket that reads the database

**Decision:** `WS /ws/notifications?token=<JWT>` (browsers cannot set headers
on WebSockets, so the token travels in the query string; an invalid token
closes with code 4401). Every 1.5 seconds the socket reads new notifications
and recently changed leads for that business, and sends `notification` and
`leads_changed` messages; the dashboard refreshes the affected lists and
shows a toast. Reading the database instead of an in-memory event bus keeps
it correct whichever process wrote the change, and costs one small query per
open tab. The dashboard reconnects with backoff, shows Live / Connecting /
Offline, and while offline still refreshes every 30 seconds.

**Docs updated:** Spec Rev 6 Section 3.2.

---

## D-026 - 2026-10-15 - Week 4 - Template list: from Meta when live, from settings otherwise

**Decision:** `GET /api/templates` reads the business's approved templates
from Meta (`/{WABA ID}/message_templates`) when `META_API_MODE=live` and the
new `WHATSAPP_BUSINESS_ACCOUNT_ID` is set; otherwise it lists the templates
named in `.env` with known body text and the number of `{{n}}` values to fill
in. The composer shows the body with the values filled in before sending.

**Docs updated:** Spec Rev 6 Section 3.2; `.env.example`.

---

## D-027 - 2026-10-15 - Week 4 - Product editing; two endpoints move to Week 5

**Decision:** `POST/PATCH/DELETE /api/products` added (duplicate SKU → 409;
an edit is validated like an import; only that product is re-embedded, on
the next search). The Products page has "Check what the assistant finds",
which runs the agents' product search for a typed customer message.
`DELETE /api/leads/{id}/personal-data` moves to Week 5 with the opt-out
work, and the analytics endpoints stay in Week 5 as planned. Settings and
notification endpoints moved into their own modules; their URLs did not
change.

**Docs updated:** Spec Rev 6 Sections 3.2, 5.

---

## D-028 - 2026-10-15 - Week 4 - Fixes from the browser run

**Context:** The dashboard was tested in a real browser (Chromium, desktop
and phone sizes) against the demo conversations
(`python -m scripts.simulate demo`). Reading the screens showed problems
the API tests had not.

**Decision / fixes** (each has a regression test):
1. A budget message ("Budget 15000, Coimbatore delivery this week?") was
   answered with a kettle: weak letter matches beat the earlier interest.
   A message now counts as naming a product only if its words match a
   product's name or category (refines D-021).
2. "Need 2, one for my mother" was not read as a quantity, so the assistant
   kept asking "How many would you need?". "need / want / order / buy / take /
   book N" now counts, but not sizes or money ("need 6L", "want 2 litre",
   "need 5000 rs").
3. Messages are now ordered by time in the conversation view, not by
   timestamp text (a reply appeared above the voice note it answered).
4. Instagram and Messenger customers without a name are shown as
   "Instagram customer" / "Messenger customer", not "Unnamed customer".
5. A lead the assistant handed over says "Waiting for a person" instead of
   "You're handling this".
6. Alert emails/Telegram messages now link to the lead's page
   (`DASHBOARD_URL` default changed to `http://127.0.0.1:8000`).
7. `GET /api/events` now returns `result_note`, which the Week 2-3 run log
   already referred to.
8. The demo script waits for the assistant's reply before a customer's next
   message, as a person would, so the demo conversations read in order.

**Docs updated:** Spec Rev 6 Section 10.

---

## D-029 - 2026-10-18 - Week 5 - Follow-ups run in the worker; rules are business settings

**Context:** Spec Section 1 named APScheduler and Section 3.3 a daily
`run_daily_check()`.

**Decision:** No APScheduler. The worker that already handles webhooks and the
outbox runs a follow-up pass every `NURTURE_CHECK_MINUTES` (default 10), so
there is still one process and no second scheduler to keep alive on Windows.
The rules are per business (Settings page, `GET/PUT /api/settings/nurture`):
- **Sequence:** up to 5 steps, each "N days after the customer's last message
  (or after the lead arrived) send template T". Default: day 2
  `nurture_followup_1`, day 5 `nurture_offer` (skipped while no offer text is
  set), day 10 `nurture_last_check`.
- **Who:** WhatsApp number, recorded opt-in, not opted out, no person handling
  the conversation, not Contacted / Bought / Lost, data not erased.
  Contacted is excluded too: a salesperson is already following the lead up.
- **Stops on reply:** any customer message (any channel, comments included)
  ends the sequence; it starts again from step 1 if they go quiet again.
- **Cap:** at most 6 follow-ups per lead, ever. No new sequence starts for
  a customer quiet for more than 30 days. This was found when upgrading the
  Week 4 database: old leads would otherwise get a sudden "just checking in".
- **Business hours:** only between 10:00 and 19:00 in `BUSINESS_TIMEZONE`
  (Asia/Kolkata); a follow-up due at night waits for the morning. The
  `tzdata` package is now required, because Windows has no time zone database.
- **Never twice:** each follow-up has a key (lead + quiet period + step), so
  two passes at once cannot send it twice. Follow-ups go through the Send Gate
  as marketing templates with sender `nurture`; a takeover pauses them like
  the assistant.
`python -m scripts.nurture` shows what is planned; `run --days-ahead 3` sends
what would be due in 3 days (for demos). The Settings page lists what is
planned next, worked out live.

**Docs updated:** Spec Rev 7 Sections 1, 2, 3.2, 3.3, 5; proposal Rev 6.

---

## D-030 - 2026-10-18 - Week 5 - What opting out means

**Context:** The opt-out confirmation said "If you change your mind, just send
us a message here", but a customer who opted out and wrote again was never
answered. Only 8 English/Hindi words were recognised.

**Decision:** Opting out stops everything the business starts: follow-ups and
templates, at once and for good. If the customer writes to us later, that
conversation is answered normally, because they started it. Follow-ups stay
off until they send **START** (recorded as a new opt-in, source `keyword`).
Recognised: whole-message opt-out words in English, Hindi, Hinglish, Tamil and
Tanglish ("STOP", "band karo", "बंद करो", "मत भेजो", "நிறுத்து", "anupa vendam"),
the template buttons "Stop messages" and "Stop promotions", and short
messages starting with stop / unsubscribe / don't send / no more that are
about messages or offers ("stop sending these offers", "Unsubscribe me").
Messages about an order or a product never opt out ("cancel my order", "stop
the order", "don't send me the 4L, send the 6L"). Matching ignores
punctuation and emoji, and keeps Hindi/Tamil vowel signs (Python's usual
word-character rule would drop them and never match "बंद"). The dashboard offers free-text
replies to such a customer only after they write again, never templates.

**Docs updated:** Spec Rev 7 Sections 9, 10; meta_setup section 5; proposal Rev 6.

---

## D-031 - 2026-10-18 - Week 5 - Asking for WhatsApp opt-in in the chat

**Context:** Follow-ups need recorded opt-in. Only lead forms collected it, so
customers from WhatsApp ads and DMs (the main entry point) could never get a
follow-up. `meta_setup.md` already promised a one-time question.

**Decision:** On WhatsApp, once the customer has answered all qualifying
questions, the assistant's reply ends with "Can we send you offers and
reminders on WhatsApp? Reply YES or NO." The question is added by code word
for word (with the built-in catalog reply it replaces the usual closing
question, so the message asks one question). It is asked once per customer,
and only while follow-ups are switched on. A YES-type answer (yes, ok, haan,
हाँ, சரி, ...) to that message is recorded as opt-in with the exact wording
and source `ctwa_message` or `dm`; a NO is recorded on the lead's history and
never asked again.

**Docs updated:** Spec Rev 7 Sections 3.3, 4.1, 10; meta_setup section 5.

---

## D-032 - 2026-10-18 - Week 5 - Erasing personal data

**Decision:** `DELETE /api/leads/{id}/personal-data` (button on the lead page,
with a confirmation step). Removed: name, phone, email, every platform ID,
the conversation summary and details given (city, budget), the text and raw
payload of every message, the raw webhook events that contain the customer's
number or IDs, and alert texts. Messages still waiting to be sent are
cancelled, and the lead is marked opted out. Kept, without anything
identifying: source, channel, product interest, score, status, dates, message
counts and costs, consent records. Analytics and cost totals stay correct. The
same number writing again later becomes a new lead. The Sheets mirror drops
the data at its next refresh; logs never held it (personal data is masked).

**Docs updated:** Spec Rev 7 Sections 3.2, 4.1, 11; proposal Rev 6.

---

## D-033 - 2026-10-18 - Week 5 - Analytics definitions; hot-label agreement measured in the dashboard

**Decision:** `GET /api/analytics/summary` and `/costs?days=7|30|90` (and any
1-365), shown on a new Analytics page. Periods use the business time zone.
- **First response:** from the customer's first message, or from the lead
  form, to our first reply.
- **Wrote back:** the customer wrote after our first message.
- **Qualified / hot:** score at or above the Settings thresholds.
- **Re-engaged:** the customer wrote within 7 days after a follow-up.
- **Costs:** estimates from the rate card, per day, channel and kind of
  message, per lead, per qualified lead and per sale, plus what the free
  72-hour ad window saved.
The third target in proposal Section 9 (the salesperson agrees with "hot" for
at least 80% of hot leads) is measured with a one-tap "Is this lead really
hot?" on hot leads.

**Docs updated:** Spec Rev 7 Section 3.2; proposal Rev 6 Section 9.

---

## D-034 - 2026-10-18 - Week 5 - Evaluation harness and how its results are reported

**Decision:** `python -m scripts.evaluate` replays labelled comments and
messages through the real agents with the model configured in `.env`. It
builds a throwaway database from the catalog and sends nothing. It writes a
report to `data/eval/reports/`. Comments are scored on the action taken
(reply, complaint, hide, ignore), because "purchase interest" and "product
question" lead to the same action; exact intent is reported too. Bundled sets
are synthetic: 82 cases used to find problems, and 24 held-out cases written
afterwards and run once. With keyword rules only, the held-out first run
scored: comment action 75%, product answers 92.9%, opt-out 1 of 3 recognised,
no wrong prices, no false opt-outs. These are the honest generalisation
figures for the rules (Week 5 run log). The real test is the business's own
50-100 messages with the chosen Ollama model (`data/eval/README.md`).
Messages in Hindi or Tamil script are handed to a person by the built-in
search; the multilingual embedding model is the fix (D-008).

**Docs updated:** Spec Rev 7 Sections 5, 10; proposal Rev 6 Section 9.

---

## D-035 - 2026-10-18 - Week 5 - Fixes from the evaluation and the browser run

Each has a regression test.
1. **Complaints and order problems in a DM got a product list** ("the mixer
   stopped working", "my order hasn't arrived", "do you repair mixers?"). They
   are a handoff trigger in spec Section 10 but only comments had it. Now the
   assistant apologises, hands over and alerts.
2. "2 mixer" was not read as a quantity: a small number right before a word
   naming a found product now counts.
3. Comments: "Beautiful colour" was read as a question ("colour" and "size"
   alone no longer count). Added "visit my page" to spam, and "leaks",
   "overheats" and "order innum varala" / "abhi tak nahi aaya" to complaints.
4. Opt-out rule widened (D-030), with tests for false opt-outs.
5. Analytics: a lead-form customer who wrote back later was counted as
   "never answered".
6. The Settings list of planned follow-ups showed the worker's last schedule,
   up to 10 minutes old (empty right after the demo); it is now worked out
   live.
7. The opt-in message asked two questions at once (D-031).
8. The inbox showed a follow-up as "[template nurture_followup_1] Arjun |
   PowerChef..."; it now says "Follow-up: Check-in template", and the
   conversation shows the template text the customer received.
9. Erased leads still offered "Take over"; the phone Analytics page left an
   empty cell; "0 s" now reads "under 1 s".

**Docs updated:** Spec Rev 7 Section 10.

---

## D-036 - 2026-10-22 - Week 6 - Docker packaging

**Decision:** One image holds the API, the background worker and the
built dashboard. It is built in two stages: Node builds the dashboard,
then `python:3.12-slim` runs it from a virtual environment as the non-root
user `leadgen` (uid 10001). Data lives in the `/data` volume: database,
backups and search index. The start-up script (`scripts/bootstrap.py`)
does the following:
- refuses to start with the default or a short `JWT_SECRET` (exit code 2);
- runs the migrations;
- creates the first account from `INITIAL_USER_EMAIL` /
  `INITIAL_USER_PASSWORD`;
- loads the catalog once if the business has no products;
- warns when a mounted folder is not writable (Linux bind mounts);
- starts uvicorn.

`docker-compose.yml` reads `backend/.env` and publishes port 8000 on
127.0.0.1 only. ngrok on the host forwards to it. It reaches Ollama on the
host through `host.docker.internal`. `docker-compose.ollama.yml` adds
Ollama as a second container, with an optional GPU block. Base images can
be swapped with the build arguments `NODE_IMAGE` / `PYTHON_IMAGE`, for
registry mirrors.

**Why:**
- One command to install and update, the same on Windows and on a Linux
  server.
- Running without Docker still works unchanged; the image adds nothing
  the code depends on.
- A weak secret in a container that may be exposed is refused rather than
  warned about.

**Verified:** On every push, CI builds the image from the official base
images and then:
- checks that a weak secret is refused;
- starts the container and waits for it to be healthy;
- loads the demo and runs the 10 browser tests against the container;
- restarts it and signs in again.

The sandbox itself cannot pull from Docker Hub (blocked by policy). There,
the same Dockerfile was built from stand-in base images and passed the
same checks. Docker Desktop on Windows has not been run by the developer.

**Docs updated:** Spec Rev 8 Sections 1, 11; proposal Rev 7 Section 5;
docs/docker.md.

---

## D-037 - 2026-10-22 - Week 6 - Daily backups inside the server

**Decision:** The worker checks every hour and makes one backup per day
(business time zone). It uses SQLite's online backup API, which is safe
while the server writes, and runs `PRAGMA integrity_check` on the copy. It
keeps the newest `BACKUP_KEEP` (14) in `BACKUP_DIR`.
`python -m scripts.backup` offers:
- `now`: back up immediately;
- `list`: show the backups;
- `restore <file> --yes`: restore one. The server must be stopped first,
  and the current database is kept as `*.before-restore-*`.

`/api/health` and Settings -> System show the time of the last backup.

**Why:** The spec asked for a daily backup with a tested restore. A
separate scheduler or cron job would be one more thing to install on
Windows.

**Limits:**
- Backups sit next to the database, so the business must copy them off the
  machine.
- Erasing a customer's personal data (D-032) cleans the live database.
  Older backups keep it until they are pruned.

Both are in docs/limitations_and_next_phase.md.

**Docs updated:** Spec Rev 8 Sections 1, 3.2, 11; proposal Rev 7 notes.

---

## D-038 - 2026-10-22 - Week 6 - Lead stages back to Meta (Conversions API)

**Decision:** Built the Phase 6 stretch goal. It is off unless
`META_FEEDBACK_ENABLED=true`. The proposal named only the "Conversions API
for CRM". Meta's lead ID exists only for lead-form leads, so
Click-to-WhatsApp leads (the main WhatsApp entry point, proposal Section 7) use the "Conversions
API for business messaging" instead.

| Lead from | API flavour | Identifies the lead by | Events |
|---|---|---|---|
| Lead form | CRM: `action_source` `system_generated`, `custom_data.event_source` `crm` | `user_data.lead_id` (15-17 digits) | `Lead`, `Qualified` (hot), `Contacted`, `Bought`, `Lost` |
| Click-to-WhatsApp ad | business messaging: `action_source` `business_messaging`, `messaging_channel` `whatsapp` | `user_data.ctwa_clid` + `whatsapp_business_account_id` | `QualifiedLead` (hot), `Purchase` (Bought) |

How it works:
- **Queueing:** events are written to `meta_events` in the same
  transaction as the stage change. The worker sends them like the outbox,
  retrying temporary errors up to 5 times.
- **Once per stage:** each stage is sent at most once per lead (the
  idempotency key).
- **Purchase value:** a `Purchase` carries `currency` INR and `value` =
  catalog price x the quantity the customer gave. When either is unknown
  it is not sent, because a wrong value would teach Meta the wrong thing.
- **Missing IDs:** messaging events are not queued without
  `WHATSAPP_BUSINESS_ACCOUNT_ID`.
- **Test mode:** with `META_API_MODE=fake` events are only recorded, with
  status `logged`.
- **Erasure:** erasing a lead cancels its queued events and empties every
  payload.
- **Visibility:** `GET /api/meta-events`, and a line in Settings -> System.

Comment and DM leads have no Meta ad identifier and are not reported.

**Why:** It closes the loop the proposal calls "the most direct link
between the system and more sales per rupee of ad spend". Built off by
default because it needs a dataset and stage names set up in Events
Manager first (meta_setup.md section 10).

**Not verified against Meta:** the field names follow Meta's current
documentation, but no real dataset was available. The first live events
should be checked in Events Manager -> Test events.

**Docs updated:** Spec Rev 8 Sections 2, 3.2, 3.3, 4.11; proposal Rev 7
Sections 3, 8; meta_setup.md section 10; .env.example.

---

## D-039 - 2026-10-22 - Week 6 - Fixes and additions from the failure drills and Docker

Each has a test.
1. **"Not delivered" alert.** A message that WhatsApp/Instagram refuses,
   that Meta later reports as failed, or that was being sent when the
   server died (outcome unknown, never re-sent) now alerts the salesperson
   once, as a new alert kind `message_failed`. Before, it showed only on
   the lead page, so a hot lead could wait unseen.
2. **Google Sheets signing needed `cryptography`.** The first Week 6 CI run
   failed in a fresh environment. In live use, the Sheets mirror would have
   failed to sign its token, which the local environment had hidden. The
   requirement is now `PyJWT[crypto]`, and the tests run in a fresh
   environment before every release.
3. **Lead mirror permission errors.** In Docker on Linux, the bind-mounted
   `exports` folder belongs to root, so the CSV mirror could not write. It
   now retries with the reason in the log, and the start-up script warns
   with the `chown` fix (docs/docker.md).
4. **Follow-up reason for closed leads.** The lead page showed "a person
   has taken over" for a lead marked Bought or Lost. Now it shows "Marked
   as Bought." / "Marked as Lost.".
5. **Demo lead form ID.** The demo's lead-form lead had a 9-digit ID. Meta's
   are 15-17 digits, so it could never show feedback to Meta. It now has
   a 16-digit ID with the same name and number.
6. **Backups left two extra files beside each copy.** The copy inherited
   the database's WAL mode, so the integrity check left `-wal` / `-shm`
   files next to it. Backups are now single self-contained files, and
   pruning removes such leftovers.
7. **Moving existing data into Docker** was not described. docs/docker.md
   now has the steps; they were tested with a database made without Docker.

**Docs updated:** Spec Rev 8 Sections 4.9, 11; docs/docker.md.

---

## D-040 - 2026-10-22 - Week 6 - Final model choice deferred to the business's own evaluation

**Decision:** Week 6 does not pick the Ollama model. This sandbox cannot
run Ollama, and the evaluation that decides it needs the business's PC
(speed) and 50-100 of its real messages (accuracy, languages). The
defaults stay:
- `LLM_PROVIDER=fake` until Ollama is installed;
- the built-in hashing search until `bge-m3` is measured with
  `scripts/eval_retrieval.py`.

When the report from `python -m scripts.evaluate` arrives, the model, the
embedding model and any prompt changes are chosen from it and recorded
here.

**Why:** The Week 5 held-out results with keyword rules alone were below
target (D-034). A model picked without the real messages and hardware
would be a guess.

**Docs updated:** Spec Rev 8 Section 5; proposal Rev 7 notes;
docs/limitations_and_next_phase.md.
