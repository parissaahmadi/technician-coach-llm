"""What each role may see. Views are built from ALLOWLISTS: a new field on ``Session`` or ``Case``
stays invisible until someone adds it here on purpose."""

from __future__ import annotations

from .assessment import Assessment
from .session import Session


def technician_view(s: Session) -> dict:
    at = s.asset_type
    view = {
        "session_id": s.id,
        "technician": s.owner,
        "status": s.status,
        "case": {"id": s.case.id, "title": s.case.title},
        "asset": {"id": s.case.asset_id, "name": s.case.asset_name, "type": at.label},
        "symptom": s.case.symptom,
        "lockout_applied": s.loto,
        "checks": [
            {"id": r.check_id, "title": at.checks[r.check_id].title, "result": r.result, "reading": r.reading}
            for r in s.checks
        ],
        "notes": list(s.notes),
        "coach_messages": [
            {k: m[k] for k in ("message", "next_checks", "safety_warnings", "leading_cause", "escalate", "degraded")}
            for m in s.coach_messages
        ],
        "debrief": None,
    }
    if s.status == "finished":
        fault = at.fault(s.case.fault)
        actual = fault.cause(s.case.root_cause)
        view["debrief"] = {
            "your_diagnosis": fault.cause(s.diagnosis).label,
            "correct": s.correct,
            "actual_cause": actual.label,
            "recommended_fix": actual.fix,
        }
    return view


def supervisor_view(s: Session, a: Assessment) -> dict:
    """The technician view plus everything that is for supervisors only."""
    view = technician_view(s)
    c = s.case
    view["supervisor"] = {
        "score": {"total": a.score.total, "parts": a.score.parts, "weights": a.score.weights},
        "facts": a.score.facts,
        "rubric": c.supervisor.rubric,
        "case_notes": c.supervisor.notes,
        "cost_per_hour_rials": c.supervisor.cost_per_hour_rials,
        "blocked_attempts": list(s.blocked),
        "transcript": list(s.log),
        "narrative": {
            "text_written_by": a.narrative_source,
            "summary": a.summary,
            "strengths": list(a.strengths),
            "improvements": list(a.improvements),
        },
    }
    return view
