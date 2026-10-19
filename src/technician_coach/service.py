"""Application layer: who may do what, and the only place sessions are created and read."""

from __future__ import annotations

from .assessment import Assessment, assess
from .cases import Case, get_case
from .coach import advise
from .errors import Conflict, Forbidden, NotFound
from .kb import KnowledgeBase
from .llm import LLM
from .roles import DEFAULT_USERS, SUPERVISOR, Principal, lookup
from .session import Session
from .views import supervisor_view, technician_view


class CoachService:
    def __init__(self, kb: KnowledgeBase, cases: dict[str, Case], llm: LLM,
                 users: dict[str, str] | None = None) -> None:  # fmt: skip
        self.kb = kb
        self.cases = cases
        self.llm = llm
        self.users = dict(users if users is not None else DEFAULT_USERS)
        self._sessions: dict[str, Session] = {}
        self._assessments: dict[str, Assessment] = {}

    # ------------------------------------------------------------------ helpers
    def _who(self, actor: str) -> Principal:
        return lookup(self.users, actor)

    def _session(self, sid: str) -> Session:
        try:
            return self._sessions[sid]
        except KeyError:
            raise NotFound(f"session {sid!r} does not exist") from None

    def _owned(self, actor: str, sid: str) -> Session:
        """Working on a session (checks, notes, coach, diagnosis) is for its owner only."""
        who = self._who(actor)
        who.require("work_session")
        s = self._session(sid)
        if s.owner != who.username:
            raise Forbidden(f"session {sid} belongs to {s.owner}")
        return s

    # ------------------------------------------------------------------ sessions
    def start(self, actor: str, case_id: str) -> str:
        who = self._who(actor)
        who.require("start_session")
        case = get_case(self.cases, case_id)
        sid = f"S-{len(self._sessions) + 1:04d}"
        self._sessions[sid] = Session(sid, who.username, case, self.kb.asset_type(case.asset_type))
        return sid

    def list_sessions(self, actor: str) -> list[str]:
        who = self._who(actor)
        if who.role == SUPERVISOR:
            return list(self._sessions)
        return [sid for sid, s in self._sessions.items() if s.owner == who.username]

    def apply_loto(self, actor: str, sid: str) -> bool:
        return self._owned(actor, sid).apply_loto()

    def perform_check(self, actor: str, sid: str, check_id: str) -> tuple[dict, bool]:
        rec, repeated = self._owned(actor, sid).perform_check(check_id)
        return {"check": rec.check_id, "result": rec.result, "reading": rec.reading}, repeated

    def add_note(self, actor: str, sid: str, text: str) -> str:
        return self._owned(actor, sid).add_note(text)

    def ask_coach(self, actor: str, sid: str) -> dict:
        s = self._owned(actor, sid)
        if s.status != "active":
            raise Conflict(f"session {sid} is finished")
        advice = advise(s, self.llm).to_dict()
        s.record_coach(advice)
        return advice

    def diagnose(self, actor: str, sid: str, cause_id: str) -> dict:
        s = self._owned(actor, sid)
        s.diagnose(cause_id)
        return technician_view(s)["debrief"]

    # ------------------------------------------------------------------ views
    def technician_view(self, actor: str, sid: str) -> dict:
        who = self._who(actor)
        who.require("view_technician")
        s = self._session(sid)
        if who.role != SUPERVISOR and s.owner != who.username:
            raise Forbidden(f"session {sid} belongs to {s.owner}")
        return technician_view(s)

    def assessment(self, actor: str, sid: str) -> dict:
        """Supervisor only, and only for a finished session."""
        who = self._who(actor)
        who.require("assess")
        s = self._session(sid)
        if s.status != "finished":
            raise Conflict(f"session {sid} is not finished; the technician has not submitted a diagnosis")
        if sid not in self._assessments:
            self._assessments[sid] = assess(s, self.llm)
        return supervisor_view(s, self._assessments[sid])
