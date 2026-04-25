"""PII redaction for logs / persisted transcripts.

Insurance calls are full of GDPR Article 9 (health) and Article 6 (financial)
data.  This module is the audit-friendly answer to "you handle sensitive data,
how do you log it?" — and the load-bearing piece of the Aikido bounty pitch.

Use `redact(text)` on anything before it touches a log handler or disk.
"""

from __future__ import annotations

import re

# Roughly: "DE-" then 3 letters, "-2024-", 6 digits  (and similar carrier codes)
POLICY_NUMBER = re.compile(r"\b[A-Z]{2}-[A-Z]{2,4}-\d{4}-\d{4,8}\b")

# German plate: 1–3 letters, "-", 1–2 letters, 1–4 digits
PLATE = re.compile(r"\b[A-ZÄÖÜ]{1,3}-[A-Z]{1,2}\s?\d{1,4}\b")

# 17-character VIN
VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")

# German phone (loose: +49…, 0049…, 0…)
PHONE = re.compile(r"\+?\d{2,3}[\s\-]?\d{2,4}[\s\-]?\d{3,8}")

# IBAN (DE only is fine for our use)
IBAN = re.compile(r"\bDE\d{2}[\s\-]?(?:\d{4}[\s\-]?){4}\d{2}\b")

# DOB-ish: yyyy-mm-dd
DOB = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")

# Email
EMAIL = re.compile(r"\b[\w.\-]+@[\w.\-]+\.\w{2,}\b")

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (POLICY_NUMBER, "[POLICY]"),
    (VIN, "[VIN]"),
    (PLATE, "[PLATE]"),
    (IBAN, "[IBAN]"),
    (PHONE, "[PHONE]"),
    (EMAIL, "[EMAIL]"),
    (DOB, "[DOB]"),
]


def redact(text: str) -> str:
    """Return *text* with all PII patterns replaced by tokens."""
    for pat, tok in PATTERNS:
        text = pat.sub(tok, text)
    return text


def redacted_dict(d: dict) -> dict:
    """Recursively redact string values in a dict.  Keys are preserved."""
    out: dict = {}
    for k, v in d.items():
        if isinstance(v, str):
            out[k] = redact(v)
        elif isinstance(v, dict):
            out[k] = redacted_dict(v)
        elif isinstance(v, list):
            out[k] = [redact(x) if isinstance(x, str) else x for x in v]
        else:
            out[k] = v
    return out


# --- self-test -------------------------------------------------------------
if __name__ == "__main__":
    sample = (
        "My policy is DE-HUK-2024-884421, VIN WVWZZZ1JZ3W386752, plate B-MM 4421, "
        "phone +49 172 555 0100, email max.mueller@email.de, born 1984-03-15."
    )
    print(redact(sample))
