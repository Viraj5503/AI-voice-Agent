# Telephony bridge — Twilio SIP → LiveKit room → Jamie

The whole demo only works once an inbound phone number is wired into a LiveKit room that the agent worker is listening on.

## Inca-issued credentials → our `.env` mapping

Inca usually hands out **scoped API-Key credentials** (more secure than master Auth Token). Map them like this:

| What Inca gave you   | Where it goes in `.env`     |
|----------------------|------------------------------|
| `AccountSID`         | `TWILIO_ACCOUNT_SID`         |
| `APIKeySID`          | `TWILIO_API_KEY_SID`         |
| `APIKeySecret`       | `TWILIO_API_KEY_SECRET`      |
| phone number         | `TWILIO_PHONE_NUMBER`        |

Leave `TWILIO_AUTH_TOKEN` empty — `telephony/twilio_client.py` auto-detects API-Key mode when `TWILIO_API_KEY_SID` is set.

If instead you have your *own* Twilio account with master creds, fill `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` and leave the API-Key fields empty.

### Verify the credentials work

```bash
python telephony/twilio_client.py
```

That fetches your account and lists phone numbers. Output should look like:

```
  Twilio: API-Key mode (scoped credential)
  ✓ authenticated as account 'INCA Hackathon', status=active
  ✓ 1 phone number(s) on this account:
      +49xxxxxxxxxxx  →  voice URL: (none set)
```

If you see `✗ HTTP 401`: the API-Key SID/Secret pair doesn't match the AccountSID. Re-check what Inca pasted into Slack.

## Wiring the number → LiveKit room

Two paths — pick whichever Inca's setup matches.

### Path A — Inca-provisioned SIP (preferred)

Ask in the hackathon Slack for the LiveKit SIP dispatch URI. Then:

1. `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` in `.env`.
2. Configure the SIP trunk in the LiveKit dashboard with the URI Inca gave you.
3. `python voice/livekit_agent.py` in one terminal.
4. Place the inbound test call.

### Path B — DIY Twilio Elastic SIP Trunk

1. Twilio Console → Elastic SIP Trunking → create a trunk.
2. Origination URI: `sip:<your-livekit-sip-uri>` from the LiveKit project page.
3. In the trunk's "Numbers" tab, add the Inca-issued number.
4. `python voice/livekit_agent.py`.
5. Call the number; LiveKit dispatches to the worker; Jamie picks up.

## Latency budget (target <740ms total)

| Step                             | Budget |
|----------------------------------|--------|
| STT (Gradium / Whisper)          | <120ms |
| Gemini 2.5 Flash first-token     | <220ms |
| Gradium TTS first-audio (TTFT)   | <300ms |
| Network + jitter                 | <100ms |

We close any remaining gap with **filler audio** (`fillers/manifest.json`): the moment a tool call is dispatched, we play "Let me just pull up the map…" so perceived latency drops below the 500ms uncanny-valley threshold.

## Multiplexing

Single WebSocket, multiple `client_req_id` concurrent TTS streams — see `voice/multiplex_demo.py`. This is the bounty pitch story for "production scale."

---

## Operating without Twilio Console access

If Inca handed you API credentials but not dashboard login, you can't open the Voice Configuration UI — but the demo doesn't depend on it. Here are the paths that work tonight while you wait on Twilio.

### Verify what you already have

```bash
python telephony/setup_sip.py list      # LiveKit trunk + rule + SIP URI
python telephony/twilio_client.py        # Twilio creds — known to 401 right now
```

The LiveKit side is already wired. The only missing hop is Twilio → LiveKit's SIP URI. Three ways to demo without that hop:

### Path 1 — Local laptop demo (zero infra, ~10 sec to start)

Best for solo iteration and proving the pipeline. No LiveKit Cloud round-trip.

```bash
python voice/livekit_agent.py console
```

Uses MacBook mic + speakers via `sounddevice`. Same JamieAgent code as production, same bridge events. The only difference: no LiveKit room, no telephony.

### Path 2 — Browser caller via LiveKit Agents Playground (~3 min)

Best for showing judges. Caller experience matches a real phone call.

1. `python voice/livekit_agent.py start` (already running ✓)
2. Open https://agents-playground.livekit.io
3. "Connect to a custom server" → paste:
   - URL: `wss://bbh-inca-n9i26bo3.livekit.cloud`
   - API Key + API Secret from `.env`
4. Click "Connect" → playground creates a room → your worker auto-dispatches → talk to Jamie in the browser.

This path uses your LiveKit project end-to-end (token auth, SIP-side dispatch rule still works for phone tomorrow). The browser caller is a fully realistic stand-in for Twilio.

### Path 3 — Programmatic Twilio config (when creds work)

Once Inca refreshes the API Key:

```bash
python telephony/configure_twilio.py status   # what's set today
TWIML_URL=https://your-host/twiml.xml \
  python telephony/configure_twilio.py apply  # point # at LiveKit
python telephony/configure_twilio.py revert   # undo
```

The `apply` step needs a public URL serving the TwiML payload (which the script prints for you). For hackathon speed: `python -m http.server 5000` + `ngrok http 5000`. Or a Cloudflare Worker. Or once you have Console access, a TwiML Bin.
