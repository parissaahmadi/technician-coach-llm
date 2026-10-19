import pytest
from helpers import kb

from technician_coach import cases as cases_mod
from technician_coach import coach, heuristics
from technician_coach.session import Session

K = kb()
CASES = cases_mod.load_cases(K)


def ctx_for(case_id, checks=(), loto=False):
    c = CASES[case_id]
    s = Session("S-1", "tech.a", c, K.asset_type(c.asset_type))
    s.loto = loto
    for k in checks:
        s.perform_check(k)
    return coach.build_context(s)


def test_states_rule_out_and_support():
    st = heuristics.cause_states(ctx_for("CASE-001", ["oil_level", "oil_filter_dp"]))
    assert st["low_oil_level"] == "ruled_out" and st["clogged_oil_filter"] == "ruled_out"
    assert st["clogged_cooler"] == "open"
    st = heuristics.cause_states(ctx_for("CASE-001", ["cooler_fins"], loto=True))
    assert st["clogged_cooler"] == "supported"


def test_first_suggestion_is_most_likely_cause_per_minute():
    out = heuristics.suggest(ctx_for("CASE-001"))
    assert out["next_checks"][0] == "oil_level"  # 20 weight / 2 min beats cooler 35 / 6 min
    assert len(out["next_checks"]) == 3 and out["leading_cause"] is None


def test_suggestions_never_repeat_a_performed_check():
    out = heuristics.suggest(ctx_for("CASE-001", ["oil_level"]))
    assert "oil_level" not in out["next_checks"]


def test_unique_supported_cause_becomes_the_leading_one():
    out = heuristics.suggest(ctx_for("CASE-002", ["thermostatic_valve"], loto=True))
    assert out["leading_cause"] == "stuck_thermostatic_valve" and out["next_checks"] == []
    assert "submit your diagnosis" in out["message"]


def test_all_causes_ruled_out_escalates():
    c = ctx_for("CASE-001", ["oil_level", "oil_filter_dp", "fan_motor"])
    for chk in c["causes"]:
        pass
    # rule out the rest by hand-made context (the seed cases always contain a real cause)
    c["done"] += [{"check": "cooler_fins", "result": "normal", "reading": "x"},
                  {"check": "thermostatic_valve", "result": "normal", "reading": "x"}]  # fmt: skip
    out = heuristics.suggest(c)
    assert out["escalate"] is True and out["next_checks"] == [] and "supervisor" in out["message"]


def test_two_supported_causes_with_no_separating_check_left_escalate():
    c = ctx_for("CASE-001", ["oil_level"])
    c["done"] += [{"check": "cooler_fins", "result": "abnormal", "reading": "x"},
                  {"check": "fan_motor", "result": "abnormal", "reading": "x"}]  # fmt: skip
    out = heuristics.suggest(c)
    assert out["leading_cause"] is None and out["escalate"] is True and out["next_checks"] == []


def test_two_supported_causes_ask_for_a_separating_check_when_one_exists():
    ctx = {
        "symptom": "s", "loto": True, "notes": [], "safety": {"loto": "L", "extras": []},
        "causes": [{"id": "a", "label": "A", "weight": 50, "defining": ["x", "z"]},
                   {"id": "b", "label": "B", "weight": 50, "defining": ["y", "z"]}],
        "checks": [{"id": k, "title": k, "minutes": 3, "isolation": False} for k in "xyz"],
        "done": [{"check": "x", "result": "abnormal", "reading": ""},
                 {"check": "y", "result": "abnormal", "reading": ""}],
    }  # fmt: skip
    out = heuristics.suggest(ctx)
    assert out["next_checks"] == ["z"] and "Two causes" in out["message"] and out["leading_cause"] is None


def test_many_checks_without_a_lead_suggest_escalation():
    c = ctx_for("CASE-001", ["oil_level", "oil_filter_dp", "fan_motor"])
    c["done"] += [{"check": f"extra{i}", "result": "normal", "reading": "x"} for i in range(6)]
    out = heuristics.suggest(c)
    assert out["escalate"] is True and "escalating" in out["message"]


def test_lockout_warning_only_when_an_isolating_check_is_suggested_and_not_applied():
    out = heuristics.suggest(ctx_for("CASE-001", ["oil_level", "oil_filter_dp", "fan_motor"]))
    assert out["next_checks"][0] == "cooler_fins" and out["safety_warnings"]
    out = heuristics.suggest(ctx_for("CASE-001", ["oil_level", "oil_filter_dp", "fan_motor"], loto=True))
    assert out["safety_warnings"] == []


def test_suggest_is_deterministic():
    c = ctx_for("CASE-003")
    assert heuristics.suggest(c) == heuristics.suggest(c)


@pytest.mark.parametrize("case_id", ["CASE-001", "CASE-002", "CASE-003", "CASE-004", "CASE-005"])
def test_following_the_coach_always_solves_every_case(case_id):
    """Solvability: the rule-based coach, obeyed step by step, reaches the true cause."""
    c = CASES[case_id]
    s = Session("S-1", "tech.a", c, K.asset_type(c.asset_type))
    s.apply_loto()
    for _ in range(len(s.asset_type.checks) + 1):
        out = heuristics.suggest(coach.build_context(s))
        if out["leading_cause"] and not out["next_checks"]:
            assert out["leading_cause"] == c.root_cause
            return
        assert out["next_checks"], out
        s.perform_check(out["next_checks"][0])
    raise AssertionError("coach never converged")


def test_mock_narrative_reflects_the_facts():
    ctx = {"score": {"parts": {"diagnosis": 0, "safety": 10, "efficiency": 5, "evidence": 0},
                     "weights": {"diagnosis": 50, "safety": 20, "efficiency": 20, "evidence": 10}},
           "facts": {"blocked_attempts": 1, "extra_minutes": 9, "checks_performed": 4,
                     "actual_minutes": 15, "optimal_minutes": 6}, "correct": False}  # fmt: skip
    n = heuristics.mock_narrative(ctx)
    assert "incorrect" in n["summary"] and n["strengths"] == []
    assert len(n["improvements"]) == 4
