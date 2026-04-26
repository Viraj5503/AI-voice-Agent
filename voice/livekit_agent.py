"""Production voice agent — LiveKit + Gradium STT/TTS + GeminiBrain.

Architecture (proven working for phone calls):
  - Gradium STT   — temperature=0.0 suppresses Whisper noise hallucination
  - Gradium TTS   — Emma voice with warmth tuning
  - Silero VAD    — turn detection
  - GeminiBrain   — custom streaming LLM (gemini-2.5-flash), NOT the livekit
                    google plugin — this lets us inject CRM context, tool
                    results, and claim state on every turn

Event flow:
  Twilio call → LiveKit SIP trunk (bbh-inca-n9i26bo3.sip.livekit.cloud)
  → Dispatch rule creates room → worker joins
  → Gradium STT transcribes → user_input_transcribed fires
  → GeminiBrain streams reply → session.say() speaks it
  → GLiNER2 extracts entities → WebSocket bridge → dashboard

Env vars required (all in .env):
    GOOGLE_API_KEY, GEMINI_MODEL
    GRADIUM_API_KEY, GRADIUM_VOICE_ID
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
    TWILIO_ACCOUNT_SID, TWILIO_API_KEY_SID, TWILIO_API_KEY_SECRET, TWILIO_PHONE_NUMBER
    DEMO_CRM_PROFILE  (default: max_mueller)

Run:
    python voice/livekit_agent.py start   # production worker (phone calls)
    python voice/livekit_agent.py dev     # local dev mode
    python voice/livekit_agent.py console # laptop mic/speaker (no phone)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env", override=True)
except Exception:
    pass

from agent.claim_state import ClaimState
from agent.gemini_client import GeminiBrain
from agent.prompts import build_jamie_system_prompt, opening_line
from agent.pii_redact import redact
from extraction.gliner2_service import ExtractionService
from bridge.client import publish as bridge_publish

# ── livekit plugin imports (fail loudly if not installed) ─────────────────────
try:
    from livekit.agents import (
        AutoSubscribe,
        JobContext,
        WorkerOptions,
        cli,
    )
    from livekit.agents.voice import Agent, AgentSession
    from livekit.plugins import gradium as lk_gradium
    from livekit.plugins import silero as lk_silero
    _VOICE_DEPS = True
except Exception as _e:
    _VOICE_DEPS = False
    _voice_import_msg = str(_e)


# ── Twilio env-var check ──────────────────────────────────────────────────────
_TWILIO_VARS = [
    "TWILIO_ACCOUNT_SID",
    "TWILIO_API_KEY_SID",
    "TWILIO_API_KEY_SECRET",
    "TWILIO_PHONE_NUMBER",
]


def _check_env() -> None:
    """Warn loudly about missing keys before the worker starts."""
    missing = [v for v in _TWILIO_VARS if not os.environ.get(v)]
    if missing:
        print(f"  ⚠ Missing Twilio env vars: {', '.join(missing)}", file=sys.stderr)
        print("    Phone calls will not be routed correctly.", file=sys.stderr)

    if not os.environ.get("GRADIUM_API_KEY"):
        print("  ✗ GRADIUM_API_KEY not set — STT/TTS will fail!", file=sys.stderr)
        sys.exit(1)

    if not os.environ.get("GOOGLE_API_KEY"):
        print("  ⚠ GOOGLE_API_KEY not set — GeminiBrain will use stub replies",
              file=sys.stderr)


# ── helpers ───────────────────────────────────────────────────────────────────

def load_crm(name: str) -> dict:
    path = REPO / "data" / "crm" / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    print(f"  ⚠ CRM profile not found: {path}", file=sys.stderr)
    return {}


LABEL_ALIASES: dict[str, str] = {
    "accident_date": "accident_datetime",
    "accident_time": "accident_datetime",
    "injury_description": "injuries",
    "damage_description": "how_it_happened",
    "witness_name": "witnesses",
}


def _location_keywords(text: str) -> bool:
    lower = text.lower()
    return any(k in lower for k in (
        "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9", "a10",
        "autobahn", "köln", "koln", "cologne", "berlin", "münchen",
        "munich", "stuttgart", "hauptstraße", "hauptstrasse",
    ))


# ── async background helpers ──────────────────────────────────────────────────

async def _emit_transcript(speaker: str, text: str) -> None:
    await bridge_publish({
        "type": "transcript",
        "speaker": speaker,
        "text": redact(text),
    })


async def _emit_extraction(
    state: ClaimState,
    text: str,
    extractor: ExtractionService,
) -> None:
    """Run GLiNER2 extraction + fraud detection, push results to dashboard."""
    try:
        out = await asyncio.to_thread(extractor.extract, text)
        for label, info in out["pillars"].items():
            mapped = LABEL_ALIASES.get(label, label)
            state.fill(mapped, info["text"], confidence=info["score"])
            await bridge_publish({
                "type": "entity",
                "label": mapped,
                "value": info["text"],
                "confidence": info["score"],
            })
        for label, info in out["fraud"].items():
            state.flag_fraud(label, info["text"], severity="medium")
            await bridge_publish({
                "type": "fraud_signal",
                "signal": label,
                "severity": "medium",
                "evidence": info["text"],
            })
    except Exception as e:
        print(f"  [extraction] error: {e}", file=sys.stderr)


async def _run_tavily_weather(location: str, tool_results: list[dict]) -> None:
    """Fire Tavily weather lookup and append to tool_results (in-place)."""
    try:
        from tools.tavily_lookup import lookup_weather as _lw
        await bridge_publish({
            "type": "tool_call",
            "name": "tavily_lookup_weather",
            "args": {"location": location},
        })
        result = await asyncio.to_thread(_lw, location)
        await bridge_publish({
            "type": "tool_result",
            "name": "tavily_lookup_weather",
            "result": result,
        })
        tool_results.append({"name": "tavily_lookup_weather", "result": result})
    except Exception as e:
        print(f"  [tavily] weather error: {e}", file=sys.stderr)


# ── LiveKit entrypoint ────────────────────────────────────────────────────────

async def entrypoint(ctx: JobContext) -> None:
    """Called once per dispatched job (one inbound phone call = one job).

    Flow:
      1. Connect to the LiveKit room (audio-only for telephony).
      2. Build Deepgram STT + Silero VAD + Gradium TTS + Agent shell.
      3. Register user_input_transcribed listener — this is what fires when
         the caller finishes speaking (replaces the old on_user_turn_completed
         hook which was only available when using the google LLM plugin).
      4. Start the session, speak the opening line, wait until the call ends.
    """
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    room_name = ctx.room.name
    print(f"  [agent] connected to room: {room_name}", file=sys.stderr)

    # ── per-call state ─────────────────────────────────────────────────────
    crm_name = os.environ.get("DEMO_CRM_PROFILE", "max_mueller")
    crm = load_crm(crm_name)
    state = ClaimState(call_id=f"lk-{room_name}")
    extractor = ExtractionService()
    brain = GeminiBrain()
    tool_results: list[dict] = []
    history: list[dict[str, str]] = []

    # Announce call start to dashboard (populates Known Context panel)
    await bridge_publish({"type": "call_start", "crm": crm})

    # ── Gradium TTS config ──────────────────────────────────────────────────
    voice_id = os.environ.get("GRADIUM_VOICE_ID") or None
    tts_json_config = {
        "temp":          float(os.environ.get("GRADIUM_TEMP",    0.85)),
        "padding_bonus": float(os.environ.get("GRADIUM_PADDING", 0.3)),
        "cfg_coef":      float(os.environ.get("GRADIUM_CFG",     2.2)),
        "rewrite_rules": os.environ.get("GRADIUM_LANGUAGE", "en"),
    }
    tts_kwargs: dict = {"json_config": tts_json_config}
    if voice_id:
        tts_kwargs["voice_id"] = voice_id

    # ── build the Agent shell ───────────────────────────────────────────────
    # NOTE: We pass NO `llm=` here. GeminiBrain handles LLM logic manually so
    # we get per-turn CRM context + claim state injection.  The Agent object
    # here is purely an audio pipeline shell (STT / TTS / VAD).
    vad = lk_silero.VAD.load()
    # Gradium STT — three knobs working together to stop the live
    # fragment-as-turn segmentation issue (one continuous statement
    # producing 3-4 separate user_input_transcribed events with
    # is_final=True) without adding perceived latency:
    #
    #   temperature=0.0 suppresses Whisper-style noise hallucinations
    #     ("Marama", "Thank you", "I live in Chicago" from background
    #     ambient sound).
    #
    #   vad_threshold=0.85 (up from SDK default 0.6) requires MORE
    #     confident silence before Gradium emits a final transcript.
    #     Mid-sentence breaths under that threshold get batched into
    #     the same transcript instead of fragmenting into separate
    #     user_input_transcribed events that each fire a turn.
    #
    #   buffer_size_seconds=0.12 (up from default 0.08) widens the
    #     interim-transcript debounce window so micro-fragments
    #     collapse into one event before reaching the listener.
    #
    # All three env-overridable so they can be retuned at the venue
    # without a redeploy.  These are the surviving STT-segmentation
    # fixes applicable to the current GeminiBrain-direct architecture.
    stt = lk_gradium.STT(
        temperature=0.0,
        vad_threshold=float(os.environ.get("GRADIUM_VAD_THRESHOLD", 0.85)),
        buffer_size_seconds=float(os.environ.get("GRADIUM_BUFFER_S", 0.12)),
    )
    tts = lk_gradium.TTS(**tts_kwargs)

    agent = Agent(
        instructions=build_jamie_system_prompt(crm, state),
        tts=tts,
        stt=stt,
        vad=vad,
    )

    session = AgentSession()

    # ── user speech handler ────────────────────────────────────────────────
    @session.on("user_input_transcribed")
    def on_user_speech(stt_event) -> None:  # type: ignore[no-untyped-def]
        """Fires every time the caller finishes a sentence (is_final=True)."""
        if not getattr(stt_event, "is_final", True):
            return
        text = (getattr(stt_event, "transcript", "") or "").strip()
        if not text:
            return

        print(f"  [caller] {text}", file=sys.stderr)
        asyncio.create_task(_handle_caller_turn(text))

    async def _handle_caller_turn(text: str) -> None:
        # 1. Dashboard: caller transcript
        await _emit_transcript("caller", text)

        # 2. GLiNER2 extraction (background — doesn't block TTS)
        asyncio.create_task(_emit_extraction(state, text, extractor))

        # 3. Heuristic Tavily location lookup
        if _location_keywords(text):
            asyncio.create_task(_run_tavily_weather(text[:80], tool_results))

        # 4. Build fresh system prompt with current claim state
        last_jamie = next(
            (h["text"] for h in reversed(history) if h["role"] == "model"),
            None,
        )
        sys_prompt = build_jamie_system_prompt(
            crm, state,
            last_jamie_reply=last_jamie,
            tool_results=tool_results,
        )

        # 5. Stream reply from GeminiBrain
        chunks: list[str] = []
        try:
            async for piece in brain.stream_reply(sys_prompt, history, text):
                chunks.append(piece)
        except Exception as e:
            print(f"  [brain] error: {e}", file=sys.stderr)
            chunks = ["Sorry, let me just pull that up — one moment."]

        reply = "".join(chunks).strip()
        if not reply:
            return

        print(f"  [jamie] {reply}", file=sys.stderr)

        # 6. Speak the reply
        await session.say(reply, allow_interruptions=True)

        # 7. Dashboard: Jamie transcript
        await _emit_transcript("jamie", reply)

        # 8. Update conversation history
        history.append({"role": "user",  "text": text})
        history.append({"role": "model", "text": reply})

    # ── start the session and speak the opener ─────────────────────────────
    agent_task = asyncio.create_task(
        session.start(agent, room=ctx.room)
    )

    # Small pause to let the audio pipeline initialise before speaking
    await asyncio.sleep(1)

    opener = opening_line(crm)
    print(f"  [jamie] {opener}", file=sys.stderr)
    await session.say(opener, allow_interruptions=True)
    history.append({"role": "model", "text": opener})
    await _emit_transcript("jamie", opener)

    # ── wait for the call to end ───────────────────────────────────────────
    try:
        await agent_task
    finally:
        await bridge_publish({
            "type": "call_end",
            "claim_json": state.to_dict(),
        })
        print(f"  [agent] call ended — {len(state.pillars)} pillars filled",
              file=sys.stderr)


# ── CLI entry ─────────────────────────────────────────────────────────────────

def main() -> None:
    if not _VOICE_DEPS:
        print(
            "livekit-agents not installed.\n\n"
            '    pip install "livekit-agents[gradium,google,silero]>=1.4,<2.0"\n\n'
            f"(import error: {_voice_import_msg})"
        )
        sys.exit(2)

    _check_env()

    from livekit.agents import WorkerOptions, cli as lk_cli
    lk_cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    main()