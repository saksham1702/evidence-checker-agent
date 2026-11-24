"""Offline tests for the code-side checks. Run: python test_checks.py (or pytest)."""

from checks import check_cross_conflict, check_decision, locate_quote

TEXT = "Intro line.\nThe controller shall notify the  breach within 72 hours.\nEvery paid post carries a “Sponsored” label."

PASSAGES = {
    "ev_1.p1": {"source_id": "ev_1", "rule_id": "r1", "clarity": "clear", "quote": "x"},
    "ev_1.p2": {"source_id": "ev_1", "rule_id": "r1", "clarity": "cut_off", "quote": "x"},
    "ev_1.p3": {"source_id": "ev_1", "rule_id": "r1", "clarity": "clear", "quote": "x"},
    "ev_2.p1": {"source_id": "ev_2", "rule_id": "r1", "clarity": "clear", "quote": "x"},
}


def decision(result, cited=(), a=(), b=()):
    return {"rule_id": "r1", "source_id": "ev_1", "result": result, "cited_ids": list(cited),
            "side_a": list(a), "side_b": list(b), "abstain_reason": None, "changed_by": None}


def test_locate_quote_tolerates_whitespace_and_quote_style():
    start, end = locate_quote("shall notify the breach within 72 hours.", TEXT)
    assert TEXT[start:end] == "shall notify the  breach within 72 hours."
    start, end = locate_quote('Every paid post carries a "Sponsored" label.', TEXT)
    assert TEXT[start:end].endswith("“Sponsored” label.")
    start, end = locate_quote("- The controller shall notify the breach within 72 hours.", TEXT)
    assert TEXT[start:end].startswith("The controller")  # an added list bullet is formatting, not wording


def test_locate_quote_rejects_edits_ellipses_and_scraps():
    assert locate_quote("The controller must notify the breach within 72 hours.", TEXT) is None
    assert locate_quote("The controller shall ... within 72 hours.", TEXT) is None
    assert locate_quote("72 hours", TEXT) is None  # too short to count as support


def test_pass_with_invented_id_becomes_abstain():
    d = check_decision(decision("pass", cited=["ev_1.p9"]), PASSAGES)
    assert d["result"] == "abstain" and d["abstain_reason"] == "no_valid_citation"
    assert d["needs_human_review"]


def test_fail_citing_another_page_becomes_abstain():
    d = check_decision(decision("fail", cited=["ev_2.p1"]), PASSAGES)
    assert d["result"] == "abstain"


def test_pass_on_cut_off_text_only_becomes_abstain():
    d = check_decision(decision("pass", cited=["ev_1.p2"]), PASSAGES)
    assert d["result"] == "abstain" and d["abstain_reason"] == "no_clear_citation"


def test_valid_pass_survives_and_needs_no_review():
    d = check_decision(decision("pass", cited=["ev_1.p1", "ev_1.p9"]), PASSAGES)
    assert d["result"] == "pass" and d["cited_ids"] == ["ev_1.p1"] and not d["needs_human_review"]


def test_conflict_needs_two_real_sides():
    assert check_decision(decision("conflict", a=["ev_1.p1"], b=["ev_1.p3"]), PASSAGES)["result"] == "conflict"
    assert check_decision(decision("conflict", a=["ev_1.p1"], b=["ev_1.p1"]), PASSAGES)["result"] == "abstain"
    assert check_decision(decision("conflict", a=["ev_1.p1"], b=[]), PASSAGES)["result"] == "abstain"


def test_cross_page_conflict_must_span_two_pages():
    ok = check_cross_conflict({"summary": "", "side_a": ["ev_1.p1"], "side_b": ["ev_2.p1", "bogus"]}, "r1", PASSAGES)
    assert ok["side_b"] == ["ev_2.p1"]
    assert check_cross_conflict({"summary": "", "side_a": ["ev_1.p1"], "side_b": ["ev_1.p3"]}, "r1", PASSAGES) is None


def test_cross_page_conflict_needs_clear_text_on_each_side():
    assert check_cross_conflict({"summary": "", "side_a": ["ev_1.p2"], "side_b": ["ev_2.p1"]}, "r1", PASSAGES) is None


def run_loop(replies, max_steps):
    """Drive the evidence loop with canned extractor replies instead of the model."""
    import main
    import stages
    seen = []

    def fake_extract(src, rules, feedback):
        seen.append(feedback)
        return replies[min(len(seen), len(replies)) - 1]

    real, stages.extract = stages.extract, fake_extract
    try:
        src = {"id": "ev_1", "source": "test", "status": "ok", "truncated": False, "text": TEXT}
        return main.gather_evidence([src], [{"id": "r1", "text": "Report breaches in 72 hours."}], max_steps) + (seen,)
    finally:
        stages.extract = real


BAD = [{"rule_id": "r1", "quote": "The controller must notify the breach", "clarity": "clear", "note": ""}]
GOOD = [{"rule_id": "r1", "quote": "The controller shall notify the breach within 72 hours.", "clarity": "clear", "note": ""}]


def test_evidence_loop_asks_again_after_a_bad_quote():
    passages, rejected, steps, left, seen = run_loop([BAD, GOOD], max_steps=3)
    assert len(steps) == 2 and len(rejected) == 1 and not left
    assert "not found word for word" in seen[1] and BAD[0]["quote"] in seen[1]
    assert passages["ev_1.p1"]["quote"] == "The controller shall notify the  breach within 72 hours."


def test_evidence_loop_stops_at_the_step_limit():
    passages, rejected, steps, left, seen = run_loop([BAD], max_steps=2)
    assert len(steps) == 2 and len(seen) == 2 and not passages
    assert left == {"ev_1": ["r1"]}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
