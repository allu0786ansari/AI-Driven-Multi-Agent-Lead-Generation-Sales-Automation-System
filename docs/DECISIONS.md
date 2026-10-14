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
