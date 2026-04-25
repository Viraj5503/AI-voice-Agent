"""Score a Jamie transcript for repetition + hallucination + naturalness.

Why: we can't iterate on Jamie's prompt by reading transcripts manually —
that's slow and biased.  This script asks Gemini-as-judge to score along
four explicit axes against the source transcript + the original CRM JSON.

Usage:
    # Score the most recent transcript
    python scripts/eval_jamie.py

    # Score a specific transcript
    python scripts/eval_jamie.py --file transcripts/max_rear_end_a4_2026...json

    # Score every transcript in transcripts/ and print the trend
    python scripts/eval_jamie.py --all

The judge is deliberately a DIFFERENT Gemini model than the brain (defaults
to gemini-2.5-pro vs. brain's gemini-flash-latest) — slower, more rigorous,
less likely to give Jamie a free pass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from statistics import mean

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env", override=True)
except Exception:
    pass


# gemini-2.5-pro is the most rigorous judge but its free-tier quota is
# tight (and the user's was already exhausted during the hackathon).
# gemini-2.5-flash-lite is the survivor — independent quota bucket and
# more than capable for the four scoring axes we care about.
JUDGE_MODEL = os.environ.get("GEMINI_JUDGE_MODEL", "gemini-2.5-flash-lite")
# Models we'll auto-fall-through to if the configured judge is throttled.
JUDGE_FALLBACKS = [
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]


JUDGE_PROMPT = """\
You are an expert insurance-claims supervisor reviewing a transcript of an
agent named Jamie taking a first-notice-of-loss phone call.  You also have
access to the CRM record Jamie was supposed to read from.

Score her performance on four axes, 0–10 (10 = perfect).  Be strict.  A
real supervisor would dock points for soft repetition, vague answers, or
inventing facts — do the same.

Return ONLY a JSON object, exact keys, integer scores 0–10:
{
  "no_repetition":      <score: 10 if Jamie never asks the same thing twice, 0 if she repeats every turn>,
  "no_hallucination":   <score: 10 if every CRM fact she quotes appears verbatim in the JSON, 0 if she invents anything>,
  "naturalness":        <score: 10 if she sounds like a warm human on a phone, 0 if scripted/robotic>,
  "completeness":       <score: 10 if she gathered the high-priority pillars (injuries, location, time, party, police, drivable) in the available turns, 0 if she missed half>,
  "issues":             [<list of short strings — one per concrete problem you found, e.g. "asked drivable in turn 3 and again in turn 4">]
}

CRM (the only authoritative source for caller / vehicle / coverage facts):
```json
{crm_json}
```

TRANSCRIPT:
{transcript_block}

Remember: ONLY the JSON object, no preamble, no markdown fence, no commentary.
"""


def find_latest_transcript() -> Path | None:
    d = REPO / "transcripts"
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def format_transcript(transcript: list[dict]) -> str:
    lines = []
    for i, t in enumerate(transcript, start=1):
        speaker = t["speaker"].upper()
        lines.append(f"[{i:02d}] {speaker}: {t['text']}")
    return "\n".join(lines)


def score_one(transcript_path: Path) -> dict:
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    crm_name = data["crm_profile"]
    crm = json.loads((REPO / "data" / "crm" / f"{crm_name}.json").read_text(encoding="utf-8"))

    # Use replace() not format() — the example JSON in JUDGE_PROMPT has
    # literal curly braces that confuse str.format.
    prompt = (
        JUDGE_PROMPT
        .replace("{crm_json}", json.dumps(crm, ensure_ascii=False, indent=2))
        .replace("{transcript_block}", format_transcript(data["transcript"]))
    )

    try:
        from google import genai            # type: ignore
        from google.genai import types       # type: ignore
    except Exception as e:
        return {"error": f"google-genai missing: {e}"}

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return {"error": "GOOGLE_API_KEY not set — can't run the judge"}

    client = genai.Client(api_key=api_key)
    # Walk the fallback list — quota exhaustion on one model is normal.
    seen: set[str] = set()
    rotation: list[str] = []
    for m in [JUDGE_MODEL] + JUDGE_FALLBACKS:
        if m and m not in seen:
            rotation.append(m); seen.add(m)
    last_err: Exception | None = None
    resp = None
    for model in rotation:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            break
        except Exception as e:
            last_err = e
            continue
    if resp is None:
        return {"error": f"all judge models exhausted; last: {type(last_err).__name__}: {last_err}"}

    raw = (getattr(resp, "text", "") or "").strip()
    try:
        scores = json.loads(raw)
    except Exception:
        # tolerate a code-fenced response
        cleaned = raw.strip("` \n").lstrip("json").strip()
        try:
            scores = json.loads(cleaned[cleaned.find("{"): cleaned.rfind("}") + 1])
        except Exception:
            return {"error": f"judge returned non-JSON: {raw[:200]}"}

    return scores


def print_scorecard(path: Path, scores: dict) -> None:
    print(f"\n{path.relative_to(REPO)}")
    print("-" * 60)
    if "error" in scores:
        print(f"  ✗ {scores['error']}")
        return
    for key in ("no_repetition", "no_hallucination", "naturalness", "completeness"):
        v = scores.get(key)
        bar = "█" * int(v) + "░" * (10 - int(v)) if isinstance(v, int) else "?"
        print(f"  {key:18} [{bar}] {v}/10")
    issues = scores.get("issues", []) or []
    if issues:
        print(f"  issues:")
        for i in issues:
            print(f"    • {i}")
    else:
        print("  issues: (none)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="path to a transcript JSON")
    parser.add_argument("--all", action="store_true",
                        help="score every transcript in transcripts/")
    args = parser.parse_args()

    if args.all:
        files = sorted((REPO / "transcripts").glob("*.json"))
        if not files:
            print("(no transcripts yet — run scripts/run_demo_auto.py first)")
            return
        all_scores: list[dict] = []
        for f in files:
            scores = score_one(f)
            print_scorecard(f, scores)
            if "error" not in scores:
                all_scores.append(scores)

        if all_scores:
            print("\nAVERAGES across", len(all_scores), "transcripts")
            print("-" * 60)
            for key in ("no_repetition", "no_hallucination",
                        "naturalness", "completeness"):
                vals = [s[key] for s in all_scores if isinstance(s.get(key), int)]
                if vals:
                    print(f"  {key:18} {mean(vals):.2f}")
        return

    path = Path(args.file) if args.file else find_latest_transcript()
    if not path or not path.exists():
        print("no transcript to score.  run scripts/run_demo_auto.py first.")
        sys.exit(1)
    scores = score_one(path)
    print_scorecard(path, scores)


if __name__ == "__main__":
    main()
