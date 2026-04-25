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
