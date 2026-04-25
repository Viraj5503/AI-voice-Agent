"""Build a Twilio REST Client from .env, picking the right auth mode.

Inca typically issues *scoped API-Key* credentials (AccountSID + APIKeySID +
APIKeySecret), not master credentials.  Both work — twilio-python's `Client`
just wants `(username, password, account_sid)`.

Mapping:
    Master mode      Client(account_sid, auth_token)
    API-Key mode     Client(api_key_sid, api_key_secret, account_sid)

`get_twilio_client()` figures out which mode you have and returns a usable
Client (or raises with a clear message).
"""

from __future__ import annotations

import os
from typing import Any


def get_twilio_client() -> Any:
    """Return a configured `twilio.rest.Client`, or raise ValueError."""
    try:
        from twilio.rest import Client  # type: ignore
    except ImportError as e:  # pragma: no cover - install hint
        raise RuntimeError(
            "twilio package missing — `pip install twilio` (already in requirements.txt)"
        ) from e

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    api_key_sid = os.environ.get("TWILIO_API_KEY_SID")
    api_key_secret = os.environ.get("TWILIO_API_KEY_SECRET")

    if not account_sid:
        raise ValueError(
            "TWILIO_ACCOUNT_SID is required.  Both auth modes need it."
        )

    # Prefer API-Key mode if both are present (Inca's mode, more secure).
    if api_key_sid and api_key_secret:
        return Client(api_key_sid, api_key_secret, account_sid)

    if auth_token:
        return Client(account_sid, auth_token)

    raise ValueError(
        "No Twilio auth credentials.  Set EITHER:\n"
        "  TWILIO_AUTH_TOKEN  (master mode)\n"
        "OR:\n"
        "  TWILIO_API_KEY_SID + TWILIO_API_KEY_SECRET  (API-Key mode — Inca usually)"
    )


def auth_mode_summary() -> str:
    """Human-readable summary of which mode is configured.  For diagnostics."""
    if os.environ.get("TWILIO_API_KEY_SID") and os.environ.get("TWILIO_API_KEY_SECRET"):
        return "API-Key mode (scoped credential)"
    if os.environ.get("TWILIO_AUTH_TOKEN"):
        return "Master mode (account_sid + auth_token)"
    return "no auth configured"


# --- self-test -------------------------------------------------------------
if __name__ == "__main__":
    from dotenv import load_dotenv  # type: ignore
    from pathlib import Path

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
    print(f"  Twilio: {auth_mode_summary()}")
    try:
        c = get_twilio_client()
        # tiny call: fetch the account record so we know auth is real
        acc = c.api.accounts(os.environ["TWILIO_ACCOUNT_SID"]).fetch()
        print(f"  ✓ authenticated as account {acc.friendly_name!r}, status={acc.status}")
        nums = list(c.incoming_phone_numbers.list(limit=5))
        if nums:
            print(f"  ✓ {len(nums)} phone number(s) on this account:")
            for n in nums:
                print(f"      {n.phone_number}  →  voice URL: {n.voice_url or '(none set)'}")
        else:
            print("  · no phone numbers on the account yet")
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")
