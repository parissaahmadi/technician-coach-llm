"""The coaching turn: build a technician-safe context, call the model, validate what comes back."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from . import heuristics, prompts
from .errors import LLMBadResponse, LLMUnavailable
from .llm import LLM
from .session import Session

MAX_MESSAGE = 600
MAX_WARNINGS = 4
MAX_WARNING_LEN = 300
FALLBACK_NOTICE = "(The AI service was not available, so this advice comes from the standard fault tree.) "


@dataclass(frozen=True)
class Advice:
    message: str
    next_checks: tuple[str, ...]
    safety_warnings: tuple[str, ...]
    leading_cause: str | None
    escalate: bool
    degraded: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["next_checks"] = list(self.next_checks)
        d["safety_warnings"] = list(self.safety_warnings)
        return d


def clean_text(value: object, limit: int) -> str:
    """Printable text only, whitespace collapsed, cut to ``limit`` characters."""
    if not isinstance(value, str):
        return ""
    kept = "".join(ch if ch.isprintable() or ch in "\n\t " else " " for ch in value)
    return " ".join(kept.split())[:limit]


def build_context(session: Session) -> dict:
    """Everything the coaching model may see: public knowledge plus what THIS technician has done.

    Deliberately absent: the root cause, the readings of checks not yet performed, the rubric,
    supervisor notes, costs, scores. (``tests/test_leaks.py`` enforces this with canary strings.)
    """
    at = session.asset_type
    fault = at.fault(session.case.fault)
    return {
        "asset": {"id": session.case.asset_id, "name": session.case.asset_name, "type": at.label},
        "fault_title": fault.title,
        "symptom": session.case.symptom,
        "causes": [{"id": c.id, "label": c.label, "weight": c.weight, "defining": list(c.defining)}
                   for c in fault.causes],  # fmt: skip
        "checks": [{"id": c.id, "title": c.title, "minutes": c.minutes, "isolation": c.isolation}
                   for c in at.checks.values()],  # fmt: skip
        "done": [{"check": r.check_id, "result": r.result, "reading": r.reading} for r in session.checks],
        "loto": session.loto,
        "notes": list(session.notes),
        "safety": {"loto": at.loto, "extras": list(at.safety_extras)},
    }


def _parse_json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMBadResponse("no JSON object in the reply")
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError:
        raise LLMBadResponse("reply is not valid JSON") from None
    if not isinstance(obj, dict):
        raise LLMBadResponse("reply is not a JSON object")
    return obj


def validate_advice(obj: dict, ctx: dict) -> Advice:
    """Accept only what the context allows: known check ids, known cause ids, bounded text."""
    message = clean_text(obj.get("message"), MAX_MESSAGE)
    if not message:
        raise LLMBadResponse("reply has no message")
    known_checks = {c["id"]: c for c in ctx["checks"]}
    done = {d["check"] for d in ctx["done"]}
    nxt: list[str] = []
    raw_checks = obj.get("next_checks")
    for cid in raw_checks if isinstance(raw_checks, list) else []:
        if isinstance(cid, str) and cid in known_checks and cid not in done and cid not in nxt:
            nxt.append(cid)
    nxt = nxt[: heuristics.MAX_SUGGESTIONS]
    cause_ids = {c["id"] for c in ctx["causes"]}
    lead = obj.get("leading_cause")
    lead = lead if isinstance(lead, str) and lead in cause_ids else None
    warnings: list[str] = []
    raw_w = obj.get("safety_warnings")
    for w in raw_w if isinstance(raw_w, list) else []:
        text = clean_text(w, MAX_WARNING_LEN)
        if text and text not in warnings:
            warnings.append(text)
    # Safety is enforced here, whatever the model said.
    if any(known_checks[c]["isolation"] for c in nxt) and not ctx["loto"]:
        required = [ctx["safety"]["loto"], *ctx["safety"]["extras"][:1]]
        warnings = [r for r in required if r not in warnings] + warnings  # lockout text always first
    return Advice(message, tuple(nxt), tuple(warnings[:MAX_WARNINGS + 2]), lead,
                  obj.get("escalate") is True)  # fmt: skip


def advise(session: Session, llm: LLM) -> Advice:
    """One coaching turn. Parse failures get one repair attempt; an unavailable or unusable
    service degrades to the rule-based advice instead of failing the technician."""
    ctx = build_context(session)
    user = prompts.render("coach", ctx)
    try:
        try:
            return validate_advice(_parse_json_object(llm.complete(prompts.COACH_SYSTEM, user)), ctx)
        except LLMBadResponse:
            return validate_advice(
                _parse_json_object(llm.complete(prompts.COACH_SYSTEM, user + "\n\n" + prompts.REPAIR)), ctx)
    except (LLMUnavailable, LLMBadResponse):
        fb = validate_advice(heuristics.suggest(ctx), ctx)
        return Advice(FALLBACK_NOTICE + fb.message, fb.next_checks, fb.safety_warnings,
                      fb.leading_cause, fb.escalate, degraded=True)  # fmt: skip
