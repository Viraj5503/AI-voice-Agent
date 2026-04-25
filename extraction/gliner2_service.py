"""GLiNER2 zero-shot extractor for FNOL transcripts.

Primary model:   fastino/gliner2-base-v1   (Pioneer-aligned)
Fallback model:  knowledgator/gliner-bi-large-v2.0  (community baseline)

We expose two surfaces:

  ExtractionService.extract(text) -> dict
      Pull the claim pillars + fraud signals out of one transcript chunk.

  run_async_extractor(stream, on_update)
      Long-running coroutine that consumes a transcript stream and pushes
      pillar updates to a callback (the bridge fan-out, in production).

If `gliner` is not installed (e.g. in CI), the service falls back to a regex
heuristic extractor so the rest of the pipeline still works.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable, Awaitable
from dataclasses import dataclass
from typing import Any

# GLiNER works best with NATURAL-LANGUAGE entity labels, not snake_case.
# We keep the canonical pillar IDs (snake_case, used everywhere else in the
# codebase) and a parallel list of human-readable phrasings GLiNER actually
# understands.  predict_entities() uses the human labels; we map back to
# the canonical IDs before returning.
CLAIM_LABELS: list[str] = [
    "accident_date",
    "accident_time",
    "accident_location",
    "road_type",
    "weather_conditions",
    "other_party_plate",
    "other_party_name",
    "other_party_insurer",
    "police_case_number",
    "injury_description",
    "vehicle_drivable",
    "fault_admission",
    "witness_name",
    "damage_description",
    "settlement_preference",
]

# Human-readable label → canonical ID.  These phrasings are what we feed
# into GLiNER.predict_entities() — bi-encoder GLiNERs match the entity
# label embedding against text embeddings, so vague snake_case labels
# (police_case_number) match almost nothing while "police case number"
# matches reliably.  Threshold also drops from 0.45 → 0.30 because the
# bi-encoder's natural similarities run lower than the cross-encoder's.
HUMAN_TO_ID: dict[str, str] = {
    "date of the accident":        "accident_date",
    "time of the accident":        "accident_time",
    "accident location":           "accident_location",
    "road type":                   "road_type",
    "weather conditions":          "weather_conditions",
    "other vehicle license plate": "other_party_plate",
    "other driver's name":         "other_party_name",
    "other party insurer":         "other_party_insurer",
    "police case number":          "police_case_number",
    "injury":                      "injury_description",
    "vehicle drivable":            "vehicle_drivable",
    "admission of fault":          "fault_admission",
    "witness name":                "witness_name",
    "vehicle damage":              "damage_description",
    "preferred repair shop":       "settlement_preference",
}

FRAUD_LABELS: list[str] = [
    "delayed_reporting",
    "known_to_other_party",
    "vehicle_listed_for_sale",
    "prior_similar_incident",
    "timeline_inconsistency",
]

FRAUD_HUMAN_TO_ID: dict[str, str] = {
    "delayed reporting":              "delayed_reporting",
    "known relationship to other party": "known_to_other_party",
    "vehicle listed for sale":         "vehicle_listed_for_sale",
    "prior similar incident":          "prior_similar_incident",
    "timeline inconsistency":          "timeline_inconsistency",
}


_MODEL_CANDIDATES = [
    "fastino/gliner2-base-v1",
    "knowledgator/gliner-bi-large-v2.0",
    "knowledgator/gliner-multitask-large-v0.5",
]


@dataclass
class Extraction:
    label: str
    text: str
    score: float


class ExtractionService:
    """Wraps GLiNER2 with sane defaults and a regex fallback."""

    def __init__(self, model_name: str | None = None, threshold: float = 0.30) -> None:
        self.threshold = threshold
        self.model_name: str | None = None
        self._model: Any = None
        self._mode: str = "stub"

        try:
            from gliner import GLiNER  # type: ignore

            for candidate in ([model_name] if model_name else _MODEL_CANDIDATES):
                if not candidate:
                    continue
                try:
                    self._model = GLiNER.from_pretrained(candidate)
                    self.model_name = candidate
                    self._mode = "gliner"
                    break
                except Exception:
                    continue
        except Exception:
            # gliner not installed at all — stay in stub mode
            self._model = None

    @property
    def mode(self) -> str:
        """'gliner' if a real model is loaded, else 'stub'."""
        return self._mode

    # -----------------------------------------------------------------
    def extract(self, text: str) -> dict[str, Any]:
        """Pull pillar + fraud entities from one transcript chunk."""
        t0 = time.perf_counter()
        pillars: dict[str, Extraction] = {}
        fraud: dict[str, Extraction] = {}

        if self._mode == "gliner" and self._model is not None:
            # Feed GLiNER the natural-language phrasings; map back to canonical IDs.
            human_labels = list(HUMAN_TO_ID.keys()) + list(FRAUD_HUMAN_TO_ID.keys())
            ents = self._model.predict_entities(
                text, human_labels, threshold=self.threshold
            )
            for e in ents:
                human = e["label"]
                lab = HUMAN_TO_ID.get(human) or FRAUD_HUMAN_TO_ID.get(human)
                if not lab:
                    continue
                ex = Extraction(label=lab, text=e["text"], score=float(e.get("score", 0.0)))
                bucket = fraud if lab in FRAUD_LABELS else pillars
                # keep highest-confidence per label
                if lab not in bucket or bucket[lab].score < ex.score:
                    bucket[lab] = ex
        else:
            # regex / heuristic fallback so pipeline still runs in dev
            for lab, ext in _stub_extract(text):
                bucket = fraud if lab in FRAUD_LABELS else pillars
                if lab not in bucket or bucket[lab].score < ext.score:
                    bucket[lab] = ext

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return {
            "pillars": {k: v.__dict__ for k, v in pillars.items()},
            "fraud": {k: v.__dict__ for k, v in fraud.items()},
            "elapsed_ms": round(elapsed_ms, 2),
            "mode": self._mode,
            "model": self.model_name,
        }


# ----- async streaming wrapper --------------------------------------------
async def run_async_extractor(
    transcript_stream: AsyncIterator[str],
    on_update: Callable[[dict[str, Any]], Awaitable[None]],
    chunk_words: int = 20,
    service: ExtractionService | None = None,
) -> None:
    """Consume a stream of transcript text and emit extraction events."""
    svc = service or ExtractionService()
    buffer: list[str] = []
    async for piece in transcript_stream:
        buffer.append(piece)
        words = " ".join(buffer).split()
        if len(words) < chunk_words:
            continue
        chunk = " ".join(words[-chunk_words * 2 :])  # short rolling context
        result = await asyncio.to_thread(svc.extract, chunk)
        if result["pillars"] or result["fraud"]:
            await on_update(result)
        # don't reset; we keep a rolling buffer so split entities aren't lost
        buffer = [chunk]


# ----- regex fallback so this module is useful before the model downloads -
_RE_PLATE = re.compile(r"\b([A-ZÄÖÜ]{1,3}-[A-Z]{1,2}\s?\d{1,4})\b")
_RE_DATE = re.compile(r"\b(\d{1,2}\.\d{1,2}\.\d{2,4}|today|yesterday)\b", re.I)
_RE_TIME = re.compile(r"\b(\d{1,2}:\d{2})\b")
_RE_AUTOBAHN = re.compile(r"\b(A\d{1,3}|B\d{1,3}|Bundesstraße\s?\d+)\b")
_RE_INJURY = re.compile(
    r"\b(whiplash|broken\s\w+|bleeding|hospital|ambulance|hurt|injured|"
    r"Schmerz|verletzt|Krankenhaus)\b",
    re.I,
)
_RE_DRIVABLE_NEG = re.compile(r"\b(not\s+drivable|kann\s+nicht\s+fahren|totaled?)\b", re.I)
_RE_DRIVABLE_POS = re.compile(r"\b(drivable|still\s+drives|still\s+drove\s+home)\b", re.I)
_RE_POLICE = re.compile(r"\b(police|Polizei|case\s+number\s*[:#]?\s*\w+)\b", re.I)
_RE_WEATHER = re.compile(r"\b(rain|snow|fog|ice|sun|clear|storm|Regen|Schnee|Eis)\b", re.I)
_RE_DELAY = re.compile(r"\b(\d+)\s+(weeks?|months?|days?)\s+ago\b", re.I)


def _stub_extract(text: str) -> list[tuple[str, Extraction]]:
    out: list[tuple[str, Extraction]] = []
    for m in _RE_PLATE.finditer(text):
        out.append(("other_party_plate", Extraction("other_party_plate", m.group(1), 0.6)))
    for m in _RE_DATE.finditer(text):
        out.append(("accident_date", Extraction("accident_date", m.group(1), 0.5)))
    for m in _RE_TIME.finditer(text):
        out.append(("accident_time", Extraction("accident_time", m.group(1), 0.5)))
    for m in _RE_AUTOBAHN.finditer(text):
        out.append(("road_type", Extraction("road_type", m.group(1), 0.7)))
        out.append(("accident_location", Extraction("accident_location", m.group(1), 0.6)))
    for m in _RE_INJURY.finditer(text):
        out.append(("injury_description", Extraction("injury_description", m.group(0), 0.7)))
    for m in _RE_DRIVABLE_NEG.finditer(text):
        out.append(("vehicle_drivable", Extraction("vehicle_drivable", "no", 0.6)))
    for m in _RE_DRIVABLE_POS.finditer(text):
        out.append(("vehicle_drivable", Extraction("vehicle_drivable", "yes", 0.6)))
    for m in _RE_POLICE.finditer(text):
        out.append(("police_case_number", Extraction("police_case_number", m.group(0), 0.5)))
    for m in _RE_WEATHER.finditer(text):
        out.append(("weather_conditions", Extraction("weather_conditions", m.group(0), 0.6)))
    for m in _RE_DELAY.finditer(text):
        out.append(("delayed_reporting", Extraction("delayed_reporting", m.group(0), 0.6)))
    return out


# --- self-test -------------------------------------------------------------
if __name__ == "__main__":
    import json
    svc = ExtractionService()
    print("mode:", svc.mode, "model:", svc.model_name)
    sample = (
        "I was on the A4 around 14:30 today, it was pouring rain, "
        "and the other guy had a plate K-AB 1234. I think I have whiplash. "
        "The police came, the case number is 2026-04-25-7711."
    )
    print(json.dumps(svc.extract(sample), indent=2, ensure_ascii=False))
