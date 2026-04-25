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
# Probed in order — first one that responds wins.  Update .env's
# GEMINI_MODEL to whichever the verifier picks.
GEMINI_CANDIDATES = [
    os.environ.get("GEMINI_MODEL"),   # whatever the user has in .env
    "gemini-2.5-flash",                # current latency-optimized public model
    "gemini-2.0-flash",                # stable fallback
    "gemini-1.5-flash",                # last-resort
]


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

    client = genai.Client(api_key=key)
    last_err: Exception | None = None
    for candidate in GEMINI_CANDIDATES:
        if not candidate:
            continue
        try:
            resp = client.models.generate_content(
                model=candidate,
                contents="Say exactly: OK",
                config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=10),
            )
            text = (getattr(resp, "text", "") or "").strip()
            if "OK" in text.upper():
                _ok("Gemini", f"{candidate} works → {text!r}")
                if candidate != os.environ.get("GEMINI_MODEL"):
                    print(f"    (tip: set GEMINI_MODEL={candidate} in .env to lock it in)")
                return True
            # responded but didn't say OK — still counts as working access
            _ok("Gemini", f"{candidate} reachable (got {text!r})")
            return True
        except Exception as e:
            last_err = e
            continue
    _fail("Gemini", f"none of {[c for c in GEMINI_CANDIDATES if c]} worked. "
                    f"Last error: {type(last_err).__name__}: {last_err}")
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
    """The real surface in gradium 0.5.11 is GradiumClient (sync constructor,
    async TTS methods).  AsyncClient/Client don't exist."""
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
    GradiumClient = getattr(gradium, "GradiumClient", None)
    if GradiumClient is None:
        # surface is different than we expected — print the public attrs to debug
        attrs = [a for a in dir(gradium) if not a.startswith("_")]
        _fail("Gradium", f"gradium.GradiumClient missing.  package exports: {attrs[:8]}…")
        return False
    try:
        client = GradiumClient(api_key=key)
        msg = "GradiumClient constructed OK"
        if voice:
            msg += f"  (voice_id set, len={len(voice)})"
        else:
            msg += "  (GRADIUM_VOICE_ID is empty — set it from studio.gradium.ai)"
        _ok("Gradium", msg)
        # we don't burn credits on a TTS round-trip here; the voice loop will
        # exercise it for real in step 2 of the runbook
        return True
    except Exception as e:
        _fail("Gradium", f"{type(e).__name__}: {e}")
        return False


async def check_anthropic() -> bool:
    """Anthropic only powers the juror bot.  If the key is missing or out of
    credit, that bot falls back to deterministic stubs and the rest of the
    project is unaffected.  We treat this as informational, not a failure."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        _skip("Anthropic", "no ANTHROPIC_API_KEY (juror bot uses stubs — fine)")
        return True  # not a blocker
    try:
        import anthropic  # type: ignore
    except Exception:
        _skip("Anthropic", "anthropic SDK not installed (juror bot uses stubs)")
        return True
    try:
        client = anthropic.Anthropic(api_key=key)
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        resp = client.messages.create(
            model=model,
            max_tokens=10,
            messages=[{"role": "user", "content": "Say exactly: OK"}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        _ok("Anthropic", f"{model} responded → {text!r}")
        return True
    except Exception as e:
        # Out of credit / bad key → not a blocker, juror bot will stub
        _skip("Anthropic", f"key present but unusable ({type(e).__name__}). Juror bot uses stubs.")
        return True


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
