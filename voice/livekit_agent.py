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


def load_crm(name: str) -> dict:
    return json.loads((REPO / "data" / "crm" / f"{name}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
def build_session(crm: dict, state: ClaimState):
    """Construct an AgentSession with Gradium voice + native Gemini brain.

    Imports live inside the function so this module stays import-safe even
    if the optional voice deps aren't installed (e.g. on a CI box).
    """
    from livekit.agents import AgentSession
    from livekit.plugins import gradium, google as lk_google, silero

    # GRADIUM_VOICE_ID falls through to the plugin's documented default
    # (YTpq7expH9539ERJ — flagship "Emma") when unset.
    voice_id = os.environ.get("GRADIUM_VOICE_ID") or None

    return AgentSession(
        stt=gradium.STT(),
        llm=lk_google.LLM(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            temperature=0.85,
        ),
        tts=gradium.TTS(voice_id=voice_id) if voice_id else gradium.TTS(),
        vad=silero.VAD.load(),
    )


def build_agent(crm: dict, state: ClaimState):
    """The Agent subclass holds Jamie's persona + the on_user_turn_completed
    hook that forks transcripts to GLiNER2 + the dashboard bridge."""
    from livekit.agents import Agent
    from livekit.agents.llm import ChatContext

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

    await session.start(agent=agent, room=ctx.room)


def main() -> None:
    try:
        from livekit.agents import WorkerOptions, cli
    except Exception as e:
        print(
            "livekit-agents not installed.\n\n"
            "    pip install \"livekit-agents[gradium,google,silero]>=1.4,<2.0\"\n\n"
            f"(import error: {e})"
        )
        sys.exit(2)
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    main()
