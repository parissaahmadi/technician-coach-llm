import json

import pytest

from helpers import RecordingLLM, ScriptedLLM, kb
from technician_coach import cases as cases_mod
from technician_coach import coach, prompts
from technician_coach.errors import LLMBadRequest, LLMBadResponse, LLMUnavailable
from technician_coach.llm import MockLLM
from technician_coach.session import Session

K = kb()
CASES = cases_mod.load_cases(K)


def new(case_id="CASE-001", loto=False, checks=()):
    c = CASES[case_id]
    s = Session("S-1", "tech.a", c, K.asset_type(c.asset_type))
    s.loto = loto
    for k in checks:
        s.perform_check(k)
    return s


GOOD = {"message": "Check the oil level first.", "next_checks": ["oil_level"], "safety_warnings": [],
        "leading_cause": None, "escalate": False}  # fmt: skip


def test_mock_advice_is_valid_and_not_degraded():
    a = coach.advise(new(), MockLLM())
    assert a.degraded is False and a.next_checks[0] == "oil_level" and a.message


def test_json_is_found_inside_fences_and_prose():
    wrapped = "Sure! Here you go:\n```json\n" + json.dumps(GOOD) + "\n```\nHope that helps."
    a = coach.advise(new(), ScriptedLLM(wrapped))
    assert a.next_checks == ("oil_level",) and not a.degraded


def test_invalid_reply_gets_one_repair_attempt_then_works():
    llm = ScriptedLLM("I think you should look at the oil.", GOOD)
    a = coach.advise(new(), llm)
    assert not a.degraded and a.next_checks == ("oil_level",)
    assert len(llm.calls) == 2 and prompts.REPAIR in llm.calls[1][1] and prompts.REPAIR not in llm.calls[0][1]


def test_two_invalid_replies_degrade_to_the_rule_based_advice():
    llm = ScriptedLLM("nonsense", "still nonsense")
    a = coach.advise(new(), llm)
    assert a.degraded and a.message.startswith(coach.FALLBACK_NOTICE) and a.next_checks[0] == "oil_level"
    assert len(llm.calls) == 2


def test_unavailable_service_degrades_without_a_second_call():
    llm = ScriptedLLM(LLMUnavailable("down"))
    a = coach.advise(new(), llm)
    assert a.degraded and len(llm.calls) == 1


def test_bad_request_is_not_swallowed():
    with pytest.raises(LLMBadRequest):
        coach.advise(new(), ScriptedLLM(LLMBadRequest("401")))


def test_unknown_performed_and_duplicate_checks_are_dropped_and_capped():
    s = new(checks=["oil_level"])
    reply = {**GOOD, "next_checks": ["oil_level", "not_a_check", "fan_motor", "fan_motor", 7, None,
                                     "oil_filter_dp", "intake_filter", "leak_check"]}  # fmt: skip
    a = coach.advise(s, ScriptedLLM(reply))
    assert a.next_checks == ("fan_motor", "oil_filter_dp", "intake_filter")


def test_unknown_leading_cause_and_non_bool_escalate_are_neutralised():
    a = coach.advise(new(), ScriptedLLM({**GOOD, "leading_cause": "aliens", "escalate": "yes"}))
    assert a.leading_cause is None and a.escalate is False
    a = coach.advise(new(), ScriptedLLM({**GOOD, "leading_cause": "clogged_cooler", "escalate": True}))
    assert a.leading_cause == "clogged_cooler" and a.escalate is True


def test_text_is_cleaned_and_bounded():
    reply = {**GOOD, "message": "  Look\x00 at\x1b[31m the   oil  " + "x" * 2000,
             "safety_warnings": ["a" * 1000, "a" * 1000, "", 5, "careful"]}  # fmt: skip
    a = coach.advise(new(), ScriptedLLM(reply))
    assert len(a.message) <= coach.MAX_MESSAGE and "\x00" not in a.message and "\x1b" not in a.message
    assert a.message.startswith("Look at [31m the oil")
    assert a.safety_warnings[0] == "a" * coach.MAX_WARNING_LEN and "careful" in a.safety_warnings
    assert len(a.safety_warnings) == 2  # duplicates, empties and non-strings dropped


@pytest.mark.parametrize("bad", [{}, {"message": ""}, {"message": "   "}, {"message": 5}, {"next_checks": []}])
def test_reply_without_a_usable_message_is_invalid(bad):
    with pytest.raises(LLMBadResponse):
        coach.validate_advice(bad, coach.build_context(new()))


def test_safety_warning_is_enforced_even_if_the_model_forgot():
    s = new()  # no lockout yet
    a = coach.advise(s, ScriptedLLM({**GOOD, "next_checks": ["cooler_fins"], "safety_warnings": []}))
    assert a.safety_warnings[0] == s.asset_type.loto
    s2 = new(loto=True)
    a2 = coach.advise(s2, ScriptedLLM({**GOOD, "next_checks": ["cooler_fins"], "safety_warnings": []}))
    assert a2.safety_warnings == ()
    a3 = coach.advise(new(), ScriptedLLM({**GOOD, "next_checks": ["oil_level"]}))
    assert a3.safety_warnings == ()  # a non-isolating suggestion needs no lockout warning


def test_context_has_what_the_technician_may_know_and_nothing_else():
    ctx = coach.build_context(new(checks=["oil_level"]))
    assert set(ctx) == {"asset", "fault_title", "symptom", "causes", "checks", "done", "loto", "notes", "safety"}
    assert [d["check"] for d in ctx["done"]] == ["oil_level"]
    assert all(set(c) == {"id", "label", "weight", "defining"} for c in ctx["causes"])


def test_degraded_advice_is_flagged_in_the_serialised_form():
    d = coach.advise(new(), ScriptedLLM(LLMUnavailable("x"))).to_dict()
    assert d["degraded"] is True and isinstance(d["next_checks"], list)
    json.dumps(d)  # JSON-serialisable


def test_every_advice_goes_through_one_prompt_per_turn():
    rec = RecordingLLM()
    coach.advise(new(), rec)
    assert len(rec.calls) == 1 and rec.calls[0][0] == prompts.COACH_SYSTEM
