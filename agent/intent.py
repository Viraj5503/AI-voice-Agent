"""Classify which pillar(s) Jamie's reply is asking about.

Why this exists: surfacing Jamie's last reply into the prompt as an
anchor turned out to cause *anchoring bias* (the LLM kept the same
topic alive instead of moving past it).  The eval observed Jamie
asking about 'drivable' in turns 5, 7, 9 of the same call.

Real fix: track which pillars she has already asked about (regardless
of whether the caller answered) and EXCLUDE them from the 'STILL
NEEDED' section of the prompt.  If a pillar was asked but never
answered we list it under 'ASKED — REPHRASE OR MOVE ON' so Jamie
knows it's pending without re-asking.

Heuristic, not perfect.  But specifically tuned for the FNOL
intake conversation surface and verified with the smoke tests.
"""

from __future__ import annotations

import re

# (pillar_id, list of regex patterns).  Patterns are matched
# case-insensitively against Jamie's reply text.  We only want to fire
# on QUESTION-like usage, so most patterns include a "?" or a
# question-leading verb / interrogative.
_PILLAR_PATTERNS: list[tuple[str, list[str]]] = [
    ("injuries", [
        r"\b(are|is)\s+(you|anyone|anybody)\b.*\b(ok|okay|alright|hurt|injur)",
        r"\bany(one|body)?\s+(hurt|injur)",
        r"\bambulance\b",
        r"\b(physical(ly)?\s+)?(ok|okay|alright)\b\??",
    ]),
    ("accident_datetime", [
        r"\bwhen\s+(did|was|exactly)\b",
        r"\bwhat\s+time\b",
        r"\bhow\s+long\s+ago\b",
    ]),
    ("accident_location", [
        r"\bwhere\s+(were|are|did|exactly|was|the)\b",
        r"\bwhich\s+(road|street|exit|address|location)\b",
        r"\b(road|street|address)\s+name\b",
    ]),
    ("road_type", [
        r"\b(autobahn|motorway|highway|city street|parking lot|country road)\b\??",
        r"\bwhat\s+kind\s+of\s+road\b",
    ]),
    ("how_it_happened", [
        r"\bwalk\s+me\s+through\b",
        r"\b(what|how)\s+happened\b",
        r"\b(in|describe).*\bown\s+words\b",
        r"\bcan\s+you\s+(tell|describe)\b",
    ]),
    ("vehicle_drivable", [
        r"\bdriv(able|e\s+(it|the\s+car))\b\??",
        r"\bcan\s+you\s+(still\s+)?drive\b",
        r"\b(need|want)\s+a\s+tow\b",
        r"\btow(ed|ing)\b\??",
        r"\bwhere\s+is\s+the\s+(car|vehicle)\s+now\b",
    ]),
    ("other_party_involved", [
        r"\b(another|other)\s+(vehicle|car|driver|party)\b\??",
        r"\b(any(one|body))\s+else\s+involved\b",
        r"\bthird\s+party\b",
    ]),
    ("other_party_plate", [
        r"\b(other|their)\s+(party|driver|car).{0,12}(plate|license)",
        r"\b(do|did)\s+you\s+(have|get|catch).{0,12}plate\b",
        r"\bplate\s+number\b",
    ]),
    ("other_party_insurer", [
        r"\b(their|other party'?s?|other driver'?s?)\s+(insurer|insurance)\b",
        r"\bwho\s+(do they|are they)\s+insured\s+with\b",
        r"\bwhich\s+insurer\b",
    ]),
    ("police_involved", [
        r"\b(were\s+|did\s+|was\s+)?(the\s+)?police\s+(called|involved|on\s+scene|come)\b",
        r"\bdid\s+you\s+call\s+(the\s+)?police\b",
        r"\bpolice\s+show(ed|ing)?\s+up\b",
    ]),
    ("police_case_number", [
        r"\b(police\s+)?(case|reference|file|incident)\s+number\b",
        r"\b(do\s+you\s+have|got)\s+a?\s*(case|reference)\b",
    ]),
    ("witnesses", [
        r"\bany\s+witness(es)?\b",
        r"\b(was\s+there|were\s+there|did\s+anyone)\s+(see|witness)",
    ]),
    ("driver_identity", [
        r"\bwho\s+(was|were)\s+(the\s+)?driv(ing|er)\b",
        r"\bwere\s+you\s+(the\s+one\s+)?driv(ing|er)\b",
        r"\bbehind\s+the\s+wheel\b",
    ]),
    ("fault_admission", [
        r"\b(any\s+)?(one|body)\s+admit(ted)?\s+fault\b",
        r"\bwhose\s+fault\b",
        r"\bsay(ing)?\s+anything\s+about\s+fault\b",
    ]),
    ("settlement_preference", [
        r"\b(preferred|favourite)\s+(repair\s+shop|garage|workshop)\b",
        r"\b(rental\s+car|loss[\s\-]of[\s\-]use)\b\??",
        r"\bwhich\s+(garage|workshop|shop)\b",
    ]),
]

# Compile once.
_COMPILED: list[tuple[str, list[re.Pattern[str]]]] = [
    (pid, [re.compile(p, re.IGNORECASE) for p in pats])
    for pid, pats in _PILLAR_PATTERNS
]


def classify_jamie_question(text: str) -> set[str]:
    """Return the set of pillar IDs Jamie's reply asks about.

    Empty set if no pattern matched.  Multiple pillars allowed (Jamie
    sometimes asks two things at once — we want to flag all of them).
    """
    if not text:
        return set()
    found: set[str] = set()
    for pid, patterns in _COMPILED:
        if any(p.search(text) for p in patterns):
            found.add(pid)
    return found


# --- self-test -------------------------------------------------------------
if __name__ == "__main__":
    cases = [
        ("First things first: are you okay? Anyone hurt?",                 {"injuries"}),
        ("Is the car still drivable, or are you stuck waiting for a tow?", {"vehicle_drivable"}),
        ("Where exactly were you when it happened?",                        {"accident_location"}),
        ("Can you walk me through what happened, in your own words?",       {"how_it_happened"}),
        ("Were the police called?  Do you have a case number?",
            {"police_involved", "police_case_number"}),
        ("Were you the one driving?",                                       {"driver_identity"}),
        ("Just so I know what we need to arrange — is the car drivable, or are you needing a tow?",
            {"vehicle_drivable"}),
    ]
    fail = 0
    for text, expected in cases:
        got = classify_jamie_question(text)
        ok = got == expected
        mark = "ok " if ok else "FAIL"
        if not ok:
            fail += 1
        print(f"  {mark}  {text[:50]:50}  → {got}")
    print()
    print(f"  {len(cases) - fail}/{len(cases)} cases pass")
