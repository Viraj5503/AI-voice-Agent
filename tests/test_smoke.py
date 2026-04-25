"""Smoke tests — fast, no network, no API keys.

These prove the modules import + the core data path works.
Run with:  pytest -q
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_crm_profiles_load():
    for name in ["max_mueller", "helga_schmidt", "thomas_weber_fleet"]:
        p = REPO / "data" / "crm" / f"{name}.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["policyholder"]["name"]
        assert data["policy"]["policy_number"]


def test_pii_redact():
    from agent.pii_redact import redact
    sample = "policy DE-HUK-2024-884421 plate B-MM 4421 vin WVWZZZ1JZ3W386752"
    out = redact(sample)
    assert "884421" not in out
    assert "[POLICY]" in out
    assert "[PLATE]" in out
    assert "[VIN]" in out


def test_claim_state_priorities():
    from agent.claim_state import ClaimState, PILLARS
    s = ClaimState(call_id="t")
    assert len(s.unfilled_pillars()) == len(PILLARS)
    s.fill("injuries", "no injuries")
    assert "injuries" not in dict(s.unfilled_pillars())
    s.flag_fraud("delayed_reporting", "three weeks", "high")
    assert s.fraud_risk_score() >= 4


def test_extractor_stub_path():
    from extraction.gliner2_service import ExtractionService
    svc = ExtractionService()
    out = svc.extract(
        "I was on the A4 today, plate K-AB 1234, the police came, "
        "I have whiplash, three weeks ago I noticed the dent."
    )
    # In stub mode we should still pull at least a couple of these:
    keys = set(out["pillars"]) | set(out["fraud"])
    assert {"other_party_plate", "road_type"} <= keys or svc.mode == "gliner"


def test_prompt_builder_includes_crm():
    from agent.claim_state import ClaimState
    from agent.prompts import build_jamie_system_prompt
    crm = json.loads((REPO / "data" / "crm" / "max_mueller.json").read_text())
    p = build_jamie_system_prompt(crm, ClaimState(call_id="t"))
    # The prompt must inline the CRM
    assert "Max Müller" in p
    assert "Volkswagen" in p
    # ...and must include the anti-hallucination + anti-repetition rules
    assert "QUOTE THE CRM" in p
    assert "READ THE CONVERSATION HISTORY" in p
    assert "NEVER ASK FOR DATA SHOWN" in p


def test_prompt_rules_section_is_tight():
    """The repetition-causing bloat was in the *rules* section, not the
    CRM JSON dump (which is just reference data the LLM consults).  This
    test guards against rule-creep — if anyone adds long inline examples
    or scripted phrasings, the rules section will grow and Jamie will
    start parroting them.  CRM data is allowed to be as big as the
    actual JSON requires."""
    from agent.prompts import _PERSONA_AND_RULES
    assert len(_PERSONA_AND_RULES) < 2200, (
        f"rules section is {len(_PERSONA_AND_RULES)} chars — keep it under 2200 "
        "or add a justification.  Long rules = diluted instructions = repetition."
    )


def test_intent_classifier_basic():
    """The asked-pillar tracker is the real fix for repetition.  Smoke
    a few representative Jamie utterances."""
    from agent.intent import classify_jamie_question
    assert classify_jamie_question("Are you okay? Anyone hurt?") == {"injuries"}
    assert classify_jamie_question("Is the car drivable?") == {"vehicle_drivable"}
    assert classify_jamie_question("Were the police called? Got a case number?") == {
        "police_involved", "police_case_number"
    }
    assert classify_jamie_question("Just so I have it noted.") == set()


def test_asked_pillars_excluded_from_ask_next():
    """If a pillar is in asked_pillars, it must NOT appear under
    'ASK NEXT' in the prompt summary — that's what stops cycling."""
    from agent.claim_state import ClaimState
    s = ClaimState(call_id="t")
    s.mark_asked({"vehicle_drivable", "injuries"})
    summary = s.unfilled_summary_compact()
    # Both pillars should appear under "ASKED BUT NO ANSWER YET", not "ASK NEXT"
    new_section = summary.split("ASKED BUT NO ANSWER YET")[0]
    assert "vehicle_drivable" not in new_section
    assert "injuries" not in new_section
    assert "ASKED BUT NO ANSWER YET" in summary


def test_unfilled_summary_has_no_scripted_questions():
    """Pre-written sample questions in the system prompt cause the LLM to
    reuse them verbatim every turn.  We removed the hint phrasings; this
    test guards against accidentally re-introducing them."""
    from agent.claim_state import ClaimState
    s = ClaimState(call_id="t")
    summary = s.unfilled_summary_compact()
    # Forbidden literal phrasings from the old hints:
    forbidden = ["Are you or anyone else hurt?", "When exactly did this happen?",
                 "Do you have the other party's license plate?",
                 "Were the police called?"]
    for f in forbidden:
        assert f not in summary, f"scripted question leaked: {f!r}"


def test_tavily_stub_runs():
    from tools.tavily_lookup import lookup_weather
    r = lookup_weather("A4 Köln")
    assert "summary" in r
