# Meta & WhatsApp Setup (Day-1 track)

These steps need the business owner's accounts and take days of review on
Meta's side, so they run in parallel with the code. Start them now; the code
does not need them until real ads go live. Screen names in Meta's dashboards
change often, so treat menu paths as a guide and check Meta's current help
pages if something has moved.

## 1. Accounts and verification

- [ ] Meta Business account (business.facebook.com) owns the Facebook Page,
      the Instagram professional account and the ad account.
- [ ] Instagram account is a **professional** account (Business or Creator)
      and is linked to the Facebook Page.
- [ ] Business verification started in Business Settings (needs business
      documents; can take several days).
- [ ] Developer app created at developers.facebook.com (type: Business),
      connected to the Meta Business account.
- [ ] App products added: Webhooks, WhatsApp, Messenger, Instagram.
- [ ] A phone number for WhatsApp that is **not** currently used in the
      WhatsApp or WhatsApp Business phone app (or plan the migration).
      Register it, and submit the WhatsApp display name for approval.

## 2. Permissions the app will request

Request only what the system uses (spec Section 8.4). For the stakeholder's
own Page/Instagram/WhatsApp, standard access is enough while the app is used
by people with a role on the app; check the App Dashboard for current access
rules before going live.

| Purpose | Permissions (verify exact names in the App Dashboard) |
|---|---|
| Lead forms | `leads_retrieval`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement` |
| Facebook comments + private replies | `pages_read_engagement`, `pages_manage_engagement`, `pages_messaging` |
| Messenger | `pages_messaging` |
| Instagram comments, private replies, DMs | `instagram_basic`, `instagram_manage_comments`, `instagram_manage_messages` |
| WhatsApp | `whatsapp_business_messaging`, `whatsapp_business_management` |
| Ad performance (Phase 6) | `ads_read` |

## 3. Webhooks (after the server is reachable on HTTPS)

Run the backend and expose it with ngrok (see RUNLOG Week 1, step 7). Then, in
the App Dashboard, set one callback URL per object - see DECISIONS D-001:

| Meta object | Callback URL | Fields to subscribe |
|---|---|---|
| Page | `https://<your-domain>/webhooks/meta/page` | `leadgen`, `feed`, `messages` |
| Instagram | `https://<your-domain>/webhooks/meta/instagram` | `comments`, `live_comments`, `messages` |
| WhatsApp Business Account | `https://<your-domain>/webhooks/whatsapp` | `messages` |

- **Verify token**: the same value as `META_VERIFY_TOKEN` in `backend/.env`.
- **App secret**: copy from App settings > Basic into `META_APP_SECRET`.
  Every webhook is checked against it; a wrong value gives
  `401 Invalid signature` and nothing is stored.
- The Page must also be subscribed to the app (Page-level subscription for
  `leadgen`, `feed`, `messages`), otherwise no Page events arrive.
- Check deliveries: log in at `http://127.0.0.1:8000/docs`, authorize, and
  call `GET /api/events`.

## 4. WhatsApp message templates (submit for approval now)

Templates are needed for every message the business sends first: the lead
form follow-up and nurture messages. All four below promote products, so
submit them as **Marketing**. Meta reviews the wording; variables need sample
values. Replace `{{business}}`-style text with the real business name before
submitting. Add the same templates in each language customers use (to be
confirmed by the stakeholder).

### 4.1 `lead_form_welcome` - first message after a lead form

```
Hi {{1}}, thanks for your interest in {{2}}!

I'm the assistant for <Business Name>. I can share prices, offers and
delivery details right here on WhatsApp.

What would you like to know?
```
- Variables: `{{1}}` first name (sample: Priya), `{{2}}` product or category (sample: air fryers)
- Quick-reply buttons: `See prices` · `Talk to a person` · `Stop messages`

### 4.2 `nurture_followup_1` - 2 days without a reply

```
Hi {{1}}, just checking in about {{2}}. Do you have any questions I can
answer? Reply here anytime.
```
- Variables: `{{1}}` first name, `{{2}}` product
- Quick-reply buttons: `Yes, tell me more` · `Not now` · `Stop messages`

### 4.3 `nurture_offer` - 5 days without a reply

```
Hi {{1}}, {{2}} is still available and we have a special offer this week:
{{3}}. Would you like the details?
```
- Variables: `{{1}}` first name, `{{2}}` product, `{{3}}` offer text (sample: free delivery)
- Quick-reply buttons: `Send details` · `Stop messages`

### 4.4 `nurture_last_check` - 10 days without a reply

```
Hi {{1}}, this is our last reminder about {{2}}. If you're still
interested, just reply and we'll help you. Thank you!
```
- Quick-reply buttons: `I'm interested` · `Stop messages`

The `Stop messages` button is the opt-out; the Nurture Agent (Week 5) marks
the lead `opted_out` when it is tapped or when the lead types an opt-out
word.

## 5. Opt-in wording

### Lead form (Instant Form)

Add a custom question or disclaimer with a checkbox, for example:

> ☐ Yes, <Business Name> may contact me on WhatsApp at this number about my
> enquiry and related offers. I can reply STOP at any time.

The exact wording shown is saved with each lead (`consent_events.wording_shown`),
so keep a copy of every version used.

### Click-to-WhatsApp and DMs

The customer starts the conversation, so replies are allowed. Before sending
any *marketing template* later (nurture), the agent asks once in the chat,
for example: "Can we send you offers and reminders on WhatsApp? Reply YES or
NO." A YES is recorded as opt-in with source `ctwa_message` or `dm`.

### Opt-out words (to confirm with the stakeholder)

`STOP`, `UNSUBSCRIBE`, `CANCEL`, `BAND`, `BAND KARO`, plus the equivalents
in each language customers write in.

## 6. Ollama on the Windows machine (optional until Week 2)

1. Install Ollama for Windows from ollama.com.
2. In PowerShell: `ollama pull llama3.1:8b` (about 5 GB). A machine with
   16 GB RAM works; an NVIDIA GPU makes replies much faster.
3. In `backend/.env`: `LLM_PROVIDER=ollama`, `OLLAMA_MODEL=llama3.1:8b`.
4. Check: `python -m scripts.llm_smoke`.

Model choice is final only after the Week 2 evaluation on real customer
messages (candidates: Llama 3.x, Qwen, Gemma).
