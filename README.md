# Empório da Música — customer support agent

A text-message customer support agent for Empório da Música, a fictional musical instrument
store in Campo Grande/MS, built for the Artefact AI Engineer technical challenge.

The agent is **Léo**: an informal, helpful attendant who answers from the store's data
(products, promotions, orders) and from its policy manual, refuses to make things up, and
politely handles anything outside the store's scope. Python only; the LLM is Gemini (free tier).

> Language: code, docs and commit messages are in English. Léo talks to customers in
> Brazilian Portuguese because the store, its data and its policies are Brazilian.

## Quick start

Requires Python 3.10+ and a [Gemini API key](https://aistudio.google.com/apikey) (free tier).

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then put your key in GEMINI_API_KEY
python main.py                                         # chat with Léo in the terminal
python main.py web                                     # or in the browser: http://127.0.0.1:5000
```

In **GitHub Codespaces**, skip the `.env`: add `GEMINI_API_KEY` under Settings → Secrets and
variables → **Codespaces** (not "Actions", which codespaces cannot read), grant it to this
repository, create or restart the codespace and run the same `pip install` and `python main.py`
commands (for the web chat, open the forwarded port 5000).

```text
python main.py                          # new conversation
python main.py --verbose                # also print every tool call and its result
python main.py --session maria          # resume (or create) the conversation "maria"
python main.py --now 2026-02-20T10:00   # freeze the store clock (see "Clock" below)
python main.py --model gemini-3.8-flash # a stronger model than the default, uses more quota
python main.py web --port 8000          # web chat; accepts the same --now and --model
```

The web page is a chat with Léo plus a "How I got this result" panel: for any reply it shows the
question, each tool the agent called with its arguments and result, the answer, and which data
files were read. The panel shows the real tool trace, not a model-written explanation.
Conversations survive a page reload.

In the terminal, type `quit` (or `sair`) to leave. Environment variables (all optional except the key) are
listed in `.env.example`: `GEMINI_API_KEY`, `GEMINI_MODEL`, `EMPORIO_NOW`,
`EMPORIO_SESSIONS_DIR`, `EMPORIO_HANDOFF_LOG`.

Tests (no network or API key needed):

```bash
pip install pytest
pytest
```

Example conversations are regenerated with the real agent by `python scripts/run_examples.py`.

## Example conversations

Produced by running the real agent, not written by hand (see `scripts/run_examples.py`).
Each file lists the tools the agent called next to its answers.

| File | What it shows |
| --- | --- |
| [01-catalogue-budget](examples/01-catalogue-budget.md) | Catalogue search with a price limit and a recommendation |
| [02-price-promotion-payment](examples/02-price-promotion-payment.md) | Live price, an expired promotion, PIX discount and instalments |
| [03-order-status-verification](examples/03-order-status-verification.md) | Order status behind identity verification |
| [04-return-regret-policy-and-data](examples/04-return-regret-policy-and-data.md) | Return for regret: order data **and** policy rules together |
| [05-out-of-scope](examples/05-out-of-scope.md) | Questions the store does not cover |

## How it works

```text
customer message
      │
      ▼
  Agent loop (≤ 6 rounds) ──► Gemini (function calling) ──► tool calls
      ▲                                                        │
      └──────────────── tool results ◄─────────────────────────┘
```

The model never sees the CSVs or the PDF. It sees nine tools and decides which to call:

| Tool | Source | Use |
| --- | --- | --- |
| `search_products`, `get_product`, `list_categories` | `products.csv`, `categories.csv`, `promotions.csv` | Catalogue, prices, stock, promotions |
| `consult_policies` | `politicas_da_loja.md` | Returns, shipping, warranty, payment rules |
| `simulate_payment` | products + policy rules | PIX price and allowed instalments |
| `get_store_status` | clock + opening hours | Is the store open now, next opening |
| `get_order_status`, `check_return_eligibility` | `orders.csv`, `order_items.csv`, `customers.csv` | Order lookup and return windows, behind verification |
| `request_human_followup` | JSONL ticket log | Hand-off for what the agent cannot do |

Source: `src/emporio/` (`agent.py` is the loop, `persona.py` the system prompt, `registry.py`
the tool declarations, `tools/` the tools, `llm.py` the Gemini adapter, `cli.py` the terminal
chat, `web/` the Flask front end).

## Technical decisions

**Data vs. policies, decided by the model.** Facts that change (price, stock, promotions, order
status, hours) come from data tools; rules (what can be returned, when, how) come from the policy
manual. The system prompt forbids stating a price, stock level, deadline or status from memory:
every such fact must come from a tool call in the same conversation. Some questions need both
(a return request checks the order record and the returns policy), and the model chains the
tools itself.

**Function calling with deterministic tools, not Text-to-SQL.** The data is small and the useful
questions are known, so each tool encapsulates one rule (for example, "promotions never stack and
PIX does not apply to promotional prices") and returns ready-to-display values. That keeps money
arithmetic out of the model, makes behaviour testable without an LLM, and avoids the injection
and correctness risks of generated queries. The cost is flexibility: a question no tool covers
needs a new tool.

**Policy retrieval: heading chunks + BM25, no embeddings.** The manual is short and well
structured, so it is converted once (`scripts/ingest_policies.py`) into a reviewable Markdown file
and split by heading. Retrieval is a small BM25 with accent stripping, plural and prefix
stemming, a few query expansions and per-section keywords. It runs offline, is deterministic and
needs no vector store or extra API calls. With a much larger manual I would move to embeddings
plus a re-ranker.

**Identity verification before personal data (LGPD).** Order data is only returned when the order
id is accompanied by the e-mail or phone on file. An unknown order and a wrong e-mail get the same
generic answer, and after 3 failed attempts the session is locked. The failed-attempt counter is
kept even when an LLM call fails mid-turn, so provoking API errors cannot reset it.

**The agent cannot process returns or change orders.** It checks eligibility and, when something
needs a person, opens a hand-off ticket (`EM-YYYYMMDD-NNN`, appended to a JSONL file) and says
the team answers within 24 business hours.

**Robustness around the LLM.** Retries with backoff on 429/500/503/504, a bounded tool loop,
a safe reply if the model returns nothing, and rollback of a half-finished turn so the saved
history never contains a dangling tool call. Gemini "thought signatures" are replayed unchanged.

**Persistence.** One JSON file per conversation in `.sessions/` (history, verification state and
a transcript with every tool call), written atomically. `--session` resumes a conversation.

**Clock.** The sample data ends in March 2026, so "today" is configurable (`--now` or
`EMPORIO_NOW`) and the store's timezone is `America/Campo_Grande`. Opening hours, holidays
(national, Good Friday, 26 Aug for Campo Grande, 11 Oct for MS) and return windows all use it.

**Gemini free tier, with Flash-Lite as the default model.** Chosen to keep the project
reproducible at no cost, and the free tier's usage limits are the real constraint, so the default
is `gemini-3.5-flash-lite` (override with `GEMINI_MODEL` or `--model`). A light model follows long
instructions less reliably, which is why the rules live in tools (prices, instalments, return
windows are computed in code) and the prompt stays short and concrete. Google may use free-tier
content to improve its products; the data here is fictional. For real customer data use a paid
tier (or another provider behind the same small `LLM` interface in `llm.py`).

## Assumptions and data quirks

The inputs have inconsistencies. Rather than silently picking one, the policy Markdown carries
errata and internal notes ("do not repeat to the customer"), and the agent is told how to behave:

- **Phone numbers** differ between sections of the PDF; an internal note steers the agent away
  from quoting a conflicting number and towards the hand-off ticket.
- **Instalment rules:** the table and the tier text disagree; the tiers are used (1x no minimum,
  2–3x from R$ 50, 4–6x from R$ 80, 7–12x from R$ 100).
- **"300 items" in the catalogue** does not match the 65 products in the data; the agent speaks
  only about what exists in the data.
- **Receipt date:** orders have no delivery date, so `estimated_delivery` is used as the receipt
  date for return windows (regret: 7 days from receipt; defect exchange: 30 days from purchase;
  legal warranty: 90 days from receipt). All orders are treated as online purchases.
- **Promotions** are shown only when active; an expired one is acknowledged, and the current price
  is given. Price filters use the price actually paid.
- A product listed as available needs `status = active` and stock above zero.

## Known limitations and what I would do with more time

- **Prompt quality is only as validated as the example conversations.** The unit tests use a
  stubbed LLM, so they check the plumbing, not what Gemini decides to do. A proper evaluation set (questions with expected tool calls and facts)
  run on every change is the first thing I would add.
- **The web front end is a demo server.** It is Flask's development server with one lock around
  requests and no authentication. A real deployment needs a WSGI server, per-user auth and a
  WhatsApp (or similar) channel adapter; the agent itself is UI-agnostic (`Agent.respond`).
- **Streaming and latency:** replies arrive whole, and each tool round is a separate model call.
- **Free-tier limits:** quota errors are retried a few times, then the customer gets an apology.
  A queue and a fallback model would be needed in production.
- **Guardrails:** prompt-injection resistance rests on the system prompt and on tools that only
  return what the customer is verified for. I would add an adversarial test set and output checks
  (for example, verifying that every quoted price appeared in a tool result).
- **Retrieval:** BM25 is enough for this manual; it would not scale to a large or unstructured
  knowledge base.
- **Concurrency and storage:** sessions and tickets are local files, fine for a demo, not for
  several processes. I would use a database and real ticketing integration.
- **Single language:** Léo answers in the customer's language but is tuned for Portuguese.
- **Out-of-scope replies and services the data does not mention.** Testing showed the agent
  pointing customers to other shops (my own persona rule told it to) and stating that the store
  does not do repairs, which nothing in the data says. The persona now says the store only sells
  instruments, never names other businesses, and treats services as unknown until a policy
  lookup shows them, offering a hand-off otherwise. **`examples/05-out-of-scope.md` was
  generated before this change and was not regenerated, so it still shows the old behaviour**
  (run `python scripts/run_examples.py --only 05` to see the new one). The fix is untested beyond
  that: an evaluation set with out-of-scope and unknown-service cases is what would keep it from
  coming back.
- **Noisy messages are not explicitly handled.** A message that mixed a real question with pasted
  notes was answered correctly, but only because the model ignored the extra text. The same
  weakness covers instructions hidden in a message ("ignore your rules"): both deserve their own
  cases in an evaluation set, not just reliance on the model's judgement.

## Use of AI assistants

I built this with Claude (via Claude Code) as a pair-programming assistant: it helped plan the
architecture, write code, tests and this documentation, and debug problems such as PDF
extraction and Gemini tool-calling details. Product and design decisions (provider, scope of
the tools, language choices, git workflow) were made by me, and the commit history is the real,
incremental history of the work. The example conversations are generated by running the agent,
not edited by hand. Gemini is the model that powers the agent at runtime.

## Repository layout

```text
data/                    challenge data (renamed, content unchanged) + generated policies Markdown
examples/                example conversations generated by scripts/run_examples.py
scripts/                 ingest_policies.py (PDF → Markdown), run_examples.py
src/emporio/             agent, persona, tools, retrieval, Gemini adapter, CLI, web/ (Flask)
tests/                   unit tests (no network needed)
```

The challenge files were only renamed (`desafio_tecnico_ai_eng - products.csv` → `products.csv`,
`políticas_da_loja.pdf` → `politicas_da_loja.pdf`); their content is unchanged. To regenerate
`data/politicas_da_loja.md` from the PDF: `pip install pdfplumber` and
`python scripts/ingest_policies.py`.
