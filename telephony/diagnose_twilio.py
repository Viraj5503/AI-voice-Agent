"""Pinpoint why Twilio creds 401 on one device but work on another.

The creds in .env are confirmed valid on a teammate's machine — calls land
there.  On the affected machine the same creds 401.  This script narrows
the problem to one of:

  1. .env file has hidden chars (BOM, trailing whitespace, smart quotes,
     CRLF line endings) that get into the auth header.
  2. The wrong .env is being loaded (a different file higher in the
     dotenv search path is shadowing the one you think).
  3. Twilio API key has IP-allowlisting and your external IP isn't on it.
  4. Network path is mangling the Authorization header (VPN, MITM proxy,
     corporate firewall).
  5. Clock skew so severe that Twilio rejects the request (rare).

Run on BOTH the affected machine and the teammate's machine — diff the
output.  The two will agree on (1)+(2) only if the .env contents are
byte-identical, and they should agree on (5).  If (3) or (4) is the
issue, only the External IP / final HTTP status will differ.
"""

from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO / ".env", override=True)
except Exception:
    pass


def _hex_repr(s: str | None, length_only: bool = False) -> str:
    """Show an opaque-but-comparable repr of a credential string.

    Print first 4 + last 4 chars in cleartext, total length, and a sha256
    hash of the full value.  Two devices with the same secret produce
    identical hashes; any whitespace / BOM / typo causes a different hash.
    """
    if s is None:
        return "<UNSET>"
    import hashlib
    digest = hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
    head = s[:4]
    tail = s[-4:] if len(s) > 8 else ""
    # Detect whitespace + non-printable bytes
    raw = s.encode("utf-8")
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    has_trail_ws = s != s.rstrip()
    has_lead_ws = s != s.lstrip()
    has_smart_q = any(ord(c) > 127 for c in s)
    flags = []
    if has_bom: flags.append("BOM!")
    if has_trail_ws: flags.append("TRAILING_WS!")
    if has_lead_ws: flags.append("LEADING_WS!")
    if has_smart_q: flags.append("NON_ASCII!")
    flag_str = " " + " ".join(flags) if flags else ""
    return f"{head}…{tail}  len={len(s)}  sha256[:16]={digest}{flag_str}"


def section(title: str) -> None:
    print()
    print("─" * 72)
    print(f"  {title}")
    print("─" * 72)


