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

The `Stop messages` button is the opt-out. If you add Meta's own
marketing opt-out button instead (its text is `Stop promotions`), that works
too. A tap, or an opt-out word typed by the customer, marks the lead opted out
at once (Week 5, D-030).

The follow-ups (`nurture_*`) are sent automatically from Week 5 to customers
who agreed to offers and went quiet. Their timing, the offer text and the
sending hours are on the dashboard's Settings page.

## 5. Opt-in wording

### Lead form (Instant Form)

Add a custom question or disclaimer with a checkbox, for example:

> ☐ Yes, <Business Name> may contact me on WhatsApp at this number about my
> enquiry and related offers. I can reply STOP at any time.

The exact wording shown is saved with each lead (`consent_events.wording_shown`),
so keep a copy of every version used.

### Click-to-WhatsApp and DMs

The customer starts the conversation, so replies are allowed. Templates
need opt-in, so once the customer has answered the qualifying questions the
assistant asks once (Week 5, D-031):

> Can we send you offers and reminders on WhatsApp? Reply YES or NO.

A YES (yes, ok, haan, हाँ, சரி, ...) is recorded as opt-in with this wording
and source `ctwa_message` or `dm`. It is asked only while follow-ups are
switched on.

### Opt-out words (Week 5, D-030 - tell us any your customers use)

Whole message: `STOP`, `UNSUBSCRIBE`, `CANCEL`, `OPT OUT`, `STOP MESSAGES`,
`STOP PROMOTIONS`, `NO MORE MESSAGES`, `BAND`, `BAND KARO`, `MAT BHEJO`,
`बंद करो`, `मत भेजो`, `NIRUTHU`, `நிறுத்து`, `ANUPA VENDAM`, and short
messages such as "stop sending these offers" or "unsubscribe me". Messages
about an order ("cancel my order") never count. `START` subscribes again.
After an opt-out the customer gets no follow-ups or templates; if they write
to you again, that conversation is answered.

## 6. Ollama on the Windows machine (optional until Week 2)

1. Install Ollama for Windows from ollama.com.
2. In PowerShell: `ollama pull llama3.1:8b` (about 5 GB). A machine with
   16 GB RAM works; an NVIDIA GPU makes replies much faster.
3. In `backend/.env`: `LLM_PROVIDER=ollama`, `OLLAMA_MODEL=llama3.1:8b`.
4. Check: `python -m scripts.llm_smoke`.

Model choice is final only after the Week 2 evaluation on real customer
messages (candidates: Llama 3.x, Qwen, Gemma).

## 7. Instagram and Messenger (Week 3)

In `backend/.env`:

| Setting | Where to find it |
|---|---|
| `META_PAGE_ID` | Facebook Page > About > Page transparency (Page ID), or the `entry.id` of a Page webhook in `GET /api/events` |
| `INSTAGRAM_ACCOUNT_ID` | Instagram professional account ID, or the `entry.id` of an Instagram webhook |
| `META_PAGE_ACCESS_TOKEN` | Page access token with the permissions in section 2 (also used for lead forms) |

- With **Instagram via Facebook Login** (default) messages are sent from the
  Page ID with the Page token. With **Instagram Login**, set
  `INSTAGRAM_API_BASE_URL=https://graph.instagram.com` and
  `INSTAGRAM_ACCESS_TOKEN`. Check Meta's current Instagram messaging docs for
  your app type before going live.
- Instagram messaging must be allowed for connected tools: Instagram app >
  Settings > Messages > Connected tools (wording may differ).
- **Link posts and ads to products** so "How much is this?" gets the right
  answer: find the media / post / ad ID in a lead's "source detail" or in
  `GET /api/events` (notes say "post X has no linked product"), then call
  `PUT /api/settings/content-products` with `{"links": {"<id>": "<SKU>"}}`.

## 8. Salesperson alerts

`ALERTS_MODE=log` only writes alerts to the log and `GET /api/notifications`.
For real alerts set `ALERTS_MODE=live` and configure one or both:

**Telegram (recommended, free and instant)**
1. In Telegram, message **@BotFather**, send `/newbot`, follow the steps,
   copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<token>/getUpdates` in a browser and copy
   `"chat":{"id": ...}` into `TELEGRAM_CHAT_ID`.

**Email (Gmail example)**
1. Turn on 2-step verification for the Google account, then create an
   **App password** (Google Account > Security > App passwords).
2. `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USERNAME=<gmail address>`,
   `SMTP_PASSWORD=<app password>`, `ALERT_EMAIL_TO=<where alerts go>`.

`ALERT_CHANNELS=telegram` (or `email`, or `email,telegram`) chooses which are used.

## 9. Google Sheets mirror (optional; CSV works without this)

1. In Google Cloud Console create a project, enable the **Google Sheets API**.
2. Create a **service account**, add a **JSON key**, download it to
   `backend/secrets/google-service-account.json` (the `secrets` folder is not
   committed to git).
3. Create a Google Sheet with a tab named `Leads`, click **Share**, and add the
   service account's email (ends with `iam.gserviceaccount.com`) as **Editor**.
4. Copy the sheet ID from its URL (`docs.google.com/spreadsheets/d/<ID>/edit`)
   into `GOOGLE_SHEET_ID`, set `SHEETS_MODE=google`, restart the server.

## 10. Feedback to Meta - which leads bought (Week 6, optional)

When this is on, the server tells Meta how each ad lead turned out, so Meta can
show your ads to more people like the ones who bought (Conversions API). It is
off by default and nothing is sent until you turn it on.

**What is sent** (nothing else - no names, phone numbers or messages):

| Lead came from | When | Event sent | Identifies the lead by |
|---|---|---|---|
| Lead form | lead arrives / Hot / Contacted / Bought / Lost | `Lead` / `Qualified` / `Contacted` / `Bought` / `Lost` | Meta's lead ID |
| Click-to-WhatsApp ad | lead becomes Hot | `QualifiedLead` | the ad click ID (`ctwa_clid`) |
| Click-to-WhatsApp ad | marked Bought | `Purchase` with the order value in ₹ | the ad click ID |

Leads from comments and ordinary DMs have no Meta ad ID, so nothing is sent for them.
`Purchase` needs a price: the product the customer asked for x the quantity they gave
(from your product list). If either is unknown, the purchase is not reported.

**Set it up (about 15 minutes):**

1. Events Manager -> **Connect data sources** -> **Web**/**CRM** -> create a dataset
   (or use your existing Pixel). Copy its **Dataset ID** into `META_DATASET_ID`.
2. In the dataset's **Settings** -> **Conversions API** -> **Generate access token**.
   Put it in `META_CAPI_ACCESS_TOKEN`.
3. Lead forms only: in the dataset, **Set up CRM integration** -> choose "Conversions API" ->
   when asked for your CRM stages, enter exactly: `Lead`, `Qualified`, `Contacted`,
   `Bought`, `Lost`. Use the same name as `META_FEEDBACK_SOURCE_NAME` (default `Lead desk`)
   for the CRM name.
4. Click-to-WhatsApp: in the dataset, connect your **WhatsApp Business Account**
   (Settings -> Linked assets). `WHATSAPP_BUSINESS_ACCOUNT_ID` must be set in `.env`.
5. Set `META_FEEDBACK_ENABLED=true` and restart. Settings -> System shows
   "Feedback to Meta: On" and how many events were sent.
6. Check: Events Manager -> your dataset -> **Test events** / **Overview**. Events show up
   within about 20 minutes. A `failed` count in Settings means Meta refused them - the
   reason is in `GET /api/meta-events` (open `/docs` in the browser to try it).
