import io
import os

from helpers import service
from technician_coach import cli


def run(argv, stdin=""):
    out = io.StringIO()
    rc = cli.main(argv, io.StringIO(stdin), out)
    return rc, out.getvalue()


def test_list_cases():
    rc, text = run(["list-cases"])
    assert rc == 0 and "[llm: mock]" in text
    assert "CASE-001  Compressor C-1 overheats under load" in text and text.count("CASE-00") == 5


def test_play_a_full_session_from_stdin():
    script = "checks\ncoach\ncheck strainer\nloto\ncheck strainer\ndiagnose clogged_strainer\n"
    rc, text = run(["play", "CASE-004", "--as", "tech.b"], script)
    assert rc == 0
    assert "forbidden: 'Suction strainer condition' needs the machine isolated" in text
    assert "strainer: ABNORMAL - Basket packed with scale" in text
    assert "diagnosis CORRECT" in text and "SUPERVISOR ONLY" not in text


def test_supervisor_playing_also_gets_the_assessment_at_the_end():
    rc, text = run(["play", "CASE-004", "--as", "sup.c"], "loto\ncheck strainer\ndiagnose clogged_strainer\n")
    assert rc == 0 and "--- SUPERVISOR ONLY ---" in text and "score 100/100" in text


def test_errors_are_printed_not_raised_and_the_loop_continues():
    script = "check\ncheck nonsense\nfrobnicate\ndiagnose gremlins\ndiagnose clogged_cooler\nnote\ncoach\n"
    rc, text = run(["play", "CASE-001"], script)
    assert rc == 0
    assert "invalid:" in text and "unknown command 'frobnicate'" in text
    assert "not_found: cause 'gremlins'" in text and "perform at least one check" in text
    assert "Traceback" not in text


def test_repeated_check_and_loto_messages():
    rc, text = run(["play", "CASE-001"], "check oil_level\ncheck oil_level\nloto\nloto\nquit\n")
    assert "(already done)" in text and "already applied" in text


def test_unknown_case_exits_with_code_2():
    rc, _ = run(["play", "CASE-999"])
    assert rc == 2


def test_anthropic_without_a_key_fails_cleanly(tmp_path):
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        rc, _ = run(["--llm", "anthropic", "list-cases"])
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved
    assert rc == 2


def test_demo_runs_and_shows_the_role_boundary():
    rc, text = run(["demo"])
    assert rc == 0
    assert "forbidden: tech.a (technician) is not allowed to assess" in text
    assert "forbidden: session S-0001 belongs to tech.a" in text
    assert "score 80/100" in text and "--- SUPERVISOR ONLY ---" in text


def test_make_llm_selects_the_client():
    assert cli.make_llm("mock", "m").name == "mock"
    saved = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "k-test"
    try:
        c = cli.make_llm("anthropic", "model-y")
    finally:
        if saved is None:
            del os.environ["ANTHROPIC_API_KEY"]
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved
    assert c.name == "anthropic:model-y"
    assert service().llm.name == "mock"
