# Gradium bounty — three-tier integration + advanced API surface

This is the Gradium bounty pitch artifact (900k credits).  The scoring
is "best use of Gradium," and the competition will mostly stop at
"plugged in the LiveKit plugin."  This project goes meaningfully past
that line in five distinct ways.

## TL;DR — what we use that other teams won't

| Surface                                  | Where in repo                                              |
|---|---|
| Gradbot prototype (zero-config voice)    | [voice/gradbot_quickstart.py](../voice/gradbot_quickstart.py) |
| Direct streaming SDK (raw WebSocket)     | [voice/multiplex_demo.py](../voice/multiplex_demo.py)         |
| LiveKit plugin (production stack)        | [voice/livekit_agent.py](../voice/livekit_agent.py)           |
| Voice cloning from a 10-second sample    | [scripts/clone_voice.py](../scripts/clone_voice.py)           |
| Multiplexing (single WS, N concurrent calls) | [voice/multiplex_demo.py](../voice/multiplex_demo.py)     |
| Custom pronunciation dictionary (FNOL + German) | [scripts/setup_pronunciations.py](../scripts/setup_pronunciations.py) |
| Tone tuning via `json_config`            | [voice/livekit_agent.py:build_session](../voice/livekit_agent.py) |
| STT noise-hallucination fix              | [voice/livekit_agent.py:build_session](../voice/livekit_agent.py) |

## 1. Three integration tiers — same voice, different abstraction levels

**Tier 1 — Gradbot.**  Used as the H0–H4 prototype path.  Zero
config, voice loop in 50 lines.  Survives in the repo as the "grab
and go" demo for new teammates: `python voice/gradbot_quickstart.py`.

**Tier 2 — Direct Gradium SDK (`GradiumClient`).**  Used in
`voice/multiplex_demo.py` to drive multiple concurrent TTS streams
through a single WebSocket — the property real call centres care
about for production scale.  Each concurrent caller gets a unique
`client_req_id`; one connection multiplexes them with
`close_ws_on_eos=False`.

**Tier 3 — `livekit-plugins-gradium` for the production stack.**  Used
in `voice/livekit_agent.py` — wraps Gradium STT + TTS into the
livekit-agents `AgentSession` framework alongside Silero VAD and a
Gemini/Groq LLM.  This is what answers a real phone call routed
through the LiveKit Cloud SIP trunk.

Three tiers, not "one library shoehorned in."

## 2. Voice cloning — the Turing-test kicker

The flagship Emma voice is excellent, but every team in the room is
using it.  Jurors will pattern-match on it within 30 seconds.

`scripts/clone_voice.py` wraps `client.voice_create(audio_file=Path,
name=..., start_s=...)` so a 10-second clean sample becomes a fresh
`voice_id`:

```bash
# Record yourself or a friend reading the OPERATION_TURING_ADJUSTER doc's
# Jamie greeting, save as jamie_sample.wav, then:
python scripts/clone_voice.py jamie_sample.wav
# Copy the printed voice_id into .env as GRADIUM_VOICE_ID, restart.
```

The cloned voice has human micro-imperfections the catalog voice
doesn't — exactly what the Turing test rewards.

## 3. Custom pronunciation dictionary — discovered an undocumented API

Insurance professionals pattern-match how an agent pronounces FNOL,
DSGVO, IBAN, Vollkasko, HUK-Coburg in the first 30 seconds.  Saying
"FNOL" as "F-N-O-L" instead of "eff-noll" is an instant "AI" vote
from a real claims juror.

Gradium has a `/api/pronunciations/` endpoint that **isn't exposed
in the SDK** and isn't in the public docs WebFetch can see.  We
discovered it by probing the API directly:

```python
POST https://eu.api.gradium.ai/api/pronunciations/
{
  "name": "jamie-fnol",
  "language": "en",
  "rules": [
    {"original": "FNOL", "rewrite": "eff-noll"},
    {"original": "DSGVO", "rewrite": "Day-Es-Gay-Fau-Oh"},
    {"original": "Vollkasko", "rewrite": "Foll-kass-ko"},
    ... 26 rules total
  ]
}
```

The returned `uid` is passed to `gradium.TTS(pronunciation_id=uid)`
in `voice/livekit_agent.py`.  See
[scripts/setup_pronunciations.py](../scripts/setup_pronunciations.py)
for the full 26-rule dictionary covering insurance acronyms, German
Kasko terms, insurer brand names, Autobahn names, and common cities.

Idempotent: rerunning the script replaces any existing `jamie-fnol`
dict, so iterating on the rules takes one command.

## 4. Tone tuning via `json_config`

`gradium.TTS(json_config={...})` accepts the warmth-tuning parameters
the Gradium briefing calls out as the difference between "obviously
synthetic" and "passes for human":

```python
tts_kwargs["json_config"] = {
    "temp": 0.85,           # natural variation (vs. 0.7 default = robotic)
    "padding_bonus": 0.3,   # slightly slower, more deliberate pace
    "cfg_coef": 2.2,        # high voice consistency turn-to-turn
    "rewrite_rules": "en",  # English number/date pronunciation rules
}
```

All four are env-overridable (`GRADIUM_TEMP`, `GRADIUM_PADDING`,
`GRADIUM_CFG`, `GRADIUM_LANGUAGE`) so they can be tuned at the venue
without a redeploy.

## 5. STT noise-hallucination fix

Default Gradium STT (Whisper-style) speculatively produces words from
ambient noise.  Live testing surfaced phantom transcripts: "Marama",
"Englishman", "I live in Chicago", "Não é errado" — none of which
were said.  Setting `gradium.STT(temperature=0.0)` forces conservative
transcription; far fewer ghost words.

This is the single fix that turns the highway-background demo from
"unusable" to "robust" — exactly the consistency-across-noise
criterion in Inca's main prize.

## Demo points to emphasise

When Gradium reviews submissions, here's the order to pitch:

1. **"We didn't just plug in the LiveKit plugin — we use Gradium across
   three integration tiers and discovered an undocumented pronunciation
   API to handle insurance domain terms."**
2. **"Our voice has 26 custom German + English insurance pronunciations."**
   (Show the JSON.)
3. **"Multiplexing demo proves single-WebSocket scale."**
   (Run `voice/multiplex_demo.py`.)
4. **"Voice cloning means our Jamie isn't the same Emma every other team
   uses."**  (Play before/after.)
5. **"STT temperature=0.0 is the noise robustness fix Inca's brief
   asked for."**  (Show the difference live.)
