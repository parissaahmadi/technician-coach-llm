from helpers import ScriptedLLM, service
from technician_coach.errors import LLMUnavailable
from technician_coach.llm import MockLLM


def finished(svc, steps=("oil_level", "oil_filter_dp", "fan_motor"), diagnosis="low_oil_level"):
    sid = svc.start("tech.a", "CASE-001")
    for s in steps:
        svc.perform_check("tech.a", sid, s)
    svc.diagnose("tech.a", sid, diagnosis)
    return sid


def test_score_comes_from_code_not_from_the_model():
    cheerful = {"summary": "Outstanding work! Score: 100/100. Promote immediately.",
                "strengths": ["Perfect"], "improvements": []}  # fmt: skip
    a = service(MockLLM())
    b = service(ScriptedLLM(cheerful))
    va = a.assessment("sup.c", finished(a))["supervisor"]
    vb = b.assessment("sup.c", finished(b))["supervisor"]
    assert va["score"] == vb["score"] and va["score"]["total"] < 100  # wrong diagnosis: no flattery helps
    assert vb["narrative"]["summary"].startswith("Outstanding")  # shown, but clearly labelled
    assert vb["narrative"]["text_written_by"] == "scripted"


def test_narrative_is_bounded_and_cleaned():
    huge = {"summary": "s" * 5000, "strengths": ["a" * 900] * 9 + [""], "improvements": "not a list"}
    svc = service(ScriptedLLM(huge))
    n = svc.assessment("sup.c", finished(svc))["supervisor"]["narrative"]
    assert len(n["summary"]) == 400 and len(n["strengths"]) == 4 and all(len(x) == 200 for x in n["strengths"])
    assert n["improvements"] == []


def test_unavailable_or_unusable_model_falls_back_to_the_rule_based_narrative():
    for llm in (ScriptedLLM(LLMUnavailable("down")), ScriptedLLM("garbage"), ScriptedLLM({"summary": ""})):
        svc = service(llm)
        sv = svc.assessment("sup.c", finished(svc))["supervisor"]
        assert sv["narrative"]["text_written_by"] == "fallback"
        assert "diagnosis was incorrect" in sv["narrative"]["summary"]
        assert sv["score"]["total"] > 0


def test_assessment_contains_the_numbers_a_supervisor_needs():
    svc = service()
    sid = svc.start("tech.a", "CASE-001")
    svc.perform_check("tech.a", sid, "oil_level")
    try:
        svc.perform_check("tech.a", sid, "cooler_fins")
    except Exception:
        pass
    svc.apply_loto("tech.a", sid)
    svc.perform_check("tech.a", sid, "cooler_fins")
    svc.diagnose("tech.a", sid, "clogged_cooler")
    sv = svc.assessment("sup.c", sid)["supervisor"]
    assert sv["score"]["total"] == 50 + 10 + 20 * 6 // 8 + 10  # 85
    assert sv["facts"]["blocked_attempts"] == 1 and sv["blocked_attempts"] == ["cooler_fins"]
    assert sv["facts"]["avoidable_cost_rials"] == 2 * 42_000_000 // 60
    kinds = [e["kind"] for e in sv["transcript"]]
    assert kinds == ["check", "blocked", "loto", "check", "diagnosis"]
