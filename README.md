# Evidence Checker Agent

Checks a list of web pages against a list of written rules. Every result points to
word-for-word text from the saved page, disagreements inside a page and across pages
are flagged, and the program says `abstain` instead of guessing when text is missing,
blocked, cut off or unclear.

## Setup

Python 3.10 or newer and a [Groq](https://console.groq.com) API key (the free tier is enough).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
echo "GROQ_API_KEY=your_key_here" > .env      # or export GROQ_API_KEY=...
```

## Run

```bash
python main.py          # rules.json + pages.json  ->  results.json and snapshots/
python eval.py          # grades results.json against the hand labels in expected.json
python test_checks.py   # offline tests for the code checks and the step loop (no API key needed)
```

| Option | Default | Meaning |
|---|---|---|
| `--rules`, `--pages`, `--out` | `rules.json`, `pages.json`, `results.json` | input and output files |
| `--max-steps` | `3` | rounds of the evidence loop before it stops |
| `--snapshots` | `snapshots/` | where the fetched page text is saved |
| `--fresh` | off | ignore the response cache in `.cache/` |
| `CHECKER_MODEL` env var | `openai/gpt-oss-120b` | Groq model for all AI steps |

A fresh run on the sample inputs makes about 19 model calls (about 31k tokens) and takes
2 to 3 minutes, mostly waiting on the free tier's 8k tokens-per-minute limit. A re-run on
unchanged pages comes from the cache in seconds.

## How it works

```
pages.json ─► fetch: ev_1..ev_6, status ok | blocked | error | empty
              text saved to snapshots/ev_N.txt with its sha256
                │
                ▼
   evidence loop, at most --max-steps rounds
     AI extract : short word-for-word passages per rule + clear / cut_off / unclear note
     code       : find each quote in the saved text; keep it with offsets as ev_N.pK,
                  or throw it away and ask again for that rule with feedback
                │
                ▼
   per page     AI judge      sees only the passages, never the page
                AI self-check sees page text + draft; can keep or move to abstain
                code checks   cited IDs exist, pass/fail have clear support,
                              conflicts have two real sides; else abstain
                │
                ▼
   per rule     AI cross-page check over all pages' passages
                code check on its IDs, plus a pass-on-one-page / fail-on-another backstop
                │
                ▼
              results.json
```

Each later step can only make a result more careful (move it to `abstain`), never more
confident. Pages that fail to load never reach the model; every rule on them is `abstain`.

## Output: `results.json`

| Key | What it holds |
|---|---|
| `run` | model, `max_steps`, `steps_used`, per-step counts, model calls and tokens |
| `sources` | `ev_N`, URL or file, `status`, HTTP code, detail, `sha256`, snapshot path |
| `passages` | `ev_N.pK`, rule, exact quote, `start`/`end` offsets in the snapshot, clarity, note |
| `rejected_quotes` | quotes the model gave that were not in the page text |
| `rules[].pages[]` | `result`, `reason`, `citations` (ID + quote), `conflict_sides`, `abstain_reason`, `draft_result`, `changed_by`, `self_check`, `code_check_notes`, `needs_human_review` |
| `rules[].cross_page` | `status` and each conflict with `side_a` / `side_b` passage IDs |

`needs_human_review` is true on every page result that is `fail`, `conflict` or `abstain`,
and on a rule when any of its page results needs review or pages conflict.

## Sample inputs

`rules.json` holds three EU rules (breach reporting within 72 hours, labelled ads with an
explanation, verified green claims). `pages.json` holds six real, live pages:

| ID | Source | What it tests | Result |
|---|---|---|---|
| ev_1 | GDPR Article 33 (gdpr-info.eu) | backs r1 | r1 pass |
| ev_2 | DSA Article 26 | backs r2, which has two parts | r2 pass |
| ev_3 | Hunton blog: Booking.com fined for reporting a breach 22 days late | breaks r1 | r1 fail |
| ev_4 | Loyens & Loeff, 2025: Green Claims Directive still on the table | a proposal, not a rule in force | r3 abstain |
| ev_5 | Tappr guide, 2026: Green Claims Directive being withdrawn | mixed on r3, long page cut at 8,000 characters | r3 conflict |
| ev_6 | Financial Times article | paywall, HTTP 403 | blocked, all abstain |

Cross-page: r1 conflict (the law vs a company that broke it), r2 no conflict, r3 conflict
(the 2025 and 2026 pages disagree on green claims). Each page's role is in its `note` field.

## Files

| File | Job |
|---|---|
| `main.py` | runs the pipeline and writes `results.json` |
| `fetch.py` | loads each source and sets its status |
| `stages.py` | the four AI steps and their prompts and JSON schemas |
| `checks.py` | code checks: quote matching and citation validation |
| `llm.py` | Groq call with strict JSON schema output and a disk cache |
| `eval.py` | rubric scorer and independent citation audit |
| `test_checks.py` | offline tests for the code checks and the step loop |
| `expected.json` | hand labels for the sample inputs, used only by `eval.py` |
| `NOTES.md`, `decisions.md` | short note on tricky cases; design decisions and why |
