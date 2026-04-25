"""Tracks the 13 data pillars Inca expects to see in a complete FNOL claim
(plus a few sub-pillars we collect so the dashboard looks rich).

The state is the source of truth for *what Jamie still needs to ask*.  The
GLiNER2 extractor pushes updates here; the prompt builder reads from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


# Order = priority order in which Jamie should gather them.  Pillar 1
# ("policy & vehicle ID") is intentionally absent because it lives in the
# Known Context — Jamie must NEVER ask for it.
PILLARS: list[tuple[str, str]] = [
    ("injuries", "Are you or anyone else hurt? Anyone need an ambulance?"),
    ("accident_datetime", "When exactly did this happen? Date and time."),
    ("accident_location", "Where were you? Address or road name."),
    ("road_type", "Was that on the Autobahn, a city street, parking lot...?"),
    ("how_it_happened", "Walk me through what happened, in your own words."),
    ("vehicle_drivable", "Is the car still drivable? Where is it right now?"),
    ("other_party_involved", "Was another vehicle or person involved?"),
    ("other_party_plate", "Do you have the other party's license plate?"),
    ("other_party_insurer", "Do you know who their insurer is?"),
    ("police_involved", "Were the police called?"),
    ("police_case_number", "Do you have a police case or reference number?"),
    ("witnesses", "Were there any independent witnesses?"),
    ("driver_identity", "Were you driving, or was someone else?"),
    ("fault_admission", "Did anyone say anything about whose fault it was?"),
    ("settlement_preference", "Any preference on a repair shop, or do you need a rental?"),
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
        unfilled = self.unfilled_pillars()
        if not unfilled:
            return "(all gathered — wrap up the call warmly)"
        return "\n".join(f"  - {label}: {hint}" for label, hint in unfilled)

    def fraud_risk_score(self) -> int:
        """Return 0..10 based on number/severity of flagged signals."""
        weight = {"low": 1, "medium": 2, "high": 4}
        total = sum(weight.get(s["severity"], 1) for s in self.fraud_signals.values())
        return min(10, total)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
