"""H0–H4 fastest path: Gradbot voice agent.

Why Gradbot first: it ships VAD, turn-taking, fillers, barge-in, and the
Gradium TTS/STT loop in ~50 lines.  Once we have something callable, we
migrate to LiveKit + Pipecat for the production demo.

Prereq:
    pip install gradbot
    export GRADIUM_API_KEY=...
    export GRADIUM_VOICE_ID=...        # or use flagship "Emma"
    python voice/gradbot_quickstart.py --crm max_mueller

If the gradbot package isn't installed yet, this script prints clear setup
instructions and exits — useful for new teammates.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Load .env so GRADIUM_API_KEY / GRADIUM_VOICE_ID are picked up automatically.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env", override=True)
except Exception:
    pass

from agent.claim_state import ClaimState
from agent.prompts import build_jamie_system_prompt, opening_line


def load_crm(name: str) -> dict:
    path = REPO / "data" / "crm" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crm", default=os.environ.get("DEMO_CRM_PROFILE", "max_mueller"))
    parser.add_argument("--voice-id", default=os.environ.get("GRADIUM_VOICE_ID"))
    parser.add_argument("--language", default="de")
    args = parser.parse_args()

    try:
        import gradbot  # type: ignore
    except Exception:
        print(
            "gradbot is not installed.\n\n"
            "    pip install gradbot\n\n"
            "Then export GRADIUM_API_KEY and GRADIUM_VOICE_ID and re-run."
        )
        sys.exit(2)

    crm = load_crm(args.crm)
    state = ClaimState(call_id=f"gradbot-{args.crm}")
    system_prompt = build_jamie_system_prompt(crm, state)

    voice_id = args.voice_id
    voice = None
    if not voice_id:
        # Fall back to the documented flagship voice — Emma.
        try:
            voice = gradbot.flagship_voice("Emma")  # type: ignore[attr-defined]
            voice_id = getattr(voice, "voice_id", None) or getattr(voice, "id", None)
        except Exception:
            print("No GRADIUM_VOICE_ID set and flagship_voice('Emma') failed. "
                  "Visit studio.gradium.ai → copy a voice_id → export GRADIUM_VOICE_ID.")
            sys.exit(2)

    config = gradbot.SessionConfig(  # type: ignore[attr-defined]
        voice_id=voice_id,
        instructions=system_prompt,
        language=args.language,
        # Pre-prime Jamie with her opening so she speaks first when the call connects.
        first_message=opening_line(crm),
    )

    async def runner() -> None:
        input_handle, output_handle = await gradbot.run(  # type: ignore[attr-defined]
            session_config=config,
            input_format=gradbot.AudioFormat.OggOpus,   # type: ignore[attr-defined]
            output_format=gradbot.AudioFormat.OggOpus,  # type: ignore[attr-defined]
        )
        print("✅ Gradbot session active. Speak into your mic.")
        print(f"   voice_id={voice_id}  CRM={args.crm}  lang={args.language}")
        # Block forever — Gradbot drives the loop internally.
        await asyncio.Event().wait()

    asyncio.run(runner())


if __name__ == "__main__":
    main()
