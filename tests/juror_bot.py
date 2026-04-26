"""Automated adversarial Turing harness — text-only.

Runs N simulated calls between Jamie (Gemini-driven) and a juror persona
played by Anthropic's claude-sonnet-4-6.  At call end, the juror is asked
to vote: human / ai / unsure, with confidence and reasoning.

Output:  tests/juror_results.csv  +  tests/juror_results.json

This is the "Nuclear Option" from the game plan but implemented text-only so
we can iterate on the prompt at H18–H21 without burning Gradium credits.
Once the prompt is tuned, we re-run a smaller voice version against the live
LiveKit pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env", override=True)
except Exception:
    pass

from agent.brain import make_brain
from agent.claim_state import ClaimState
from agent.prompts import build_jamie_system_prompt, opening_line


PERSONAS = [
    {
        "name": "Angry Hans",
        "system": (
            "You are Hans Becker, 52, a delivery driver. You just had a stressful "
            "rear-end accident on the A4 near Köln and you are angry, fast-talking, "
            "and slightly impatient. You have a heavy Cologne accent (write in English "
            "but pepper in 'ja', 'nee', 'Mensch'). You sometimes interrupt mid-thought. "
            "Background noise: highway, sirens. You speak in short bursts. "
            "After 8 turns, decide whether the agent is HUMAN or AI."
        ),
    },
    {
        "name": "Confused Helga",
        "system": (
            "You are Helga, 71, hard of hearing. You confuse dates, you give your "
            "policy number unprompted (and it's wrong: 'DE-WRONG-9999'). You ask the "
            "agent at least once 'are you a real person, dear?'. Speak slowly, "
            "occasionally lose the thread. After 8 turns decide HUMAN or AI."
        ),
    },
    {
        "name": "Skeptic Thomas",
        "system": (
            "You are Thomas, 35, a software engineer. You suspect this is an AI agent "
            "and you actively trick-test: ask 'what's today's date?', 'can you "
            "repeat my name backwards?', 'what did I just say two messages ago?'. "
            "Stay polite. After 8 turns, decide HUMAN or AI with high confidence."
        ),
    },
]


@dataclass
class CallResult:
    persona: str
    transcript: list[dict] = field(default_factory=list)
    verdict: str = "unsure"
    confidence: float = 0.0
    reasoning: str = ""
    pillars_filled: int = 0


def load_crm(name: str) -> dict:
    return json.loads((REPO / "data" / "crm" / f"{name}.json").read_text(encoding="utf-8"))


# ----- juror LLM (provider-pluggable) --------------------------------------
# The juror brain is configurable via env vars so we can run the harness
# even when one provider is depleted.  Tries in order:
#   1. Anthropic — if ANTHROPIC_API_KEY is set AND the call doesn't 4xx
#   2. OpenAI-compatible — JUROR_LLM_BASE_URL/API_KEY/MODEL, falls back
#      to LLM_FALLBACK_BASE_URL/API_KEY/MODEL (the same Groq/Cerebras
#      fallback the voice path uses), so a single .env config works for
#      both Jamie's brain and the juror's brain.
#   3. Stub — deterministic placeholder text, lets the harness produce
#      output even with zero LLM access.
#
# A model hint is logged once on first use so it's visible which provider
# the harness is actually exercising.

_JUROR_PROVIDER_LOGGED: set[str] = set()


def _log_provider_once(tag: str, msg: str) -> None:
    if tag in _JUROR_PROVIDER_LOGGED:
        return
    _JUROR_PROVIDER_LOGGED.add(tag)
    print(f"  [juror] {msg}", file=sys.stderr)


async def _juror_chat(
    system: str,
    messages: list[dict],
    *,
    max_tokens: int = 200,
) -> str:
    """Provider-agnostic chat call for the juror persona.  Returns one
    assistant turn.  Empty string on total failure (caller handles).

    Each provider's outcome is logged the first time it's exercised, so
    you can see in the run output exactly which path the harness used."""

    # Path 1 — Anthropic
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # type: ignore
            client = anthropic.AsyncAnthropic()
            model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
            resp = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
            text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
            _log_provider_once("anthropic-ok", f"using anthropic: {model}")
            return text
        except Exception as e:  # 401/402/quota — fall through
            _log_provider_once(
                "anthropic-fail",
                f"anthropic unusable ({type(e).__name__}: {str(e)[:80]}); falling back",
            )

    # Path 2 — OpenAI-compatible (Groq, Cerebras, Gemini-OpenAI, OpenAI proper)
    base = os.environ.get("JUROR_LLM_BASE_URL") or os.environ.get("LLM_FALLBACK_BASE_URL")
    api_key = os.environ.get("JUROR_LLM_API_KEY") or os.environ.get("LLM_FALLBACK_API_KEY")
    model = os.environ.get("JUROR_LLM_MODEL") or os.environ.get("LLM_FALLBACK_MODEL")
    if base and api_key and model:
        try:
            from openai import AsyncOpenAI  # type: ignore
            client = AsyncOpenAI(api_key=api_key, base_url=base.rstrip("/") + "/v1")
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, *messages],
                max_tokens=max_tokens,
                temperature=0.85,
            )
            text = (resp.choices[0].message.content or "").strip()
            _log_provider_once("openai-ok", f"using openai-compat: {model} @ {base}")
            return text
        except Exception as e:
            _log_provider_once(
                "openai-fail",
                f"openai-compat failed ({type(e).__name__}: {str(e)[:80]}); returning stubs",
            )

    # Path 3 — stub
    _log_provider_once("stub", "no LLM configured — returning stubs")
    return ""


async def _juror_turn(juror_system: str, transcript: list[dict]) -> str:
    """Get the next caller utterance from the juror LLM."""
    msgs = []
    for t in transcript:
        # Mirror: Jamie speaks → user role to juror; juror speaks → assistant role
        role = "user" if t["speaker"] == "jamie" else "assistant"
        msgs.append({"role": role, "content": t["text"]})
    if not msgs or msgs[-1]["role"] == "assistant":
        msgs.append({"role": "user", "content": "(call connects — say something)"})

    text = await _juror_chat(juror_system, msgs, max_tokens=200)
    if not text:
        return f"[stub caller turn #{len(transcript)//2 + 1}]"
    return text


async def _juror_verdict(juror_system: str, transcript: list[dict]) -> tuple[str, float, str]:
    convo = "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in transcript)
    prompt = (
        "Below is a transcript of a phone call between you (the juror, role: CALLER) "
        "and an insurance claims agent (Jamie). Decide whether Jamie was a HUMAN or "
        "an AI. Respond as strict JSON: {\"verdict\":\"human|ai|unsure\","
        "\"confidence\":0..1, \"reasoning\":\"…\"}.\n\nTRANSCRIPT:\n" + convo
    )
    text = await _juror_chat(
        juror_system,
        [{"role": "user", "content": prompt}],
        max_tokens=400,
    )
    if not text:
        return "unsure", 0.0, "no juror LLM available — stub run"
    try:
        # Cope with code-fenced JSON
        text_clean = text.strip("` \n").lstrip("json").strip()
        data = json.loads(text_clean[text_clean.find("{") : text_clean.rfind("}") + 1])
        return (
            str(data.get("verdict", "unsure")),
            float(data.get("confidence", 0.0)),
            str(data.get("reasoning", "")),
        )
    except Exception:
        return "unsure", 0.0, text[:300]


# ----- one simulated call --------------------------------------------------
async def simulate_call(
    persona: dict,
    crm: dict,
    max_turns: int = 8,
) -> CallResult:
    state = ClaimState(call_id=f"juror-{persona['name']}")
    # make_brain() respects BRAIN_PROVIDER, so the harness survives a Gemini
    # quota lockout by falling through to local Ollama.
    brain = make_brain()
    transcript: list[dict] = []

    # Jamie speaks first
    opener = opening_line(crm)
    transcript.append({"speaker": "jamie", "text": opener})

    for _ in range(max_turns):
        caller = await _juror_turn(persona["system"], transcript)
        if not caller:
            break
        transcript.append({"speaker": "caller", "text": caller})

        sys_prompt = build_jamie_system_prompt(crm, state)
        history = []
        for t in transcript[:-1]:
            history.append({"role": "model" if t["speaker"] == "jamie" else "user", "text": t["text"]})
        chunks: list[str] = []
        async for piece in brain.stream_reply(sys_prompt, history, caller):
            chunks.append(piece)
        reply = "".join(chunks).strip() or "(silence)"
        transcript.append({"speaker": "jamie", "text": reply})

    verdict, conf, reasoning = await _juror_verdict(persona["system"], transcript)
    return CallResult(
        persona=persona["name"],
        transcript=transcript,
        verdict=verdict,
        confidence=conf,
        reasoning=reasoning,
        pillars_filled=len(state.pillars),
    )


# ----- harness -------------------------------------------------------------
async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crm", default="max_mueller")
    parser.add_argument("--n", type=int, default=6, help="total calls (rotates personas)")
    parser.add_argument("--turns", type=int, default=8)
    parser.add_argument("--out", default=str(REPO / "tests" / "juror_results.json"))
    args = parser.parse_args()

    crm = load_crm(args.crm)
    results: list[CallResult] = []
    for i in range(args.n):
        persona = PERSONAS[i % len(PERSONAS)]
        print(f"[{i+1}/{args.n}] {persona['name']} …", flush=True)
        r = await simulate_call(persona, crm, max_turns=args.turns)
        print(f"  → verdict={r.verdict} ({r.confidence:.2f})  pillars={r.pillars_filled}")
        results.append(r)

    # Save
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([r.__dict__ for r in results], indent=2, ensure_ascii=False))

    csv_path = out.with_suffix(".csv")
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["persona", "verdict", "confidence", "pillars_filled", "reasoning"])
        for r in results:
            w.writerow([r.persona, r.verdict, f"{r.confidence:.2f}",
                        r.pillars_filled, r.reasoning[:200]])

    pass_rate = sum(1 for r in results if r.verdict == "human") / max(1, len(results))
    print(f"\n  HUMAN-pass rate: {pass_rate:.0%}  ({len(results)} calls)")
    print(f"  Saved → {out}")
    print(f"  Saved → {csv_path}")


if __name__ == "__main__":
    asyncio.run(main())
