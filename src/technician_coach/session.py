"""One troubleshooting exercise: the simulator and the record of what happened."""

from __future__ import annotations

from dataclasses import dataclass, field

from .cases import Case, Observation
from .errors import Conflict, Forbidden, Invalid
from .kb import AssetType

MAX_NOTE = 500
MAX_NOTES = 30


@dataclass(frozen=True)
class CheckRecord:
    check_id: str
    result: str
    reading: str


@dataclass
class Session:
    id: str
    owner: str
    case: Case
    asset_type: AssetType
    status: str = "active"  # active | finished
    loto: bool = False
    checks: list[CheckRecord] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)  # isolation checks tried without lockout
    notes: list[str] = field(default_factory=list)
    coach_messages: list[dict] = field(default_factory=list)
    diagnosis: str | None = None
    correct: bool | None = None
    log: list[dict] = field(default_factory=list)  # ordered transcript for the supervisor

    # ----------------------------------------------------------------- helpers
    def _log(self, kind: str, **data) -> None:
        self.log.append({"seq": len(self.log) + 1, "kind": kind, **data})

    def _active(self) -> None:
        if self.status != "active":
            raise Conflict(f"session {self.id} is finished")

    def done(self) -> dict[str, CheckRecord]:
        return {c.check_id: c for c in self.checks}

    # ----------------------------------------------------------------- actions
    def apply_loto(self) -> bool:
        """Returns False when lockout was already applied."""
        self._active()
        if self.loto:
            return False
        self.loto = True
        self._log("loto")
        return True

    def perform_check(self, check_id: str) -> tuple[CheckRecord, bool]:
        """Returns ``(record, repeated)``. A blocked attempt is recorded before the error is raised."""
        self._active()
        check = self.asset_type.check(check_id)  # Invalid for unknown ids
        existing = self.done().get(check_id)
        if existing:
            return existing, True
        if check.isolation and not self.loto:
            self.blocked.append(check_id)
            self._log("blocked", check=check_id)
            raise Forbidden(
                f"'{check.title}' needs the machine isolated. Apply lockout/tagout first "
                f"(command: loto). {self.asset_type.loto}"
            )
        obs: Observation = self.case.observe(check_id)
        rec = CheckRecord(check_id, obs.result, obs.reading)
        self.checks.append(rec)
        self._log("check", check=check_id, result=obs.result)
        return rec, False

    def add_note(self, text: str) -> str:
        self._active()
        clean = " ".join((text or "").split())
        if not 1 <= len(clean) <= MAX_NOTE:
            raise Invalid(f"a note must be 1-{MAX_NOTE} characters")
        if len(self.notes) >= MAX_NOTES:
            raise Conflict(f"at most {MAX_NOTES} notes per session")
        self.notes.append(clean)
        self._log("note", text=clean)
        return clean

    def record_coach(self, advice: dict) -> None:
        self._active()
        self.coach_messages.append(advice)
        self._log("coach", leading=advice.get("leading_cause"), degraded=advice.get("degraded", False))

    def diagnose(self, cause_id: str) -> bool:
        self._active()
        fault = self.asset_type.fault(self.case.fault)
        fault.cause(cause_id)  # NotFound with the candidate list for unknown ids
        if not self.checks:
            raise Invalid("perform at least one check before submitting a diagnosis")
        self.diagnosis = cause_id
        self.correct = cause_id == self.case.root_cause
        self.status = "finished"
        self._log("diagnosis", cause=cause_id, correct=self.correct)
        return self.correct

    # ----------------------------------------------------------------- derived
    @property
    def minutes_spent(self) -> int:
        return sum(self.asset_type.checks[c.check_id].minutes for c in self.checks)
