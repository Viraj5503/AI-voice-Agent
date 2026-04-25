"""Tracks the 13 data pillars Inca expects to see in a complete FNOL claim
(plus a few sub-pillars we collect so the dashboard looks rich).

The state is the source of truth for *what Jamie still needs to ask*.  The
GLiNER2 extractor pushes updates here; the prompt builder reads from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


# Order = priority order Jamie should gather these.  Pillar 1 ("policy &
# vehicle ID") is intentionally absent — it lives in the CRM, Jamie must
# never ask for it.
#
# Each entry is (id, short_descriptor).  We deliberately do NOT include a
# pre-written sample question — putting one in the system prompt biases
# the LLM to use that exact phrasing every turn (we observed this).
PILLARS: list[tuple[str, str]] = [
    ("injuries",              "anyone hurt — caller, passengers, third party"),
    ("accident_datetime",     "when (date + time)"),
    ("accident_location",     "where (address / road name)"),
    ("road_type",             "Autobahn / city street / parking lot / other"),
    ("how_it_happened",       "free-form description of the incident"),
    ("vehicle_drivable",      "is the car drivable + current location"),
    ("other_party_involved",  "was anyone else involved"),
    ("other_party_plate",     "other vehicle license plate"),
    ("other_party_insurer",   "other party's insurer"),
    ("police_involved",       "was police called"),
    ("police_case_number",    "police case / reference number"),
    ("witnesses",             "independent witnesses (name + contact)"),
    ("driver_identity",       "who was driving (if not policyholder)"),
    ("fault_admission",       "anything said about fault at the scene"),
    ("settlement_preference", "preferred repair shop / need a rental"),
]

FRAUD_LABELS: list[str] = [
    "delayed_reporting",
    "known_to_other_party",
    "vehicle_listed_for_sale",
    "prior_similar_incident",
    "timeline_inconsistency",
]


@dataclass
class ClaimState:
    """Mutable per-call state.  One instance per call."""

    call_id: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    pillars: dict[str, Any] = field(default_factory=dict)
    fraud_signals: dict[str, Any] = field(default_factory=dict)
    emotional_mode: str = "calm"  # calm | distressed | noisy
    notes: list[str] = field(default_factory=list)
    # Pillars Jamie has *asked about* in any prior turn — regardless of
    # whether the caller answered.  This is the load-bearing field that
    # stops the prompt from listing the same pillar in STILL NEEDED
    # turn after turn (the original repetition bug).
    asked_pillars: set[str] = field(default_factory=set)

    # ----- pillar updates -----
    def fill(self, label: str, value: str, confidence: float = 1.0) -> None:
        """Record a pillar value.  No-op if already filled with high confidence."""
        existing = self.pillars.get(label)
        if existing and existing.get("confidence", 0) >= confidence:
            return
        self.pillars[label] = {"value": value, "confidence": confidence,
                               "ts": datetime.now(timezone.utc).isoformat()}

    def flag_fraud(self, signal: str, evidence: str, severity: str = "medium") -> None:
        self.fraud_signals[signal] = {"evidence": evidence, "severity": severity,
                                       "ts": datetime.now(timezone.utc).isoformat()}

    def set_mode(self, mode: str) -> None:
        if mode in ("calm", "distressed", "noisy"):
            self.emotional_mode = mode

    def mark_asked(self, pillar_ids: "set[str] | list[str]") -> None:
        """Record that Jamie asked about these pillars in her last reply.
        Call after each Jamie turn with the result of
        agent.intent.classify_jamie_question(reply)."""
        self.asked_pillars |= set(pillar_ids)

    # ----- prompt-builder helpers -----
    def filled_summary(self) -> str:
        if not self.pillars:
            return "(none yet)"
        lines = []
        for label, data in self.pillars.items():
            lines.append(f"  - {label}: {data['value']}")
        return "\n".join(lines)

    def unfilled_pillars(self) -> list[tuple[str, str]]:
        return [(k, q) for (k, q) in PILLARS if k not in self.pillars]

    def unfilled_summary(self) -> str:
        """Legacy — keep for backwards compat.  Prefer unfilled_summary_compact."""
        unfilled = self.unfilled_pillars()
        if not unfilled:
            return "(all gathered — wrap up the call warmly)"
        return "\n".join(f"  - {label}: {hint}" for label, hint in unfilled)

    def unfilled_summary_compact(self) -> str:
        """Phrase the unfilled targets as opportunistic capture goals,
        NOT a question queue.  This is the load-bearing language that
        stops Jamie from sounding like a list-walker.

        Buckets:
          OPEN     — never asked, never answered: capture if the caller
                     opens a natural door, otherwise let it sit.
          PARKED   — already asked, no answer yet: do not push; capture
                     opportunistically only.
        """
        unfilled = self.unfilled_pillars()
        if not unfilled:
            return "(all targets captured — wrap up warmly with a reference number)"
        new = [(l, d) for (l, d) in unfilled if l not in self.asked_pillars]
        pending = [(l, d) for (l, d) in unfilled if l in self.asked_pillars]

        out: list[str] = []
        if new:
            out.append("OPEN TARGETS (capture only if the caller's last sentence opens a natural door — never read these as a checklist):")
            for lab, desc in new[:4]:
                out.append(f"  • {lab}  —  {desc}")
            if len(new) > 4:
                out.append(f"  …and {len(new) - 4} more, lower priority")
        if pending:
            out.append("\nPARKED (already touched on, do NOT re-raise — let the caller bring them up):")
            for lab, desc in pending:
                out.append(f"  · {lab}  —  {desc}")
        if not new and pending:
            out.insert(0, "Every target has been touched on at least once.  Stop probing; respond to whatever the caller raises and move toward closing.")
        return "\n".join(out)

    def fraud_risk_score(self) -> int:
        """Return 0..10 based on number/severity of flagged signals."""
        weight = {"low": 1, "medium": 2, "high": 4}
        total = sum(weight.get(s["severity"], 1) for s in self.fraud_signals.values())
        return min(10, total)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # asked_pillars is a set; JSON can't serialize sets.  Use a sorted
        # list (deterministic for snapshot tests + diff-friendly).
        d["asked_pillars"] = sorted(self.asked_pillars)
        return d
