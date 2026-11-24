"""Grade a results.json the way the task rubric does.

Re-checks every citation from the saved snapshots on disk (not from the
checker's memory), compares results with hand labels in expected.json, and
exits non-zero if any citation is false or any confident result is wrong.

    python eval.py [--results results.json] [--expected expected.json]
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

DECISIVE = {"pass", "fail"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", default="results.json")
    ap.add_argument("--expected", default="expected.json")
    args = ap.parse_args()
    res = json.loads(Path(args.results).read_text())
    gold = json.loads(Path(args.expected).read_text())

    sources = {s["id"]: s for s in res["sources"]}
    passages = {p["id"]: p for p in res["passages"]}
    texts = {sid: Path(s["snapshot"]).read_text(encoding="utf-8") for sid, s in sources.items() if s.get("snapshot")}

    # True citations: each quote must sit at its offsets in an unmodified snapshot.
    problems = [f"{sid}: snapshot hash mismatch" for sid, t in texts.items()
                if hashlib.sha256(t.encode("utf-8")).hexdigest() != sources[sid]["sha256"]]
    citations = [(r["rule_id"], page["source_id"], c) for r in res["rules"] for page in r["pages"]
                 for c in page["citations"]]
    cross_ids = [(r["rule_id"], i) for r in res["rules"] for c in r["cross_page"]["conflicts"]
                 for i in c["side_a"] + c["side_b"]]
    for rule_id, source_id, c in citations:
        p = passages.get(c["id"])
        if not p or p["source_id"] != source_id or p["rule_id"] != rule_id:
            problems.append(f"{rule_id}/{source_id}: unknown or misplaced id {c['id']}")
        elif texts.get(source_id, "")[p["start"]:p["end"]] != c["quote"]:
            problems.append(f"{c['id']}: quote not at its offsets in the snapshot")
    problems += [f"{rule_id} cross-page: unknown id {i}" for rule_id, i in cross_ids if i not in passages]
    cited = len(citations) + len(cross_ids)

    # Results against hand labels.
    results = [(r["rule_id"], page) for r in res["rules"] for page in r["pages"]]
    pairs = [(page["source_id"], rule_id, gold["pages"][page["source_id"]][rule_id], page["result"])
             for rule_id, page in results if rule_id in gold["pages"].get(page["source_id"], {})]
    review_errors = sum(page["needs_human_review"] != (page["result"] != "pass") for _, page in results)
    right = sum(w == g for _, _, w, g in pairs)
    unsafe = [(s, r, w, g) for s, r, w, g in pairs if g in DECISIVE and g != w]   # confident and wrong
    timid = [(s, r, w, g) for s, r, w, g in pairs if w in DECISIVE and g == "abstain"]  # missed a sure call
    cross = [(r["rule_id"], gold["cross_page"].get(r["rule_id"]), r["cross_page"]["status"]) for r in res["rules"]]
    cross_right = sum(w == g for _, w, g in cross)
    failed = [s["id"] for s in res["sources"] if s["status"] != "ok"]
    failed_abstained = all(p["result"] == "abstain" for r in res["rules"] for p in r["pages"] if p["source_id"] in failed)
    run = res["run"]

    rows = [
        ("Works end to end", f"{len(sources)} pages, {len(failed)} failed to load ({', '.join(failed) or 'none'}), "
                             f"steps {run['steps_used']}/{run['max_steps']}"),
        ("True citations", f"{cited - len(problems)}/{cited} cited quotes and IDs verified against snapshots"),
        ("Honest abstaining", f"{len(unsafe)} confident-but-wrong, {len(timid)} over-cautious, "
                              f"failed pages all abstain: {failed_abstained}"),
        ("Page results", f"{right}/{len(pairs)} match the hand labels"),
        ("Cross-page", f"{cross_right}/{len(cross)} rules match the hand labels"),
        ("Review flags", f"{review_errors} decisions with a wrong needs_human_review flag"),
    ]
    width = max(len(k) for k, _ in rows)
    for k, v in rows:
        print(f"{k:<{width}}  {v}")
    print("results:", dict(Counter(g for *_, g in pairs)))
    for s, r, w, g in pairs:
        if w != g:
            print(f"  mismatch {s} {r}: expected {w}, got {g}")
    for r, w, g in cross:
        if w != g:
            print(f"  cross-page mismatch {r}: expected {w}, got {g}")
    for p in problems:
        print("  citation problem:", p)
    return 1 if problems or unsafe or review_errors or not failed_abstained else 0


if __name__ == "__main__":
    sys.exit(main())
