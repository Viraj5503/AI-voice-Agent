"""Automated end-to-end demo — no human input needed.

Plays a scripted caller through Jamie's full pipeline:
  - Gemini brain (real, with backoff)
  - GLiNER2 extraction (or regex stub)
  - Tavily weather lookups when location keywords appear
  - Bridge events to the dashboard
  - Saves a timestamped transcript + final claim JSON

Usage:
    # bridge + dashboard running, then:
    python scripts/run_demo_auto.py --scenario max_rear_end_a4
    python scripts/run_demo_auto.py --scenario helga_delayed_parking
    python scripts/run_demo_auto.py --list

Each scenario is a JSON file under data/scenarios/.  See
data/scenarios/max_rear_end_a4.json for the schema.

This is what we use to:
  1. Iterate on Jamie's prompt without retyping each turn
  2. Show judges a deterministic, rehearsed call (--pace slow gives a
     natural cadence; --pace fast is for prompt iteration)
  3. Feed transcripts into scripts/eval_jamie.py for scoring
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env")
except Exception:
    pass

from agent.claim_state import ClaimState
from agent.prompts import build_jamie_system_prompt, opening_line
from agent.gemini_client import GeminiBrain
from agent.intent import classify_jamie_question
from agent.pii_redact import redact
from extraction.gliner2_service import ExtractionService
from bridge.client import publish as bridge_publish
from tools.tavily_lookup import DISPATCH as TAVILY_DISPATCH


SCENARIO_DIR = REPO / "data" / "scenarios"
TRANSCRIPT_DIR = REPO / "transcripts"


# Same heuristics as the interactive demo — until we wire Gemini
# function-calling, these triggers fire Tavily for us.
def _maybe_tavily(text: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    lower = text.lower()
    if any(k in lower for k in (
        "a1","a2","a3","a4","a5","a6","a7","a8","a9","a10","autobahn",
        "köln","koln","cologne","berlin","münchen","munich","stuttgart",
        "hauptstraße", "lindenweg", "industriestraße",
    )):
        out.append(("tavily_lookup_weather", {"location": text[:80]}))
    if any(k in lower for k in ("not drivable","totaled","kann nicht fahren","tow")):
        out.append(("tavily_lookup_towing", {"location": text[:80]}))
    return out


def _detect_mode(text: str, current: str) -> str:
    lower = text.lower()
    if any(w in lower for w in (
        "panik","weine","crying","shaking","scared","i can't",
        "i don't know what to do","oh god","oh my god",
    )):
        return "distressed"
    if any(w in lower for w in (
        "highway","traffic","loud","static","[noise]","background",
    )):
        return "noisy"
    return current


# ---- pretty printer ------------------------------------------------------

class Term:
    BOLD   = "\033[1m" if sys.stdout.isatty() else ""
    DIM    = "\033[2m" if sys.stdout.isatty() else ""
    BLUE   = "\033[94m" if sys.stdout.isatty() else ""
    GREEN  = "\033[92m" if sys.stdout.isatty() else ""
    YELLOW = "\033[93m" if sys.stdout.isatty() else ""
    RED    = "\033[91m" if sys.stdout.isatty() else ""
    END    = "\033[0m" if sys.stdout.isatty() else ""


def banner(t: str) -> None:
    print(f"\n{Term.BOLD}{t}{Term.END}\n{'-' * min(len(t), 72)}")


# ---- runner --------------------------------------------------------------

async def run_scenario(scenario_path: Path, pace: str, no_bridge: bool) -> Path:
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    crm_name = scenario["crm_profile"]
    crm = json.loads((REPO / "data" / "crm" / f"{crm_name}.json").read_text())

    state = ClaimState(call_id=f"auto-{scenario['name']}-{int(time.time())}")
    brain = GeminiBrain()
    extractor = ExtractionService()

    banner(f"AUTO DEMO  •  scenario: {scenario['name']}  •  CRM: {crm_name}")
    print(f"  Gemini:    {'live ' + brain.model_name if brain._real else 'stub fallback'}")
    print(f"  Extractor: {extractor.mode}  ({extractor.model_name or 'regex stub'})")
    print(f"  Bridge:    {'OFF' if no_bridge else 'http://localhost:8765'}")
    print(f"  Pace:      {pace}")

    pace_delay = {"fast": 0.0, "normal": 1.5, "slow": 3.0}.get(pace, 1.5)

    # Transcript record
    transcript: list[dict] = []
    history: list[dict[str, str]] = []
    # Rolling buffer of tool results — passed into Jamie's prompt so she
    # can quote real Tavily output instead of inventing weather reports.
    tool_results: list[dict] = []

    async def emit(ev: dict) -> None:
        if not no_bridge:
            await bridge_publish(ev)

    async def speak(speaker: str, text: str) -> None:
        transcript.append({"speaker": speaker, "text": text,
                           "ts": _utc_iso()})
        color = Term.BLUE if speaker == "jamie" else Term.YELLOW
        print(f"\n  {color}{Term.BOLD}{speaker.upper():6}{Term.END} {color}{text}{Term.END}")
        await emit({"type": "transcript", "speaker": speaker,
                    "text": redact(text)})

    await emit({"type": "call_start", "crm": crm})

    # Jamie opens
    opener = opening_line(crm)
    await speak("jamie", opener)
    history.append({"role": "model", "text": opener})

    # Each scripted caller turn
    for turn_idx, caller_text in enumerate(scenario["caller_turns"], start=1):
        if pace_delay:
            await asyncio.sleep(pace_delay)
        await speak("caller", caller_text)
        history.append({"role": "user", "text": caller_text})

        # Mode detection
        new_mode = _detect_mode(caller_text, state.emotional_mode)
        if new_mode != state.emotional_mode:
            state.set_mode(new_mode)
            await emit({"type": "emotional_state", "state": new_mode})
            print(f"  {Term.DIM}[mode → {new_mode}]{Term.END}")

        # Tavily triggers — also stash result for Jamie's prompt so she
        # can quote it instead of inventing one.
        for name, kw in _maybe_tavily(caller_text):
            await emit({"type": "tool_call", "name": name, "args": kw})
            print(f"  {Term.DIM}[tool_call {name}]{Term.END}")
            try:
                fn = TAVILY_DISPATCH[name]
                result = await asyncio.to_thread(fn, **kw)
                await emit({"type": "tool_result", "name": name, "result": result})
                tool_results.append({"name": name, "result": result})
            except Exception as e:
                print(f"  {Term.RED}[{name} failed: {e}]{Term.END}")

        # GLiNER extraction
        extr = extractor.extract(caller_text)
        new_pillars = []
        for label, info in extr["pillars"].items():
            if label not in state.pillars:
                new_pillars.append(label)
            state.fill(label, info["text"], confidence=info["score"])
            await emit({"type": "entity", "label": label,
                        "value": info["text"], "confidence": info["score"]})
        for label, info in extr["fraud"].items():
            state.flag_fraud(label, info["text"], severity="medium")
            await emit({"type": "fraud_signal", "signal": label,
                        "severity": "medium", "evidence": info["text"]})
        if new_pillars:
            print(f"  {Term.GREEN}[+{len(new_pillars)} pillars: {', '.join(new_pillars)}]{Term.END}")

        # Jamie replies — pass her own previous line so the prompt's
        # "WHAT YOU JUST SAID" anchor is populated, and pass the recent
        # tool_results so she can quote them instead of inventing.
        last_jamie = next(
            (h["text"] for h in reversed(history) if h["role"] == "model"),
            None,
        )
        sys_prompt = build_jamie_system_prompt(
            crm, state,
            last_jamie_reply=last_jamie,
            tool_results=tool_results,
        )
        chunks: list[str] = []
        try:
            async for piece in brain.stream_reply(sys_prompt, history[:-1], caller_text):
                chunks.append(piece)
        except Exception as e:
            # Print the FULL error message — type alone isn't actionable.
            full = f"{type(e).__name__}: {e}"
            print(f"  {Term.RED}[mid-turn brain error] {full[:300]}{Term.END}")
            if not chunks:
                chunks = [
                    "Sorry — my system just hiccuped, can you give me one moment?"
                ]
        reply = ("".join(chunks)).strip() or "(silence — model returned nothing)"
        await speak("jamie", reply)
        history.append({"role": "model", "text": reply})

        # Track which pillars Jamie just asked about so the next prompt
        # excludes them from "ASK NEXT" — the real repetition fix.
        asked_now = classify_jamie_question(reply)
        if asked_now:
            state.mark_asked(asked_now)
            print(f"  {Term.DIM}[asked: {', '.join(sorted(asked_now))}]{Term.END}")

    # Wrap up
    await emit({"type": "call_end", "claim_json": state.to_dict()})

    banner("FINAL CLAIM STATE")
    filled = len(state.pillars)
    print(f"  pillars filled:    {filled} / 15")
    for k, v in state.pillars.items():
        print(f"    • {k:24} {v['value']}")
    print(f"  fraud risk score:  {state.fraud_risk_score()} / 10")
    print(f"  emotional mode:    {state.emotional_mode}")

    # Persist transcript + claim JSON to a timestamped file
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TRANSCRIPT_DIR / f"{scenario['name']}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
    out_path.write_text(json.dumps({
        "scenario": scenario,
        "crm_profile": crm_name,
        "transcript": transcript,
        "claim_state": state.to_dict(),
        "model": brain.model_name,
    }, indent=2, ensure_ascii=False))
    print(f"\n  saved → {out_path.relative_to(REPO)}")
    return out_path


# ---- cli -----------------------------------------------------------------

def list_scenarios() -> None:
    if not SCENARIO_DIR.exists():
        print("(no scenarios yet)"); return
    print("Available scenarios:")
    for p in sorted(SCENARIO_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            print(f"  {d['name']:30} {d.get('description', '')[:80]}")
        except Exception:
            print(f"  {p.stem:30} (unreadable)")


async def amain() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default=None,
                        help="scenario name (without .json) under data/scenarios/")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument("--pace", default="normal", choices=["fast", "normal", "slow"],
                        help="seconds between caller turns: fast=0, normal=1.5, slow=3.0")
    parser.add_argument("--no-bridge", action="store_true",
                        help="don't push events to the WebSocket bridge")
    args = parser.parse_args()

    if args.list or not args.scenario:
        list_scenarios()
        if not args.scenario:
            print("\nuse --scenario <name> to run one"); return
        return

    path = SCENARIO_DIR / f"{args.scenario}.json"
    if not path.exists():
        print(f"  no such scenario: {path}")
        list_scenarios()
        sys.exit(1)

    await run_scenario(path, args.pace, args.no_bridge)


if __name__ == "__main__":
    asyncio.run(amain())
