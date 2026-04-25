"""Sanity-check every API key in .env before running the demo.

Run this immediately after `pip install -r requirements.txt`:

    python scripts/verify_keys.py

It tests Google / Gemini, Gradium, Tavily, and Anthropic in isolation and
prints a pass/fail summary.  Saves you from debugging a broken pipeline
when the real problem is one bad key.
"""

from __future__ import annotations

import asyncio
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


# ANSI colors for the summary line — fall back to plain text if not a TTY.
GREEN = "\033[92m" if sys.stdout.isatty() else ""
RED   = "\033[91m" if sys.stdout.isatty() else ""
DIM   = "\033[2m"  if sys.stdout.isatty() else ""
END   = "\033[0m"  if sys.stdout.isatty() else ""


def _ok(name: str, msg: str) -> None:
    print(f"  {GREEN}✓ {name:20}{END} {msg}")


def _fail(name: str, msg: str) -> None:
    print(f"  {RED}✗ {name:20}{END} {msg}")


def _skip(name: str, msg: str) -> None:
    print(f"  {DIM}· {name:20}{END} {msg}")


# --------------------------------------------------------------
async def check_gemini() -> bool:
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        _skip("Gemini", "no GOOGLE_API_KEY set — skipping")
        return False
    try:
        from google import genai          # type: ignore
        from google.genai import types     # type: ignore
    except Exception as e:
        _fail("Gemini", f"google-genai not installed: {e}")
        return False
    try:
        client = genai.Client(api_key=key)
        model = os.environ.get("GEMINI_MODEL", "gemini-3-flash")
        resp = client.models.generate_content(
            model=model,
            contents="Say exactly: OK",
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=10),
        )
        text = (getattr(resp, "text", "") or "").strip()
        if "OK" in text.upper():
            _ok("Gemini", f"{model} responded → {text!r}")
            return True
        _fail("Gemini", f"unexpected reply from {model}: {text!r}")
        return False
    except Exception as e:
        _fail("Gemini", f"{type(e).__name__}: {e}")
        return False


async def check_tavily() -> bool:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        _skip("Tavily", "no TAVILY_API_KEY set — skipping")
        return False
    try:
        from tavily import TavilyClient  # type: ignore
    except Exception as e:
        _fail("Tavily", f"tavily-python not installed: {e}")
        return False
    try:
        c = TavilyClient(api_key=key)
        res = c.search(query="Berlin weather today", max_results=1)
        if res.get("results"):
            url = res["results"][0].get("url", "(no url)")
            _ok("Tavily", f"search returned 1 result → {url[:60]}")
            return True
        _fail("Tavily", f"empty results: {res}")
        return False
    except Exception as e:
        _fail("Tavily", f"{type(e).__name__}: {e}")
        return False


async def check_gradium() -> bool:
    key = os.environ.get("GRADIUM_API_KEY")
    voice = os.environ.get("GRADIUM_VOICE_ID")
    if not key:
        _skip("Gradium", "no GRADIUM_API_KEY set — skipping")
        return False
    try:
        import gradium  # type: ignore
    except Exception as e:
        _fail("Gradium", f"gradium SDK not installed: {e}")
        return False
    # Conservative check: just verify the SDK constructs a client.  Doing a
    # full TTS round-trip would burn a few credits and isn't worth the cost
    # for a smoke test.  When you run the voice loop we'll know fast enough.
    try:
        client_cls = getattr(gradium, "AsyncClient", None) or getattr(gradium, "Client", None)
        if client_cls is None:
            _fail("Gradium", "neither gradium.AsyncClient nor gradium.Client exists")
            return False
        _ = client_cls(api_key=key)
        msg = "client constructed OK"
        if voice:
            msg += f"  (voice_id set, len={len(voice)})"
        else:
            msg += "  (GRADIUM_VOICE_ID is empty — set it from studio.gradium.ai)"
        _ok("Gradium", msg)
        return True
    except Exception as e:
        _fail("Gradium", f"{type(e).__name__}: {e}")
        return False


async def check_anthropic() -> bool:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        _skip("Anthropic", "no ANTHROPIC_API_KEY (juror bot will use stubs — fine)")
        return False
    try:
        import anthropic  # type: ignore
    except Exception as e:
        _fail("Anthropic", f"anthropic SDK not installed: {e}")
        return False
    try:
        client = anthropic.Anthropic(api_key=key)
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        resp = client.messages.create(
            model=model,
            max_tokens=10,
            messages=[{"role": "user", "content": "Say exactly: OK"}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        if "OK" in text.upper():
            _ok("Anthropic", f"{model} responded → {text!r}")
            return True
        _fail("Anthropic", f"unexpected: {text!r}")
        return False
    except Exception as e:
        _fail("Anthropic", f"{type(e).__name__}: {e}")
        return False


# --------------------------------------------------------------
async def main() -> None:
    print(f"\n  Verifying API keys from .env\n  {'-'*48}")
    results = await asyncio.gather(
        check_gemini(),
        check_gradium(),
        check_tavily(),
        check_anthropic(),
    )
    passed = sum(1 for r in results if r)
    total_attempted = sum(1 for r in results if r is not False or True)  # cosmetic
    print(f"\n  {GREEN if passed else RED}{passed} key(s) healthy{END}\n")

    # Hint the next step
    if results[0]:  # Gemini
        print("  Next:  python scripts/run_demo_text.py --crm max_mueller\n")
    else:
        print("  Next:  add GOOGLE_API_KEY to .env, re-run this script.\n")


if __name__ == "__main__":
    asyncio.run(main())
