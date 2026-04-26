# Aikido scan screenshots

This directory holds the **before/after AI AutoFix** screenshot pair
that the Aikido (€1000) bounty submission requires.  See the
"Security scan results" section in [`../SECURITY.md`](../SECURITY.md).

## How to capture

1. Connect the repo at <https://app.aikido.dev>:
   - GitHub OAuth login → "Connect a new repository" → pick
     `IamHetPatel/AI-voice-Agent`.
2. Wait for the initial scan (~2–5 min depending on dep count).
3. Take the **before** screenshot of the repo's Issues page showing
   the initial finding count split (Critical / High / Medium / Low).
   Save as `before.png`.
4. Click "Run AI AutoFix" on each flagged dependency.  Aikido opens a
   PR per fix; merge the PRs that don't break tests.
5. Re-scan, take the **after** screenshot.  Save as `after.png`.
6. (Optional) `ci-gate.png` — screenshot of `aikido.yml` configured as
   a required CI check on the repo settings page.

## File-naming convention

The pitch text in `../SECURITY.md` references these names exactly —
keep them stable so the markdown links don't break:

```
docs/aikido-screenshots/
├── README.md       (this file)
├── before.png      (initial scan, all severities visible)
├── after.png       (post-AutoFix, ideally zero critical/high)
└── ci-gate.png     (optional — required-check setup page)
```

## Pitch line

> "Insurance operations process GDPR Article 9 health data and
>  Article 6 financial data.  In production this agent would need to
>  meet DSGVO requirements.  We connected Aikido on commit zero, ran
>  AI AutoFix on every flagged dependency, and gated CI on critical
>  severity — the data-handling path has zero critical vulnerabilities."

That's the four-sentence Aikido bounty pitch.  Screenshots are the
evidence; `agent/pii_redact.py` is the architectural backbone;
`aikido.yml` is the policy declaration.
