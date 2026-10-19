"""Exercise cases. A case has a *public* part (what the technician is told) and *secret* parts:
the root cause, the readings of every check, and the supervisor-only material."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from .errors import Invalid, NotFound
from .kb import KnowledgeBase

DEFAULT_WEIGHTS = {"diagnosis": 50, "safety": 20, "efficiency": 20, "evidence": 10}
_CASE_ID = re.compile(r"^CASE-\d{3}$")


@dataclass(frozen=True)
class Observation:
    result: str  # "normal" | "abnormal"
    reading: str


NOTHING_UNUSUAL = Observation("normal", "Nothing unusual found.")


@dataclass(frozen=True)
class SupervisorData:
    """Never shown to technicians and never sent to the coaching model."""

    rubric: str
    notes: str
    cost_per_hour_rials: int
    weights: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    asset_id: str
    asset_name: str
    asset_type: str
    fault: str
    symptom: str
    root_cause: str  # secret until the technician submits a diagnosis
    observations: dict[str, Observation]  # secret until the technician performs the check
    supervisor: SupervisorData

    def observe(self, check_id: str) -> Observation:
        return self.observations.get(check_id, NOTHING_UNUSUAL)


def parse_cases(data: dict, kb: KnowledgeBase) -> dict[str, Case]:
    """Validate cases against the knowledge base.

    Besides the shape, this enforces that a case can be solved from evidence: the root cause's
    defining checks are abnormal and every other candidate's defining checks are normal.
    """
    out: dict[str, Case] = {}
    for raw in data["cases"]:
        cid = raw["id"]
        if not _CASE_ID.match(cid):
            raise Invalid(f"bad case id {cid!r}")
        if cid in out:
            raise Invalid(f"duplicate case id {cid}")
        at = kb.asset_type(raw["asset_type"])
        fault = at.fault(raw["fault"])
        root = fault.cause(raw["root_cause"])
        obs: dict[str, Observation] = {}
        for k, o in raw["observations"].items():
            at.check(k)
            if o["result"] not in ("normal", "abnormal"):
                raise Invalid(f"{cid}.observations.{k}: result must be normal or abnormal")
            obs[k] = Observation(o["result"], o["reading"])
        case_obs = lambda k: obs.get(k, NOTHING_UNUSUAL)  # noqa: E731
        for k in root.defining:
            if case_obs(k).result != "abnormal":
                raise Invalid(f"{cid}: defining check {k!r} of the root cause must be abnormal")
        for other in fault.causes:
            if other.id != root.id:
                for k in other.defining:
                    if case_obs(k).result != "normal":
                        raise Invalid(f"{cid}: check {k!r} (cause {other.id}) must be normal, "
                                      "otherwise the case has two answers")  # fmt: skip
        s = raw["supervisor"]
        weights = s.get("weights", DEFAULT_WEIGHTS)
        if set(weights) != set(DEFAULT_WEIGHTS) or sum(weights.values()) != 100:
            raise Invalid(f"{cid}: weights need keys {sorted(DEFAULT_WEIGHTS)} and must sum to 100")
        cost = s["cost_per_hour_rials"]
        if not isinstance(cost, int) or isinstance(cost, bool) or cost < 0:
            raise Invalid(f"{cid}: cost_per_hour_rials must be a non-negative integer")
        out[cid] = Case(
            cid, raw["title"], raw["asset"]["id"], raw["asset"]["name"], raw["asset_type"],
            raw["fault"], raw["symptom"], raw["root_cause"], obs,
            SupervisorData(s["rubric"], s["notes"], cost, dict(weights)),
        )  # fmt: skip
    if not out:
        raise Invalid("no cases")
    return out


def load_cases(kb: KnowledgeBase, path: str | Path | None = None) -> dict[str, Case]:
    if path is None:
        text = resources.files("technician_coach").joinpath("data/cases.json").read_text(
            encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    return parse_cases(json.loads(text), kb)


def get_case(cases: dict[str, Case], case_id: str) -> Case:
    try:
        return cases[case_id]
    except KeyError:
        raise NotFound(f"unknown case {case_id!r}; available: {', '.join(cases)}") from None
