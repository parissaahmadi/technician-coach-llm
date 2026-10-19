from dataclasses import replace

from helpers import kb

from technician_coach import cases as cases_mod
from technician_coach.scoring import score_session
from technician_coach.session import Session

K = kb()
CASES = cases_mod.load_cases(K)


def run(case_id, steps, diagnosis, loto_first=False):
    c = CASES[case_id]
    s = Session("S-1", "tech.a", c, K.asset_type(c.asset_type))
    if loto_first:
        s.apply_loto()
    for st in steps:
        try:
            s.perform_check(st)
        except Exception:
            pass
    s.diagnose(diagnosis)
    return score_session(s, K.asset_type(c.asset_type)), s


def test_perfect_run_scores_100():
    sc, _ = run("CASE-001", ["cooler_fins"], "clogged_cooler", loto_first=True)
    assert sc.total == 100 and sc.parts == {"diagnosis": 50, "safety": 20, "efficiency": 20, "evidence": 10}
    assert sc.facts["extra_minutes"] == 0 and sc.facts["avoidable_cost_rials"] == 0


def test_extra_checks_cost_efficiency_and_money():
    sc, _ = run("CASE-001", ["oil_level", "oil_filter_dp", "fan_motor", "cooler_fins"], "clogged_cooler", loto_first=True)
    assert sc.facts["actual_minutes"] == 14 and sc.facts["optimal_minutes"] == 6
    assert sc.parts["efficiency"] == 20 * 6 // 14  # floor, documented
    assert sc.facts["extra_minutes"] == 8
    assert sc.facts["avoidable_cost_rials"] == 8 * 42_000_000 // 60  # 5,600,000


def test_blocked_attempts_take_half_the_safety_weight_each_down_to_zero():
    one, _ = run("CASE-001", ["oil_level", "cooler_fins", "cooler_fins"], "clogged_cooler")  # two blocked attempts, no lockout
    assert one.facts["blocked_attempts"] == 2 and one.parts["safety"] == 0
    c = CASES["CASE-001"]
    s = Session("S-2", "tech.a", c, K.asset_type("compressor"))
    try:
        s.perform_check("cooler_fins")
    except Exception:
        pass
    s.apply_loto()
    s.perform_check("cooler_fins")
    s.diagnose("clogged_cooler")
    assert score_session(s, K.asset_type("compressor")).parts["safety"] == 10


def test_wrong_diagnosis_gets_no_diagnosis_points_and_no_evidence_points():
    sc, s = run("CASE-001", ["oil_level"], "low_oil_level", loto_first=True)
    assert s.correct is False
    assert sc.parts["diagnosis"] == 0
    assert sc.parts["evidence"] == 0  # the deciding check for that cause was NORMAL


def test_diagnosis_without_the_deciding_check_has_no_evidence_points():
    sc, _ = run("CASE-001", ["oil_level"], "clogged_cooler", loto_first=True)
    assert sc.parts["diagnosis"] == 50 and sc.parts["evidence"] == 0


def test_no_checks_means_zero_efficiency():
    c = CASES["CASE-001"]
    s = Session("S-3", "tech.a", c, K.asset_type("compressor"))
    s.status, s.diagnosis, s.correct = "finished", "clogged_cooler", True
    assert score_session(s, K.asset_type("compressor")).parts["efficiency"] == 0


def test_custom_weights_are_used():
    raw_case = replace(CASES["CASE-001"], supervisor=replace(CASES["CASE-001"].supervisor,
                       weights={"diagnosis": 70, "safety": 10, "efficiency": 10, "evidence": 10}))
    s = Session("S-4", "tech.a", raw_case, K.asset_type("compressor"))
    s.apply_loto()
    s.perform_check("cooler_fins")
    s.diagnose("clogged_cooler")
    sc = score_session(s, K.asset_type("compressor"))
    assert sc.total == 100 and sc.parts["diagnosis"] == 70 and sc.weights["diagnosis"] == 70


def test_scores_are_never_out_of_range():
    for cid in CASES:
        at = K.asset_type(CASES[cid].asset_type)
        fault = at.fault(CASES[cid].fault)
        for cause in fault.causes:
            sc, _ = run(cid, list(at.checks)[:4], cause.id)
            assert 0 <= sc.total <= 100 and all(0 <= v <= sc.weights[k] for k, v in sc.parts.items())
