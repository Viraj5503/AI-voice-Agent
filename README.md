# Operation: Turing Adjuster — *Jamie*

Jamie is a phone-based first-notice-of-loss (FNOL) claims intake specialist for a German motor insurer. She is built to pass the Inca "Human Test" at Big Berlin Hack, sweeping the Aikido, Fastino/Pioneer, Gradium, and Entire side bounties as a side-effect of the architecture.

> The single tightest summary: **Jamie speaks from knowledge, not from a questionnaire.** Everything the insurer already knows about the caller is loaded into the system prompt before the phone rings; everything Jamie still needs to learn is gathered conversationally; everything she hears is documented asynchronously by a fine-tuned GLiNER2 extractor so the chat LLM can stay focused on sounding human.

## Architecture (one sentence)

`Inbound call → Gradium STT → Gemini 2.5 Flash (with Known-Context CRM + Tavily tools) → Gradium TTS → Caller`, while in parallel `transcript → fastino/gliner2-base-v1 → 15 claim pillars + 5 fraud signals → WebSocket → Lovable dashboard`.

## Stack — verified facts (April 2026)

The original game plan and the first round of stack research both contained hopeful claims that turned out not to match the actual docs / API responses. Corrected here so nothing breaks at H22:

| Claim | Reality | What we use |
|---|---|---|
| `gemini-3-flash` is the latency-optimized public model | A live 404 from `models/gemini-3-flash` proves it isn't on the public v1beta endpoint yet | `gemini-2.5-flash`, with auto-probe fallback to `2.0-flash` and `1.5-flash` |
| `knowledgator/gliner-multitask-large-v0.5` is current | That exact ID isn't current on HF | `fastino/gliner2-base-v1` (Pioneer-aligned) with `knowledgator/gliner-bi-large-v2.0` fallback |
| `gradium.AsyncClient` / `gradium.Client` exist | Neither — the `gradium` 0.5.11 package exports `GradiumClient` | `from gradium import GradiumClient` (sync ctor, async `tts_stream` / `tts_realtime`) |
| `<flush>` / `<break time="…"/>` SSML tags | Not documented in gradium.ai | Stick to documented `speed` / `temperature`; treat tags as best-effort |
| `entire dispatch` | Confirmed real (user has used it) on top of `entire enable` | `entire enable` then `entire dispatch` for reasoning capture |
| `GradiumTTSService` class in livekit | Real class is `gradium.TTS()` | `from livekit.plugins import gradium; gradium.TTS()` |
| `pip install gradium` (only) | Multiple paths exist | Prototype with `gradbot`, ship with `livekit-agents[gradium]` |

## Repo layout

```
agent/         Jamie's brain — system prompt, claim-state tracker, Gemini client
voice/         Gradbot quickstart + LiveKit + Gradium production voice loop
telephony/     Twilio SIP / LiveKit room glue
extraction/    GLiNER2 microservice + benchmark vs. Gemini structured output
tools/         Tavily real-time lookups exposed as Gemini function-calls
bridge/        FastAPI WebSocket bridge to the dashboard
dashboard/     Single-file React dashboard + Lovable regeneration prompt
data/          Mock CRM profiles (Known Context)
tests/         Juror-bot adversarial Turing harness
fillers/       Filler-audio manifest + generation script
docs/          SECURITY.md (Aikido), ENTIRE.md, prompts cookbook
scripts/       run_demo_text.py and other operator commands
```

## Sequential build path

If you're standing this up from scratch, follow **[docs/SEQUENTIAL_RUNBOOK.md](docs/SEQUENTIAL_RUNBOOK.md)** in order: LiveKit → Gradium → Gemini → Twilio (skip for laptop-mic demo) → Tavily → Fastino → Aikido + Entire. Each step has a one-line checkpoint you have to hit before moving on, so you never debug a broken layer through the layer above it.

## Quickstart (text-mode, no telephony, no API keys yet)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in keys as they arrive
python scripts/run_demo_text.py --crm max_mueller
```

The text-mode demo runs Jamie against you typing as the caller, with the GLiNER2 extractor live and the dashboard updating over WebSocket. No phone, no Gradium credits, no waiting on telephony provisioning. Use it to iterate on the system prompt at H0–H4 while infra is being wired.

## Quickstart (voice, Gradbot)

```bash
export GRADIUM_API_KEY=...
export GRADIUM_VOICE_ID=...   # Emma flagship is fine
python voice/gradbot_quickstart.py --crm max_mueller
```

## Quickstart (production: LiveKit + Gradium + Twilio SIP)

See `telephony/README.md`. Provision a LiveKit room, point Twilio SIP at it, run `python voice/livekit_agent.py`.

## Bounty wiring

- **Inca / Human Test** — `agent/prompts.py` (Known-Context injection), `tools/tavily_lookup.py` (real-time weather), `voice/livekit_agent.py` (low-latency loop), `tests/juror_bot.py` (proves pass-rate before judges).
- **Aikido (€1000)** — `docs/SECURITY.md`, `agent/pii_redact.py` redactor, `aikido.yml` CI gate placeholder, repo connected at `app.aikido.dev` from commit zero.
- **Fastino / Pioneer (Mac Mini)** — `extraction/gliner2_service.py` runs `fastino/gliner2-base-v1`, the model Pioneer is built around. `extraction/benchmark.py` produces the latency / cost / F1 table vs. Gemini structured output.
- **Gradium (900k credits)** — All three integration tiers shipped: Gradbot prototype, direct `gradium` SDK streaming, `livekit-agents[gradium]` production loop. Voice cloning + multiplexing demo in `voice/multiplex_demo.py`.
- **Entire** — `entire enable` run in repo root; `docs/ENTIRE.md` documents the architectural decisions.

## Runbook for hackathon day

| Hours | Owner | Goal |
|---|---|---|
| H0–H4 | Person A | Voice loop alive (Gradbot first), Person B drafts V1 prompt |
| H4–H10 | Both | GLiNER2 extraction + Tavily tool, all 13 pillars in the prompt |
| H10–H18 | Both | Aikido screenshots, Pioneer fine-tune + benchmark, Lovable dashboard polish |
| H18–H21 | Both | Juror bot run x50, tune voice params, freeze code |
| H21–H24 | Both | Pitch rehearsal, final `entire enable` summary in README |

See `OPERATION_TURING_ADJUSTER_v2.md` for the full game plan.
