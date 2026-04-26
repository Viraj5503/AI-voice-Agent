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

    # Gradium STT — temperature=0.0 stops Whisper-style noise hallucination
    # ("Marama", "Englishman", "I live in Chicago" appearing from background
    # ambient sound).  vad_threshold 0.9 / vad_bucket 2 are SDK defaults.
    stt = lk_gradium.STT(temperature=0.0)

    return AgentSession(
        stt=stt,
        llm=_build_llm(),
        tts=lk_gradium.TTS(**tts_kwargs),
        vad=lk_silero.VAD.load(),
    )


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
            # (Ollama path).  The result is folded into the next prompt
            # refresh via tool_results so Jamie can reference real
            # conditions even though she didn't "decide" to call the tool.
            if skip_tools and _location_keywords(text):
                from tools.tavily_lookup import lookup_weather as _lw
                await bridge_publish({
                    "type": "tool_call",
                    "name": "tavily_lookup_weather",
                    "args": {"location": text[:80]},
                })
                weather = await asyncio.to_thread(_lw, text[:80])
                await bridge_publish({
                    "type": "tool_result",
                    "name": "tavily_lookup_weather",
                    "result": weather,
                })
                tool_results.append({
                    "name": "tavily_lookup_weather",
                    "result": weather,
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
