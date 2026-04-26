"""Tavily real-time context lookups, exposed as Gemini function-calls.

When Jamie gets an accident location she invisibly looks up:
  - current/today's weather + road conditions there
  - 24h Abschleppdienst (towing) options
  - basic address verification

This is the single biggest Turing-test win — "I see on my system there were
heavy rains in that area this morning" makes the juror think they're talking
to a real human checking a real internal tool.

Falls back to a stub when TAVILY_API_KEY is not set so the demo never breaks.
"""

from __future__ import annotations

import os
from typing import Any

try:
    from tavily import TavilyClient  # type: ignore
    _HAVE_TAVILY = True
except Exception:
    _HAVE_TAVILY = False


def _client() -> Any | None:
    if not _HAVE_TAVILY:
        return None
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        return None
    return TavilyClient(api_key=key)


# ---- canonical functions Jamie's tool-calling LLM can invoke -------------

def lookup_weather(location: str) -> dict[str, Any]:
    """Return current weather + road conditions for a location.

    Uses topic="news" + time_range="day" + country="germany" so the result is
    fresh, relevant German weather/traffic coverage instead of generic
    knowledge-graph hits.  include_answer="advanced" asks Tavily to
    synthesise a one-paragraph summary across the top results — Jamie
    can quote that summary verbatim ("I see there were heavy rains in
    your area this morning") which is the single highest-impact Turing-
    test moment per the OPERATION doc.
    """
    c = _client()
    if c is None:
        return {
            "stub": True,
            "summary": (
                f"Heavy rain in {location} today (stubbed — set TAVILY_API_KEY "
                "for live data)."
            ),
            "source_urls": [],
        }
    try:
        res = c.search(
            query=f"weather road conditions {location} today",
            topic="news",
            time_range="day",
            country="germany",
            max_results=3,
            include_answer="advanced",
            search_depth="basic",
        )
    except Exception as e:
        return {"error": str(e), "summary": None, "source_urls": []}
    summary = res.get("answer") or (res["results"][0]["content"] if res.get("results") else None)
    return {
        "stub": False,
        "summary": summary,
        "source_urls": [r["url"] for r in res.get("results", [])[:3]],
    }


def lookup_traffic(location: str) -> dict[str, Any]:
    """Live traffic incidents / road closures at the location.

    Different signal than weather: "There's a closure on the A4 near
    Köln today" is a highly believable, specific moment that no
    canned-script bot would offer.  Uses news + day window so we never
    quote stale incident reports.
    """
    c = _client()
    if c is None:
        return {
            "stub": True,
            "summary": f"No live traffic data — stub mode for {location}",
            "source_urls": [],
        }
    try:
        res = c.search(
            query=f"traffic accident road closure {location} today",
            topic="news",
            time_range="day",
            country="germany",
            max_results=3,
            include_answer="advanced",
            search_depth="basic",
        )
    except Exception as e:
        return {"error": str(e), "summary": None, "source_urls": []}
    return {
        "stub": False,
        "summary": res.get("answer") or (res["results"][0]["content"] if res.get("results") else None),
        "source_urls": [r["url"] for r in res.get("results", [])[:3]],
    }


def lookup_towing(location: str) -> dict[str, Any]:
    """Return 24h tow / Abschleppdienst options for a location."""
    c = _client()
    if c is None:
        return {
            "stub": True,
            "summary": f"ADAC Pannenhilfe (stubbed) for {location}",
            "source_urls": [],
        }
    try:
        res = c.search(
            query=f"24 hour Abschleppdienst near {location}",
            country="germany",
            max_results=3,
            include_answer="advanced",
        )
    except Exception as e:
        return {"error": str(e), "summary": None, "source_urls": []}
    return {
        "stub": False,
        "summary": res.get("answer") or (res.get("results") or [{}])[0].get("content"),
        "source_urls": [r["url"] for r in res.get("results", [])[:3]],
    }


def lookup_address(query: str) -> dict[str, Any]:
    """Loose address sanity-check via Tavily.  Used to confirm a German
    address parses to a real location before quoting it in claim docs."""
    c = _client()
    if c is None:
        return {"stub": True, "summary": query, "source_urls": []}
    try:
        res = c.search(
            query=f"{query} address verification Germany",
            country="germany",
            max_results=2,
        )
    except Exception as e:
        return {"error": str(e), "summary": None, "source_urls": []}
    return {
        "stub": False,
        "summary": (res.get("results") or [{}])[0].get("content"),
        "source_urls": [r["url"] for r in res.get("results", [])[:2]],
    }


def lookup_qa(question: str) -> dict[str, Any]:
    """Direct fact-check using Tavily's qna_search.  Returns one
    synthesised answer string instead of a list of search results.
    Useful for "Is HUK-Coburg one of Germany's largest motor insurers?"
    style questions where Jamie wants to verify a claim before
    affirming it."""
    c = _client()
    if c is None:
        return {"stub": True, "answer": "(no Tavily key)"}
    try:
        ans = c.qna_search(
            query=question,
            topic="general",
            days=7,
            max_results=4,
        )
        return {"stub": False, "answer": ans}
    except Exception as e:
        return {"error": str(e), "answer": None}


# ---- Gemini-style function-declaration objects ---------------------------

GEMINI_TOOL_DECLS = [
    {
        "name": "tavily_lookup_weather",
        "description": (
            "Look up the current/today's weather and road conditions at a "
            "location.  Use this immediately after the caller mentions where "
            "the accident happened, so you can reference real conditions."
        ),
        "parameters": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    },
    {
        "name": "tavily_lookup_towing",
        "description": "Find 24h towing services near an accident location.",
        "parameters": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    },
    {
        "name": "tavily_lookup_address",
        "description": "Sanity-check / look up an address mentioned by the caller.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "tavily_lookup_traffic",
        "description": (
            "Look up live traffic incidents and road closures at an "
            "accident location.  Use after the caller names an Autobahn "
            "or major road — gives Jamie a believable specific fact like "
            "'I see there's a closure on the A4 near you today.'"
        ),
        "parameters": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    },
    {
        "name": "tavily_lookup_qa",
        "description": (
            "Direct one-line fact-check.  Use when the caller states "
            "something Jamie wants to verify before affirming, e.g. "
            "'Is HUK-Coburg the largest German motor insurer?'"
        ),
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
]

DISPATCH = {
    "tavily_lookup_weather": lookup_weather,
    "tavily_lookup_towing": lookup_towing,
    "tavily_lookup_address": lookup_address,
    "tavily_lookup_traffic": lookup_traffic,
    "tavily_lookup_qa": lookup_qa,
}


# --- self-test -------------------------------------------------------------
if __name__ == "__main__":
    import json
    print(json.dumps(lookup_weather("Köln-Ost A4"), indent=2, ensure_ascii=False))