def main() -> None:
    print("\nTwilio 401 diagnostic — paste this output, AND the same output from")
    print("your teammate's machine, side-by-side to compare.\n")

    # ────────────────────────────────────────────────────────────────────────
    section("1. Which .env file did dotenv actually load?")
    # dotenv searches upward from cwd by default; we forced REPO/.env.  But
    # if a different file is present higher up, env vars may have leaked.
    candidates = [
        REPO / ".env",
        Path.home() / ".env",
        Path.cwd() / ".env",
    ]
    for c in candidates:
        try:
            stat = c.stat()
            print(f"  EXISTS  {c}  ({stat.st_size} bytes, mtime={time.ctime(stat.st_mtime)})")
        except FileNotFoundError:
            print(f"  -       {c}")
    print()
    print(f"  Loaded into env: REPO/.env = {REPO / '.env'}")

    # ────────────────────────────────────────────────────────────────────────
    section("2. Twilio creds — byte-level fingerprint")
    print("  (Compare these sha256[:16] values to the teammate's run.")
    print("   If they differ, the .env isn't byte-identical → fix .env.)")
    print()
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    key_sid = os.environ.get("TWILIO_API_KEY_SID")
    key_secret = os.environ.get("TWILIO_API_KEY_SECRET")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    phone = os.environ.get("TWILIO_PHONE_NUMBER")
    for name, val in [
        ("TWILIO_ACCOUNT_SID", sid),
        ("TWILIO_API_KEY_SID", key_sid),
        ("TWILIO_API_KEY_SECRET", key_secret),
        ("TWILIO_AUTH_TOKEN", auth_token),
        ("TWILIO_PHONE_NUMBER", phone),
    ]:
        print(f"  {name:24} {_hex_repr(val)}")

    # ────────────────────────────────────────────────────────────────────────
    section("3. Raw bytes of the relevant .env lines")
    # If the file has CRLF line endings or surrounding quotes, dotenv may
    # parse cleanly but a hand-edit would surface those bytes here.
    try:
        env_path = REPO / ".env"
        if env_path.exists():
            for line in env_path.read_bytes().splitlines():
                if b"TWILIO" in line:
                    print(f"  {line!r}")
        else:
            print("  (REPO/.env not found)")
    except Exception as e:
        print(f"  (couldn't read .env: {e})")

    # ────────────────────────────────────────────────────────────────────────
    section("4. External IP (for IP-allowlist check on Twilio API key)")
    try:
        import httpx
        r = httpx.get("https://api.ipify.org", timeout=5.0)
        print(f"  Outbound IP: {r.text}")
        print("  If teammate sees a different IP, and the Twilio API key has")
        print("  IP-allowlisting enabled, that's why one works and one doesn't.")
    except Exception as e:
        print(f"  (couldn't fetch IP: {e})")

    # ────────────────────────────────────────────────────────────────────────
    section("5. Live Twilio API call — both endpoints, both auth modes")
    if not (sid and key_sid and key_secret):
        print("  SKIP — missing creds")
        return

    import httpx
    # Test against the standard endpoint and the EU edge.  Some accounts are
    # provisioned in IE only.
    endpoints = [
        ("api.twilio.com", "https://api.twilio.com"),
        ("ie1.api.twilio.com (EU edge)", "https://ie1.api.twilio.com"),
    ]
    for name, base in endpoints:
        url = f"{base}/2010-04-01/Accounts/{sid}.json"
        # API-Key auth
        try:
            r = httpx.get(url, auth=(key_sid, key_secret), timeout=10.0)
            body = r.text[:300] if r.status_code != 200 else "OK"
            print(f"  API-Key auth → {name:30} HTTP {r.status_code}  {body}")
            # Also show the request headers — if a proxy is mangling
            # Authorization, this is where to spot it
            sent_auth = r.request.headers.get("authorization", "(none)")
            sent_auth_short = sent_auth[:20] + "…" if len(sent_auth) > 20 else sent_auth
            print(f"    sent Authorization header: {sent_auth_short}")
        except Exception as e:
            print(f"  API-Key auth → {name:30} ERROR: {type(e).__name__}: {e}")

    # ────────────────────────────────────────────────────────────────────────
    section("6. Manual auth header construction (bypasses httpx auth=)")
    # If httpx is rewriting the header, building it ourselves catches that.
    auth_str = f"{key_sid}:{key_secret}"
    encoded = base64.b64encode(auth_str.encode()).decode()
    headers = {"Authorization": f"Basic {encoded}"}
    try:
        r = httpx.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
            headers=headers,
            timeout=10.0,
        )
        body = r.text[:300] if r.status_code != 200 else "OK"
        print(f"  Manual Basic auth → HTTP {r.status_code}  {body}")
    except Exception as e:
        print(f"  Manual Basic auth → ERROR: {type(e).__name__}: {e}")

    # ────────────────────────────────────────────────────────────────────────
    section("7. Diagnosis cheat sheet")
    print("""
  If sha256[:16] of TWILIO_API_KEY_SECRET differs from teammate:
    → your .env has a hidden character / typo.  Re-copy from a
      shared source.  Trim trailing whitespace.

  If sha256 matches BUT all live API calls still 401:
    → Twilio API key has IP-allowlisting and your IP isn't allowed.
      Compare the External IP in section 4 with the teammate's.
      Ask Inca to add your IP to the allowlist OR disable
      allowlisting on this hackathon key.

  If both sha256 and IP look fine but you still get 401:
    → check for a VPN, MITM proxy, or corp firewall on this device.
      Try `curl --user $TWILIO_API_KEY_SID:$TWILIO_API_KEY_SECRET
            https://api.twilio.com/2010-04-01/Accounts/$SID.json`
      from the same machine; if curl works and Python doesn't, the
      issue is python-side (proxies, certifi, etc).
""")


if __name__ == "__main__":
    main()
