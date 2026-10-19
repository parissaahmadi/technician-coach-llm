"""Command line: ``technician-coach list-cases | play CASE --as USER | demo``."""

from __future__ import annotations

import argparse
import os
import sys
from typing import TextIO

from . import cases as cases_mod
from . import kb as kb_mod
from .errors import CoachError
from .llm import DEFAULT_MODEL, LLM, AnthropicHTTP, MockLLM
from .roles import DEFAULT_USERS
from .service import CoachService

HELP = """\
commands:
  checks                 list the checks you can perform
  causes                 list the candidate causes
  loto                   apply lockout/tagout (needed before some checks)
  check <id>             perform a check
  note <text>            add a note to the record
  coach                  ask the coach what to do next
  diagnose <cause_id>    submit your diagnosis (ends the exercise)
  quit
"""


def make_llm(kind: str, model: str) -> LLM:
    if kind == "mock":
        return MockLLM()
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    return AnthropicHTTP(key, model, base)


def make_service(llm: LLM) -> CoachService:
    kb = kb_mod.load_kb()
    return CoachService(kb, cases_mod.load_cases(kb), llm)


def _print_advice(advice: dict, out: TextIO) -> None:
    print(f"coach: {advice['message']}", file=out)
    for w in advice["safety_warnings"]:
        print(f"  SAFETY: {w}", file=out)
    if advice["next_checks"]:
        print(f"  suggested checks: {', '.join(advice['next_checks'])}", file=out)
    if advice["escalate"]:
        print("  -> consider escalating to your supervisor", file=out)


def _print_debrief(d: dict, out: TextIO) -> None:
    verdict = "CORRECT" if d["correct"] else "NOT CORRECT"
    print(f"diagnosis {verdict}: you said '{d['your_diagnosis']}'", file=out)
    print(f"actual cause: {d['actual_cause']}", file=out)
    print(f"recommended fix: {d['recommended_fix']}", file=out)


def _print_supervisor(view: dict, out: TextIO) -> None:
    s = view["supervisor"]
    print("--- SUPERVISOR ONLY ---", file=out)
    parts = ", ".join(f"{k} {v}/{s['score']['weights'][k]}" for k, v in s["score"]["parts"].items())
    print(f"score {s['score']['total']}/100 ({parts})", file=out)
    f = s["facts"]
    print(f"checks: {f['checks_performed']}, minutes: {f['actual_minutes']} (shortest path {f['optimal_minutes']}), "
          f"blocked attempts: {f['blocked_attempts']}, avoidable cost: {f['avoidable_cost_rials']:,} rials", file=out)  # fmt: skip
    print(f"rubric: {s['rubric']}", file=out)
    n = s["narrative"]
    print(f"narrative ({n['text_written_by']}, not used for the score): {n['summary']}", file=out)
    for item in n["strengths"]:
        print(f"  + {item}", file=out)
    for item in n["improvements"]:
        print(f"  - {item}", file=out)


def play(svc: CoachService, actor: str, case_id: str, stdin: TextIO, out: TextIO) -> int:
    sid = svc.start(actor, case_id)
    v = svc.technician_view(actor, sid)
    print(f"{v['case']['title']}  [{v['asset']['name']}]\nsymptom: {v['symptom']}\n(type 'help')", file=out)
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        cmd, _, arg = line.partition(" ")
        try:
            if cmd == "help":
                print(HELP, file=out, end="")
            elif cmd == "checks":
                at = svc.kb.asset_type(svc.cases[case_id].asset_type)
                for c in at.checks.values():
                    flag = "  [lockout]" if c.isolation else ""
                    print(f"  {c.id:<20} {c.minutes:>3} min  {c.title}{flag}", file=out)
            elif cmd == "causes":
                at = svc.kb.asset_type(svc.cases[case_id].asset_type)
                for c in at.fault(svc.cases[case_id].fault).causes:
                    print(f"  {c.id:<26} {c.label}", file=out)
            elif cmd == "loto":
                print("lockout/tagout applied" if svc.apply_loto(actor, sid) else "already applied", file=out)
            elif cmd == "check":
                r, repeated = svc.perform_check(actor, sid, arg.strip())
                print(f"{r['check']}: {r['result'].upper()} - {r['reading']}" + (" (already done)" if repeated else ""), file=out)
            elif cmd == "note":
                svc.add_note(actor, sid, arg)
                print("noted", file=out)
            elif cmd == "coach":
                _print_advice(svc.ask_coach(actor, sid), out)
            elif cmd == "diagnose":
                _print_debrief(svc.diagnose(actor, sid, arg.strip()), out)
                break
            elif cmd in ("quit", "exit"):
                break
            else:
                print(f"unknown command {cmd!r}; type 'help'", file=out)
        except CoachError as e:
            print(f"{e.code}: {e}", file=out)
    if svc.users[actor] == "supervisor" and svc.technician_view(actor, sid)["status"] == "finished":
        _print_supervisor(svc.assessment(actor, sid), out)
    return 0


