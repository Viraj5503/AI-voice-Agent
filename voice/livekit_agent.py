"""Production voice agent: LiveKit Agents + Gradium TTS + Gemini brain.

This is the stack we point Twilio SIP at for the live demo.  It uses the
documented pieces:

    pip install "livekit-agents[gradium]~=1.4"
    from livekit.plugins import gradium
    tts = gradium.TTS(voice_id=...)

A Gemini 3 Flash chat client (`agent.gemini_client.GeminiBrain`) drives the
turns; transcript fragments are forked off to the GLiNER2 extractor and the
WebSocket bridge.

If the optional packages are missing we exit with a clear setup hint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from agent.claim_state import ClaimState
from agent.prompts import build_jamie_system_prompt, opening_line
from agent.gemini_client import GeminiBrain
from agent.pii_redact import redact
from extraction.gliner2_service import ExtractionService
from bridge.client import publish as bridge_publish
from tools.tavily_lookup import DISPATCH as TAVILY_DISPATCH, GEMINI_TOOL_DECLS


def load_crm(name: str) -> dict:
    path = REPO / "data" / "crm" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


async def _emit_transcript(speaker: str, text: str) -> None:
    await bridge_publish({
        "type": "transcript",
        "speaker": speaker,
        "text": redact(text),
    })


async def _emit_extraction(state: ClaimState, text: str, extractor: ExtractionService) -> None:
    """Run the extractor in a thread, push entities to the bridge + state."""
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


async def run_agent(crm_name: str) -> None:
    try:
        from livekit import agents  # type: ignore  # noqa: F401
        from livekit.agents import (  # type: ignore
            AgentSession,
            Agent,
            JobContext,
            WorkerOptions,
            cli,
        )
        from livekit.plugins import gradium  # type: ignore
    except Exception as e:
        print(
            "livekit-agents[gradium] not installed.\n\n"
            "    pip install \"livekit-agents[gradium]~=1.4\"\n\n"
            f"(import error: {e})"
        )
        sys.exit(2)

    crm = load_crm(crm_name)
    state = ClaimState(call_id=f"lk-{crm_name}")
    extractor = ExtractionService()
    brain = GeminiBrain(tools=GEMINI_TOOL_DECLS)

    voice_id = os.environ.get("GRADIUM_VOICE_ID")
    tts = gradium.TTS(voice_id=voice_id) if voice_id else gradium.TTS()
    # Use Gradium STT if exposed by the plugin; otherwise rely on LK's default.
    stt = getattr(gradium, "STT", None)
    stt_inst = stt() if stt else None

    class JamieAgent(Agent):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__(instructions=build_jamie_system_prompt(crm, state))

        async def on_user_message(self, msg: str) -> str:  # pragma: no cover - integration
            await _emit_transcript("caller", msg)
            asyncio.create_task(_emit_extraction(state, msg, extractor))

            chunks: list[str] = []
            async for piece in brain.stream_reply(
                build_jamie_system_prompt(crm, state),
                history=[],
                user_message=msg,
            ):
                chunks.append(piece)
            reply = "".join(chunks).strip()
            await _emit_transcript("jamie", reply)
            return reply

    async def entrypoint(ctx: "JobContext") -> None:  # pragma: no cover - integration
        await ctx.connect()
        await bridge_publish({"type": "call_start", "crm": crm})
        session = AgentSession(stt=stt_inst, tts=tts)  # type: ignore[arg-type]
        await session.start(agent=JamieAgent(), room=ctx.room)
        await session.say(opening_line(crm))
        await session.aclose_when_finished()
        await bridge_publish({"type": "call_end", "claim_json": state.to_dict()})

    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))  # type: ignore[arg-type]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crm", default=os.environ.get("DEMO_CRM_PROFILE", "max_mueller"))
    args = parser.parse_args()
    asyncio.run(run_agent(args.crm))


if __name__ == "__main__":
    main()
