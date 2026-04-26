"""Synthetic FNOL training data generator for Pioneer / Fastino bounty.

Why this exists: GLiNER zero-shot scores ~0.32 F1 on our claim labels
(extraction/benchmark_results.json).  Pioneer's whole pitch is that
synthetic data + fine-tuning closes the gap to 0.85+.  This script is
the synthetic-data half of that pipeline.

Approach:
  We can't reliably ask an LLM for "tokenized_text + word indices" in
  one shot — token alignment between LLM output and downstream tokeniser
  is brittle.  Instead we ask for transcripts with INLINE markers
  ([[accident_location:A4 near Köln-Ost]]) and parse those markers
  into GLiNER's (start_word, end_word_inclusive, label) tuples.  The
  final transcript reads naturally; only the training-time annotations
  are computed.

Output: data/synthetic/fnol_train.jsonl  (one JSON record per line)

Run with:
  # Uses LLM_BASE_URL / LLM_API_KEY / LLM_MODEL from .env (any
  # OpenAI-compatible provider — Gemini-OpenAI, Groq, OpenAI, etc.).
  python extraction/synthetic_data.py --count 50

  # Override model on a flaky day:
  LLM_MODEL=gemini-2.5-flash-lite python extraction/synthetic_data.py --count 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env", override=True)
except Exception:
    pass


# Labels we care about — mirrors extraction.gliner2_service.CLAIM_LABELS +
# FRAUD_LABELS but kept here standalone so this file has no extraction
# import dependency at generation time.
TARGET_LABELS = [
    "accident_location",
    "accident_time",
    "weather_conditions",
    "road_type",
    "other_party_plate",
    "other_party_name",
    "other_party_insurer",
    "police_case_number",
    "injury_description",
    "vehicle_drivable",
    "fault_admission",
    "settlement_preference",
    "delayed_reporting",
    "known_to_other_party",
    "vehicle_listed_for_sale",
]


SYS_PROMPT = """You write synthetic German motor-insurance FNOL caller \
transcripts for training a small NER model.  You produce 5 short \
transcripts per call, each 1–4 sentences, voiced as a real person on a \
phone after a fender-bender.  Vary speakers, locations, times, fault \
patterns, weather, dialects.  Some calls report delayed damage; some \
admit known-to-other-party; some are clean fault-other.  Keep it \
realistic and varied — no repetition.

WITHIN each transcript, wrap any phrase that matches a target label \
with double-bracket markers:

  [[label:exact phrase from the text]]

Only use these labels (lowercase, snake_case):

  accident_location, accident_time, weather_conditions, road_type, \
other_party_plate, other_party_name, other_party_insurer, \
police_case_number, injury_description, vehicle_drivable, \
fault_admission, settlement_preference, delayed_reporting, \
known_to_other_party, vehicle_listed_for_sale

Rules:
  - Wrap only the phrase that IS the label, not surrounding context.
  - You may use multiple labels per transcript.  Some transcripts can \
have only 2-3; others 6-7.
  - Do not nest markers.  Do not invent labels.
  - Plate format: German style like "K-AB 1234", "B-MM 4421".
  - Output exactly 5 transcripts, separated by a blank line.  No \
