#!/usr/bin/env python3
"""Smoke-test for the extraction pipeline (GLiNER + regex hybrid).

Run: .venv/bin/python tests/test_extraction.py
"""

import sys
sys.path.insert(0, ".")

from extraction.gliner2_service import ExtractionService

svc = ExtractionService()
print(f"mode: {svc.mode}   model: {svc.model_name}\n")

TESTS = {
    "AUTO": (
        "I was driving on the A4 around 14:30 today, it was raining heavily. "
        "The other car had plate K-AB 1234. I think I have whiplash. "
        "Police came, the case number is 2026-04-25-7711. My car is not drivable.",
        {"incident_datetime", "incident_location", "injuries_or_symptoms",
         "other_party_plate", "vehicle_drivable", "police_or_ambulance", "police_case_number"},
    ),
    "HEALTH": (
        "I have a health insurance claim. I went to the Berlin Medical Center hospital "
        "last Tuesday with severe chest pain and a high fever. Dr. Schmidt treated me. "
        "I need the treatment and medication costs reimbursed.",
        {"claim_type", "incident_location", "injuries_or_symptoms",
         "provider_name", "treatment_received"},
    ),
    "MIXED": (
        "I had a car accident yesterday at 09:15 and I also got injured — "
        "I have a broken wrist and I was taken to the hospital by ambulance. "
        "The police came and gave case number 7742.",
        {"incident_datetime", "injuries_or_symptoms", "police_or_ambulance", "police_case_number"},
    ),
    "MINIMAL (single sentence)": (
        "My car crashed on the Autobahn and I'm hurt.",
        {"incident_location", "injuries_or_symptoms"},
    ),
}

total_expected = 0
total_found    = 0

for name, (text, expected) in TESTS.items():
    result = svc.extract(text)
    found  = set(result["pillars"].keys())

    print(f"=== {name} (mode={result['mode']}, {result['elapsed_ms']}ms) ===")
    for label, info in result["pillars"].items():
        tick = "✓" if label in expected else "~"
        print(f"  {tick} {label:38s} → {repr(info['text'][:48])}  ({info['score']:.2f})")
    for label, info in result["fraud"].items():
        print(f"  🚨 FRAUD {label:33s} → {repr(info['text'][:40])}  ({info['score']:.2f})")

    missing = expected - found
    if missing:
        print(f"  ⚠  MISSING: {', '.join(sorted(missing))}")

    total_expected += len(expected)
    total_found    += len(expected & found)
    print()

pct = 100 * total_found / total_expected if total_expected else 0
print("=" * 58)
print(f"COVERAGE: {total_found}/{total_expected} expected pillars found  ({pct:.0f}%)")
if pct >= 80:
    print("✅  PASS — extraction pipeline healthy")
elif pct >= 50:
    print("⚠️  PARTIAL — GLiNER semantic matching is weak; regex fills the gap")
else:
    print("❌  FAIL — check label vocabulary and threshold")
