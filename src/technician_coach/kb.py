"""Fault-tree knowledge base: asset types, checks, faults and their candidate causes.

Everything in here is *public* to technicians. Case-specific secrets live in ``cases.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .errors import Invalid, NotFound

_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    minutes: int
    isolation: bool  # needs lockout/tagout first
    how: str


@dataclass(frozen=True)
class Cause:
    id: str
    label: str
    weight: int  # relative likelihood, 1..100
    defining: tuple[str, ...]  # checks whose result decides this cause
    fix: str


@dataclass(frozen=True)
class Fault:
    id: str
    title: str
    causes: tuple[Cause, ...]

    def cause(self, cause_id: str) -> Cause:
        for c in self.causes:
            if c.id == cause_id:
                return c
        raise NotFound(f"cause {cause_id!r} is not a candidate for '{self.id}'; "
                       f"candidates: {', '.join(c.id for c in self.causes)}")  # fmt: skip


@dataclass(frozen=True)
class AssetType:
    id: str
    label: str
    loto: str
    safety_extras: tuple[str, ...]
    checks: dict[str, Check]
    faults: dict[str, Fault]

    def check(self, check_id: str) -> Check:
        try:
            return self.checks[check_id]
        except KeyError:
            raise Invalid(f"unknown check {check_id!r} for {self.label}; "
                          f"valid checks: {', '.join(self.checks)}") from None  # fmt: skip

    def fault(self, fault_id: str) -> Fault:
        try:
            return self.faults[fault_id]
        except KeyError:
            raise NotFound(f"unknown fault {fault_id!r} for {self.label}") from None


@dataclass(frozen=True)
class KnowledgeBase:
    asset_types: dict[str, AssetType]

    def asset_type(self, type_id: str) -> AssetType:
        try:
            return self.asset_types[type_id]
        except KeyError:
            raise NotFound(f"unknown asset type {type_id!r}") from None


def _ident(value: object, where: str) -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise Invalid(f"{where}: bad identifier {value!r}")
    return value


def parse_kb(data: dict) -> KnowledgeBase:
    """Validate and convert raw JSON. Raises ``Invalid`` with the location of the first problem."""
    types: dict[str, AssetType] = {}
    for tid, raw in (data.get("asset_types") or {}).items():
        _ident(tid, "asset type")
        checks: dict[str, Check] = {}
        for cid, c in raw["checks"].items():
            _ident(cid, f"{tid}.checks")
            minutes = c["minutes"]
            if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
                raise Invalid(f"{tid}.checks.{cid}: minutes must be a positive integer")
            if not isinstance(c["isolation"], bool):
                raise Invalid(f"{tid}.checks.{cid}: isolation must be true or false")
            checks[cid] = Check(cid, c["title"], minutes, c["isolation"], c["how"])
        faults: dict[str, Fault] = {}
        for fid, f in raw["faults"].items():
            _ident(fid, f"{tid}.faults")
            causes = []
            for c in f["causes"]:
                cid = _ident(c["id"], f"{tid}.{fid}.causes")
                w = c["weight"]
                if not isinstance(w, int) or isinstance(w, bool) or not 1 <= w <= 100:
                    raise Invalid(f"{tid}.{fid}.{cid}: weight must be an integer 1..100")
                if not c["defining"]:
                    raise Invalid(f"{tid}.{fid}.{cid}: needs at least one defining check")
                for k in c["defining"]:
                    if k not in checks:
                        raise Invalid(f"{tid}.{fid}.{cid}: defining check {k!r} does not exist")
                causes.append(Cause(cid, c["label"], w, tuple(c["defining"]), c["fix"]))
            if len({c.id for c in causes}) != len(causes):
                raise Invalid(f"{tid}.{fid}: duplicate cause ids")
            if len(causes) < 2:
                raise Invalid(f"{tid}.{fid}: a fault needs at least two candidate causes")
            faults[fid] = Fault(fid, f["title"], tuple(causes))
        types[tid] = AssetType(
            tid, raw["label"], raw["safety"]["loto"], tuple(raw["safety"].get("extras", ())),
            checks, faults,
        )  # fmt: skip
    if not types:
        raise Invalid("knowledge base has no asset types")
    return KnowledgeBase(types)


def load_kb(path: str | Path | None = None) -> KnowledgeBase:
    if path is None:
        text = resources.files("technician_coach").joinpath("data/knowledge_base.json").read_text(
            encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    return parse_kb(json.loads(text))
