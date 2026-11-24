"""Evidence Checker Agent.

Checks every page in pages.json against every rule in rules.json. Each result
cites passages that were found word for word in the saved page text, and falls
back to abstain whenever the text is missing, cut off or unclear.

    python main.py                      # rules.json + pages.json -> results.json
    python main.py --max-steps 2 --fresh
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path

import checks
import llm
import stages
from fetch import load_source

HERE = Path(__file__).parent


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load_env(path: Path) -> None:
    """Read KEY=VALUE lines from .env without overriding the real environment."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# ------------------------------------------------------ 1. evidence loop

def gather_evidence(sources: list[dict], rules: list[dict], max_steps: int) -> tuple[dict, list, list, dict]:
    """Bounded loop: ask for passages, check every quote in code, ask again only
    for rules whose quotes were not found word for word. Stops after max_steps.
    """
    rules_by_id = {r["id"]: r for r in rules}
    by_id = {s["id"]: s for s in sources}
    passages, rejected, steps, feedback = {}, [], [], {}
    todo = {s["id"]: list(rules_by_id) for s in sources if s["status"] == "ok"}

    for step in range(1, max_steps + 1):
        if not todo:
            break
        log(f"step {step}/{max_steps}: extracting evidence from {', '.join(todo)}")
        before = len(passages)
        bad = {sid: read_source(by_id[sid], [rules_by_id[r] for r in ids], feedback.get(sid, ""), step, passages)
               for sid, ids in todo.items()}
        bad = {sid: quotes for sid, quotes in bad.items() if quotes}
        thrown = list(chain.from_iterable(bad.values()))
        rejected += thrown
        feedback.update({sid: retry_feedback(quotes) for sid, quotes in bad.items()})
        steps.append({"step": step, "sources": list(todo), "passages_kept": len(passages) - before,
                      "quotes_rejected": len(thrown)})
        todo = {sid: sorted({q["rule_id"] for q in quotes}) for sid, quotes in bad.items()}

    if todo:
        log(f"step limit reached, quotes still unverified for: {todo}")
    return passages, rejected, steps, todo


def read_source(src: dict, rules: list[dict], feedback: str, step: int, passages: dict) -> list[dict]:
    """One extraction call for one page. Quotes found in the saved text are added
    to passages; the ones that were not are returned."""
    try:
        found = stages.extract(src, rules, feedback)
    except Exception as e:  # one failed call must not sink the run
        log(f"  {src['id']}: extraction failed ({e})")
        src["extract_error"] = str(e)
        return []
    bad = []
    for p in found:
        span = checks.locate_quote(p["quote"], src["text"])
        if span is None:
            bad.append({"source_id": src["id"], "rule_id": p["rule_id"], "quote": p["quote"], "step": step})
        elif not any(q["source_id"] == src["id"] and q["rule_id"] == p["rule_id"] and (q["start"], q["end"]) == span
                     for q in passages.values()):  # skip a passage already found on an earlier step
            pid = f"{src['id']}.p{sum(q['source_id'] == src['id'] for q in passages.values()) + 1}"
            passages[pid] = {"id": pid, "source_id": src["id"], "rule_id": p["rule_id"],
                             "quote": src["text"][span[0]:span[1]],  # exact saved text, not the model's copy
                             "start": span[0], "end": span[1],
                             "clarity": p["clarity"], "note": p["note"], "step": step}
    return bad


def retry_feedback(bad: list[dict]) -> str:
    return ("FEEDBACK FROM THE PREVIOUS STEP: these quotes were not found word for word in "
            "the page text (or were under 20 characters) and were thrown away:\n"
            + "\n".join(f'- {q["rule_id"]}: "{q["quote"]}"' for q in bad)
            + "\nCopy the text exactly, or return no passage for that rule.")


# ------------------------------------------- 2-4. judge, self-check, code

def new_decision(rule_id: str, source_id: str, **fields) -> dict:
    return {"rule_id": rule_id, "source_id": source_id, "result": "abstain", "reason": "",
            "abstain_reason": None, "cited_ids": [], "side_a": [], "side_b": [],
            "draft_result": None, "changed_by": None, "self_check": None, **fields}


