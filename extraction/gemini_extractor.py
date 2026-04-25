"""Pillar extraction via Gemini structured output.

Why this exists alongside `gliner2_service.py`:

  - GLiNER2 is fast and free but its bi-encoder needs careful label
    tuning; on noisy real-world transcripts it under-extracts.
  - Gemini-Lite, even on a slammed free tier, is a reliable extractor
    if we hit it sparingly (one call per caller turn, batched).
  - The Pioneer / Fastino bounty narrative WANTS this comparison —
    "free GLiNER for speed, paid LLM for accuracy, here's the F1
    benchmark."

We use Gemini-Lite specifically (not Flash or Pro) because:
  - It has its own quota bucket (we observed it surviving when Flash
    was 429-throttled).
  - $0.00001875 / $0.000075 per 1k tok in/out — basically free.
  - Latency ~250-400ms.

Falls back gracefully to the GLiNER service if the Gemini call fails.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from .gliner2_service import (
    CLAIM_LABELS, FRAUD_LABELS, ExtractionService as _GLiNERService,
    HUMAN_TO_ID, FRAUD_HUMAN_TO_ID,
)


_EXTRACT_PROMPT = """\
You are a strict information extractor.  Read the SHORT transcript chunk \
below — it is one or two sentences from a phone call about an insurance claim.

Extract ONLY information that is explicitly stated.  Do NOT infer, do NOT \
fill in details that aren't there.  If a label has nothing to extract, \
omit it entirely from the output (do NOT write "null" or "unknown").

Return a single JSON object with these allowed keys (snake_case):
  accident_date, accident_time, accident_location, road_type,
  weather_conditions, other_party_plate, other_party_name, other_party_insurer,
  police_case_number, injury_description, vehicle_drivable, fault_admission,
  witness_name, damage_description, settlement_preference,
  delayed_reporting, known_to_other_party, vehicle_listed_for_sale,
  prior_similar_incident, timeline_inconsistency.

For each present key the value is the literal text snippet from the
transcript that supports it (verbatim, no paraphrasing).

Transcript:
"""


class GeminiExtractor:
    """Drop-in replacement for ExtractionService.extract() using Gemini-Lite."""

    def __init__(
        self,
        model: str = "gemini-2.5-flash-lite",
        fallback: _GLiNERService | None = None,
    ) -> None:
        self.model = model
        self._fallback = fallback or _GLiNERService()
        self._enabled = bool(os.environ.get("GOOGLE_API_KEY"))
        self._client: Any = None
        if self._enabled:
            try:
                from google import genai  # type: ignore
                self._client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
            except Exception:
                self._enabled = False

    @property
    def mode(self) -> str:
        if self._enabled:
            return f"gemini-extract ({self.model})"
        return self._fallback.mode

    @property
    def model_name(self) -> str:
        return self.model if self._enabled else (self._fallback.model_name or "")

    # -----------------------------------------------------------------
    def extract(self, text: str) -> dict[str, Any]:
        """Synchronous extraction — pillar IDs only, falls back to GLiNER."""
        t0 = time.perf_counter()
        if not self._enabled:
            return self._fallback.extract(text)

        try:
            from google.genai import types  # type: ignore
            resp = self._client.models.generate_content(
                model=self.model,
                contents=_EXTRACT_PROMPT + text,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                    max_output_tokens=600,
                ),
            )
            raw = (getattr(resp, "text", "") or "").strip()
            data = json.loads(raw) if raw else {}
        except Exception:
            # Quota / network / parse failure → use the cheap fallback so the
            # demo never has empty pillars on the dashboard.
            return self._fallback.extract(text)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        pillars: dict[str, dict[str, Any]] = {}
        fraud: dict[str, dict[str, Any]] = {}
        for k, v in data.items():
            if not isinstance(v, str) or not v.strip():
                continue
            if k in CLAIM_LABELS:
                pillars[k] = {"label": k, "text": v.strip(), "score": 0.95}
            elif k in FRAUD_LABELS:
                fraud[k] = {"label": k, "text": v.strip(), "score": 0.9}
        return {
            "pillars": pillars,
            "fraud": fraud,
            "elapsed_ms": round(elapsed_ms, 2),
            "mode": "gemini-extract",
            "model": self.model,
        }


# --- self-test -------------------------------------------------------------
if __name__ == "__main__":
    from dotenv import load_dotenv  # type: ignore
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    e = GeminiExtractor()
    print("mode:", e.mode)
    out = e.extract(
        "Hi, no one's hurt but my car got hit on the A4 near Köln-Ost about 30 minutes ago."
    )
    print(json.dumps(out, indent=2, ensure_ascii=False))
