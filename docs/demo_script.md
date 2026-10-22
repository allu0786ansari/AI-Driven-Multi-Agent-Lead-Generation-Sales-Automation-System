# Demo script (Week 6)

Two versions of the demo:

- **A. Rehearsal (15 minutes, no Meta account needed).** Simulated customers, signed exactly
  like Meta's webhooks. Use it to practise, and as the fallback if anything live misbehaves.
- **B. Live (25 minutes).** A real lead form, a real Instagram comment and a real WhatsApp
  message, on your own phone, in front of the audience.

Both use the Docker install (docs/docker.md). Without Docker, run the same commands without the
`docker compose exec app` prefix, from the `backend` folder with the venv active.

---

## Before the day (once)

| Check | How |
|---|---|
| Server healthy | `docker compose ps` shows `healthy`; http://127.0.0.1:8000/api/health says `"status":"ok"` |
| Model | Settings -> System -> AI model says "Ollama" with no error (or decide to show test mode and say so) |
| Backup | Settings -> System -> Last backup shows today. Take one now: `docker compose exec app python -m scripts.backup` |
| Live only: ngrok | `ngrok http --url=<your-static-domain> 8000`; Meta app -> Webhooks shows the three subscriptions as verified |
| Live only: mode | `.env` has `META_API_MODE=live`, `WHATSAPP_MODE=live`; templates approved (meta_setup section 4) |
| Live only: test assets | A test Instant Form on your Page; a test Instagram post; a second phone with WhatsApp |
| Live only: alerts | Your salesperson phone gets a WhatsApp/email alert when you run `simulate wa-hot` |
| Screen | Dashboard on the laptop; second browser window on the phone (`http://<PC IP>:8000`) |

Fresh database for the audience (optional): stop, rename the volume or restore an empty backup,
start again. Never do this on the database with your real customers in it.

---

## A. Rehearsal (simulated)

1. **Empty inbox** - open http://127.0.0.1:8000, sign in. Inbox is empty or quiet.
2. **A comment on an ad post** -
   `docker compose exec app python -m scripts.simulate ig-comment --text "price of air fryer?"`
   - Show: the public reply under the post ("Sent you the details in a DM"), the private DM with
     the real price from the catalog, and the new lead with source "Comment".
   - Say: the price comes from your product list, never from the model's memory.
3. **A Click-to-WhatsApp ad** - `docker compose exec app python -m scripts.simulate ctwa`
   - Show: answered in seconds; cost ₹0 on the lead page ("free 72-hour ad window").
4. **The customer becomes hot** - `docker compose exec app python -m scripts.simulate wa-hot`
   - Show: Needs you count goes up, the hot alert (Alerts page and your phone if alerts are set
     up), the score and the "why" on the lead page, the opt-in question at the end of the reply.
5. **The salesperson takes over** - on the lead page: Take over -> type a reply -> Send.
   - Say: from now on the assistant stays quiet in this conversation.
6. **A lead form** - `docker compose exec app python -m scripts.simulate leadgen`
   - Show: the welcome template sent at once (the customer ticked the opt-in box), ₹0.86 cost.
7. **Follow-ups** - `docker compose exec app python -m scripts.nurture run --days-ahead 3`
   - Show: Settings -> Follow-ups, and the follow-up in the lead-form customer's conversation.
8. **Opt-out** - `docker compose exec app python -m scripts.simulate stop`
   - Show: the confirmation; the lead page says why no more follow-ups are planned.
9. **Close the loop** - mark one lead Bought and one Lost (status menu on the lead page).
   - Show: Analytics -> from lead to sale, first-reply speed, costs.
   - If feedback to Meta is on: Settings -> System -> "Feedback to Meta: On - 2 recorded (test mode)".
10. **Safety** - `docker compose exec app python -m scripts.simulate bad-signature` -> rejected,
    nothing stored. `... simulate fb-complaint` -> hidden from the public, handed to a person.

---

## B. Live

Keep the rehearsal commands in a second window as the fallback for each step.

1. **Lead form (Meta's Lead Ads Testing Tool)** -
   developers.facebook.com/tools/lead-ads-testing -> your Page -> your form -> **Create lead**
   (use your second phone's number). Then **Track status**: it should say "Success" within seconds.
   - The second phone receives the welcome template; the lead appears in the dashboard.
   - Delete the test lead in the tool afterwards (it allows a limited number per form).
2. **Instagram comment** - from a personal account, comment "price?" on the test post.
   - The reply appears under the comment and the DM arrives; show both on the phone.
   - Comments on your own account's posts by the account itself are ignored on purpose.
3. **WhatsApp** - from the second phone, write to the business number:
   "Need the 6L air fryer, 2 pieces, delivery Chennai this week, budget 15000".
   - Answer with the product and price; hot alert on the salesperson's phone; reply "Yes" to
     the opt-in question.
4. **Take over from the phone** - open the dashboard on the salesperson's phone, take over, reply.
   The customer phone receives it.
5. **Mark it Bought** - Analytics updates. With feedback to Meta on, Events Manager -> your
   dataset -> Overview shows the event after a while (not instantly; show it at the end).
6. **What happens if...** (talk, or show from the drills): the model is down -> the customer still
   gets the right price; WhatsApp refuses a message -> the salesperson gets a "Not delivered"
   alert; Meta sends the same webhook three times -> one answer.

---

## Questions the audience usually asks

| Question | Answer |
|---|---|
| Can it say a wrong price? | Prices are copied from your product list; a reply with a price that is not in it is blocked and the catalog answer is sent instead. Tested on every build. |
| What does it cost per month? | See docs/limitations_and_next_phase.md, "Costs". Inside ad windows and 24-hour reply windows WhatsApp messages are free; templates cost about ₹0.86 each (marketing). |
| What if the internet or PC goes down? | Meta retries webhooks for a while; anything received is processed after restart, never twice. Daily backups. |
| Does it send messages to people who said stop? | No - checked before every message, in English, Hindi, Hinglish, Tamil and Tanglish. |
| Can it run for several shops? | Not yet: one business per installation. That is the next phase. |
| Where is the customer data? | On your PC (or your server), in one database file, backed up daily. Nothing goes to an AI company if you use Ollama. |
