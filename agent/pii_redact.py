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

# German phone — supports 2/3/4 digit groups, country prefix optional.
# Catches "+49 172 555 0100", "+49-172-555-0100", "0172 5550100", etc.
PHONE = re.compile(
    r"\+?\d{1,3}[\s\-]?\d{2,5}[\s\-]?\d{2,4}(?:[\s\-]?\d{2,5})?"
)

# IBAN (DE only is fine for our use)
IBAN = re.compile(r"\bDE\d{2}[\s\-]?(?:\d{4}[\s\-]?){4}\d{2}\b")

# DOB-ish: yyyy-mm-dd
DOB = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")

# Email
EMAIL = re.compile(r"\b[\w.\-]+@[\w.\-]+\.\w{2,}\b")

# Credit card — 13–19 digit groups separated by spaces or hyphens.  Validated
# loosely (we don't run Luhn) — better to over-redact a similar-shaped number
# than leak a real card.
CREDIT_CARD = re.compile(r"\b(?:\d[ \-]?){13,19}\b")

# German Sozialversicherungsnummer (social security): 12 chars,
# 2 digits + 1 letter + 7 digits + 2 letters.  e.g. "12 030484 W 023".
SOCIAL_SECURITY_DE = re.compile(
    r"\b\d{2}\s?\d{6}\s?[A-Z]\s?\d{3}\b"
)

# German health insurance card number (Krankenversichertennummer):
# 1 letter + 9 digits, sometimes with a check digit (10 total).
HEALTH_CARD_DE = re.compile(r"\b[A-Z]\d{9,10}\b")

# German driver's license number — alphanumeric ~11 chars.  Format varies
# by Bundesland but typically: letter or digit blocks.  Conservative: catch
# any 10–12 char alphanumeric with at least one letter and at least one digit.
DRIVER_LICENSE_DE = re.compile(
    r"\b(?=[A-Z0-9]{10,12}\b)(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{10,12}\b"
)

# Generic credit-card-like Luhn-eligible 16-digit run (catches stripped formats).
CC_NO_SEPARATORS = re.compile(r"\b\d{16}\b")

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Order matters — apply MOST SPECIFIC patterns first so the looser
    # ones (PHONE, CREDIT_CARD) don't greedily consume well-formatted IDs
    # before they're recognised.  E.g. PHONE alone would otherwise eat
    # an ISO date like "1984-03-15" because it parses as 4-digit + 2-digit
    # + 2-digit groups.
    (DOB, "[DOB]"),
    (POLICY_NUMBER, "[POLICY]"),
    (VIN, "[VIN]"),
    (IBAN, "[IBAN]"),
    (SOCIAL_SECURITY_DE, "[SVNR]"),
    (HEALTH_CARD_DE, "[HEALTH_CARD]"),
    (DRIVER_LICENSE_DE, "[DL]"),
    (CREDIT_CARD, "[CARD]"),
    (CC_NO_SEPARATORS, "[CARD]"),
    (PLATE, "[PLATE]"),
    (EMAIL, "[EMAIL]"),
    (PHONE, "[PHONE]"),
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
