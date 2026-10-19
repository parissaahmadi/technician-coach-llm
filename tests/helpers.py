"""Shared test helpers."""

import copy
import json
from importlib import resources

from technician_coach import cases as cases_mod
from technician_coach import kb as kb_mod
from technician_coach.llm import MockLLM
from technician_coach.service import CoachService

CANARY = {
    "rubric": "CANARY-RUBRIC-Q7X",
    "notes": "CANARY-NOTES-M3Z",
    "unperformed": "CANARY-UNPERFORMED-K9P",
    "cost": 987654321,
}


def raw(name):
    return json.loads(resources.files("technician_coach").joinpath(f"data/{name}.json").read_text(encoding="utf-8"))


def kb():
    return kb_mod.load_kb()


def canary_cases(k=None):
    """CASE-001 with canary strings in every secret field."""
    k = k or kb()
    data = copy.deepcopy(raw("cases"))
    c = data["cases"][0]
    c["observations"]["leak_check"] = {"result": "normal", "reading": CANARY["unperformed"]}
    c["observations"]["inlet_valve"] = {"result": "normal", "reading": CANARY["unperformed"]}
    c["supervisor"]["rubric"] = CANARY["rubric"]
    c["supervisor"]["notes"] = CANARY["notes"]
    c["supervisor"]["cost_per_hour_rials"] = CANARY["cost"]
    return cases_mod.parse_cases(data, k)


def service(llm=None, cases=None):
    k = kb()
    return CoachService(k, cases or cases_mod.load_cases(k), llm or MockLLM())


class RecordingLLM:
    """Wraps another client and keeps every prompt that was sent."""

    def __init__(self, inner=None):
        self.inner = inner or MockLLM()
        self.name = "recording"
        self.calls = []  # (system, user)

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.inner.complete(system, user)

    @property
    def prompts(self):
        return [u for _, u in self.calls]


class ScriptedLLM:
    """Returns the given replies in order; an Exception instance is raised instead."""

    name = "scripted"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r)


def walk_keys(obj):
    """Every dict key anywhere inside a JSON-like object."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_keys(v)
