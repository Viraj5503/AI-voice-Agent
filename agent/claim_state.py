"""Tracks the 13 data pillars Inca expects to see in a complete FNOL claim
(plus a few sub-pillars we collect so the dashboard looks rich).

The state is the source of truth for *what Jamie still needs to ask*.  The
GLiNER2 extractor pushes updates here; the prompt builder reads from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


# Default targets for the insurance_fnol domain.  Used when no DomainConfig
# is supplied — keeps single-domain tests + the original quickstart working.
# For multi-domain, pass `targets=` (and optionally fraud_labels) into
# ClaimState; load via agent.domain.load_domain(...) to get them.
PILLARS: list[tuple[str, str]] = [
    ("claim_type",            "is this an auto, health, or medical claim?"),
    ("incident_datetime",     "when did the incident or medical event occur (date + time)"),
    ("incident_location",     "where (address, hospital, clinic, or road name)"),
    ("injuries_or_symptoms",  "description of injuries or health symptoms"),
    ("how_it_happened",       "free-form description of the incident or medical issue"),
    ("treatment_received",    "what medical treatment was received (if health claim)"),
    ("provider_name",         "name of doctor or hospital (if health claim)"),
    ("vehicle_drivable",      "is the car drivable (if auto claim)"),
    ("other_party_involved",  "was anyone else involved (other driver, doctor, etc.)"),
    ("police_or_ambulance",   "was police or ambulance called?"),
    ("witnesses",             "independent witnesses (if auto claim)"),
    ("fault_admission",       "anything said about fault at the scene (if auto claim)"),
    ("settlement_preference", "preferred repair shop or reimbursement method"),
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
    """Mutable per-call state.  One instance per call.

    `targets` defaults to the insurance-FNOL pillar list.  Pass a domain-
    specific list (from agent.domain.DomainConfig.targets) to use this
    state in any other domain — banking, telco, healthcare, etc.
    """

    call_id: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    pillars: dict[str, Any] = field(default_factory=dict)
    fraud_signals: dict[str, Any] = field(default_factory=dict)
    emotional_mode: str = "calm"  # calm | distressed | noisy
    notes: list[str] = field(default_factory=list)
    # Targets Jamie has *asked about* in any prior turn — regardless of
    # whether the caller answered.  Load-bearing field for anti-repetition.
    asked_pillars: set[str] = field(default_factory=set)
    # Domain-driven target list.  Defaults to insurance-FNOL pillars so
    # legacy tests / the single-domain quickstart still work unchanged.
    targets: list[tuple[str, str]] = field(default_factory=lambda: list(PILLARS))

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
        # Use this state's domain-driven `targets`, NOT the module-level
        # PILLARS — that's the multi-domain hook.
        return [(k, q) for (k, q) in self.targets if k not in self.pillars]

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
        n_total = len(self.targets)
        if not unfilled:
            return f"(all {n_total} targets captured — wrap up warmly with a reference number)"
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
