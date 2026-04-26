"""Build a Gradium pronunciation dictionary tuned for insurance + German.

Why this matters for the Turing test: per the OPERATION doc Strategic
Insight #2, Inca jurors are likely real insurance professionals.  They
will pattern-match how Jamie pronounces:

  * Industry acronyms — "FNOL" said as letters (eff-en-oh-ell) instead of
    the natural single word "eff-noll" is an instant AI tell.
  * German policy lingo — Vollkasko, Teilkasko, Schutzbrief, Werkstatt-
    bindung have specific stress patterns native German agents know.
  * Currency / dates / plate formats — "EUR" said as "E-U-R" vs "euro",
    "B-MM 4421" said as letters vs "B M M four four two one".

Gradium exposes a /api/pronunciations/ endpoint whose UIDs are passed
to gradium.TTS(pronunciation_id=...).  This script:

  python scripts/setup_pronunciations.py create   # idempotent — replaces
                                                  # any existing 'jamie-fnol'
                                                  # dictionary
  python scripts/setup_pronunciations.py list     # show current dicts
  python scripts/setup_pronunciations.py delete <uid>

After `create`, paste the printed UID into .env as GRADIUM_PRONUNCIATION_ID
and restart the agent.  voice/livekit_agent.py picks it up automatically.

The rules below are an opinionated starting set.  Edit before running if
you want different phonetic spellings — the trade-off is more entries =
more processing per turn but also fewer obvious robotic moments.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env", override=True)
except Exception:
    pass

import httpx

GRADIUM_BASE = os.environ.get("GRADIUM_BASE_URL", "https://eu.api.gradium.ai")
DICT_NAME = "jamie-fnol"


# Pronunciation rules.  `original` is the source text (case-insensitive
# match by default).  `rewrite` is what the TTS engine actually
# vocalises.  Use space-separated letters or hyphens to force spelling-
# out; use phonetic words for natural pronunciation.
RULES = [
    # --- Industry acronyms (pronounced as single words by humans) ---
    {"original": "FNOL", "rewrite": "eff-noll"},
    {"original": "DSGVO", "rewrite": "Day-Es-Gay-Fau-Oh"},
    {"original": "GDPR", "rewrite": "G D P R"},
    {"original": "IBAN", "rewrite": "EE-bahn"},
    {"original": "VIN", "rewrite": "vin"},
    {"original": "ADAC", "rewrite": "ah-dack"},
    # --- Policy / claim shorthand ---
    {"original": "TPL", "rewrite": "T P L"},
    {"original": "TPA", "rewrite": "T P A"},
    {"original": "BI",  "rewrite": "bodily injury"},
    {"original": "PD",  "rewrite": "property damage"},
    {"original": "SF",  "rewrite": "S F"},   # SF-Klasse (no-claims class)
    # --- German insurance terms ---
    {"original": "Vollkasko",  "rewrite": "Foll-kass-ko"},
    {"original": "Teilkasko",  "rewrite": "Tile-kass-ko"},
    {"original": "Schutzbrief", "rewrite": "Shoots-breef"},
    {"original": "Werkstattbindung", "rewrite": "Verk-shtatt-bin-doong"},
    {"original": "Rabattschutz", "rewrite": "Rah-batt-shoots"},
    {"original": "Fahrerschutz", "rewrite": "Fah-rer-shoots"},
    # --- Insurer brands (German jurors will say these as one word) ---
    {"original": "HUK-Coburg", "rewrite": "Hook Coburg"},
    {"original": "Vorsicht", "rewrite": "Fore-zickt"},
    # --- Currency / units ---
    {"original": "EUR", "rewrite": "euro"},
    # German policy-number prefix — "DE-HUK-2024-..." sounds robotic if
    # the dashes get over-pronounced.  Encourage natural reading.
    {"original": "DE-HUK", "rewrite": "D E hook"},
    # --- Common locations Jamie might say ---
    {"original": "Köln-Ost", "rewrite": "Köln Ost"},
    {"original": "München-Süd", "rewrite": "München Süd"},
    {"original": "A4", "rewrite": "A four"},
    {"original": "A1", "rewrite": "A one"},
    {"original": "A9", "rewrite": "A nine"},
]


def _headers() -> dict[str, str]:
    key = os.environ.get("GRADIUM_API_KEY")
    if not key:
        print("GRADIUM_API_KEY not set in .env")
        sys.exit(2)
    return {"x-api-key": key, "content-type": "application/json"}


def _list_dicts() -> list[dict]:
    r = httpx.get(f"{GRADIUM_BASE}/api/pronunciations/", headers=_headers(), timeout=10.0)
    r.raise_for_status()
    body = r.json()
    return body.get("dictionaries", body) if isinstance(body, dict) else body


def cmd_list() -> int:
    dicts = _list_dicts()
    print(f"\n  {len(dicts)} pronunciation dict(s) on this account:\n")
    for d in dicts:
        uid = d.get("uid", "?")
        name = d.get("name", "?")
        lang = d.get("language", "?")
        n_rules = len(d.get("rules", []))
        marker = "  ← jamie" if name == DICT_NAME else ""
        print(f"  {uid:24}  name={name!r:20}  lang={lang:4}  rules={n_rules}{marker}")
    print()
    return 0


def cmd_create(language: str) -> int:
    # Idempotent: delete any existing 'jamie-fnol' first
    for d in _list_dicts():
        if d.get("name") == DICT_NAME:
            uid = d["uid"]
            httpx.delete(f"{GRADIUM_BASE}/api/pronunciations/{uid}",
                         headers=_headers(), timeout=10.0)
            print(f"  · removed existing '{DICT_NAME}' ({uid})")
            break

    payload = {"name": DICT_NAME, "language": language, "rules": RULES}
    r = httpx.post(f"{GRADIUM_BASE}/api/pronunciations/",
                   headers=_headers(), json=payload, timeout=15.0)
    if r.status_code >= 400:
        print(f"  ✗ HTTP {r.status_code}: {r.text[:300]}")
        return 1
    body = r.json()
    uid = body["uid"]
    print()
    print("  ────────────────────────────────────────────────────────────")
    print(f"  ✓ pronunciation dict created — uid: {uid}")
    print(f"    {len(body['rules'])} rules,  language={body['language']}")
    print("  ────────────────────────────────────────────────────────────")
    print()
    print("  Next: open .env and add this line:")
    print(f"      GRADIUM_PRONUNCIATION_ID={uid}")
    print("  then restart voice/livekit_agent.py.")
    print()
    return 0


def cmd_delete(uid: str) -> int:
    r = httpx.delete(f"{GRADIUM_BASE}/api/pronunciations/{uid}",
                     headers=_headers(), timeout=10.0)
    print(f"  delete {uid} → HTTP {r.status_code}")
    return 0 if r.status_code in (200, 204) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmd", choices=["list", "create", "delete"])
    parser.add_argument("uid", nargs="?", help="(for delete) dictionary uid")
    parser.add_argument("--language", default="en",
                        help="dictionary language (en or de). Default 'en'.")
    args = parser.parse_args()

    if args.cmd == "list":
        sys.exit(cmd_list())
    if args.cmd == "create":
        sys.exit(cmd_create(args.language))
    if args.cmd == "delete":
        if not args.uid:
            print("delete requires a uid argument")
            sys.exit(2)
        sys.exit(cmd_delete(args.uid))


if __name__ == "__main__":
    main()
