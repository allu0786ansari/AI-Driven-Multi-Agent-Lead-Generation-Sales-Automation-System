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