numbering, no preamble, no markdown, no commentary.  Just the 5 \
transcripts."""


# Regex to find [[label:phrase]] markers — phrase can contain anything
# except `]]`.
_MARKER_RE = re.compile(r"\[\[([a-z_]+):([^\[\]]+?)\]\]")


def _parse_markers(annotated: str) -> tuple[list[str], list[tuple[int, int, str]]]:
    """Strip [[label:text]] markers, return (tokens, ner_spans).

    Each span is (start_word_idx, end_word_idx_inclusive, label).  We
    rebuild the plain text by replacing markers with their inner phrase,
    then split on whitespace, then map each replaced phrase back to its
    word range.

    Skips silently any marker whose label isn't in TARGET_LABELS or
    whose phrase doesn't survive the round-trip — robustness over
    perfectionism, since the LLM will occasionally produce malformed
    markers.
    """
    plain_parts: list[str] = []
    spans_pending: list[tuple[int, int, str]] = []  # (char_start, char_end, label)
    cursor = 0
    char_pos = 0
    for m in _MARKER_RE.finditer(annotated):
        plain_parts.append(annotated[cursor:m.start()])
        char_pos += len(annotated[cursor:m.start()])
        label = m.group(1).lower()
        phrase = m.group(2).strip()
        spans_pending.append((char_pos, char_pos + len(phrase), label))
        plain_parts.append(phrase)
        char_pos += len(phrase)
        cursor = m.end()
    plain_parts.append(annotated[cursor:])
    plain = "".join(plain_parts)

    # Convert character-range spans → word-range spans on whitespace tokenise.
    words = plain.split()
    # Compute a char range for each word.
    word_ranges: list[tuple[int, int]] = []
    pos = 0
    for w in words:
        # Skip whitespace before each word in the original
        while pos < len(plain) and plain[pos].isspace():
            pos += 1
        word_ranges.append((pos, pos + len(w)))
        pos += len(w)

    ner: list[tuple[int, int, str]] = []
    for cs, ce, label in spans_pending:
        if label not in TARGET_LABELS:
            continue
        # start_word: first word whose start is >= cs.  end_word: last
        # word whose start is < ce.  This is robust to trailing punctuation
        # ("rain.", "1234.") that gets attached to a word by whitespace
        # tokenisation — the punctuation slips into the entity span, which
        # is fine for training and consistent with how a downstream NER
        # consumer would see the same text.
        start_word: int | None = None
        end_word: int | None = None
        for i, (ws, _) in enumerate(word_ranges):
            if start_word is None and ws >= cs:
                start_word = i
            if ws < ce:
                end_word = i
        if start_word is None or end_word is None or end_word < start_word:
            continue  # alignment failed — skip silently
        ner.append((start_word, end_word, label))
    return words, ner


_BATCH_TOKENS = 2500  # ~5 transcripts of ~500 tokens each.  Bumping to
# 1500 truncates to 1 transcript; 4000+ wastes quota on long tails.


async def _generate_batch_openai(client, model: str) -> str:
    """Fetch one batch (5 transcripts) via OpenAI-compatible chat completions."""
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": "Generate 5 transcripts now."},
        ],
        max_tokens=_BATCH_TOKENS,
        temperature=0.95,
    )
    return (resp.choices[0].message.content or "").strip()


def _generate_batch_gemini(client, model: str) -> str:
    """Fetch one batch via google-genai (used elsewhere in the codebase).

    google-genai is sync, hence no async wrapper needed.  Model rotation
    is handled at the provider-list level by main().
    """
    from google.genai import types  # type: ignore
    resp = client.models.generate_content(
        model=model,
        contents=["Generate 5 transcripts now."],
        config=types.GenerateContentConfig(
            system_instruction=SYS_PROMPT,
            temperature=0.95,
            max_output_tokens=_BATCH_TOKENS,
        ),
    )
    return (resp.text or "").strip()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=50, help="approx. # of training examples to generate")
    parser.add_argument("--out", default=str(REPO / "data" / "synthetic" / "fnol_train.jsonl"))
    args = parser.parse_args()

    # Provider selection — try in order.  Each provider is a tuple of
    # (kind, label, generator-fn-or-async, *args).  The first one that
    # successfully returns a non-empty batch wins; any rate-limit / 429 /
    # 5xx automatically falls through to the next.
    #
    # Default order: Gemini direct (most reliable), then OpenAI-compat
    # via LLM_*, then OpenAI-compat via LLM_FALLBACK_*.  Reorder by
    # setting SYNTH_PREFER=openai if you want LLM_* tried first instead.
    providers: list[tuple[str, str, object, object]] = []

    google_key = os.environ.get("GOOGLE_API_KEY")
    if google_key:
        from google import genai  # type: ignore
        gem_client = genai.Client(api_key=google_key)
        # Model rotation — known-good aliases first (avoid retrying a
        # deprecated user-env model on every batch).  User's GEMINI_MODEL
        # is still tried as a last-resort override.
        gem_models = [
            "gemini-flash-latest",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            os.environ.get("GEMINI_MODEL"),
        ]
        seen: set[str] = set()
        for m in gem_models:
            if not m or m in seen:
                continue
            seen.add(m)
            providers.append(("gemini", f"gemini:{m}", gem_client, m))

    for prefix, label_suffix in [("LLM_", "primary"), ("LLM_FALLBACK_", "fallback")]:
        base = os.environ.get(f"{prefix}BASE_URL")
        api_key = os.environ.get(f"{prefix}API_KEY")
        model_name = os.environ.get(f"{prefix}MODEL")
        if not (base and api_key and model_name):
            continue
        final_base = base.rstrip("/")
        if not final_base.endswith("/v1"):
            final_base += "/v1"
        from openai import AsyncOpenAI  # type: ignore
        oai_client = AsyncOpenAI(api_key=api_key, base_url=final_base)
        providers.append(
            ("openai", f"openai-compat:{model_name}@{base} ({label_suffix})", oai_client, model_name)
        )

    if not providers:
        print("  [synth] no LLM configured.  Set GOOGLE_API_KEY, LLM_*, or LLM_FALLBACK_*.")
        sys.exit(2)

    if (os.environ.get("SYNTH_PREFER") or "").lower() == "openai":
        # Move openai-compat to the front
        providers.sort(key=lambda p: 0 if p[0] == "openai" else 1)

    print(f"  [synth] provider order: {[p[1] for p in providers]}", file=sys.stderr)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    async def _try_batch() -> str:
        """Try each provider in order until one returns text."""
        for kind, label, client, model_name in providers:
            try:
                if kind == "gemini":
                    # google-genai is sync; run in thread to keep async control.
                    text = await asyncio.to_thread(_generate_batch_gemini, client, model_name)
                else:
                    text = await _generate_batch_openai(client, model_name)
                if text:
                    return text
            except Exception as e:
                short = str(e).split("\n", 1)[0][:120]
                print(f"  [synth]   {label} failed: {type(e).__name__}: {short}",
                      file=sys.stderr)
                continue
        return ""

    # Each batch returns ~5 transcripts.  We add some headroom to hit the
    # requested count.
    batches_needed = max(1, args.count // 5 + 1)
    written = 0
    skipped = 0

    with out_path.open("w", encoding="utf-8") as f:
        for batch_idx in range(batches_needed):
            text = await _try_batch()
            if not text:
                print(f"  [synth] batch {batch_idx + 1} — every provider failed",
                      file=sys.stderr)
                continue
            transcripts = [t.strip() for t in re.split(r"\n\s*\n", text) if t.strip()]
            for tr in transcripts:
                if len(tr) < 30 or "[[" not in tr:
                    skipped += 1
                    continue
                words, ner = _parse_markers(tr)
                if len(ner) < 2:
                    skipped += 1
                    continue
                if written >= args.count:
                    break
                rec = {"tokenized_text": words, "ner": ner}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
            print(f"  [synth] batch {batch_idx + 1}/{batches_needed} → {written}/{args.count} written, {skipped} skipped",
                  flush=True)
            if written >= args.count:
                break

    print(f"\n  ✓ wrote {written} examples to {out_path}")
    print(f"    (skipped {skipped} malformed/sparse)")
    print()
    print("  Next: python extraction/finetune_gliner.py --epochs 2")


if __name__ == "__main__":
    asyncio.run(main())