def decide_page(src: dict, rules: list[dict], passages: dict) -> list[dict]:
    """pass / fail / conflict / abstain for every rule on one page."""
    if src["status"] != "ok":
        return [checks.check_decision(new_decision(
            r["id"], src["id"], abstain_reason=f"page_{src['status']}",
            reason=f"The page did not load ({src['status']}: {src['detail']}), so there is no text to check."),
            passages) for r in rules]

    rules_by_id = {r["id"]: r for r in rules}
    mine = {r["id"]: [p for p in passages.values() if p["source_id"] == src["id"] and p["rule_id"] == r["id"]]
            for r in rules}
    decisions = {}
    for r in rules:
        if not mine[r["id"]]:
            failed = "extract_error" in src
            decisions[r["id"]] = new_decision(
                r["id"], src["id"], abstain_reason="llm_error" if failed else "no_relevant_text",
                reason="Evidence extraction failed for this page." if failed
                else "No passage on this page relates to this rule.")

    to_judge = [r for r in rules if mine[r["id"]]]
    if to_judge:
        try:
            drafts = stages.judge(src["id"], to_judge, mine)
        except Exception as e:
            log(f"  {src['id']}: judge failed ({e})")
            drafts = []
        for d in drafts:
            if d["rule_id"] in decisions:
                continue  # duplicate or unexpected rule from the model
            decisions[d["rule_id"]] = new_decision(
                d["rule_id"], src["id"], result=d["result"], reason=d["reason"], draft_result=d["result"],
                cited_ids=d["cited_ids"], side_a=d["side_a"], side_b=d["side_b"],
                abstain_reason="judge_abstained" if d["result"] == "abstain" else None)
        for r in to_judge:
            decisions.setdefault(r["id"], new_decision(
                r["id"], src["id"], abstain_reason="llm_error", reason="The judge step returned no result."))

        to_review = [d for d in decisions.values() if d["result"] != "abstain"]
        if to_review:
            self_review(src, rules_by_id, to_review, passages)

    return [checks.check_decision(decisions[r["id"]], passages) for r in rules]


def self_review(src: dict, rules_by_id: dict, drafts: list[dict], passages: dict) -> None:
    """AI self-check. It can keep a draft or move it to abstain, nothing else."""
    try:
        reviews = {c["rule_id"]: c for c in stages.self_check(src, rules_by_id, drafts, passages)}
    except Exception as e:
        log(f"  {src['id']}: self-check failed ({e}), drafts move to abstain")
        reviews = {}
    for d in drafts:
        c = reviews.get(d["rule_id"])
        d["self_check"] = c
        if c and c["supports_result"] and not c["still_unclear"]:
            if c["keep"] and c["quotes_found"]:
                continue
            # "Is this quote in the text?" is a string test, and code already passed every
            # cited quote word for word. Doubts about meaning above still always downgrade.
            cited = d["cited_ids"] + d["side_a"] + d["side_b"]
            if not c["quotes_found"] and cited and all(i in passages for i in cited):
                d["code_check_notes"] = ["self-check said a quote was missing; code found every cited quote "
                                         "word for word in the saved text, so that doubt was overruled"]
                continue
        doubt = c["note"] if c else "the self-check did not run"
        d.update(result="abstain", abstain_reason="self_check_doubt", changed_by="self_check",
                 reason=f"Self-check doubt: {doubt} (draft was {d['draft_result']}: {d['reason']})")


# --------------------------------------------------------- 5. cross-page

def cross_page_check(rule: dict, decisions: list[dict], passages: dict) -> dict:
    pages = [{"source_id": d["source_id"], "result": d["result"],
              "passages": [p for p in passages.values() if p["rule_id"] == rule["id"] and p["source_id"] == d["source_id"]]}
             for d in decisions]
    pages = [p for p in pages if p["passages"]]
    conflicts, note = [], ""
    if len(pages) < 2:
        note = "Fewer than two pages had evidence for this rule, so there is nothing to compare."
    else:
        try:
            raw = stages.cross_page(rule, pages)
        except Exception as e:
            log(f"  {rule['id']}: cross-page check failed ({e})")
            raw, note = [], f"AI cross-page check failed: {e}"
        for c in raw:
            valid = checks.check_cross_conflict(c, rule["id"], passages)
            if valid:
                conflicts.append({**valid, "found_by": "ai"})
            else:
                note = "Some AI-reported conflicts were dropped because their IDs were invalid or one-sided."

    # Code backstop: one page passes and another fails the same rule.
    pass_ids = [i for d in decisions if d["result"] == "pass" for i in d["cited_ids"]]
    fail_ids = [i for d in decisions if d["result"] == "fail" for i in d["cited_ids"]]
    if pass_ids and fail_ids and not any(_spans(c, pass_ids, fail_ids, passages) for c in conflicts):
        conflicts.append({"summary": "One page backs this rule while another page goes against it.",
                          "side_a": pass_ids, "side_b": fail_ids, "found_by": "code"})
    return {"status": "conflict" if conflicts else "no_conflict", "conflicts": conflicts, "note": note}


def _spans(conflict: dict, pass_ids: list, fail_ids: list, passages: dict) -> bool:
    """Does this conflict already put a passing page against a failing page?"""
    a, b, p, f = (_pages(ids, passages) for ids in (conflict["side_a"], conflict["side_b"], pass_ids, fail_ids))
    return bool((a & p and b & f) or (a & f and b & p))


