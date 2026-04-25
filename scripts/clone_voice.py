"""Clone a voice for Jamie from a 10-second audio sample.

Per the OPERATION doc Strategic Insight #8 — Gradium supports custom voice
creation from a short clip.  Cloned voices have human micro-imperfections
the flagship Emma doesn't, which materially shifts the Turing-test pass
rate.

Recommended workflow:
  1. Record ~10 seconds of a real human saying something natural like:
        "Hi, this is Jamie from Vorsicht claims, just answering — I'm
         working from home today so bear with me if the line sounds a
         bit off."
     Save as .wav (or .mp3, .ogg, .flac).  Quiet room, headset mic, no
     music behind.
  2. Run:    python scripts/clone_voice.py path/to/sample.wav
  3. Copy the printed voice_id into your .env as GRADIUM_VOICE_ID.
  4. Restart voice/livekit_agent.py — Jamie now sounds like that human.

Usage:
    python scripts/clone_voice.py jamie_sample.wav
    python scripts/clone_voice.py jamie_sample.wav --name "Jamie cloned"
    python scripts/clone_voice.py --list           # show existing voices
    python scripts/clone_voice.py --delete <uid>   # remove one we made
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

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env", override=True)
except Exception:
    pass


def _client():
    try:
        import gradium  # type: ignore
    except Exception as e:
        print(f"gradium SDK not installed: {e}\n  pip install gradium")
        sys.exit(2)
    key = os.environ.get("GRADIUM_API_KEY")
    if not key:
        print("GRADIUM_API_KEY not set in .env")
        sys.exit(2)
    return gradium.GradiumClient(api_key=key)


async def cmd_create(audio_path: Path, name: str | None, start_s: float) -> int:
    if not audio_path.exists():
        print(f"file not found: {audio_path}")
        return 2

    client = _client()
    print(f"  uploading {audio_path.name} ({audio_path.stat().st_size:,} bytes)…")
    try:
        voice = await client.voice_create(
            audio_file=audio_path,
            name=name or f"jamie-{audio_path.stem}",
            description="Cloned voice for Jamie's FNOL agent",
            start_s=start_s,
        )
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")
        return 1

    uid = voice.get("uid") or voice.get("voice_id")
    print()
    print("  ────────────────────────────────────────────────────────────")
    print(f"  ✓ voice created — uid: {uid}")
    print(f"    name:        {voice.get('name')}")
    print(f"    description: {voice.get('description')}")
    print("  ────────────────────────────────────────────────────────────")
    print()
    print("  Next: open .env and set:")
    print(f"      GRADIUM_VOICE_ID={uid}")
    print("  then restart voice/livekit_agent.py to hear Jamie speak in this voice.")
    return 0


async def cmd_list() -> int:
    client = _client()
    try:
        result = await client.voice_list()
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")
        return 1

    voices = result.get("voices", []) if isinstance(result, dict) else result
    print(f"\n  {len(voices)} voice(s) on this account:\n")
    for v in voices:
        if isinstance(v, dict):
            uid = v.get("uid") or v.get("voice_id") or "?"
            name = v.get("name") or "(unnamed)"
            desc = (v.get("description") or "").strip()[:60]
            print(f"  {uid:24}  {name:30}  {desc}")
        else:
            print(f"  {v}")
    print()
    return 0


async def cmd_delete(uid: str) -> int:
    client = _client()
    try:
        ok = await client.voice_delete(uid)
        print(f"  {'✓ deleted' if ok else '✗ not deleted'}: {uid}")
        return 0 if ok else 1
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", nargs="?", help="path to audio sample (.wav/.mp3/.ogg/.flac)")
    parser.add_argument("--name", default=None, help="voice name (default: jamie-<filename>)")
    parser.add_argument("--start-s", type=float, default=0.0, help="skip first N seconds of audio (default 0)")
    parser.add_argument("--list", action="store_true", help="list voices on this account")
    parser.add_argument("--delete", metavar="UID", help="delete a voice by uid")
    args = parser.parse_args()

    if args.list:
        rc = asyncio.run(cmd_list())
    elif args.delete:
        rc = asyncio.run(cmd_delete(args.delete))
    elif args.audio:
        rc = asyncio.run(cmd_create(Path(args.audio).expanduser().resolve(), args.name, args.start_s))
    else:
        parser.print_help()
        rc = 2
    sys.exit(rc)


if __name__ == "__main__":
    main()
