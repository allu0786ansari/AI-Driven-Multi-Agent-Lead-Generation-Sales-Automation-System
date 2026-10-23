# Known limitations, measured figures and the next phase (Week 6)

Where the prototype stands at the end of the six-week build. Decisions behind each point are in
docs/DECISIONS.md. Weekly test results are in docs/RUNLOG.md.

## What was built against the proposal

| Proposal phase | Status |
|---|---|
| 0. Platform approvals (Meta verification, number, templates) | **Your side**, in progress (docs/meta_setup.md) |
| 1. Capture: lead forms, comments, DMs, Click-to-WhatsApp | Done, Weeks 1-3 |
| 2. Answers from the product list (RAG), price guard | Done, Week 2 |
| 3. Qualification, scoring, hot alerts, takeover | Done, Week 3 |
| 4. Dashboard | Done, Week 4 |
| 5. Follow-ups, consent, erasure, analytics, evaluation | Done, Week 5 |
| 6. Lifecycle tests, failure drills, Docker, backups, demo | Done, Week 6 |
| Stretch: report stages back to Meta (Conversions API) | Done, Week 6, off by default |
| Final model choice | **Waiting for your evaluation report** (see below) |

## Known limitations

**Not yet tried against the real Meta platform.**
Every Meta and WhatsApp call follows Meta's documented formats and is tested against built-in
stand-ins (`META_API_MODE=fake`). This build environment has no Meta app or phone number. The
first real lead form, comment and WhatsApp message happen on your side (demo_script.md, part B).
Expect small fixes there, for example a permission that is missing from a token.

**The model is not chosen yet.**
The sandbox cannot run Ollama, so every measured figure here comes from test mode: catalog
answers and keyword rules, with no model. Test-mode replies are correct but read like a list, and
two customers asking the same thing get the same words. The choice of model, and how fast it
answers on your PC, depend on the report from `python -m scripts.evaluate` on your machine,
ideally on 50-100 of your own customers' messages. Until then, the figures to compare against are
the Week 5 held-out results (D-034).

**Hindi- and Tamil-script product questions go to a person.**
The built-in product search reads Latin script only, which covers English, Hinglish and Tanglish.
"मिक्सर कितने का है" is handed to the salesperson, which is safe but not automatic. The fix is
the multilingual embedding model (D-008, `EMBEDDING_PROVIDER=ollama` with `bge-m3`). Choose it
with `scripts/eval_retrieval.py` once Ollama is installed.

**Voice notes and photos are not understood.**
The customer gets a holding reply, the conversation is handed to a person, and an alert is sent.

**One business, one server process.**
The webhooks, worker, follow-ups, mirror and backups all run inside one process with a SQLite
file. This is plenty for one shop: in the demo, replies went out within 1.1 s at the 95th
percentile. But it cannot run on two servers at once, and it serves one business per
installation. A second shop means a second installation (its own `.env`, database and Meta app
settings).

**Backups are on the same machine.**
The daily backups (14 kept) sit next to the database, so a dead disk takes both. Copy
`data\backups` (or the Docker volume, docs/docker.md) somewhere else every week, for example to a
USB drive or cloud storage.

**Erased customers remain in older backups for up to 14 days.**
"Erase personal data" cleans the live database at once. Backups made before the erasure still
contain the data until they are pruned, after `BACKUP_KEEP` days. If a customer needs proof of
complete deletion sooner, delete the backups older than the erasure date by hand
(`scripts.backup list` shows the dates).

**Costs are estimates.**
The dashboard multiplies each message by the rate card in `.env` (marketing ₹0.8631, service and
utility ₹0.12, free inside the ad and reply windows). Meta's invoice is the real figure. Update
the rates when Meta changes them, and check the invoice against Analytics -> Costs in the first
month.

