"""Supervisor-only assessment: a deterministic score plus a model-written narrative."""

from __future__ import annotations

from dataclasses import dataclass

from . import prompts
from .coach import _parse_json_object, clean_text
from .errors import LLMBadResponse, LLMUnavailable
from .heuristics import mock_narrative
from .llm import LLM
from .scoring import Score, score_session
from .session import Session

MAX_ITEMS = 4


@dataclass(frozen=True)
class Assessment:
    score: Score
    summary: str
    strengths: tuple[str, ...]
    improvements: tuple[str, ...]
    narrative_source: str  # the client name ("mock", "anthropic:...") or "fallback"


def build_context(s: Session, score: Score) -> dict:
    """Includes secrets on purpose. Only ever built on the supervisor path."""
    at = s.asset_type
    fault = at.fault(s.case.fault)
    return {
        "asset": {"id": s.case.asset_id, "name": s.case.asset_name},
        "symptom": s.case.symptom,
        "actual_cause": fault.cause(s.case.root_cause).label,
        "submitted_cause": fault.cause(s.diagnosis).label if s.diagnosis else None,
        "correct": s.correct,
        "rubric": s.case.supervisor.rubric,
        "case_notes": s.case.supervisor.notes,
        "transcript": [dict(e) for e in s.log],
        "technician_notes": list(s.notes),
        "score": {"parts": score.parts, "weights": score.weights},
        "facts": score.facts,
    }


def _items(raw: object) -> tuple[str, ...]:
    out = []
    for x in raw if isinstance(raw, list) else []:
        text = clean_text(x, 200)
        if text:
            out.append(text)
    return tuple(out[:MAX_ITEMS])


def _from_obj(obj: dict) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    summary = clean_text(obj.get("summary"), 400)
    if not summary:
        raise LLMBadResponse("narrative has no summary")
    return summary, _items(obj.get("strengths")), _items(obj.get("improvements"))


def assess(s: Session, llm: LLM) -> Assessment:
    score = score_session(s, s.asset_type)  # numbers come from code only
    ctx = build_context(s, score)
    user = prompts.render("assessment", ctx)
    try:
        summary, strengths, improvements = _from_obj(_parse_json_object(llm.complete(prompts.ASSESS_SYSTEM, user)))
        source = llm.name
    except (LLMUnavailable, LLMBadResponse):
        summary, strengths, improvements = _from_obj(mock_narrative(ctx))
        source = "fallback"
    return Assessment(score, summary, strengths, improvements, source)