def _pages(ids: list[str], passages: dict) -> set[str]:
    return {passages[i]["source_id"] for i in ids}


# ----------------------------------------------------------------- output

def public(d: dict, passages: dict) -> dict:
    ids = list(dict.fromkeys(d["cited_ids"] + d["side_a"] + d["side_b"]))
    out = {"source_id": d["source_id"], "result": d["result"], "reason": d["reason"],
           "citations": [{"id": i, "quote": passages[i]["quote"]} for i in ids]}
    if d["side_a"] or d["side_b"]:
        out["conflict_sides"] = {"side_a": d["side_a"], "side_b": d["side_b"]}
    out.update(abstain_reason=d["abstain_reason"], draft_result=d["draft_result"], changed_by=d["changed_by"],
               self_check=d["self_check"], code_check_notes=d["code_check_notes"],
               needs_human_review=d["needs_human_review"])
    return out


def print_table(rules: list[dict], sources: list[dict], by_rule: dict, cross: dict) -> None:
    head = f"{'rule':<6}" + "".join(f"{s['id']:<10}" for s in sources) + "cross-page"
    print(head)
    for r in rules:
        cells = "".join(f"{d['result']:<10}" for d in by_rule[r["id"]])
        print(f"{r['id']:<6}{cells}{cross[r['id']]['status']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rules", default="rules.json")
    ap.add_argument("--pages", default="pages.json")
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--snapshots", default="snapshots", help="folder for the saved page text")
    ap.add_argument("--max-steps", type=int, default=3, help="rounds of the evidence loop (default 3)")
    ap.add_argument("--fresh", action="store_true", help="ignore the LLM response cache")
    args = ap.parse_args()
    if args.max_steps < 1:
        ap.error("--max-steps must be at least 1")

    load_env(HERE / ".env")
    if not os.environ.get("GROQ_API_KEY"):
        log("GROQ_API_KEY is not set. Put it in .env or export it.")
        return 1
    llm.use_cache = not args.fresh

    try:
        rules = json.loads(Path(args.rules).read_text())
        pages = json.loads(Path(args.pages).read_text())
        assert all(isinstance(r.get("id"), str) and isinstance(r.get("text"), str) for r in rules), \
            "every rule needs a string id and text"
        assert len({r["id"] for r in rules}) == len(rules), "rule ids must be unique"
        assert all(isinstance(p.get("source"), str) for p in pages), "every page needs a string source"
    except (OSError, ValueError, AttributeError, AssertionError) as e:
        log(f"bad input file: {e}")
        return 1
    root = Path(args.pages).resolve().parent

    snap_dir = Path(args.snapshots)
    snap_dir.mkdir(exist_ok=True)
    for old in snap_dir.glob("ev_*.txt"):
        old.unlink()
    sources = []
    for i, entry in enumerate(pages, 1):
        src = load_source(f"ev_{i}", entry, root)
        if src["text"]:
            src["snapshot"] = str(snap_dir / f"{src['id']}.txt")
            Path(src["snapshot"]).write_text(src["text"], encoding="utf-8")
        log(f"{src['id']}: {src['status']:<8} {src['source']} {src['detail']}")
        sources.append(src)

    passages, rejected, steps, unresolved = gather_evidence(sources, rules, args.max_steps)

    decisions = []
    for src in sources:
        log(f"judging {src['id']}")
        decisions += decide_page(src, rules, passages)

    by_rule = {r["id"]: [d for d in decisions if d["rule_id"] == r["id"]] for r in rules}
    cross = {}
    for r in rules:
        log(f"cross-page check {r['id']}")
        cross[r["id"]] = cross_page_check(r, by_rule[r["id"]], passages)

    rule_out = []
    for r in rules:
        ds = by_rule[r["id"]]
        rule_out.append({
            "rule_id": r["id"], "rule": r["text"],
            "needs_human_review": any(d["needs_human_review"] for d in ds) or cross[r["id"]]["status"] == "conflict",
            "counts": {k: sum(d["result"] == k for d in ds) for k in stages.RESULTS},
            "pages": [public(d, passages) for d in ds],
            "cross_page": cross[r["id"]],
        })

    results = {
        "run": {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "model": llm.model(), "max_steps": args.max_steps, "steps_used": len(steps), "steps": steps,
                "unverified_after_limit": unresolved, "llm": llm.stats},
        "sources": [{k: v for k, v in s.items() if k != "text"} for s in sources],
        "passages": list(passages.values()),
        "rejected_quotes": rejected,
        "rules": rule_out,
    }
    Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_table(rules, sources, by_rule, cross)
    log(f"wrote {args.out}  ({llm.stats['api_calls']} API calls, {llm.stats['cache_hits']} cached)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
