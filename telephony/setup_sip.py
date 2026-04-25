"""LiveKit Cloud SIP setup helper.

Idempotently creates the inbound SIP trunk and dispatch rule that lets a
Twilio (or any SIP) inbound call land in a fresh LiveKit room — which our
livekit-agents worker (`python voice/livekit_agent.py start`) joins as Jamie.

Usage:
    python telephony/setup_sip.py list      # show current state
    python telephony/setup_sip.py setup     # create trunk + rule (idempotent)
    python telephony/setup_sip.py teardown  # delete the trunk + rule we own

After `setup`:
    1. Note the SIP URI printed.  That's what Twilio must dial.
    2. In Twilio Console:  Phone Numbers → your number →
       Voice Configuration → "A call comes in" → SIP URI → paste the URI.
       (Or set up an Elastic SIP Trunk with the URI as Origination.)
    3. Run the agent:  python voice/livekit_agent.py start
    4. Call your Twilio number.  LiveKit dispatches the agent to the new
       room and Jamie picks up.

We tag the trunk + rule with name="jamie-inbound" so re-runs are idempotent
and `teardown` knows what's safe to delete.
"""

from __future__ import annotations

import argparse
import asyncio
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

from livekit import api  # noqa: E402

JAMIE_TAG = "jamie-inbound"


def _client() -> api.LiveKitAPI:
    url = os.environ.get("LIVEKIT_URL")
    key = os.environ.get("LIVEKIT_API_KEY")
    secret = os.environ.get("LIVEKIT_API_SECRET")
    if not (url and key and secret):
        raise SystemExit(
            "LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET must be set in .env"
        )
    if key == secret or len(secret) < 24:
        raise SystemExit(
            "LIVEKIT_API_SECRET looks wrong.  In .env, LIVEKIT_API_KEY and\n"
            "LIVEKIT_API_SECRET are currently the same string (or the secret\n"
            "is too short).  Open https://cloud.livekit.io → your project →\n"
            "Settings → Keys, and copy BOTH the API Key and the (longer) API\n"
            "Secret into .env separately.  The secret is normally 40+ chars."
        )
    return api.LiveKitAPI(url=url, api_key=key, api_secret=secret)


def _twilio_number() -> str | None:
    n = os.environ.get("TWILIO_PHONE_NUMBER")
    return n.strip() if n else None


# --------------------------------------------------------------------------
async def cmd_list() -> int:
    lk = _client()
    try:
        trunks = await lk.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
        rules = await lk.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())
    finally:
        await lk.aclose()

    print(f"\nLiveKit URL:  {os.environ.get('LIVEKIT_URL')}")
    print(f"Twilio #:     {_twilio_number() or '(not set)'}")

    print(f"\nInbound trunks ({len(trunks.items)}):")
    for t in trunks.items:
        marker = "  ← jamie" if t.name == JAMIE_TAG else ""
        nums = ", ".join(t.numbers) or "(any)"
        print(f"  {t.sip_trunk_id}  name={t.name!r:24}  numbers=[{nums}]{marker}")

    print(f"\nDispatch rules ({len(rules.items)}):")
    for r in rules.items:
        marker = "  ← jamie" if r.name == JAMIE_TAG else ""
        # rule.trunk_ids is repeated string
        trunk_ids = ", ".join(r.trunk_ids) or "(any)"
        kind = r.rule.WhichOneof("rule") or "?"
        print(f"  {r.sip_dispatch_rule_id}  name={r.name!r:24}  kind={kind:14}  trunks=[{trunk_ids}]{marker}")
    return 0


# --------------------------------------------------------------------------
async def _find_jamie_trunk(lk: api.LiveKitAPI) -> api.SIPInboundTrunkInfo | None:
    resp = await lk.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
    for t in resp.items:
        if t.name == JAMIE_TAG:
            return t
    return None


async def _find_jamie_rule(lk: api.LiveKitAPI) -> api.SIPDispatchRuleInfo | None:
    resp = await lk.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())
    for r in resp.items:
        if r.name == JAMIE_TAG:
            return r
    return None


