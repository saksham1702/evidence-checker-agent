# Decisions

This file records the decisions I made while building the checker and the reason for
each. Most of them follow from one principle: the model reads and judges, plain code
verifies anything that can be verified exactly, and each later step may only make an
answer more cautious.

## Reading the brief

- I treat a page as a **pass** when it states the rule, states a stricter version of it,
  or shows the rule being followed.
- I treat a page as a **fail** when it says something that goes against the rule, or
  reports that someone broke it.
- I use **conflict** only when the page says opposite things about the same rule.
- I use **abstain** when the text is missing, unclear or cut off, or when the page did
  not load.
- An article that restates a rule and then reports a breach is a fail, not a conflict,
  because the article itself is consistent.
- Each page has an ID of the form `ev_N`, and each quote taken from it has an ID of the
  form `ev_N.pK`. This allows a conflict within one page to cite two separate passages.
- `needs_human_review` is set to true for every fail, conflict and abstain result.
- I count a cross-page conflict when two pages state things that cannot both be true,
  or when one page passes a rule that another page fails. Two companies following
  different practices does not count.
- I read "a second AI step" as a separate call with a separate job, not a second model.

## Tools

- I used plain Python with the Groq SDK and trafilatura, and no agent framework. The
  flow is a fixed pipeline, and a framework would add code without adding any checks.
- Every step uses Groq's `gpt-oss-120b`, the strongest model available on the key.
- Every call uses a strict JSON schema, so the output never needs repair.
- Temperature is 0 with a fixed seed, so runs repeat as closely as Groq allows.
- trafilatura extracts the readable text from each page and drops menus and comments.

## Loading pages

- HTTP 401, 402, 403, 407, 429 and 451 mark a page as **blocked**, since the site refused
  the request. Any other failure marks it as **error**.
- A page with fewer than 200 characters of text is marked **empty**.
- Page text is capped at 8,000 characters, cut at a word boundary and flagged as
  truncated. This keeps every call within the free tier's limit of 8k tokens a minute.
- Pages that fail to load are never sent to the model, and every rule on them abstains.
- Each page's text is saved to `snapshots/` with its hash, so any citation can be
  checked afterwards.
- I did not add link following, as none of the six pages required it.

## Extracting quotes

- One extraction call per page covers all the rules. Separate calls per rule would have
  tripled the run time on the free tier.
- The model may return up to five quotes per rule and is asked for one per part of the
  rule. With a limit of three, it missed the second half of the advertising rule.
- Each quote is marked as clear, cut off or unclear, with a short note.
- Code checks that every quote appears on the page exactly. Differences in spacing, quote
  marks, dash characters and a leading bullet are ignored; everything else must match.
  A quote must be at least 20 characters long.
- I added bullets and the less common dash characters to that list after they caused
  correct quotes to be rejected.
- The stored quote is taken from the saved page by its position, rather than copied from
  the model's answer.
- Quotes that fail the check are sent back to the model with feedback, for that rule
  only, for up to three rounds in total.
- Every prompt marks the page text as untrusted, in case a page contains instructions.

## Judging

- The judge receives only the quotes, assembled in code, and never sees the full page.
- A pass or fail requires at least one quote marked clear.
- A conflict requires clear quotes on both sides, drawn from different passages.
- The self-check reads the page alongside the draft result. It may keep the result or
  change it to abstain, but it can never make it more confident.
- Code decides whether a quote is on the page, and the model decides what it means. In
  one run the self-check claimed an exact quote was wrong; code now overrules such a
  claim and records that it did so.
- Any cited ID that does not exist, or that belongs to another page, is removed. A pass
  or fail left without a valid quote becomes abstain.

## Comparing pages

- One AI check per rule looks at the quotes from all pages together.
- Code discards any conflict that does not span two pages or lacks clear text on each
  side. I added this after the AI built a conflict on a sentence that was cut off.
- Code also flags a pass on one page against a fail on another, so this case does not
  depend on a single model call.

## Sample pages

- I used real pages only. The first version included two invented pages, but the brief
  asks for real URLs or saved snapshots, so I replaced them.
- The six pages are two laws, one article about a company that broke a law, two articles
  that disagree about green claims, and one paywalled page that fails to load.
- I wrote the expected results by hand before running the checker on these pages.

## Running the checker

- Responses are cached on disk, so a repeated run is free and gives the same result.
  The `--fresh` option bypasses the cache.
- The SDK retries when it hits a rate limit, so the free tier makes a run slower but
  does not break it.
- A missing API key or a malformed input file stops the run with a clear message.
- Eleven offline tests cover the code checks and the retry loop and need no API key.

## The additional feature

- I added one extra feature, `eval.py`. It is an automatic grader that re-checks every
  quote against the saved pages and scores the results against the expected results.
- I chose not to build an HTML report, as it would not prove anything about accuracy.
- I chose not to add a vote from a second model, as it would double the cost on the free
  tier. It is the check I propose in NOTES.
