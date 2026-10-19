"""Rule-based troubleshooting advice from the fault tree.

Used in two places: by ``MockLLM`` (it plays the part of the model, so the demo runs without an API
key) and as the fallback when the real service is unavailable. Input is exactly the *context* that
is sent to the model, so it can only use what the technician is allowed to see.
"""

from __future__ import annotations

MAX_SUGGESTIONS = 3
ESCALATE_AFTER = 8  # checks without a leading cause


def cause_states(ctx: dict) -> dict[str, str]:
    """ruled_out | supported | open, per candidate cause."""
    done = {d["check"]: d["result"] for d in ctx["done"]}
    states = {}
    for c in ctx["causes"]:
        results = [done[k] for k in c["defining"] if k in done]
        if "normal" in results:
            states[c["id"]] = "ruled_out"
        elif "abnormal" in results:
            states[c["id"]] = "supported"
        else:
            states[c["id"]] = "open"
    return states


def _rank_checks(ctx: dict, live: list[dict], only: set[str] | None = None) -> list[dict]:
    """Unperformed checks, best first: most likely causes covered per minute, then id."""
    done = {d["check"] for d in ctx["done"]}
    scored = []
    for chk in ctx["checks"]:
        if chk["id"] in done or (only is not None and chk["id"] not in only):
            continue
        weight = sum(c["weight"] for c in live if chk["id"] in c["defining"])
        if weight:
            scored.append((-(weight * 1000) // chk["minutes"], chk["id"], chk))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in scored]


def _describe(chk: dict) -> str:
    extra = ", needs lockout first" if chk["isolation"] else ""
    return f"{chk['title']} ({chk['minutes']} min{extra})"


def suggest(ctx: dict) -> dict:
    states = cause_states(ctx)
    by_id = {c["id"]: c for c in ctx["causes"]}
    live = [c for c in ctx["causes"] if states[c["id"]] != "ruled_out"]
    supported = [c for c in live if states[c["id"]] == "supported"]
    checks = {c["id"]: c for c in ctx["checks"]}
    done = {d["check"] for d in ctx["done"]}
    warnings: list[str] = []
    result = {"message": "", "next_checks": [], "safety_warnings": warnings,
              "leading_cause": None, "escalate": False}  # fmt: skip

    if not live:
        result["message"] = (
            "Every candidate cause in the standard fault tree has been ruled out by your checks. "
            "Do not guess: stop here, write down what you measured and escalate to your supervisor."
        )
        result["escalate"] = True
        return result

    if len(supported) == 1:
        lead = supported[0]
        result["leading_cause"] = lead["id"]
        remaining = [k for k in lead["defining"] if k not in done]
        if remaining:
            nxt = [checks[k] for k in remaining][:MAX_SUGGESTIONS]
            result["next_checks"] = [c["id"] for c in nxt]
            result["message"] = (
                f"Your evidence points to '{lead['label']}'. Before you commit, confirm it with: "
                f"{'; '.join(_describe(c) for c in nxt)}. What else would look different if you were wrong?"
            )
        else:
            result["message"] = (
                f"The evidence supports '{lead['label']}' and no other candidate is supported. "
                "If you agree, submit your diagnosis."
            )
    else:
        pool = supported if len(supported) > 1 else live
        ranked = _rank_checks(ctx, pool)
        if not ranked:
            result["message"] = ("There are no more standard checks to separate the remaining causes. "
                                 "Escalate to your supervisor with your notes.")  # fmt: skip
            result["escalate"] = True
            return result
        top = ranked[:MAX_SUGGESTIONS]
        result["next_checks"] = [c["id"] for c in top]
        first = top[0]
        target = max((c for c in pool if first["id"] in c["defining"]), key=lambda c: (c["weight"], c["id"]))
        if len(supported) > 1:
            intro = "Two causes are still supported by your readings, so look for a check that separates them."
        elif ctx["done"]:
            intro = "Nothing decisive yet. Keep the cheap, high-value checks first."
        else:
            intro = f"Symptom: {ctx['symptom']} Start with quick checks that cover the most likely causes."
        result["message"] = (
            f"{intro} Next: {_describe(first)}. What would you expect to see if "
            f"'{target['label']}' were the problem?"
        )

    if len(ctx["done"]) >= ESCALATE_AFTER and result["leading_cause"] is None:
        result["escalate"] = True
        result["message"] += " You have done many checks without a clear cause; consider escalating."

    needs_isolation = [checks[k] for k in result["next_checks"] if checks[k]["isolation"]]
    if needs_isolation and not ctx["loto"]:
        warnings.append(ctx["safety"]["loto"])
        if ctx["safety"]["extras"]:
            warnings.append(ctx["safety"]["extras"][0])
    return result


# --------------------------------------------------------------------------- assessment narrative
def mock_narrative(ctx: dict) -> dict:
    """A short supervisor-facing review built from the scoring facts (no model involved)."""
    parts, weights, facts = ctx["score"]["parts"], ctx["score"]["weights"], ctx["facts"]
    strengths: list[str] = []
    improvements: list[str] = []
    if parts["diagnosis"]:
        strengths.append("Reached the correct diagnosis.")
    else:
        improvements.append("The diagnosis was wrong; compare the evidence gathered with the readings that rule causes out.")
    if parts["safety"] == weights["safety"]:
        strengths.append("No attempt to start an isolating check without lockout/tagout.")
    else:
        improvements.append(
            f"{facts['blocked_attempts']} attempt(s) to start an isolating check before lockout/tagout; "
            "revisit the lockout procedure with this technician."
        )
    if facts["extra_minutes"] == 0:
        strengths.append("Went straight to the deciding check(s).")
    else:
        improvements.append(
            f"{facts['extra_minutes']} minutes more checking than the shortest path; look at which "
            "quick checks could have come first."
        )
    if parts["evidence"] == weights["evidence"]:
        strengths.append("The diagnosis was backed by the deciding reading.")
    else:
        improvements.append("The diagnosis was submitted without the deciding reading for that cause.")
    outcome = "correct" if ctx["correct"] else "incorrect"
    summary = (
        f"The diagnosis was {outcome}. {facts['checks_performed']} check(s), {facts['actual_minutes']} "
        f"minutes against a shortest path of {facts['optimal_minutes']} minutes."
    )
    return {"summary": summary, "strengths": strengths, "improvements": improvements}
