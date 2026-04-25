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


# gemini-3-flash isn't on the public v1beta endpoint as of April 2026 — fall
# back to gemini-2.5-flash, which is the actual latency-optimized model.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


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

        # google-genai 1.73.x: aio.models.generate_content_stream is an
        # async function that returns an AsyncIterator.  Confirmed by
        # introspection (iscoroutinefunction=True, returns AsyncIterator).
        # Must `await` first, then `async for` over the result.
        #
        # We wrap with a small retry loop because Gemini's free tier has a
        # tight RPM cap that's easy to brush in a real call.  Backoff is
        # cheap; failing the demo isn't.
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                stream = await self._client.aio.models.generate_content_stream(
                    model=self.model_name,
                    contents=contents,
                    config=config,
                )
                async for chunk in stream:
                    text = getattr(chunk, "text", None)
                    if text:
                        yield text
                return
            except Exception as e:
                last_err = e
                msg = str(e)
                # 429 = rate limit, 503 = backend overload — both worth retrying.
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "503" in msg or "UNAVAILABLE" in msg:
                    delay = (2 ** attempt) + random.uniform(0, 0.4)
                    await asyncio.sleep(delay)
                    continue
                # other errors (auth, model-not-found, bad request) fail fast
                raise
        # exhausted all retries — fall back to a brief filler so the demo
        # doesn't dead-air.  Better to look human-imperfect than to crash.
        yield "Sorry, just one second — my system is being a bit slow…"
        if last_err:
            raise last_err


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
