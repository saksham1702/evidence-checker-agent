"""Checks done in plain code, after the AI steps. They can only make a result
more careful (move it to abstain), never more confident.
"""

MIN_QUOTE_CHARS = 20  # anything shorter ("72 hours") is too thin to count as support

# Curly quotes, dash/hyphen variants and odd spaces that models swap for plain forms.
# One character in, one character out, so offsets still line up with the saved text.
_PLAIN = str.maketrans({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"',
                        0x2010: "-", 0x2011: "-", 0x2012: "-", 0x2013: "-", 0x2014: "-", 0x2212: "-",
                        0x00A0: " ", 0x202F: " "})
_BULLETS = ("- ", "* ", "\N{BULLET} ")  # list markers a model may add in front of a quote


def _normalise(text: str) -> tuple[str, list[int]]:
    """Plain punctuation, runs of whitespace collapsed to one space.

    Also returns, for each output character, its index in the original text,
    so a match can be mapped back to exact offsets in the saved page.
    """
    chars, index = [], []
    prev_space = False
    for i, ch in enumerate(text.translate(_PLAIN)):
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        chars.append(ch)
        index.append(i)
    return "".join(chars), index


def locate_quote(quote: str, text: str) -> tuple[int, int] | None:
    """Return (start, end) of quote inside text, or None if it is not there word for word.

    Only whitespace, quote/dash style and a leading list bullet may differ;
    wording, case and order must match.
    """
    needle, _ = _normalise(quote.strip())
    while needle.startswith(_BULLETS):
        needle = needle[2:]
    if len(needle) < MIN_QUOTE_CHARS:
        return None
    hay, index = _normalise(text)
    pos = hay.find(needle)
    if pos < 0:
        return None
    return index[pos], index[pos + len(needle) - 1] + 1


def check_decision(decision: dict, passages: dict[str, dict]) -> dict:
    """Validate one page-level decision against the passage registry.

    passages holds only quotes that were found in the saved text, so a cited ID
    that is missing from it is either invented or a rejected quote.
    """
    notes = []
    rule_id, source_id = decision["rule_id"], decision["source_id"]

    def keep_valid(ids: list[str]) -> list[str]:
        good = [i for i in dict.fromkeys(ids)
                if i in passages and passages[i]["rule_id"] == rule_id and passages[i]["source_id"] == source_id]
        dropped = [i for i in ids if i not in good]
        if dropped:
            notes.append(f"dropped citation ids that do not exist for this rule and page: {dropped}")
        return good

    result = decision["result"]
    if result == "abstain":  # nothing to downgrade, but any IDs kept for the reviewer must still be real
        for key in ("cited_ids", "side_a", "side_b"):
            decision[key] = keep_valid(decision[key])
    elif result in ("pass", "fail"):
        decision["cited_ids"] = keep_valid(decision["cited_ids"])
        if not decision["cited_ids"]:
            _to_abstain(decision, "no_valid_citation", f"{result} had no real supporting text", notes)
        elif not any(passages[i]["clarity"] == "clear" for i in decision["cited_ids"]):
            _to_abstain(decision, "no_clear_citation", f"{result} rested only on cut-off or unclear passages", notes)
    elif result == "conflict":
        decision["side_a"] = keep_valid(decision["side_a"])
        decision["side_b"] = keep_valid(decision["side_b"])
        if not decision["side_a"] or not decision["side_b"] or set(decision["side_a"]) & set(decision["side_b"]):
            _to_abstain(decision, "no_valid_citation", "conflict needs two separate, valid sides", notes)

    decision["code_check_notes"] = decision.get("code_check_notes", []) + notes
    decision["needs_human_review"] = decision["result"] in ("fail", "conflict", "abstain")
    return decision


def _to_abstain(decision: dict, code: str, why: str, notes: list[str]) -> None:
    notes.append(f"switched {decision['result']} to abstain: {why}")
    decision.update(result="abstain", abstain_reason=code, changed_by="code_check",
                    cited_ids=[], side_a=[], side_b=[])


def check_cross_conflict(conflict: dict, rule_id: str, passages: dict[str, dict]) -> dict | None:
    """Keep a cross-page conflict only if both sides cite real passages for this
    rule, the two sides come from different pages, and each side has clear text."""
    sides = []
    for key in ("side_a", "side_b"):
        ids = [i for i in dict.fromkeys(conflict[key]) if i in passages and passages[i]["rule_id"] == rule_id]
        sides.append(ids)
    # a page on both sides is an inside-page disagreement, not a cross-page one: drop it from both
    shared = {passages[i]["source_id"] for i in sides[0]} & {passages[i]["source_id"] for i in sides[1]}
    side_a, side_b = ([i for i in ids if passages[i]["source_id"] not in shared] for ids in sides)
    if not all(any(passages[i]["clarity"] == "clear" for i in ids) for ids in (side_a, side_b)):
        return None
    return {**conflict, "side_a": side_a, "side_b": side_b}
