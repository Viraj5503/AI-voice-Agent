"""Batch-synthesize the filler manifest with Jamie's Gradium voice.

Run once *before* the call so we don't burn live credits or risk timing on a
streamed-TTS call mid-conversation.

Saves PCM 48kHz mono files into fillers/audio/<id>.pcm.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUDIO = HERE / "audio"
MANIFEST = HERE / "manifest.json"


async def synth_one(client, voice_id: str, text: str, out_path: Path) -> None:
    """Use gradium 0.5.11's tts_stream() — simpler API for batch synthesis
    than tts_realtime().  Returns a stream whose .iter_bytes() yields PCM."""
    from gradium import TTSSetup  # type: ignore
    setup = TTSSetup(voice_id=voice_id, output_format="pcm")  # type: ignore[call-arg]
    stream = await client.tts_stream(setup, text)  # type: ignore[attr-defined]
    chunks: list[bytes] = []
    async for audio in stream.iter_bytes():
        chunks.append(audio)
    out_path.write_bytes(b"".join(chunks))


async def main() -> None:
    try:
        import gradium  # type: ignore
    except Exception:
        print("Install gradium first:  pip install gradium")
        sys.exit(2)

    api_key = os.environ.get("GRADIUM_API_KEY")
    voice_id = os.environ.get("GRADIUM_VOICE_ID")
    if not api_key or not voice_id:
        print("Set GRADIUM_API_KEY and GRADIUM_VOICE_ID before running.")
        sys.exit(2)

    AUDIO.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text())
    client = gradium.GradiumClient(api_key=api_key)  # type: ignore[attr-defined]

    total = sum(len(items) for items in manifest["categories"].values())
    done = 0
    for cat, items in manifest["categories"].items():
        for item in items:
            out = AUDIO / f"{item['id']}.pcm"
            if out.exists():
                done += 1
                continue
            await synth_one(client, voice_id, item["text"], out)
            done += 1
            print(f"[{done}/{total}] {cat:16}  {item['id']}")
    print(f"\nDone. {done} clips in {AUDIO}")


if __name__ == "__main__":
    asyncio.run(main())
