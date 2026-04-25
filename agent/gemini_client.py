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

import os
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


DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash")


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
        self.tools = tools or []
        self._real = bool(self.api_key) and _HAVE_GENAI
        self._client: Any = None
        if self._real:
            # google-genai picks up GOOGLE_API_KEY from env automatically
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

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.85,
            tools=self.tools or None,
            response_modalities=["TEXT"],
        )

        # google-genai exposes both sync and async streaming; we use async.
        stream = await self._client.aio.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=config,
        )
        async for chunk in stream:
            text = getattr(chunk, "text", None)
            if text:
                yield text


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
