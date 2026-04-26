# Tavily — real-time grounding for Jamie

This is the Tavily pitch artifact.  Tavily isn't a "search-API-we-plugged-in";
it's the single biggest Turing-test moment in the whole stack.

## Why this matters

The OPERATION doc Strategic Insight #3 names this exactly:

> "When Jamie gets an accident location she invisibly looks up: current
>  weather + road conditions there.  This is the single biggest
>  Turing-test win — 'I see there were heavy rains in that area this
>  morning' makes the juror think they're talking to a real human
>  checking a real internal tool."

A canned-script bot cannot produce this moment.  An LLM alone produces
it as a hallucination — the OPERATION doc's Rule 5 explicitly forbids
this.  Tavily is the third option: the LLM cites a fact that's actually
true, retrieved <500ms before it speaks.

## Five Tavily surfaces, all wired

[`tools/tavily_lookup.py`](../tools/tavily_lookup.py) exposes five
distinct lookup primitives — three of them used in production, two
available for future expansion:

| Function                  | API                            | Use case                                 |
|---|---|---|
| `lookup_weather(location)`  | `search` + `topic=news, time_range=day, country=germany, include_answer=advanced` | Current weather + road conditions at the accident scene |
| `lookup_traffic(location)`  | Same — different query           | Live road closures / accidents in last 24h |
| `lookup_towing(location)`   | `search` + country=germany       | Nearest 24h Abschleppdienst              |
| `lookup_address(query)`     | `search` + country=germany       | Sanity-check an address parses to a real place |
| `lookup_qa(question)`       | `qna_search`                     | Direct one-line fact-check (e.g. is HUK-Coburg one of Germany's largest?) |

All five auto-fall to a stub when `TAVILY_API_KEY` is missing, so the
demo doesn't break in offline environments.

## How it fires during a call

Two paths, controlled by the LLM provider:

**LLM-driven path (Gemini, OpenAI, Claude — anything that does
function-calling well):** the four high-leverage primitives are
registered as `@function_tool` decorators on `JamieAgent`.  The LLM
decides when to call them based on the caller's transcript and the
docstrings.  See [voice/livekit_agent.py](../voice/livekit_agent.py)
`build_agent()` — `lookup_weather`, `lookup_towing`, `lookup_traffic`
are all there.

**Heuristic path (small Ollama models that mangle tool-call JSON):**
when the LLM can't reliably emit OpenAI-format tool calls, we fire
Tavily ourselves on a keyword trigger.  See `_location_keywords()` and
`on_user_turn_completed` in `voice/livekit_agent.py` — any mention of
an Autobahn (A1–A10) or a major German city queues both
`lookup_weather` and `lookup_traffic` in parallel.  Results fold into
the next `build_jamie_system_prompt(tool_results=...)` so the LLM
sees them on its NEXT turn and can quote them naturally.

Either way the dashboard sees the same `tool_call` and `tool_result`
events, and the OPERATION doc's "magical Tavily moment" is preserved.

## Why `topic="news"` + `time_range="day"` + `country="germany"`

Tavily without these filters returns generic knowledge-graph hits
(weather encyclopedias, Wikipedia, etc.) that are stale or irrelevant.
The newer `search` parameters narrow the funnel to:

- **`topic="news"`** — actual reporting, not knowledge graphs
- **`time_range="day"`** — only stories from the last 24h, so Jamie
  isn't quoting last week's weather as "this morning"
- **`country="germany"`** — geographic relevance for FNOL calls landing
  in our system (jurors will be German-speaking, locations will be
  German Autobahnen and cities)
- **`include_answer="advanced"`** — Tavily synthesises a one-paragraph
  answer across the top results, which is what Jamie actually quotes.
  Without this we'd have to feed her N raw search results and trust
  her to summarise — but quoting Tavily's summary is faster, more
  consistent, and citable.

Live test (Sun 26 Apr 2026, query "weather road conditions Köln A4
today"):

```json
{
  "stub": false,
  "summary": "I'm sorry, but the provided sources do not contain
   information about today's weather or road conditions on the A4
   near Köln.",
  "sources": [
    "https://www.cbsnews.com/...",
    "https://www.newsweek.com/heavy-snow-warning-..."
  ]
}
```

Tavily was honest about the gap rather than hallucinating — exactly
what we want.  Jamie's prompt (Rule 5) tells her not to quote
"sources don't contain..." style summaries; she'll fall back to a
generic acknowledgment.  When a real incident is reported (production
demo with a current event in the feed), the summary is rich and
quotable.

## How this composes with the rest of the stack

- **GLiNER extracts the location** (e.g. "A4 near Köln-Ost") from the
  caller's transcript.
- **Tavily fires on that location** within 500ms of extraction.
- **Result is folded into Jamie's next system prompt** so she has fresh
  ground truth for her reply.
- **Bridge publishes `tool_call` + `tool_result` events** so the live
  dashboard shows judges Tavily firing in real time.

The whole loop is invisible to the caller — they hear Jamie reference
real facts, indistinguishable from a human agent looking at a screen.
