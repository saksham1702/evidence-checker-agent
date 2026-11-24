# Notes

## What I built

A Python pipeline on Groq (`gpt-oss-120b`, strict JSON schemas). It loads six real pages,
pulls word-for-word passages per rule in a loop capped at three steps, judges each rule
from the passages alone, has the model review its draft against the page, then checks
everything in code and compares pages with each other. Every quote in `results.json` is
an exact slice of a saved snapshot, with offsets and a hash, and `eval.py` re-checks that
from disk. It matches all 18 hand labels and all 3 cross-page labels.

Caveat: I tuned the prompts on an earlier set of pages, then swapped in these six and
wrote their labels before running them. The first run on them scored 18/18 and 3/3 with
no changes after. One prompt line ("a page that restates the rule and reports someone
broke it is fail, not conflict") was added just before, with the Booking.com page in mind.

## Tricky cases

1. **Quotes that were almost right.** In step 1 the model gave three quotes the code
   rejected: one started "The Dutch DPA noted" where the page says "the Dutch DPA noted",
   one used "..." to skip text, and one swapped a comma for a full stop. Each looked fine
   to a reader. The loop sent them back with feedback and step 2 returned exact sentences.
   Without the code check, all three would have been cited as page text.

2. **A proposal that reads like a rule.** The Loyens & Loeff page says the Green Claims
   proposal requires companies to substantiate and verify claims. The extractor marked it
   clear and the judge drafted `pass`. The self-check caught that a proposal is not a rule
   in force and moved it to `abstain`.

3. **A mixed page.** The Tappr page describes two laws: the withdrawn Green Claims proposal
   (independent checks never came into force) and the Empowering Consumers Directive (a
   generic "green" claim needs recognised proof, in force now). For r3 that is "partly
   yes, partly no". The checker has no such result, so it says `conflict`, which sends it
   to a human. Two of the passages it cites say "would have needed", which is weaker
   support than the judge treats it as.

## Known limits

- The r3 cross-page result matches the label, but the pair the model cites does not truly
  disagree: both pages say verification was only proposed. The real tension (2025 "still
  on the table" vs 2026 "listed for withdrawal") is about dates, and no rule asks for them.
- Page text is capped at 8,000 characters for the free tier's token limit; ev_5 was cut
  and flagged.
- Live pages change and Groq is not fully deterministic; the cache makes re-runs repeat
  on one machine only.

## One check I would add

A second-opinion judge on a different model (for example `qwen/qwen3.8-27b` on Groq) that
gets the same passages; when the two disagree, the result becomes `abstain`. In an earlier
run the self-check claimed an exact GDPR quote read "result to" instead of "result in";
one model can be confidently wrong in a way its own review shares.
