# Security posture — DSGVO-grade for FNOL

Insurance call intake processes Article 9 health data (injury descriptions), Article 6 financial data (policy + bank metadata implicit in CRM lookups), and location data. Below is the actual security surface of this repo and what we've done about it.

## What lives where, and what's redacted

| Surface | Contents | Redaction |
|---|---|---|
| Live transcript on the bridge | Caller utterances, Jamie replies | `agent/pii_redact.py` strips policy #, plate, VIN, IBAN, phone, email, DOB before `bridge.publish` |
| Persisted juror-bot transcripts (`tests/juror_results.json`) | Synthetic jurors only — no real PII, but redaction still applied for hygiene | yes |
| GLiNER2 extractor inputs | Caller utterances | local-only (no external API call); model runs on-device |
| Tavily lookups | Free-form location strings only — no PII sent | n/a |
| Gemini Flash (rolling) | Full conversation | governed by Google's API data-use terms (no training on API data); model alias `gemini-flash-latest` |

## PII redactor

`agent.pii_redact.redact(text)` — used by every code path that writes to a log handler, the bridge, or disk. Patterns covered: German policy numbers (loose), plates, VINs, IBANs, phones, DOBs, emails. See `tests/test_smoke.py::test_pii_redact`.

## Aikido pipeline (€1000 bounty)

Connect this repo to `app.aikido.dev` from commit zero. Then:

1. Watch the dashboard scan dependency vulns.
2. Run AI AutoFix on anything flagged.
3. Capture before/after screenshots — those are the submission artifact.
4. Add `aikido.yml` to CI (placeholder in repo root) so PRs are blocked on critical CVEs.

The pitch line: *"Insurance companies process GDPR Article 9 health data and Article 6 financial data. In production, this agent would need to meet DSGVO requirements. Aikido has been running on this repo since the first commit — zero critical vulnerabilities in the data-handling path."*

## Threat model — quick

- **Caller-side prompt injection**: Jamie's prompt explicitly forbids acknowledging being an AI. Tested via `tests/juror_bot.py` against the Skeptic Thomas persona.
- **Telephony abuse**: rate-limit inbound calls at the LiveKit / Twilio edge before they ever hit the agent.
- **Tavily quota exhaustion**: every Tavily call is wrapped with try/except; a quota error degrades gracefully to a generic filler ("Let me note that for the adjuster…").
- **Gemini hallucination of policy facts**: the system prompt is regenerated *every turn* with the live CRM JSON, so Jamie reads from authoritative state, not memory.
- **Logging leakage**: `pii_redact.redact` is the choke-point.
