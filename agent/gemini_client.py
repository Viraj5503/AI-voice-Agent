"""Thin wrapper around Gemini 3 Flash via google-genai.

We deliberately keep this layer tiny so the swap to Gemini 3 Pro (or back to
2.5) is one config flip.  Streaming is the default — the voice loop pushes
each token to Gradium TTS as it arrives.

If GOOGLE_API_KEY is not set, we transparently fall back to a deterministic
echo-stub LLM so the rest of the system can be developed and demoed without
keys.  The stub is good enough to exercise the GLiNER2 → bridge → dashboard
path.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from collections.abc import AsyncIterator
from typing import Any

# google-genai is optional at runtime.  Guarded import so the rest of the
# project still imports without it (e.g. on a CI box that only runs lint).
try:
    from google import genai          # type: ignore
    from google.genai import types     # type: ignore
    _HAVE_GENAI = True
except Exception:  # pragma: no cover - graceful degradation
    _HAVE_GENAI = False


# gemini-flash-latest is a Google-maintained alias that always routes to the
# current best public Flash model.  Two reasons we prefer the alias over a
# pinned version: (a) per-model rate limits hot-spot — when you're 429-d on
# gemini-2.5-flash you're often still fine on the alias because it routes
# elsewhere; (b) Google silently deprecates pinned versions ("This model is
# no longer available to new users" — observed live on gemini-2.0-flash).
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

# Each model has its OWN per-day and per-minute quota bucket.  When we've
# hammered one model and it 429s persistently, rotating to a different
# model usually works.  Order: cheapest/fastest first.
_FALLBACK_MODELS = [
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-1.5-flash",
    "gemini-pro-latest",
    "gemini-2.5-pro",
]


class GeminiBrain:
    """Streaming chat against Gemini 3 Flash with system-prompt + tool support."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        tools: list[Any] | None = None,
    ) -> None:
        self.model_name = model or DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        # Tools wiring is intentionally deferred for the text demo.  The
        # voice loop in voice/livekit_agent.py adds Tavily callables here
        # once we have the function-calling round-trip working.
        self.tools = tools or []
        self._real = bool(self.api_key) and _HAVE_GENAI
        self._client: Any = None
        if self._real:
            # google-genai picks up GOOGLE_API_KEY from env automatically,
            # but we pass it explicitly so .env-loaded keys win over shell.
            self._client = genai.Client(api_key=self.api_key)

    # --------------------------------------------------------------
    async def stream_reply(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_message: str,
    ) -> AsyncIterator[str]:
        """Yield text chunks of Jamie's next reply."""
        if not self._real:
            async for chunk in _stub_stream(user_message):
                yield chunk
            return

        contents: list[Any] = []
        for turn in history:
            role = "user" if turn["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=turn["text"])]))
        contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

        # NOTE: we deliberately do NOT pass tools here for the text demo;
        # mid-stream function-call handling needs a request/response loop
        # that's wired in voice/livekit_agent.py for the production stack.
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.85,
        )

        # google-genai 1.73.x: aio.models.generate_content_stream is an async
        # function that returns an AsyncIterator (introspected — must await).
        #
        # Resilience strategy, in order:
        #  1. Backoff on the configured model: 2s, 6s, 14s.  Free-tier RPM
        #     resets per minute so ~22s of waiting handles transient bursts.
        #  2. If still 429 after that, ROTATE to a different model.  Each
        #     model has its own per-minute and per-day quota bucket, so a
        #     daily-quota exhaustion on gemini-flash-latest doesn't block
        #     gemini-2.5-flash etc.
        #  3. If every fallback also 429s, raise with a clear message so
        #     the caller can show it (we no longer hide errors as bare
        #     "ClientError" strings).
        #
        # Per-attempt logging: each retry prints to stderr so the user sees
        # what's happening instead of staring at a hung terminal.

        BACKOFFS = (2.0, 6.0, 14.0)
        last_err: Exception | None = None

        # Build the model rotation: configured model first, then fallbacks
        # we haven't already tried.
        rotation: list[str] = [self.model_name]
        for m in _FALLBACK_MODELS:
            if m and m not in rotation:
                rotation.append(m)

        for model_idx, model in enumerate(rotation):
            for attempt, base in enumerate(BACKOFFS):
                try:
                    stream = await self._client.aio.models.generate_content_stream(
                        model=model,
                        contents=contents,
                        config=config,
                    )
                    got_any = False
                    async for chunk in stream:
                        text = getattr(chunk, "text", None)
                        if text:
                            got_any = True
                            yield text
                    if got_any:
                        if model != self.model_name:
                            print(
                                f"  [gemini] success on fallback model {model!r} "
                                f"(your configured {self.model_name!r} was throttled)",
                                file=sys.stderr,
                            )
                        return
                    # empty stream — rare; treat as transient
                    if attempt < len(BACKOFFS) - 1:
                        await asyncio.sleep(base + random.uniform(0, 0.4))
                        continue
                    break  # try next model
                except Exception as e:
                    last_err = e
                    short = str(e).split("\n")[0][:160]
                    rate_limited = (
                        "429" in short or "RESOURCE_EXHAUSTED" in short
                        or "503" in short or "UNAVAILABLE" in short
                    )
                    if rate_limited:
                        if attempt < len(BACKOFFS) - 1:
                            delay = base + random.uniform(0, 0.4)
                            print(
                                f"  [gemini] {model} rate-limited "
                                f"(attempt {attempt + 1}/{len(BACKOFFS)}); "
                                f"retrying in {delay:.1f}s.  Detail: {short}",
                                file=sys.stderr,
                            )
                            await asyncio.sleep(delay)
                            continue
                        # last attempt on this model — fall through to next
                        if model_idx < len(rotation) - 1:
                            print(
                                f"  [gemini] {model} exhausted; rotating to "
                                f"{rotation[model_idx + 1]!r}",
                                file=sys.stderr,
                            )
                        break
                    # non-rate-limit error: don't retry, fail loud
                    print(
                        f"  [gemini] non-retryable error on {model}: {short}",
                        file=sys.stderr,
                    )
                    raise

        # All models / all retries exhausted.  Yield a graceful filler so
        # the call doesn't dead-air, then surface the real error in the
        # raised exception (callers should print str(exc), not type(exc)).
        yield (
            "Sorry, my system is being a bit slow today — "
            "give me just a second and I'll pull that up."
        )
        msg = (
            "all Gemini models exhausted (rate limit / daily quota).  "
            "Wait a few minutes, run scripts/diagnose_gemini.py to see "
            "which models still work, or upgrade quota at "
            "https://aistudio.google.com/app/usage"
        )
        if last_err:
            raise RuntimeError(f"{msg}.  Last error: {last_err}") from last_err
        raise RuntimeError(msg)


# ----- stub fallback -------------------------------------------------------

_STUB_REPLIES = [
    "Oh gosh, I'm so sorry to hear that. First — are you physically okay? Anyone hurt at all?",
    "Okay, that's a relief. Where exactly were you when it happened? An address or road name is fine.",
    "Right, got that. And the car — is it still drivable, or has it been towed somewhere?",
    "Okay. Was anyone else involved — another vehicle, a pedestrian?",
    "Mm-hmm, noting that. Were the police called to the scene at all?",
    "Got it, thank you. Just give me one second to type all that into the report…",
]


async def _stub_stream(user_message: str):
    """Deterministic round-robin reply so we can iterate without an LLM."""
    import asyncio
    idx = abs(hash(user_message)) % len(_STUB_REPLIES)
    reply = _STUB_REPLIES[idx]
    for word in reply.split():
        yield word + " "
        await asyncio.sleep(0.02)
