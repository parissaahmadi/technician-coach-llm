"""Users, roles and the permission table. Checked in code, never delegated to the model."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import Forbidden

TECHNICIAN = "technician"
SUPERVISOR = "supervisor"

# Fictional users.
DEFAULT_USERS: dict[str, str] = {
    "tech.a": TECHNICIAN,
    "tech.b": TECHNICIAN,
    "sup.c": SUPERVISOR,
}

# action -> roles allowed. Ownership rules (own session only) are enforced in the service.
PERMISSIONS: dict[str, frozenset[str]] = {
    "start_session": frozenset({TECHNICIAN, SUPERVISOR}),
    "work_session": frozenset({TECHNICIAN, SUPERVISOR}),  # checks, notes, coach, diagnose (owner only)
    "view_technician": frozenset({TECHNICIAN, SUPERVISOR}),  # technicians: own sessions only
    "view_supervisor": frozenset({SUPERVISOR}),
    "assess": frozenset({SUPERVISOR}),
    "list_all_sessions": frozenset({SUPERVISOR}),
}


@dataclass(frozen=True)
class Principal:
    username: str
    role: str

    def require(self, action: str) -> None:
        if self.role not in PERMISSIONS[action]:
            raise Forbidden(f"{self.username} ({self.role}) is not allowed to {action.replace('_', ' ')}")


def lookup(users: dict[str, str], username: str) -> Principal:
    if not isinstance(username, str) or username not in users:
        raise Forbidden(f"unknown user {username!r}")
    return Principal(username, users[username])
