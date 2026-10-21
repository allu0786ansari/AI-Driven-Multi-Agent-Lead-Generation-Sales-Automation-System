# Evaluation sets

`python -m scripts.evaluate` replays these messages through the real agents
(with the model set in `backend/.env`) and checks them against the
prototype's targets (proposal Section 9). Nothing is sent and your database
is not touched.

| File | What it is |
|---|---|
| `agents_eval.sample.jsonl` | 82 synthetic cases (36 comments, 46 messages) in English, Hinglish, Tanglish, Hindi and Tamil script. Used during Week 5 to find and fix problems, so it is no longer a fair test on its own. |
| `agents_holdout.sample.jsonl` | 24 cases written after the rules were fixed and run once, to see how well the rules generalise (Week 5 run log). |
| `retrieval_eval.sample.jsonl` | Product-search cases for choosing the embedding model (`python -m scripts.eval_retrieval`). |

**The real test is your own customers' messages.** Collect 50-100 real
comments and messages (proposal Phase 0), label them as below, save them as
`data/eval/<name>.real.jsonl` (ignored by git, because they contain personal
data) and run:

```powershell
python -m scripts.evaluate --file data/eval/my_customers.real.jsonl
```

Remove names and phone numbers from the text before saving.

## Format: one JSON object per line

Comment on a post or ad - the label is what a person would decide:

```json
{"id": "c01", "kind": "comment", "lang": "en", "text": "Price?", "intent": "product_question"}
```

`intent`: `purchase_interest`, `product_question` (both get a private reply),
`complaint` (apology + a person), `spam` (hidden), `other` (no action).

Message (WhatsApp, Instagram DM, Messenger):

```json
{"id": "m01", "kind": "message", "lang": "hinglish", "text": "1000 watt mixer kitne ka hai?",
 "expect": {"skus": ["MG-1000-4J"], "slots": {"budget": 5000}}}
```

`expect` keys (all optional):
- `skus`: any one of these products in the answer counts as right
- `handoff`: `true` if a person should answer (complaints, orders, things you don't sell)
- `opt_out`: `true` if the message means "stop messaging me"; `false` to check that it does not
- `slots`: what the customer told us: `budget`, `quantity`, `timeline` (`now`, `this_week`, `this_month`, `later`), `city` (or `"PIN 600041"`)
- `context_interest` (next to `expect`): what they asked about earlier, for messages like "ok, budget 15000"

`lang` is free text (`en`, `hinglish`, `tanglish`, `hi`, `ta`, ...) and is
used to show results per language.

Reports are written to `data/eval/reports/` (Markdown and JSON).
