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

`agent.pii_redact.redact(text)` — used by every code path that writes to a log handler, the bridge, or disk. **Twelve patterns covered**, all unit-tested in `tests/test_smoke.py::test_pii_redact` + `test_pii_redact_extended`:

| Category | Pattern | Token |
|---|---|---|
| Policy number (DE-XXX-YYYY-NNNNNN) | `POLICY_NUMBER` | `[POLICY]` |
| 17-char VIN | `VIN` | `[VIN]` |
| German licence plate | `PLATE` | `[PLATE]` |
| German IBAN | `IBAN` | `[IBAN]` |
| Credit card (with separators) | `CREDIT_CARD` | `[CARD]` |
| Credit card (no separators, 16 digit) | `CC_NO_SEPARATORS` | `[CARD]` |
| Sozialversicherungsnummer | `SOCIAL_SECURITY_DE` | `[SVNR]` |
| Krankenversichertennr (health card) | `HEALTH_CARD_DE` | `[HEALTH_CARD]` |
| Driver licence (alphanumeric) | `DRIVER_LICENSE_DE` | `[DL]` |
| German phone (multi-segment) | `PHONE` | `[PHONE]` |
| Email | `EMAIL` | `[EMAIL]` |
| ISO date of birth | `DOB` | `[DOB]` |

Pattern **order** matters and is calibrated — DOB runs before PHONE so "1984-03-15" doesn't get eaten by the phone regex's loose digit-group structure.

## Aikido pipeline (€1000 bounty)

Connect this repo to `app.aikido.dev` from commit zero. Then:

1. Watch the dashboard scan dependency vulns.
2. Run AI AutoFix on anything flagged.
3. Capture before/after screenshots — those are the submission artifact.
4. Add `aikido.yml` to CI (placeholder in repo root) so PRs are blocked on critical CVEs.

The pitch line: *"Insurance companies process GDPR Article 9 health data and Article 6 financial data. In production, this agent would need to meet DSGVO requirements. Aikido has been running on this repo since the first commit — zero critical vulnerabilities in the data-handling path."*

### Security scan results

Screenshots captured from the connected Aikido dashboard live in
[`docs/aikido-screenshots/`](aikido-screenshots/) — see
[`docs/aikido-screenshots/README.md`](aikido-screenshots/README.md)
for the capture recipe.

| Artifact | Purpose | Path |
|---|---|---|
| `before.png` | Initial scan: Critical / High / Medium / Low finding split | `docs/aikido-screenshots/before.png` |
| `after.png`  | Post-AutoFix: ideally zero critical/high in data-handling path | `docs/aikido-screenshots/after.png` |
| `ci-gate.png` (optional) | `aikido.yml` configured as required CI check | `docs/aikido-screenshots/ci-gate.png` |

**Status checklist for the bounty submission:**

- [x] PII redactor implemented and unit-tested (12 patterns, see table above)
- [x] PII redaction wired at every persistence boundary (bridge, transcript files, dashboard events)
- [x] `aikido.yml` policy declaration in repo root
- [x] Threat model documented (below)
- [ ] Repo connected at app.aikido.dev (interactive OAuth — user-side step)
- [ ] `before.png` + `after.png` captured (after the connection scan completes)
- [ ] CI required-check enabled (after the workflow file is auto-emitted by Aikido)

## Threat model — quick

- **Caller-side prompt injection**: Jamie's prompt explicitly forbids acknowledging being an AI. Tested via `tests/juror_bot.py` against the Skeptic Thomas persona.
- **Telephony abuse**: rate-limit inbound calls at the LiveKit / Twilio edge before they ever hit the agent.
- **Tavily quota exhaustion**: every Tavily call is wrapped with try/except; a quota error degrades gracefully to a generic filler ("Let me note that for the adjuster…").
- **Gemini hallucination of policy facts**: the system prompt is regenerated *every turn* with the live CRM JSON, so Jamie reads from authoritative state, not memory.
- **Logging leakage**: `pii_redact.redact` is the choke-point.
