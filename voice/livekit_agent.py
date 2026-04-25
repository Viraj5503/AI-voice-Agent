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
    load_dotenv(REPO / ".env")
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
    from livekit.agents import AgentSession, Agent, function_tool
    from livekit.agents.llm import ChatContext
    from livekit.plugins import gradium as lk_gradium
    from livekit.plugins import google as lk_google
    from livekit.plugins import silero as lk_silero
    _VOICE_DEPS = True
except Exception as _voice_import_error:  # noqa: BLE001
    _VOICE_DEPS = False
    _voice_import_msg = str(_voice_import_error)


def load_crm(name: str) -> dict:
    return json.loads((REPO / "data" / "crm" / f"{name}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
def build_session(crm: dict, state: ClaimState):
    """Construct an AgentSession with Gradium voice + native Gemini brain."""
    # GRADIUM_VOICE_ID falls through to the plugin's documented default
    # (YTpq7expH9539ERJ — flagship "Emma") when unset.
    voice_id = os.environ.get("GRADIUM_VOICE_ID") or None

    return AgentSession(
        stt=lk_gradium.STT(),
        llm=lk_google.LLM(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            temperature=0.85,
        ),
        tts=lk_gradium.TTS(voice_id=voice_id) if voice_id else lk_gradium.TTS(),
        vad=lk_silero.VAD.load(),
    )


def build_agent(crm: dict, state: ClaimState):
    """The Agent subclass holds Jamie's persona + the on_user_turn_completed
    hook that forks transcripts to GLiNER2 + the dashboard bridge."""
    extractor = ExtractionService()

    class JamieAgent(Agent):
        def __init__(self) -> None:
            super().__init__(
                instructions=build_jamie_system_prompt(crm, state),
            )

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

            # Refresh Jamie's system prompt with the new claim state.
            self.update_instructions(build_jamie_system_prompt(crm, state))

        async def on_exit(self) -> None:  # type: ignore[override]
            await bridge_publish({
                "type": "call_end",
                "claim_json": state.to_dict(),
            })

    return JamieAgent()


# --------------------------------------------------------------------------
async def entrypoint(ctx) -> None:  # type: ignore[no-untyped-def]
    """LiveKit-agents entrypoint — invoked once per dispatched job."""
    await ctx.connect()

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
