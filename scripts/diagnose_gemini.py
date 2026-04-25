"""Detailed Gemini diagnostic — when verify_keys.py swallows the real cause.

Step 1: list all models the key has access to (ListModels endpoint).
Step 2: probe a few candidate model names with a tiny real call, printing
        the FULL error per attempt instead of only the last.

If your key is rate-limited you'll see 429 / RESOURCE_EXHAUSTED here even
when generate_content_stream returns 404.  If you see 404 on a model that
ListModels says you have access to, that's a real bug worth reporting.

Run:    python scripts/diagnose_gemini.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env", override=True)
except Exception:
    pass


def main() -> None:
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        print("  GOOGLE_API_KEY is not set.  Add it to .env and re-run.")
        sys.exit(1)

    try:
        from google import genai            # type: ignore
        from google.genai import types       # type: ignore
    except Exception as e:
        print(f"  google-genai not installed: {e}")
        sys.exit(2)

    print(f"\n  GOOGLE_API_KEY set (length {len(key)}).  Probing live API…")
    print(f"  GEMINI_MODEL in env: {os.environ.get('GEMINI_MODEL', '(unset)')}\n")

    client = genai.Client(api_key=key)

    # Step 1 — list models
    print("  ┌── Step 1: models your key can list ──────────────────────")
    available: list[str] = []
    try:
        for m in client.models.list():
            name = (getattr(m, "name", "") or "").replace("models/", "")
            methods = (
                getattr(m, "supported_actions", None)
                or getattr(m, "supported_generation_methods", None)
                or []
            )
            if "generateContent" in methods:
                available.append(name)
        for name in available[:25]:
            print(f"  │  ✓ {name}")
        if len(available) > 25:
            print(f"  │   … and {len(available) - 25} more")
        if not available:
            print("  │  (no models with generateContent — key has no Gemini API access)")
    except Exception as e:
        print(f"  │  ListModels failed: {type(e).__name__}: {e}")
        print("  │")
        print("  │  Most likely cause: this is a Google CLOUD project key for")
        print("  │  Vertex AI, not a Gemini AI Studio key.  Get a fresh AI Studio")
        print("  │  key at https://aistudio.google.com/app/apikey")
        return

    # Step 2 — probe candidate models, showing the full error per attempt
    print(f"\n  ┌── Step 2: probe candidate models ────────────────────────")
    candidates = [
        os.environ.get("GEMINI_MODEL"),
        "gemini-2.5-flash",
        "gemini-flash-latest",
        "gemini-2.0-flash",
    ]
    seen: set[str] = set()
    candidates = [c for c in candidates if c and c not in seen and not seen.add(c)]

    winner: str | None = None
    for cand in candidates:
        if cand not in available:
            print(f"  │  · {cand:30}  not in your account's model list — skipping")
            continue
        t0 = time.perf_counter()
        try:
            resp = client.models.generate_content(
                model=cand,
                contents="Say exactly: OK",
                config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=10),
            )
            ms = int((time.perf_counter() - t0) * 1000)
            text = (getattr(resp, "text", "") or "").strip()
            print(f"  │  ✓ {cand:30}  {ms}ms   reply={text!r}")
            if not winner:
                winner = cand
        except Exception as e:
            ms = int((time.perf_counter() - t0) * 1000)
            err = str(e)
            short = err.split("\n")[0][:120]
            print(f"  │  ✗ {cand:30}  {ms}ms   {type(e).__name__}: {short}")
            # 429 hint
            if "429" in err or "RESOURCE_EXHAUSTED" in err or "rate" in err.lower():
                print(f"  │      → rate-limited.  Wait ~60s and retry, or upgrade quota at")
                print(f"  │        https://aistudio.google.com/app/usage")
        # tiny pace between probes so we don't trigger RPM cap mid-diagnostic
        time.sleep(2)

    print()
    if winner:
        print(f"  Recommendation: set in .env →  GEMINI_MODEL={winner}")
        cur = os.environ.get("GEMINI_MODEL")
        if cur != winner:
            print(f"  (currently {cur!r} — change it)")
    else:
        print("  No candidate worked.  If ListModels showed models above but every")
        print("  generateContent call 4xx-ed, you're either rate-limited (wait & retry)")
        print("  or your key is on a tier that lacks Gemini access.  In that case,")
        print("  generate a fresh AI Studio key at https://aistudio.google.com/app/apikey")


if __name__ == "__main__":
    main()
