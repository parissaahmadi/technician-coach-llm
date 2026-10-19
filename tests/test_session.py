import pytest

from helpers import kb
from technician_coach import cases as cases_mod
from technician_coach.errors import Conflict, Forbidden, Invalid, NotFound
from technician_coach.session import Session

K = kb()
CASES = cases_mod.load_cases(K)


def new(case_id="CASE-001"):
    c = CASES[case_id]
    return Session("S-1", "tech.a", c, K.asset_type(c.asset_type))


def test_check_returns_the_case_reading():
    rec, repeated = new().perform_check("oil_level")
    assert (rec.result, rec.reading, repeated) == ("normal", "Level at three quarters of the glass.", False)


def test_isolating_check_is_blocked_without_lockout_and_the_attempt_is_recorded():
    s = new()
    with pytest.raises(Forbidden, match="lockout"):
        s.perform_check("cooler_fins")
    assert s.blocked == ["cooler_fins"] and s.checks == []
    assert [e["kind"] for e in s.log] == ["blocked"]
    s.apply_loto()
    rec, _ = s.perform_check("cooler_fins")
    assert rec.result == "abnormal" and s.blocked == ["cooler_fins"]


def test_loto_is_idempotent():
    s = new()
    assert s.apply_loto() is True
    assert s.apply_loto() is False
    assert [e["kind"] for e in s.log] == ["loto"]


def test_repeating_a_check_does_not_add_a_record():
    s = new()
    s.perform_check("oil_level")
    rec, repeated = s.perform_check("oil_level")
    assert repeated and len(s.checks) == 1 and s.minutes_spent == 2


def test_unknown_check_lists_valid_ones():
    with pytest.raises(Invalid, match="oil_level"):
        new().perform_check("magic_wand")


def test_blocked_check_counts_no_minutes():
    s = new()
    with pytest.raises(Forbidden):
        s.perform_check("thermostatic_valve")
    assert s.minutes_spent == 0


def test_notes_are_cleaned_and_bounded():
    s = new()
    assert s.add_note("  two   spaces \n and newline ") == "two spaces and newline"
    for bad in ("", "   ", "x" * 501):
        with pytest.raises(Invalid):
            s.add_note(bad)
    for i in range(29):
        s.add_note(f"note {i}")
    with pytest.raises(Conflict, match="at most"):
        s.add_note("one too many")


def test_diagnosis_needs_a_check_and_a_known_cause():
    s = new()
    with pytest.raises(Invalid, match="at least one check"):
        s.diagnose("clogged_cooler")
    s.perform_check("oil_level")
    with pytest.raises(NotFound, match="candidates"):
        s.diagnose("gremlins")
    assert s.status == "active"
    assert s.diagnose("clogged_cooler") is True
    assert s.status == "finished" and s.correct is True


def test_wrong_diagnosis_is_recorded_as_wrong():
    s = new()
    s.perform_check("oil_level")
    assert s.diagnose("low_oil_level") is False and s.correct is False


def test_finished_session_accepts_nothing():
    s = new()
    s.perform_check("oil_level")
    s.diagnose("fan_failure")
    for action in (s.apply_loto, lambda: s.perform_check("fan_motor"), lambda: s.add_note("x"),
                   lambda: s.diagnose("clogged_cooler"), lambda: s.record_coach({})):  # fmt: skip
        with pytest.raises(Conflict, match="finished"):
            action()