async def cmd_setup() -> int:
    lk = _client()
    try:
        # 1. Trunk
        trunk = await _find_jamie_trunk(lk)
        if trunk:
            print(f"  ✓ trunk already exists: {trunk.sip_trunk_id}")
        else:
            phone = _twilio_number()
            numbers = [phone] if phone else []
            req_trunk = api.SIPInboundTrunkInfo(
                name=JAMIE_TAG,
                numbers=numbers,
                # No auth — Twilio's Voice config posts a SIP INVITE without
                # SIP-level credentials.  LiveKit relies on the trunk's number
                # match for inbound routing.
            )
            created = await lk.sip.create_inbound_trunk(
                api.CreateSIPInboundTrunkRequest(trunk=req_trunk)
            )
            trunk = created
            print(f"  ✓ trunk created: {trunk.sip_trunk_id}")

        # 2. Dispatch rule (Individual = each inbound call gets its own room)
        rule = await _find_jamie_rule(lk)
        if rule:
            print(f"  ✓ dispatch rule already exists: {rule.sip_dispatch_rule_id}")
        else:
            individual = api.SIPDispatchRuleIndividual(
                room_prefix="jamie-call",
            )
            req_rule = api.CreateSIPDispatchRuleRequest(
                name=JAMIE_TAG,
                trunk_ids=[trunk.sip_trunk_id],
                rule=api.SIPDispatchRule(dispatch_rule_individual=individual),
            )
            rule = await lk.sip.create_dispatch_rule(req_rule)
            print(f"  ✓ dispatch rule created: {rule.sip_dispatch_rule_id}")

        # Derive the SIP URI from the project URL.  LiveKit Cloud projects
        # accept inbound SIP at `<project>.sip.livekit.cloud`.  We extract
        # the project hostname from the wss URL.
        lk_url = os.environ.get("LIVEKIT_URL", "")
        # wss://<project>.<region>.livekit.cloud  →  <project>.sip.livekit.cloud
        host = lk_url.replace("wss://", "").replace("ws://", "").rstrip("/")
        project = host.split(".")[0]
        sip_uri = f"sip:{project}.sip.livekit.cloud"

        phone = _twilio_number() or "(your Twilio #)"
        print()
        print("  ────────────────────────────────────────────────────────────")
        print(f"  SIP URI to give Twilio:   {sip_uri}")
        print(f"  Inbound number expected:  {phone}")
        print(f"  Trunk ID:                 {trunk.sip_trunk_id}")
        print(f"  Dispatch rule ID:         {rule.sip_dispatch_rule_id}")
        print("  ────────────────────────────────────────────────────────────")
        print()
        print("  Next:")
        print("  1. Twilio Console → Phone Numbers → " + phone)
        print("       Voice Configuration → 'A call comes in' →")
        print(f"       choose 'SIP URI' and paste: {sip_uri}")
        print("  2. python voice/livekit_agent.py start    (in a new terminal)")
        print(f"  3. Place a test call to {phone}")
        return 0
    finally:
        await lk.aclose()


# --------------------------------------------------------------------------
async def cmd_teardown() -> int:
    lk = _client()
    try:
        rule = await _find_jamie_rule(lk)
        trunk = await _find_jamie_trunk(lk)
        if rule:
            await lk.sip.delete_dispatch_rule(
                api.DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule.sip_dispatch_rule_id)
            )
            print(f"  ✓ deleted dispatch rule {rule.sip_dispatch_rule_id}")
        else:
            print("  · no jamie dispatch rule to delete")
        if trunk:
            await lk.sip.delete_trunk(
                api.DeleteSIPTrunkRequest(sip_trunk_id=trunk.sip_trunk_id)
            )
            print(f"  ✓ deleted trunk {trunk.sip_trunk_id}")
        else:
            print("  · no jamie trunk to delete")
        return 0
    finally:
        await lk.aclose()


# --------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cmd", choices=["list", "setup", "teardown"])
    args = parser.parse_args()
    fn = {"list": cmd_list, "setup": cmd_setup, "teardown": cmd_teardown}[args.cmd]
    rc = asyncio.run(fn())
    sys.exit(rc)


if __name__ == "__main__":
    main()