def demo(svc: CoachService, out: TextIO) -> int:
    """Scripted run: a technician with a few mistakes, then what each role can see."""
    def say(text: str = "") -> None:
        print(text, file=out)

    def tech(cmd: str, fn):
        say(f"\n[tech.a] {cmd}")
        try:
            result = fn()
        except CoachError as e:
            say(f"  {e.code}: {e}")
            return None
        return result

    sid = svc.start("tech.a", "CASE-001")
    view = svc.technician_view("tech.a", sid)
    say(f"{view['case']['title']}  [{view['asset']['name']}]\nsymptom: {view['symptom']}")
    advice = tech("coach", lambda: svc.ask_coach("tech.a", sid))
    _print_advice(advice, out)
    r = tech("check cooler_fins   (tries the isolating check first)", lambda: svc.perform_check("tech.a", sid, "cooler_fins"))
    for chk in ("oil_level", "fan_motor"):
        r, _ = tech(f"check {chk}", lambda c=chk: svc.perform_check("tech.a", sid, c))
        say(f"  {r['result'].upper()} - {r['reading']}")
    tech("loto", lambda: svc.apply_loto("tech.a", sid))
    r, _ = tech("check cooler_fins", lambda: svc.perform_check("tech.a", sid, "cooler_fins"))
    say(f"  {r['result'].upper()} - {r['reading']}")
    tech("note Dust and fibres everywhere in the hall.", lambda: svc.add_note("tech.a", sid, "Dust and fibres everywhere in the hall."))
    advice = tech("coach", lambda: svc.ask_coach("tech.a", sid))
    _print_advice(advice, out)
    debrief = tech("diagnose clogged_cooler", lambda: svc.diagnose("tech.a", sid, "clogged_cooler"))
    _print_debrief(debrief, out)

    say("\n--- what the technician can reach ---")
    tech("assessment (as tech.a)", lambda: svc.assessment("tech.a", sid))
    tech("technician_view of tech.a's session (as tech.b)", lambda: svc.technician_view("tech.b", sid))
    say("\n--- what the supervisor sees ---")
    _print_supervisor(svc.assessment("sup.c", sid), out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="technician-coach", description=__doc__)
    ap.add_argument("--llm", choices=["mock", "anthropic"], default="mock",
                    help="mock (default) needs no key; anthropic needs ANTHROPIC_API_KEY")  # fmt: skip
    ap.add_argument("--model", default=os.environ.get("COACH_MODEL", DEFAULT_MODEL))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-cases", help="list the exercise cases")
    p = sub.add_parser("play", help="work through a case (commands from stdin)")
    p.add_argument("case")
    p.add_argument("--as", dest="actor", default="tech.a", choices=sorted(DEFAULT_USERS))
    sub.add_parser("demo", help="scripted run showing technician and supervisor views")
    return ap


def main(argv: list[str] | None = None, stdin: TextIO | None = None, out: TextIO | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    stdin = stdin or sys.stdin
    try:
        svc = make_service(make_llm(args.llm, args.model))
        print(f"[llm: {svc.llm.name}]", file=out)
        if args.cmd == "list-cases":
            for c in svc.cases.values():
                print(f"{c.id}  {c.title}  ({c.asset_name})", file=out)
            return 0
        if args.cmd == "play":
            return play(svc, args.actor, args.case, stdin, out)
        return demo(svc, out)
    except CoachError as e:
        print(f"error: {e.code}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
