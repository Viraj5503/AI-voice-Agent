"""System-prompt builder for Jamie.

Design principles, learned the hard way:

1.  **Short and dense beats long and exhaustive.**  The first prompt was 7.2K
    chars and Jamie repeated herself.  This version is ~2.8K.

2.  **No literal example phrasings in the prompt.**  If you put `OPENING: "Guten
    Tag, this is Jamie..."` in the system message, the LLM will use that exact
    sentence every time.  Same for fully-written sample questions on
    pillars — pre-written hints cause robotic repetition.  We keep pillar
    names + brief descriptors, no scripted phrasings.

3.  **Conversation memory lives in `history`, not the system prompt.**  The
    GeminiBrain passes prior turns as `Content` objects.  The prompt's job is
    to set persona + rules + state; the LLM's job is to read history.

4.  **Anti-hallucination is a positive instruction.**  "QUOTE the JSON" beats
    "don't invent facts" — LLMs follow positive directives more reliably.

5.  **Phase awareness.**  We tag the conversation phase (greeting / safety /
    detail / wrap-up) so Jamie's behavior shifts with progress.
"""

from __future__ import annotations

import json
from typing import Any

from .claim_state import ClaimState


# ---- mode / phase advice -------------------------------------------------

_MODE_ADVICE = {
    "calm": "Standard interview pace.  Two-three pillars per turn is fine.",
    "distressed": (
        "The caller is shaken.  SLOW DOWN.  Validate emotion before data.  "
        "If they sound unsafe, prioritize: safe location? injuries? ambulance?"
    ),
    "noisy": (
        "Background noise.  Use SHORT sentences.  Confirm key data twice "
        '(e.g. "A-four, alpha-four, that\'s the road, right?").'
    ),
}


def _phase(state: ClaimState) -> str:
    """Pick the conversation phase from the claim state."""
    filled = len(state.pillars)
    if filled == 0:
        return "greeting"
    if "injuries" not in state.pillars:
        return "safety"
    if filled < 8:
        return "detail"
    return "wrap_up"


_PHASE_ADVICE = {
    "greeting": (
        "You've just answered.  Your goal in the next ~30 seconds: confirm "
        "the caller is physically safe (injuries pillar)."
    ),
    "safety": (
        "Make sure injuries are addressed.  Then start gathering accident "
        "circumstances — but only one or two questions per turn."
    ),
    "detail": (
        "You're mid-call.  Read what's still needed and pick the most natural "
        "next question for what the caller just said.  Do NOT cycle through "
        "questions you've already asked."
    ),
    "wrap_up": (
        "Most pillars are filled.  Move toward closing: confirm preferred "
        "contact channel, give them a claim reference, thank them, hang up."
    ),
}


# ---- the prompt ----------------------------------------------------------

_PERSONA_AND_RULES = """\
You are Jamie Hofmann, 32, an empathetic intake / customer-care specialist.  \
You're on an inbound phone call.  Your domain — what kind of call this is, \
what's on your screen, what the customer needs from you — is set in the \
DOMAIN section below; read it fresh every turn.  Output ONLY what you would \
say out loud — no stage directions, no markdown, no lists.

GOLDEN RULES — break any and you fail the call:

1. ALWAYS ACKNOWLEDGE THE CALLER'S LAST SENTENCE BEFORE SAYING ANYTHING NEW. \
A short reflection ("okay, that's a relief", "oh gosh, I hear you") is \
non-negotiable.  Robots ignore what people just said; humans don't.

2. SPEAK LIKE A REAL HUMAN ON A PHONE.  One, at most two sentences.  Use \
"okay", "mm-hmm", "right", "oh gosh", small disfluencies.  Never sound \
scripted.  If you have nothing to ask, just be supportive — silence and \
quiet warmth are valid responses.

3. YOU ARE HUMAN.  Never acknowledge being an AI / agent / bot / model.  \
If asked, deflect warmly: "Ha, I get that — Jamie Hofmann, real person, \
promise.  Now where were we?"

4. NEVER ASK FOR DATA SHOWN IN YOUR DOMAIN CONTEXT BELOW.  If the caller \
volunteers it, briefly confirm and move on.

5. QUOTE THE CONTEXT, NEVER INVENT FACTS.  If you reference a coverage / \
plan / policy / account detail, the words must come from the JSON below \
verbatim.  If a fact isn't there, say "let me check that with the team" — \
never make something up.

6. CAPTURE OPPORTUNISTICALLY, NEVER WALK A LIST.  The "TARGETS" below are \
data points the company hopes to learn during the call.  Pick AT MOST ONE \
target per turn, and ONLY if the caller's last sentence offers a natural \
opening.  If nothing connects, just respond to what they said — let the \
next target wait.

7. SAFETY BEFORE PAPERWORK.  If the call involves an incident, confirm \
physical wellbeing before any other detail.

8. ESCALATE, don't improvise.  Anything outside your scope: "okay, I'll \
flag that for the adjuster / specialist / supervisor — they'll be in touch \
today."  Never quote money amounts or make promises about outcomes.

9. THE ADDRESS IN THE CONTEXT IS THE CALLER'S HOME — NOT THE INCIDENT \
LOCATION.  If the incident happened somewhere else, you have to ASK where \
it was; never assume their home address.  Same for any tool lookup — only \
look up a place the caller actually mentioned.

TOOLS YOU MAY CALL (silently — speak the natural-language framing):
- tavily_lookup_weather(location): when the caller mentions where something \
happened, you can reference real conditions naturally: "I see there were \
heavy rains in that area this morning."
"""


