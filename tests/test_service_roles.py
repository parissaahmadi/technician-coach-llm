import pytest
from helpers import RecordingLLM, service

from technician_coach.errors import Conflict, Forbidden, Invalid, NotFound


def finished(svc, who="tech.a", case="CASE-001"):
    sid = svc.start(who, case)
    svc.perform_check(who, sid, "oil_level")
    svc.diagnose(who, sid, "low_oil_level")
    return sid


def test_unknown_users_are_refused_everywhere():
    svc = service()
    for call in (lambda: svc.start("mallory", "CASE-001"), lambda: svc.list_sessions("mallory"),
                 lambda: svc.assessment("mallory", "S-0001")):  # fmt: skip
        with pytest.raises(Forbidden, match="unknown user"):
            call()
    with pytest.raises(Forbidden):
        svc.start(None, "CASE-001")


def test_session_ids_and_unknown_things():
    svc = service()
    assert svc.start("tech.a", "CASE-001") == "S-0001" and svc.start("tech.b", "CASE-002") == "S-0002"
    with pytest.raises(NotFound, match="CASE-999"):
        svc.start("tech.a", "CASE-999")
    with pytest.raises(NotFound, match="session"):
        svc.perform_check("tech.a", "S-9999", "oil_level")


def test_only_the_owner_can_work_a_session():
    svc = service()
    sid = svc.start("tech.a", "CASE-001")
    for call in (lambda: svc.perform_check("tech.b", sid, "oil_level"), lambda: svc.apply_loto("tech.b", sid),
                 lambda: svc.add_note("tech.b", sid, "hi"), lambda: svc.ask_coach("tech.b", sid),
                 lambda: svc.diagnose("tech.b", sid, "clogged_cooler")):  # fmt: skip
        with pytest.raises(Forbidden, match="belongs to tech.a"):
            call()
    with pytest.raises(Forbidden, match="belongs to tech.a"):  # a supervisor may look, not play for them
        svc.perform_check("sup.c", sid, "oil_level")
    assert svc.technician_view("tech.a", sid)["checks"] == []  # nothing was recorded by the attempts


def test_technician_views_are_private_but_supervisors_can_see_any():
    svc = service()
    sid = svc.start("tech.a", "CASE-001")
    assert svc.technician_view("tech.a", sid)["technician"] == "tech.a"
    assert svc.technician_view("sup.c", sid)["session_id"] == sid
    with pytest.raises(Forbidden):
        svc.technician_view("tech.b", sid)


def test_listing_shows_own_sessions_to_technicians_and_all_to_supervisors():
    svc = service()
    a = svc.start("tech.a", "CASE-001")
    b = svc.start("tech.b", "CASE-002")
    assert svc.list_sessions("tech.a") == [a] and svc.list_sessions("tech.b") == [b]
    assert svc.list_sessions("sup.c") == [a, b]


def test_assessment_is_supervisor_only_and_refused_before_any_model_call():
    rec = RecordingLLM()
    svc = service(rec)
    sid = finished(svc)
    for who in ("tech.a", "tech.b"):
        with pytest.raises(Forbidden, match="not allowed to assess"):
            svc.assessment(who, sid)
    assert rec.calls == []  # the check happens in code, before the model is involved at all


def test_assessment_needs_a_finished_session_and_is_cached():
    rec = RecordingLLM()
    svc = service(rec)
    sid = svc.start("tech.a", "CASE-001")
    with pytest.raises(Conflict, match="not finished"):
        svc.assessment("sup.c", sid)
    svc.perform_check("tech.a", sid, "oil_level")
    svc.diagnose("tech.a", sid, "low_oil_level")
    first = svc.assessment("sup.c", sid)
    again = svc.assessment("sup.c", sid)
    assert first == again and len(rec.calls) == 1  # one model call, then cached


def test_finished_sessions_cannot_be_coached_again():
    svc = service()
    sid = finished(svc)
    with pytest.raises(Conflict, match="finished"):
        svc.ask_coach("tech.a", sid)


def test_coach_messages_are_recorded_in_the_view():
    svc = service()
    sid = svc.start("tech.a", "CASE-001")
    svc.ask_coach("tech.a", sid)
    msgs = svc.technician_view("tech.a", sid)["coach_messages"]
    assert len(msgs) == 1 and msgs[0]["next_checks"][0] == "oil_level"


def test_a_role_that_is_not_in_the_permission_table_is_denied_by_default():
    from technician_coach.service import CoachService

    svc = service()
    custom = CoachService(svc.kb, svc.cases, svc.llm, users={"x.y": "viewer"})
    with pytest.raises(Forbidden, match="not allowed"):
        custom.start("x.y", "CASE-001")


def test_diagnose_returns_only_the_debrief():
    svc = service()
    sid = svc.start("tech.a", "CASE-001")
    svc.perform_check("tech.a", sid, "oil_level")
    d = svc.diagnose("tech.a", sid, "low_oil_level")
    assert set(d) == {"your_diagnosis", "correct", "actual_cause", "recommended_fix"} and d["correct"] is False
    sid2 = svc.start("tech.a", "CASE-001")
    with pytest.raises(Invalid, match="at least one check"):
        svc.diagnose("tech.a", sid2, "clogged_cooler")
