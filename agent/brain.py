"""Provider-pluggable conversational brain.

Default behavior is now GEMINI-FIRST and GEMINI-LOCKED for consistency.
If Gemini is unavailable, we stay on the Gemini stub unless you explicitly
opt in to cross-provider fallback with:

    BRAIN_ALLOW_NON_GEMINI_FALLBACK=1

This avoids accidental drift between model families (Gemini vs llama/gpt)
during tuning and evaluation.  Providers available:

    BRAIN_PROVIDER=gemini    (default — agent.gemini_client.GeminiBrain)
    BRAIN_PROVIDER=ollama    (local llama3 / qwen, no quota)
    BRAIN_PROVIDER=openai    (if you have an OpenAI key)

All brains expose the same async interface:

    async for chunk in brain.stream_reply(system_prompt, history, user_msg):
        ...

`make_brain()` reads BRAIN_PROVIDER and returns the right one.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from typing import Any, Protocol


class Brain(Protocol):
    """The minimal interface every brain implementation must satisfy."""

    model_name: str
    _real: bool   # True if the brain is wired to a live LLM, False = stub

    def stream_reply(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_message: str,
    ) -> AsyncIterator[str]: ...


def _try_gemini() -> Brain | None:
    try:
        from .gemini_client import GeminiBrain
        b = GeminiBrain()
        # The stub falls back to deterministic replies when no key.  We
        # still return it for offline development; callers can check ._real.
        return b
    except Exception as e:
        print(f"  [brain] gemini unavailable: {e}", file=sys.stderr)
        return None


def _try_ollama() -> Brain | None:
    try:
        from .ollama_brain import OllamaBrain
        b = OllamaBrain()
        if not b.probe_sync():
            print(
                f"  [brain] ollama not reachable at {b.base_url} "
                f"or model '{b.model_name}' not pulled — falling through",
                file=sys.stderr,
            )
            return None
        return b
    except Exception as e:
        print(f"  [brain] ollama unavailable: {e}", file=sys.stderr)
        return None


def _try_openai() -> Brain | None:
    try:
        from .openai_brain import OpenAIBrain
        b = OpenAIBrain()
        if not b._real:
            return None
        return b
    except Exception as e:
        print(f"  [brain] openai unavailable: {e}", file=sys.stderr)
        return None


_FACTORIES = {
    "gemini": _try_gemini,
    "ollama": _try_ollama,
    "openai": _try_openai,
}


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _allow_non_gemini_fallback() -> bool:
    return _is_truthy(os.environ.get("BRAIN_ALLOW_NON_GEMINI_FALLBACK"))


def make_brain(prefer: str | None = None) -> Brain:
    """Return a usable brain.

    Behavior:
      1) Try preferred provider first.
      2) If preferred is Gemini and fallback is NOT explicitly enabled,
         stay on Gemini (live or stub) and do not silently switch provider.
      3) If fallback is enabled, try remaining providers.
      4) Worst case, return Gemini stub so local flows still run.
    """
    preferred = prefer or os.environ.get("BRAIN_PROVIDER", "gemini")
    order = [preferred]
    for k in ("gemini", "ollama", "openai"):
        if k not in order:
            order.append(k)

    # Strict-by-default Gemini path: no cross-provider drift unless opt-in.
    if preferred == "gemini" and not _allow_non_gemini_fallback():
        g = _try_gemini()
        if g is not None:
            if getattr(g, "_real", False):
                print(f"  [brain] using gemini: {g.model_name}", file=sys.stderr)
            else:
                print(
                    "  [brain] gemini key/model unavailable — using gemini stub "
                    "(set BRAIN_ALLOW_NON_GEMINI_FALLBACK=1 to allow ollama/openai)",
                    file=sys.stderr,
                )
            return g

    for prov in order:
        if prov not in _FACTORIES:
            continue
        b = _FACTORIES[prov]()
        if b is not None and getattr(b, "_real", False):
            print(f"  [brain] using {prov}: {b.model_name}", file=sys.stderr)
            return b

    # Worst case — return the gemini stub so the demo runs without keys.
    fallback = _try_gemini()
    if fallback is not None:
        print(f"  [brain] no live provider — falling back to stub", file=sys.stderr)
        return fallback
    raise RuntimeError("no brain available — install google-genai at minimum")
