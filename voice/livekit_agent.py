"""Production voice agent — LiveKit + Gradium STT/TTS + native Gemini LLM.

Verified against the actual installed packages:
    livekit-agents 1.5.6
    livekit-plugins-gradium 1.5.6
    livekit-plugins-google 1.5.6
    livekit-plugins-silero 1.5.6   (VAD)

The hook surface in livekit-agents 1.5 is:
    Agent.on_enter / on_exit / on_user_turn_completed
    NOT on_user_message — that doesn't exist.

We use livekit.plugins.google.LLM directly instead of wrapping our custom
GeminiBrain — the text demo keeps using GeminiBrain because it doesn't need
the AgentSession pipeline; the voice path uses the native plugin so we get
proper turn handling, streaming, tool calling, and barge-in for free.

Run:
    pip install "livekit-agents[gradium,google,silero]>=1.4,<2.0"
    python voice/livekit_agent.py dev    # local dev mode
    python voice/livekit_agent.py start  # production worker
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
from agent.prompts import build_jamie_system_prompt, opening_line
from agent.pii_redact import redact
from extraction.gliner2_service import ExtractionService
from bridge.client import publish as bridge_publish

# livekit-agents requires plugins to register on the MAIN thread. Lazy-importing
# inside build_session() runs in a worker subprocess thread and fails with
# "Plugins must be registered on the main thread". So we import top-level here.
# Wrapped in try/except for CI boxes that don't have the optional voice deps.
try:
    from livekit.agents import (
        AgentSession,
        Agent,
        AutoSubscribe,
        function_tool,
    )
    from livekit.agents.llm import ChatContext
    from livekit.plugins import gradium as lk_gradium
    from livekit.plugins import google as lk_google
    from livekit.plugins import openai as lk_openai
    from livekit.plugins import silero as lk_silero
    _VOICE_DEPS = True
except Exception as _voice_import_error:  # noqa: BLE001
    _VOICE_DEPS = False
    _voice_import_msg = str(_voice_import_error)

# Semantic end-of-turn detection — separate import path so a missing
# plugin can't break the whole file.  When present, we hand a model
# instance to AgentSession(turn_detection=...) so the agent uses an ML
# prediction of "is the user actually done speaking?" instead of
# pure-VAD silence.  This is the canonical fix for the fragment-as-turn
# segmentation issue.  See docs/turn-detector page on docs.livekit.io.
try:
    from livekit.plugins.turn_detector.multilingual import MultilingualModel
    from livekit.plugins.turn_detector.english import EnglishModel
    _TURN_DETECTOR_AVAILABLE = True
except Exception:
    _TURN_DETECTOR_AVAILABLE = False


def load_crm(name: str) -> dict:
    return json.loads((REPO / "data" / "crm" / f"{name}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
def _on_ollama() -> bool:
    return (os.environ.get("BRAIN_PROVIDER") or "ollama").lower() == "ollama"


def _build_primary_llm():
    """Build the PRIMARY LLM based on BRAIN_PROVIDER.

    BRAIN_PROVIDER=gemini  →  livekit-plugins-google native Gemini.
    BRAIN_PROVIDER=ollama  →  livekit-plugins-openai pointed at LLM_BASE_URL
                              (defaults to local Ollama, but supports any
                              OpenAI-compatible /v1 endpoint via LLM_*).
    default                →  ollama (free-tier quota survival).

    Useful LLM_BASE_URL escape hatches when the laptop is overloaded or
    local quality is too low:
      - Groq Cloud      base=https://api.groq.com/openai
                        model=llama-3.3-70b-versatile (free 100k TPD)
      - Cerebras Cloud  base=https://api.cerebras.ai
                        model=llama-3.3-70b  (free tier, sub-200ms)
      - OpenAI direct   base=https://api.openai.com
                        model=gpt-4o-mini
    Timeout 30s tolerates Ollama's ~10s first-call warmup; harmless on
    hosted providers since they respond in <1s.
    """
    if not _on_ollama():
        return lk_google.LLM(
            model=os.environ.get("GEMINI_MODEL", "gemini-flash-latest"),
            temperature=0.85,
        )
    base_url_root = os.environ.get(
        "LLM_BASE_URL",
        os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    )
    return lk_openai.LLM(
        model=os.environ.get(
            "LLM_MODEL", os.environ.get("OLLAMA_MODEL", "llama3.2")
        ),
        api_key=os.environ.get("LLM_API_KEY", "ollama"),
        base_url=base_url_root.rstrip("/") + "/v1",
        temperature=0.85,
        timeout=30.0,
    )


def _build_fallback_llm():
    """Build a SECONDARY LLM from LLM_FALLBACK_* env vars, or None.

    The fallback is always OpenAI-compatible (works for Groq, Cerebras,
    OpenAI, even another Ollama instance).  None of the providers in this
    industry can be trusted on demo day — Gemini hits per-minute quota,
    Groq hits per-day token caps, Anthropic runs out of credit, local
    Ollama can OOM the laptop.  Wrapping primary + fallback in
    livekit.agents.llm.FallbackAdapter means a single 429 / 5xx / timeout
    on the primary cleanly reroutes mid-call without dropping the user.
    """
    base = os.environ.get("LLM_FALLBACK_BASE_URL")
    if not base:
        return None
    return lk_openai.LLM(
        model=os.environ.get("LLM_FALLBACK_MODEL", "llama-3.1-8b-instant"),
        api_key=os.environ.get("LLM_FALLBACK_API_KEY", ""),
        base_url=base.rstrip("/") + "/v1",
        temperature=0.85,
        timeout=30.0,
    )


def _build_llm():
    """Return either the primary LLM or a FallbackAdapter([primary, fallback]).

    Logged on construction so you can see at boot which path is active.
    """
    primary = _build_primary_llm()
    fallback = _build_fallback_llm()
    if fallback is None:
        print(
            f"  [llm] using {type(primary).__name__} (no fallback configured)",
            file=sys.stderr,
        )
        return primary
    from livekit.agents.llm import FallbackAdapter
    print(
        f"  [llm] primary={type(primary).__name__}  "
        f"fallback={type(fallback).__name__}@{os.environ.get('LLM_FALLBACK_BASE_URL')}",
        file=sys.stderr,
    )
    return FallbackAdapter([primary, fallback])


def build_session(crm: dict, state: ClaimState):
    """Construct an AgentSession with Gradium voice + provider-pluggable brain."""
    # GRADIUM_VOICE_ID falls through to the plugin's documented default
    # (YTpq7expH9539ERJ — flagship "Emma") when unset.
    voice_id = os.environ.get("GRADIUM_VOICE_ID") or None

    # Gradium TTS tone tuning — humanic warmth for the Turing test.
    # temp 0.85   = natural variation, not robotic consistency
    # padding_bonus 0.3 = slightly slower / more deliberate pace
    # cfg_coef 2.2  = high voice consistency turn-to-turn
    # rewrite_rules en  = English number/date pronunciation rules.
    #                     Set GRADIUM_LANGUAGE=de in .env to flip German.
    tts_json_config = {
        "temp": float(os.environ.get("GRADIUM_TEMP", 0.85)),
        "padding_bonus": float(os.environ.get("GRADIUM_PADDING", 0.3)),
        "cfg_coef": float(os.environ.get("GRADIUM_CFG", 2.2)),
        "rewrite_rules": os.environ.get("GRADIUM_LANGUAGE", "en"),
    }
    tts_kwargs = {"json_config": tts_json_config}
    if voice_id:
        tts_kwargs["voice_id"] = voice_id

    # Custom pronunciation dictionary — set up via scripts/setup_pronunciations.py.
    # Forces "FNOL" → "eff-noll", "DSGVO" → German lettering, "Vollkasko" with
    # native German stress, etc.  Real insurance pros pattern-match these
    # acronyms instantly; getting them wrong is a ~free Turing-test "AI" vote.
    pron_id = os.environ.get("GRADIUM_PRONUNCIATION_ID") or None
    if pron_id:
        tts_kwargs["pronunciation_id"] = pron_id

    # Gradium STT — three knobs working together to stop the
    # fragment-as-turn problem live console runs surfaced:
    #
    # temperature=0.0 stops Whisper-style noise hallucination ("Marama",
    # "I live in Chicago" appearing from background ambient sound).
    #
    # vad_threshold=0.85 (up from SDK default 0.6) requires MORE
    # confident silence before Gradium emits a final transcript.  Mid-
    # sentence breaths under that threshold get batched into the same
    # transcript instead of fragmenting into 3-4 separate "turns".
    #
    # buffer_size_seconds=0.12 (up from default 0.08) widens the
    # interim-transcript debounce window so micro-fragments collapse
    # into one event before reaching the agent.
    stt = lk_gradium.STT(
        temperature=0.0,
        vad_threshold=float(os.environ.get("GRADIUM_VAD_THRESHOLD", 0.85)),
        buffer_size_seconds=float(os.environ.get("GRADIUM_BUFFER_S", 0.12)),
    )

    # Semantic end-of-turn detection.  Runs a small ML model on every
    # interim transcript to predict whether the user is actually done
    # speaking — far more accurate than VAD silence alone, and it has
    # near-zero latency overhead (~50ms inference).  Without it,
    # livekit-agents falls back to VAD-mode endpointing, which fires
    # on_user_turn_completed too early on natural mid-sentence pauses
    # and triggers the "preemptive generation … chat context has
    # changed" retry loop.
    #
    # TURN_DETECTOR=multilingual (default) covers German + English;
    # TURN_DETECTOR=english is lighter for English-only deployments;
    # TURN_DETECTOR=disabled falls back to pure-VAD detection.
    turn_pref = (os.environ.get("TURN_DETECTOR") or "multilingual").lower()
    turn_detection_arg = None
    if _TURN_DETECTOR_AVAILABLE and turn_pref != "disabled":
        try:
            if turn_pref == "english":
                turn_detection_arg = EnglishModel()
            else:
                turn_detection_arg = MultilingualModel()
        except Exception as e:
            print(f"  [turn-detector] init failed ({type(e).__name__}: {e}); "
                  "falling back to VAD-only endpointing", file=sys.stderr)
            turn_detection_arg = None
    elif not _TURN_DETECTOR_AVAILABLE:
        print("  [turn-detector] livekit-plugins-turn-detector not installed; "
              "VAD-only endpointing in use", file=sys.stderr)

    # Endpointing — uses the modern turn_handling API (min/max
    # endpointing args were deprecated in v2.0 of livekit-agents).
    # mode="dynamic" makes the agent observe actual pause patterns
    # turn-to-turn and self-adjust the cutoff: zero latency cost on
    # first turn, gets better as the call progresses.  With the
    # turn_detector model active, these are mostly bumpers — semantic
    # detection usually fires first.  When the model is uncertain or
    # disabled, the dynamic timer takes over.  Widen min from the 0.5s
    # default so natural breath pauses don't commit a partial turn.
    turn_handling_arg = {
        "endpointing": {
            "mode": os.environ.get("ENDPOINT_MODE", "dynamic"),
            "min_delay": float(os.environ.get("MIN_ENDPOINT_DELAY_S", 0.8)),
            "max_delay": float(os.environ.get("MAX_ENDPOINT_DELAY_S", 4.0)),
        },
    }

    session_kwargs: dict = {
        "stt": stt,
        "llm": _build_llm(),
        "tts": lk_gradium.TTS(**tts_kwargs),
        "vad": lk_silero.VAD.load(),
        "turn_handling": turn_handling_arg,
    }
    if turn_detection_arg is not None:
        session_kwargs["turn_detection"] = turn_detection_arg

    return AgentSession(**session_kwargs)


def _location_keywords(text: str) -> bool:
    """Same triggers scripts/run_demo_auto.py uses for heuristic Tavily."""
    lower = text.lower()
    return any(k in lower for k in (
        "a1","a2","a3","a4","a5","a6","a7","a8","a9","a10","autobahn",
        "köln","koln","cologne","berlin","münchen","munich","stuttgart",
        "hauptstraße","hauptstrasse","lindenweg","industriestraße",
    ))


def build_agent(crm: dict, state: ClaimState):
    """The Agent subclass holds Jamie's persona + the on_user_turn_completed
    hook that forks transcripts to GLiNER2 + the dashboard bridge.

    Tool registration is provider-aware: on Ollama (llama3.2 3B) the
    OpenAI-compatible tool-call JSON is mangled (model emits
    `{"location": {"type":"string","value":"..."}}` instead of just
    `"..."`), so we disable LLM-driven tools and fire Tavily heuristically
    from on_user_turn_completed instead.  Same dashboard tool_call /
    tool_result events fire either way.
    """
    extractor = ExtractionService()
    skip_tools = _on_ollama()
    # Recent tool results — fed into build_jamie_system_prompt so the LLM
    # can quote real Tavily output instead of inventing weather facts.
    tool_results: list[dict] = []

    class JamieAgent(Agent):
        def __init__(self) -> None:
            super().__init__(
                instructions=build_jamie_system_prompt(crm, state),
            )
            # livekit-agents always merges class-decorated @function_tool
            # methods via find_function_tools() regardless of the `tools`
            # constructor arg.  To actually disable tools on Ollama (where
            # llama3.2 mangles the OpenAI tool-call JSON), wipe _tools and
            # the chat_ctx tools list after super init.
            if skip_tools:
                self._tools = []
                self._chat_ctx = self._chat_ctx.copy(tools=[])

        async def on_enter(self) -> None:  # type: ignore[override]
            # Push a call_start event with the full CRM so the dashboard
            # populates its Known Context panel before the first word.
            await bridge_publish({"type": "call_start", "crm": crm})
            # Speak the opening line immediately
            await self.session.say(opening_line(crm))

        @function_tool
        async def lookup_weather(self, location: str) -> str:
            """Look up the current weather and road conditions at the given
            location. Call this immediately after the caller mentions where
            the accident happened so you can reference real conditions."""
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
            return result.get("summary") or "(no weather data)"

        @function_tool
        async def lookup_towing(self, location: str) -> str:
            """Find 24-hour towing services (Abschleppdienst) near an accident
            location. Use when the caller's vehicle is not drivable."""
            from tools.tavily_lookup import lookup_towing as _lt
            await bridge_publish({
                "type": "tool_call",
                "name": "tavily_lookup_towing",
                "args": {"location": location},
            })
            result = await asyncio.to_thread(_lt, location)
            await bridge_publish({
                "type": "tool_result",
                "name": "tavily_lookup_towing",
                "result": result,
            })
            return result.get("summary") or "(no towing data)"

        @function_tool
        async def lookup_traffic(self, location: str) -> str:
            """Look up live traffic incidents and road closures at the given
            location.  Use after the caller mentions an Autobahn or major
            road — gives Jamie a fresh, specific fact like "I see there's
            a closure on the A4 today" that's far more believable than
            generic acknowledgments.  Pulls last 24h of German news."""
            from tools.tavily_lookup import lookup_traffic as _ltr
            await bridge_publish({
                "type": "tool_call",
                "name": "tavily_lookup_traffic",
                "args": {"location": location},
            })
            result = await asyncio.to_thread(_ltr, location)
            await bridge_publish({
                "type": "tool_result",
                "name": "tavily_lookup_traffic",
                "result": result,
            })
            return result.get("summary") or "(no traffic incidents reported)"

        async def on_user_turn_completed(  # type: ignore[override]
            self,
            turn_ctx: ChatContext,
            new_message,
        ) -> None:
            """Called every time the user finishes a turn.

            Live transcript + GLiNER2 extraction → dashboard.
            We also rebuild the system prompt so Jamie always reads from
            the freshest claim state.
            """
            text = (new_message.text_content or "").strip()
            if not text:
                return

            await bridge_publish({
                "type": "transcript",
                "speaker": "caller",
                "text": redact(text),
            })

            # Run the extractor in a thread (gliner is sync / CPU-bound)
            out = await asyncio.to_thread(extractor.extract, text)
            for label, info in out["pillars"].items():
                state.fill(label, info["text"], confidence=info["score"])
                await bridge_publish({
                    "type": "entity",
                    "label": label,
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

            # Heuristic Tavily fire when LLM-driven tools are disabled
            # (Ollama path).  Two parallel lookups — weather AND live
            # traffic incidents — both grounded to the German news bucket
            # in the last 24h.  Results fold into the next prompt refresh
            # via tool_results so Jamie can reference real conditions
            # ("I see there were heavy rains" / "I see there's a closure
            # on the A4 today") even though she didn't decide to call.
            if skip_tools and _location_keywords(text):
                from tools.tavily_lookup import (
                    lookup_weather as _lw,
                    lookup_traffic as _lt,
                )
                location_arg = text[:80]
                # Weather
                await bridge_publish({
                    "type": "tool_call",
                    "name": "tavily_lookup_weather",
                    "args": {"location": location_arg},
                })
                weather = await asyncio.to_thread(_lw, location_arg)
                await bridge_publish({
                    "type": "tool_result",
                    "name": "tavily_lookup_weather",
                    "result": weather,
                })
                tool_results.append({
                    "name": "tavily_lookup_weather",
                    "result": weather,
                })
                # Traffic
                await bridge_publish({
                    "type": "tool_call",
                    "name": "tavily_lookup_traffic",
                    "args": {"location": location_arg},
                })
                traffic = await asyncio.to_thread(_lt, location_arg)
                await bridge_publish({
                    "type": "tool_result",
                    "name": "tavily_lookup_traffic",
                    "result": traffic,
                })
                tool_results.append({
                    "name": "tavily_lookup_traffic",
                    "result": traffic,
                })

            # Refresh Jamie's system prompt with the new claim state.
            # update_instructions is async — without await, the prompt never
            # actually refreshes turn-to-turn (RuntimeWarning fires).
            await self.update_instructions(
                build_jamie_system_prompt(crm, state, tool_results=tool_results)
            )

        async def on_exit(self) -> None:  # type: ignore[override]
            await bridge_publish({
                "type": "call_end",
                "claim_json": state.to_dict(),
            })

    return JamieAgent()


# --------------------------------------------------------------------------
async def entrypoint(ctx) -> None:  # type: ignore[no-untyped-def]
    """LiveKit-agents entrypoint — invoked once per dispatched job.

    auto_subscribe=AUDIO_ONLY matters for telephony.  When a Twilio SIP
    call lands in a LiveKit room via the inbound trunk, only audio
    tracks exist — subscribing to the default (all tracks) makes the
    agent wait on phantom video tracks that never arrive.  Audio-only
    keeps console mode + LiveKit Cloud playground working too (their
    rooms also have no video).
    """
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    crm_name = os.environ.get("DEMO_CRM_PROFILE", "max_mueller")
    crm = load_crm(crm_name)
    state = ClaimState(call_id=f"lk-{ctx.job.id if hasattr(ctx, 'job') else crm_name}")

    session = build_session(crm, state)
    agent = build_agent(crm, state)

    # Publish Jamie's spoken responses to the dashboard.  on_user_turn_completed
    # already covers the caller side; the conversation_item_added event fires
    # for both roles, so we filter to 'assistant' here to avoid double-publish.
    def _on_item_added(ev) -> None:  # type: ignore[no-untyped-def]
        item = getattr(ev, "item", None)
        if item is None or getattr(item, "role", None) != "assistant":
            return
        text = (item.text_content or "").strip()
        if not text:
            return
        asyncio.create_task(bridge_publish({
            "type": "transcript",
            "speaker": "jamie",
            "text": text,
        }))

    session.on("conversation_item_added", _on_item_added)

    # Filler-audio injection on listening → thinking transition.
    # Why: even with Gemini Flash @ 600ms, there's a perceptible silence
    # gap right after the caller stops speaking and before Jamie's actual
    # response starts streaming.  Real human agents fill that gap with
    # "mm-hmm", "right", "okay so".  Robots leave it silent.  We inject
    # a randomly-chosen short empathy token via session.say() the moment
    # the agent transitions to "thinking" — this TTSes through Gradium
    # in ~150-200ms (well under the LLM's first-token latency) and the
    # actual reply queues seamlessly behind it.
    #
    # Probability gate (FILLER_RATE) so it doesn't fire on every turn —
    # a real human says "mm-hmm" maybe 50-70% of the time, not 100%.
    # Set FILLER_RATE=0 to disable; FILLER_RATE=1 to always-on.
    import random
    _EMPATHY_FILLERS = [
        "Mm-hmm.",
        "Right.",
        "Okay.",
        "Got it.",
        "Mm.",
        "Right, okay.",
        "Yeah.",
    ]
    filler_rate = float(os.environ.get("FILLER_RATE", "0.6"))

    def _on_state_change(ev) -> None:  # type: ignore[no-untyped-def]
        if getattr(ev, "old_state", None) != "listening":
            return
        if getattr(ev, "new_state", None) != "thinking":
            return
        if random.random() > filler_rate:
            return
        filler = random.choice(_EMPATHY_FILLERS)
        # allow_interruptions=True so the actual LLM response can talk
        # over the tail of the filler if it arrives faster than expected.
        try:
            asyncio.create_task(session.say(filler, allow_interruptions=True))
        except Exception:
            pass  # never crash a call over a filler

    session.on("agent_state_changed", _on_state_change)

    await session.start(agent=agent, room=ctx.room)


def main() -> None:
    if not _VOICE_DEPS:
        print(
            "livekit-agents not installed.\n\n"
            "    pip install \"livekit-agents[gradium,google,silero]>=1.4,<2.0\"\n\n"
            f"(import error: {_voice_import_msg})"
        )
        sys.exit(2)
    from livekit.agents import WorkerOptions, cli
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    main()