**Feedback to Meta is limited.**
Only lead-form leads (by Meta's lead ID) and Click-to-WhatsApp leads (by the ad click ID) can be
reported. Comment and DM leads have no Meta ad identifier. A purchase value is worked out from
the catalog price and the quantity the customer gave, not from a real invoice.

**Docker on Windows was not run here.**
The image is built and tested on every push to GitHub (official base images): the container
starts, refuses a weak secret, loads the demo and passes the 10 browser tests, and its data
survives a restart. Docker Desktop on Windows itself was not run in this environment, so your
first `docker compose up` is the real check (docs/docker.md, Troubleshooting).

**The sandbox's own Docker Hub access is blocked.**
Here, the image was built from stand-in base images that are equivalent to the official ones,
with the same Dockerfile. That is why the GitHub run above is the reference.

## Measured figures (sandbox, test mode, Week 6)

| What | Figure |
|---|---|
| First reply after a customer writes (demo, 8 leads) | median 0.7 s, 95th percentile 1.1 s; target is under 60 s |
| Server memory (running, after the demo) | about 100 MB, without the model |
| Database after the demo (8 leads, 16 replies) | 0.25 MB; a backup is the same size |
| Docker image | about 300 MB (Python 3.12 slim + built dashboard) |
| Container start to healthy | about 9 s (first start, includes the database setup) |
| Messaging cost of the demo (16 messages) | ₹1.97 estimated: 2 marketing templates ₹1.73, 2 service replies ₹0.24, 12 free (ad window or Instagram/Messenger); ₹0.60 saved by the free ad window |
| Automated tests | backend 424, dashboard 35 unit + 10 browser, on Python 3.11 and 3.13 |

With Ollama, reply time is dominated by the model. On a CPU-only PC, expect several seconds per
reply. Measure it with the evaluation report.

## Costs per month (planning)

The formula from the proposal, Section 7, with the rates above:

```
WhatsApp ≈ lead-form leads × ₹0.86            (welcome template)
         + follow-ups sent × ₹0.86            (marketing templates)
         + assistant replies outside the free ad window × ₹0.12
Instagram and Messenger replies: free
```

An example, to show the scale. **These are assumptions, not measurements.** 200 lead-form leads
(₹172), 300 follow-ups (₹258) and 1,500 replies outside ad windows (₹180) come to about ₹610 a
month before GST and any provider markup. Replace these with your own numbers after the first
month; Analytics -> Costs shows the real counts.

Other costs:
- The PC or server that runs it. It must be on during business hours, or 24/7 to answer at night.
- Electricity or the VM bill if Ollama runs on a GPU machine.
- A static ngrok domain, or another HTTPS address for the webhooks. ngrok's free plan includes one
  static domain; check their current plans.
- Ad spend, which is not part of this system.

## Next phase (proposed order)

1. **Go live in a controlled way** (2-3 weeks, your side and mine). Approve the templates, then
   switch `META_API_MODE=live` with a small ad budget. Watch Alerts -> "Not delivered", Analytics
   -> Costs and the Meta invoice. Fix what the real platform shows.
2. **Choose the model** from your evaluation report on real messages: answer accuracy, Hindi/Tamil
   handling and speed on your hardware. Switch the product search to `bge-m3` if Ollama is there.
   Record the choice in DECISIONS.
3. **Measure against the baseline** from Phase 0 for 4 weeks: first-reply time, reply rate, cost
   per qualified lead, from lead to sale. Turn on feedback to Meta once the stage names are set
   up in Events Manager.
4. **Hosting.** Move to a small always-on server with HTTPS (a reverse proxy instead of ngrok),
   off-machine backups and an uptime check that alerts you when the server is down.
5. **When volume or a second business needs it:**
   - Postgres (with pgvector) instead of SQLite. The code already uses SQLAlchemy and Alembic.
   - The worker as its own process, so the web server and sending can scale apart.
   - Several businesses per installation: business ID on every table, per-business Meta tokens.
   - Roles for owner and salesperson, and lead assignment.
6. **Nice to have:** voice-note transcription (Whisper), photos of a product matched to the
   catalog, a payment link in the chat, Google Sheets as a two-way catalog source.
