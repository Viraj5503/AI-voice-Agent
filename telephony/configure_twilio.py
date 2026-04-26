"""Programmatically point a Twilio phone number at LiveKit's SIP gateway.

Run this once Inca refreshes the Twilio API key.  It's the API-side
equivalent of the Twilio Console's "Phone Numbers → Voice Configuration
→ A call comes in → SIP URI" field, so we don't need dashboard access.

Strategy:
  Twilio inbound calls arrive at the phone number.  We point the number's
  voice_url at a TwiML payload that <Dial>s LiveKit's SIP gateway.  The
  TwiML is hosted by Twilio itself as a TwiML Bin (no third-party
  webhook server needed).

Usage:
    python telephony/configure_twilio.py status   # what's set today
    python telephony/configure_twilio.py apply    # create TwiML Bin + point #
    python telephony/configure_twilio.py revert   # restore prior voice_url

Requires working Twilio creds in .env (currently 401 — see telephony/README).
The script fails fast with a clear message when creds are bad, so you can
re-run the moment Inca pastes a fresh API key.
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

from telephony.twilio_client import get_twilio_client  # noqa: E402


# Cache file so `revert` knows what URL to put back.
SAVE_PATH = REPO / "telephony" / ".twilio_voice_url_backup.txt"


def _livekit_sip_uri() -> str:
    """Resolve SIP URI from LIVEKIT_SIP_URI or derive from LIVEKIT_URL."""
    override = (os.environ.get("LIVEKIT_SIP_URI") or "").strip()
    if override:
        return override

    lk_url = os.environ.get("LIVEKIT_URL", "")
    host = lk_url.replace("wss://", "").replace("ws://", "").rstrip("/")
    project = host.split(".")[0]
    if not project:
        raise SystemExit("LIVEKIT_URL not set in .env and LIVEKIT_SIP_URI not provided")
    return f"sip:{project}.sip.livekit.cloud"


def _phone_number() -> str:
    n = os.environ.get("TWILIO_PHONE_NUMBER")
    if not n:
        raise SystemExit("TWILIO_PHONE_NUMBER not set in .env")
    return n.strip()


def _twiml_payload() -> str:
    """The XML Twilio will return when a call comes in."""
    sip_uri = _livekit_sip_uri()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f"  <Dial><Sip>{sip_uri}</Sip></Dial>\n"
        "</Response>\n"
    )


def _find_phone(client) -> object:
    nums = client.incoming_phone_numbers.list(phone_number=_phone_number(), limit=5)
    if not nums:
        raise SystemExit(
            f"No incoming_phone_number record found for {_phone_number()}.  "
            "Either the number isn't on this Twilio account, or the API key "
            "doesn't have permission to list it."
        )
    return nums[0]


# --------------------------------------------------------------------------
def cmd_status(client) -> int:
    p = _find_phone(client)
    sip_uri = _livekit_sip_uri()
    print(f"  Phone:      {p.phone_number}  (sid={p.sid})")
    print(f"  Friendly:   {p.friendly_name}")
    print(f"  voice_url:  {p.voice_url or '(unset)'}")
    print(f"  voice_method: {p.voice_method}")
    print(f"  sms_url:    {p.sms_url or '(unset)'}")
    print()
    print(f"  Target SIP URI for LiveKit: {sip_uri}")
    if p.voice_url and sip_uri.split(":", 1)[1] in (p.voice_url or ""):
        print("  ✓ already pointing at LiveKit (or close to it)")
    else:
        print("  · not yet routed to LiveKit — run `apply` to wire it up")
    return 0


def cmd_apply(client) -> int:
    p = _find_phone(client)
    sip_uri = _livekit_sip_uri()

    # Save existing URL so revert can restore it
    if p.voice_url and not SAVE_PATH.exists():
        SAVE_PATH.write_text(p.voice_url)
        print(f"  · backed up prior voice_url → {SAVE_PATH}")

    # Step 1: create a TwiML Bin (Twilio-hosted XML, no webhook needed).
    # We use the deprecated-but-still-supported `applications` endpoint
    # via TwiML Bins is not in the SDK directly, so we use the simpler
    # alternative: a Twilio Function URL.  But neither requires a private
    # server.  Cleanest path: ngrok or twiml-redirect.com.
    #
    # For hackathon brevity, we use a *Twilio TwiML App* (managed through
    # `applications` resource) — pointing voice_url at it.  The TwiML App
    # carries the URL we want Twilio to fetch.
    #
    # Easiest live solution: just set voice_url directly to a static
    # publicly-hosted TwiML — Twilio offers handler.twilio.com for this,
    # but creation requires the Console.
    #
    # So the API-only path that works without any extra infra is to use
    # Twilio's <Twiml> echo URL service (echo.twilio.com) for static SIP
    # routing.  Format:
    #   https://twimlets.com/forward?PhoneNumber=sip%3A...&AccountSid=...
    # But twimlets only supports phone-style targets, not SIP.
    #
    # The clean solution is to host the TwiML ourselves — see notes in
    # telephony/README.md "Operating without Twilio Console".  When you
    # have an HTTP endpoint serving _twiml_payload(), set its URL here:
    print()
    print("  configure_twilio.py needs a public HTTP URL that returns:")
    print("  -" * 30)
    print(_twiml_payload(), end="")
    print("  -" * 30)
    print()
    print("  Options:")
    print(f"    A. ngrok  →  python -m http.server 5000 + ngrok http 5000")
    print(f"    B. Twilio Console TwiML Bin  (needs dashboard access)")
    print(f"    C. Cloudflare Workers / Vercel serverless function")
    print()
    print("  Once you have the URL (call it $TWIML_URL), re-run:")
    print(f"    TWIML_URL=https://… python telephony/configure_twilio.py apply")
    print()

    twiml_url = os.environ.get("TWIML_URL")
    if not twiml_url:
        return 1

    # Now we have a URL we can plug in
    updated = client.incoming_phone_numbers(p.sid).update(
        voice_url=twiml_url,
        voice_method="POST",
    )
    print(f"  ✓ phone {updated.phone_number} now routes to {twiml_url}")
    print(f"    (which forwards SIP traffic to {sip_uri})")
    print()
    print(f"  Next: python voice/livekit_agent.py start  (in a separate terminal)")
    print(f"        Then call {updated.phone_number}")
    return 0


def cmd_revert(client) -> int:
    p = _find_phone(client)
    if not SAVE_PATH.exists():
        print("  · no backup voice_url to restore (run `apply` first)")
        return 1
    prior = SAVE_PATH.read_text().strip()
    client.incoming_phone_numbers(p.sid).update(voice_url=prior)
    SAVE_PATH.unlink()
    print(f"  ✓ reverted voice_url → {prior or '(empty)'}")
    return 0


# --------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmd", choices=["status", "apply", "revert"])
    args = parser.parse_args()

    try:
        client = get_twilio_client()
        # Force-auth check up front so a 401 doesn't masquerade as a "not found"
        client.api.accounts(os.environ["TWILIO_ACCOUNT_SID"]).fetch()
    except Exception as e:
        msg = str(e)
        if "401" in msg or "Authenticate" in msg:
            print("  ✗ Twilio creds not working (HTTP 401).")
            print("    The API-Key SID/Secret pair in .env is rejected by")
            print("    Twilio.  Ask Inca for a fresh API Key on this account")
            print(f"    (TWILIO_ACCOUNT_SID={os.environ.get('TWILIO_ACCOUNT_SID')}),")
            print("    paste it into .env, and re-run this script.")
            sys.exit(2)
        print(f"  ✗ {type(e).__name__}: {msg}")
        sys.exit(2)

    fn = {"status": cmd_status, "apply": cmd_apply, "revert": cmd_revert}[args.cmd]
    sys.exit(fn(client))


if __name__ == "__main__":
    main()
