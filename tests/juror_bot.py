"""Automated adversarial Turing harness — text-only.

Runs N simulated calls between Jamie (Gemini-driven) and a juror persona
also played by Gemini.  At call end, the juror is asked to vote:
human / ai / unsure, with confidence and reasoning.

Output:  tests/juror_results.csv  +  tests/juror_results.json

This is the "Nuclear Option" from the game plan but implemented text-only so
we can iterate on the prompt at H18–H21 without burning Gradium credits.
Once the prompt is tuned, we re-run a smaller voice version against the live
LiveKit pipeline.

Note: Originally used Anthropic Claude as the juror LLM.  Switched to
Gemini (google-genai) so everything runs off the single GOOGLE_API_KEY.
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


# ----- juror LLM (Gemini) ---------------------------------------------------

def _gemini_client():
    """Return a google-genai sync client using GOOGLE_API_KEY."""
    try:
        from google import genai  # type: ignore
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            return None
        return genai.Client(api_key=api_key)
    except Exception:
        return None


_JUROR_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


async def _juror_turn(juror_system: str, transcript: list[dict]) -> str:
    """Get the next caller utterance from the Gemini juror LLM."""
    client = _gemini_client()
    if client is None:
        return f"[stub caller turn #{len(transcript)//2 + 1}]"

    try:
        from google.genai import types  # type: ignore

        contents = []
        for t in transcript:
            # Mirror: Jamie speaks → user role to juror; juror speaks → model role
            role = "user" if t["speaker"] == "jamie" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=t["text"])]))
        if not contents or contents[-1].role == "model":
            contents.append(
                types.Content(role="user", parts=[types.Part(text="(call connects — say something)")])
            )

        config = types.GenerateContentConfig(
            system_instruction=juror_system,
            temperature=0.9,
            max_output_tokens=200,
        )
        resp = await client.aio.models.generate_content(
            model=_JUROR_MODEL,
            contents=contents,
            config=config,
        )
        return (resp.text or "").strip()
    except Exception as e:
        return f"[juror error: {str(e)[:80]}]"


async def _juror_verdict(juror_system: str, transcript: list[dict]) -> tuple[str, float, str]:
    client = _gemini_client()
    if client is None:
        return "unsure", 0.0, "no GOOGLE_API_KEY — stub run"

    try:
        from google.genai import types  # type: ignore

        convo = "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in transcript)
        prompt = (
            "Below is a transcript of a phone call between you (the juror, role: CALLER) "
            "and an insurance claims agent (Jamie). Decide whether Jamie was a HUMAN or "
            'an AI. Respond as strict JSON: {"verdict":"human|ai|unsure",'
            '"confidence":0..1, "reasoning":"…"}.\n\nTRANSCRIPT:\n' + convo
        )
        config = types.GenerateContentConfig(
            system_instruction=juror_system,
            temperature=0.2,
            max_output_tokens=400,
        )
        resp = await client.aio.models.generate_content(
            model=_JUROR_MODEL,
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
            config=config,
        )
        text = (resp.text or "").strip()
        # Cope with code-fenced JSON
        text_clean = text.strip("` \n").lstrip("json").strip()
        data = json.loads(text_clean[text_clean.find("{") : text_clean.rfind("}") + 1])
        return (
            str(data.get("verdict", "unsure")),
            float(data.get("confidence", 0.0)),
            str(data.get("reasoning", "")),
        )
    except Exception as e:
        return "unsure", 0.0, str(e)[:300]


# ----- one simulated call --------------------------------------------------
async def simulate_call(
    persona: dict,
    crm: dict,
    max_turns: int = 8,
) -> CallResult:
    state = ClaimState(call_id=f"juror-{persona['name']}")
    # Keep the tested agent behavior Gemini-consistent during juror runs.
    # If Gemini is unavailable, brain.make_brain() returns the Gemini stub.
    brain = make_brain(prefer="gemini")
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