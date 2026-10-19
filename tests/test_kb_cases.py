import copy

import pytest
from helpers import kb, raw

from technician_coach import cases as cases_mod
from technician_coach import kb as kb_mod
from technician_coach.errors import Invalid, NotFound


def test_seed_knowledge_base_and_cases_load():
    k = kb()
    assert set(k.asset_types) == {"compressor", "pump", "press"}
    cases = cases_mod.load_cases(k)
    assert list(cases) == [f"CASE-00{i}" for i in range(1, 6)]


def test_every_cause_has_a_defining_check_that_exists():
    for at in kb().asset_types.values():
        for fault in at.faults.values():
            for cause in fault.causes:
                assert cause.defining and all(k in at.checks for k in cause.defining)


@pytest.mark.parametrize(
    "mutate,msg",
    [
        (lambda d: d["asset_types"]["pump"]["checks"]["strainer"].update(minutes=0), "positive integer"),
        (lambda d: d["asset_types"]["pump"]["checks"]["strainer"].update(minutes=2.5), "positive integer"),
        (lambda d: d["asset_types"]["pump"]["checks"]["strainer"].update(isolation="yes"), "true or false"),
        (lambda d: d["asset_types"]["pump"]["faults"]["low_flow"]["causes"][0].update(weight=0), "weight"),
        (lambda d: d["asset_types"]["pump"]["faults"]["low_flow"]["causes"][0].update(weight=101), "weight"),
        (lambda d: d["asset_types"]["pump"]["faults"]["low_flow"]["causes"][0].update(defining=["nope"]), "does not exist"),
        (lambda d: d["asset_types"]["pump"]["faults"]["low_flow"]["causes"][0].update(defining=[]), "defining"),
        (lambda d: d["asset_types"]["pump"]["faults"]["low_flow"]["causes"][1].update(id="closed_suction_valve"), "duplicate"),
        (lambda d: d["asset_types"]["pump"]["faults"]["low_flow"].update(causes=[d["asset_types"]["pump"]["faults"]["low_flow"]["causes"][0]]), "two"),
        (lambda d: d["asset_types"].update({"Bad Name": d["asset_types"]["pump"]}), "identifier"),
        (lambda d: d.update(asset_types={}), "no asset types"),
    ],
)
def test_kb_rejects_bad_data(mutate, msg):
    d = copy.deepcopy(raw("knowledge_base"))
    mutate(d)
    with pytest.raises(Invalid, match=msg):
        kb_mod.parse_kb(d)


def case_data():
    return copy.deepcopy(raw("cases"))


@pytest.mark.parametrize(
    "mutate,msg",
    [
        (lambda c: c.update(root_cause="fan_failure"), "must be abnormal"),  # root cause's check is normal
        (lambda c: c["observations"].update(oil_level={"result": "abnormal", "reading": "x"}), "two answers"),
        (lambda c: c["observations"].update(oil_level={"result": "maybe", "reading": "x"}), "normal or abnormal"),
        (lambda c: c["observations"].update(no_such_check={"result": "normal", "reading": "x"}), "unknown check"),
        (lambda c: c["supervisor"].update(weights={"diagnosis": 60, "safety": 20, "efficiency": 20, "evidence": 10}), "sum to 100"),
        (lambda c: c["supervisor"].update(weights={"diagnosis": 100}), "sum to 100"),
        (lambda c: c["supervisor"].update(cost_per_hour_rials=-1), "non-negative"),
        (lambda c: c.update(id="C1"), "bad case id"),
        (lambda c: c.update(fault="nonsense"), "unknown fault"),
    ],
)
def test_case_validation(mutate, msg):
    d = case_data()
    mutate(d["cases"][0])
    with pytest.raises((Invalid, NotFound), match=msg):
        cases_mod.parse_cases(d, kb())


def test_duplicate_case_ids_and_empty_file():
    d = case_data()
    d["cases"].append(copy.deepcopy(d["cases"][0]))
    with pytest.raises(Invalid, match="duplicate"):
        cases_mod.parse_cases(d, kb())
    with pytest.raises(Invalid, match="no cases"):
        cases_mod.parse_cases({"cases": []}, kb())


def test_unlisted_checks_default_to_normal():
    c = cases_mod.load_cases(kb())["CASE-001"]
    assert c.observe("intake_filter").result == "normal"
    assert c.observe("cooler_fins").result == "abnormal"


def test_get_case_lists_alternatives():
    with pytest.raises(NotFound, match="CASE-001"):
        cases_mod.get_case(cases_mod.load_cases(kb()), "CASE-999")