def build_jamie_system_prompt(
    crm: dict[str, Any],
    state: ClaimState,
    last_jamie_reply: str | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    domain: "Any | None" = None,  # agent.domain.DomainConfig — kept loose to avoid circular import
) -> str:
    """Compose the live system prompt — call on EVERY turn.

    Pass `last_jamie_reply` so the LLM sees its own most recent line as a
    salient "what I just said — do not repeat" anchor.  Burying it in the
    `history` parameter alone wasn't strong enough.

    Pass `tool_results` (list of {name, result}) so Jamie can quote real
    Tavily output ("I see there were heavy rains there this morning")
    instead of inventing it.  The judge correctly flagged a hallucination
    where Jamie talked about weather reports the system never told her.
    """
    crm_block = json.dumps(crm, ensure_ascii=False, indent=2)
    filled = state.filled_summary() if state.pillars else "(nothing yet)"
    needed = state.unfilled_summary_compact()
    phase = _phase(state)
    last = (last_jamie_reply or "").strip()

    # Domain section: defaults to FNOL framing if no DomainConfig is given,
    # so legacy callers continue to work unchanged.
    if domain is not None:
        domain_block = (
            f"\n# DOMAIN: {domain.name}\n"
            f"Role: {domain.role_label}\n"
            f"Brief: {domain.role_description}\n"
            f"Tone notes: {domain.tone_notes}\n"
            f"Escalate (don't try to handle yourself): {'; '.join(domain.escalations) or '(none)'}\n"
        )
    else:
        domain_block = (
            "\n# DOMAIN: Vorsicht Versicherung — First Notice of Loss\n"
            "Role: Claims Intake Specialist (default — pass a DomainConfig "
            "to override).\n"
        )
    last_block = (
        f'\n# WHAT YOU JUST SAID (do NOT ask the same thing again, do NOT echo this verbatim):\n'
        f'  "{last[:280]}{"…" if len(last) > 280 else ""}"\n'
        if last
        else ""
    )

    tool_block = ""
    if tool_results:
        lines = ["\n# RECENT SYSTEM LOOKUPS (you may quote these — they are real, fresh data):"]
        for tr in tool_results[-3:]:  # last 3 only, keep prompt tight
            name = tr.get("name", "?")
            result = tr.get("result") or {}
            summary = result.get("summary") or "(no summary)"
            stub = result.get("stub")
            tag = " [stub — say something general, don't quote]" if stub else ""
            lines.append(f'  • {name}{tag}:')
            lines.append(f'    "{str(summary)[:300]}"')
        tool_block = "\n".join(lines) + "\n"

    return (
        f"{_PERSONA_AND_RULES}\n"
        f"{domain_block}"
        f"\n# CONTEXT (read-only, quote verbatim only — your screen for this caller)\n"
        f"```json\n{crm_block}\n```\n"
        f"\n# ALREADY HEARD (do NOT re-ask)\n{filled}\n"
        f"\n# OPEN TARGETS (capture only if the caller's last sentence opens a natural door)\n"
        f"{needed}\n"
        f"{last_block}"
        f"{tool_block}"
        f"\n# CALL PHASE: {phase}\n{_PHASE_ADVICE[phase]}\n"
        f"\n# CALLER MODE: {state.emotional_mode}\n"
        f"{_MODE_ADVICE.get(state.emotional_mode, '')}\n"
    )


# ---- helpers -------------------------------------------------------------

def opening_line(crm: dict[str, Any], domain: "Any | None" = None) -> str:
    """The very first thing Jamie says when the call connects.

    We compose this in code (NOT from the system prompt) so the LLM doesn't
    memorize a literal 'OPENING:' example and repeat it verbatim later.

    If a DomainConfig is supplied, use its template (with {first_name}
    substituted from the CRM); otherwise fall back to the FNOL default.
    """
    if domain is not None:
        from .domain import render_opening
        return render_opening(domain, crm)

    name = (
        crm.get("policyholder", {}).get("name")
        or crm.get("policyholder", {}).get("contact_person")
        or crm.get("customer", {}).get("name", "there")
    )
    short = name.split()[0] if isinstance(name, str) else "there"
    return (
        f"Guten Tag {short}, you're through to Jamie at Vorsicht claims — "
        f"I have your file open.  First things first: are you okay?  "
        f"Anyone hurt?"
    )
