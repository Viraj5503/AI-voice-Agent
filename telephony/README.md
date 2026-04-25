# Telephony bridge — Twilio SIP → LiveKit room → Jamie

The whole demo only works once an inbound phone number is wired into a LiveKit room that the agent worker is listening on. Two paths:

## Path A — INCA-provided number (preferred)
INCA usually provisions a German number that you can point at a LiveKit room directly via SIP. Ask in the hackathon Slack as soon as doors open.

1. Get the SIP URI / dispatch token from INCA.
2. Set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` in `.env`.
3. Run `python voice/livekit_agent.py` in one terminal.
4. Place the inbound test call.

## Path B — DIY Twilio SIP trunk
1. Buy a number on Twilio (any DE number works for the demo).
2. In Twilio Console → Elastic SIP Trunking → create a trunk.
3. Origination URI: `sip:<your-livekit-sip-uri>` from the LiveKit project page.
4. Map the bought number → trunk.
5. Set `TWILIO_*` and `LIVEKIT_*` in `.env`.
6. Run `python voice/livekit_agent.py`.
7. Call the Twilio number; LiveKit dispatches the call to the worker, the worker connects Jamie.

## Latency budget (target)
- STT (Gradium / Whisper)      <  120 ms
- Gemini 3 Flash first token   <  220 ms
- Gradium TTS first audio      <  300 ms (documented TTFT)
- Network + jitter              <  100 ms
                              ─────────
                                 ~740 ms — already brushing the uncanny valley.

We close the gap with **filler audio**: as soon as we know we're going to call a tool (Tavily) we play "Let me just pull up the map…" while the tool runs. Net perceived latency drops below the 500 ms threshold.

## Multiplexing
We support concurrent calls without N×WebSocket overhead — see `voice/multiplex_demo.py`.
