"""Gradium multiplexing flex — bounty pitch artifact.

Demonstrates that a single WebSocket can drive multiple concurrent TTS streams
(distinguished by client_req_id), so we don't open N connections for N callers.
This is the production property that real insurance call centers care about.

Run:
    export GRADIUM_API_KEY=...
    python voice/multiplex_demo.py
"""

from __future__ import annotations

import asyncio
import os
import sys


SCRIPTS = [
    "Hi, you're through to Jamie at Vorsicht claims. How can I help?",
    "Got it, just pulling up your file now.",
    "Okay, I'm so sorry to hear that — first, are you physically okay?",
    "Right, and that's on the A4 you said? Let me check the road conditions.",
]


async def main() -> None:
    try:
        import gradium  # type: ignore
    except Exception:
        print("Install gradium first:  pip install gradium")
        sys.exit(2)

    api_key = os.environ.get("GRADIUM_API_KEY")
    voice_id = os.environ.get("GRADIUM_VOICE_ID")
    if not api_key:
        print("Set GRADIUM_API_KEY (and ideally GRADIUM_VOICE_ID).")
        sys.exit(2)

    client = gradium.AsyncClient(api_key=api_key)  # type: ignore[attr-defined]

    async def synth(idx: int, text: str) -> None:
        req_id = f"jamie-stream-{idx}"
        try:
            stream = await client.tts_realtime(  # type: ignore[attr-defined]
                voice_id=voice_id,
                output_format="pcm",
                client_req_id=req_id,
                close_ws_on_eos=False,  # multiplexing on
            )
            await stream.send_text(text)
            total_bytes = 0
            async for audio in stream:
                total_bytes += len(audio)
            print(f"[{req_id}] received {total_bytes} bytes for: {text[:42]}...")
        except Exception as e:
            print(f"[{req_id}] error: {e}")

    await asyncio.gather(*(synth(i, t) for i, t in enumerate(SCRIPTS)))


if __name__ == "__main__":
    asyncio.run(main())
