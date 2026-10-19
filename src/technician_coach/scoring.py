"""Deterministic scoring. Code decides the numbers; the language model only writes the narrative,
so a clever note or a manipulated model answer cannot change a score."""

from __future__ import annotations

from dataclasses import dataclass

from .kb import AssetType
from .session import Session


@dataclass(frozen=True)
class Score:
    total: int
    parts: dict[str, int]
    weights: dict[str, int]
    facts: dict


def score_session(session: Session, asset_type: AssetType) -> Score:
    case = session.case
    w = case.supervisor.weights
    fault = asset_type.fault(case.fault)
    root = fault.cause(case.root_cause)
    optimal = list(root.defining)
    optimal_minutes = sum(asset_type.checks[k].minutes for k in optimal)
    actual_minutes = session.minutes_spent
    done = session.done()

    diagnosis = w["diagnosis"] if session.correct else 0
    safety = max(0, w["safety"] - (w["safety"] // 2) * len(session.blocked))
    efficiency = (w["efficiency"] * optimal_minutes // max(optimal_minutes, actual_minutes)) if actual_minutes else 0

    evidence = 0
    if session.diagnosis:
        chosen = fault.cause(session.diagnosis)
        seen = [k for k in chosen.defining if k in done]
        supported = [k for k in seen if done[k].result == "abnormal"]
        if len(supported) == len(chosen.defining):
            evidence = w["evidence"]
        elif supported:
            evidence = w["evidence"] // 2

    parts = {"diagnosis": diagnosis, "safety": safety, "efficiency": efficiency, "evidence": evidence}
    extra = max(0, actual_minutes - optimal_minutes)
    facts = {
        "optimal_checks": optimal,
        "optimal_minutes": optimal_minutes,
        "actual_minutes": actual_minutes,
        "extra_minutes": extra,
        "blocked_attempts": len(session.blocked),
        "checks_performed": len(session.checks),
        "avoidable_cost_rials": extra * case.supervisor.cost_per_hour_rials // 60,
    }
    return Score(sum(parts.values()), parts, dict(w), facts)
