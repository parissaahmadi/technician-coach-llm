![ci](https://github.com/<USERNAME>/technician-coach-llm/actions/workflows/ci.yml/badge.svg)

# technician-coach-llm

A troubleshooting **coach for maintenance technicians**, driven by a language model, with one design
goal: **what is for supervisors stays away from technicians** (and away from the coaching model).

A technician works through an exercise on a (fictional) plant: they read the symptom, perform checks,
ask the coach what to do next and finally submit a diagnosis. The coach asks questions and names the
next best check; it never gets to see the answer. A supervisor can then read a scored assessment of how
the technician worked: score breakdown, wasted minutes and cost, safety lapses, the rubric.

Everything is **fictional** (Quanta compressors, Ironwood presses, Vortex pumps, users `tech.a`,
`tech.b`, `sup.c`). **The fault trees are made up for this demo. Do not use them for real maintenance
decisions.**

Pure standard library at runtime. It runs **without an API key**: the default `mock` mode is a
deterministic rule-based stand-in that plays the model's part. A real model is one flag away
(`--llm anthropic`), see "Real model" below for what is and is not tested.

```
technician / supervisor
        |
   service.py  --- who may do what (roles, ownership) --- checked BEFORE any model call
        |
   session.py  simulator: lockout gate, readings only when a check is performed
        |                      |
   coach.py                assessment.py   (supervisor path only)
   context = public KB +   context = secrets + transcript
   what THIS technician    score = scoring.py (code, deterministic)
   has done                narrative = model (text only)
        |                      |
        +------ llm.py: MockLLM  |  AnthropicHTTP (urllib, retry/backoff)
        |
   views.py  technician_view / supervisor_view: allowlists
```

## Try it

```bash
uv sync
uv run technician-coach list-cases
uv run technician-coach demo                     # scripted run: technician, then supervisor
uv run technician-coach play CASE-004 --as tech.b   # type commands, `help` lists them
```

Real output of `technician-coach demo` (mock mode):

```
[llm: mock]
Compressor C-1 overheats under load  [Screw compressor C-1]
symptom: The compressor trips on high oil temperature (105 °C) after about 40 minutes at full load. It runs normally at part load.

[tech.a] coach
coach: Symptom: The compressor trips on high oil temperature (105 °C) after about 40 minutes at full load. It runs normally at part load. Start with quick checks that cover the most likely causes. Next: Oil level in the sight glass (2 min). What would you expect to see if 'Oil level too low' were the problem?
  SAFETY: Lock out and tag out the machine and release all stored energy before opening any guard, housing, line or filter.
  SAFETY: Oil above 50 °C can cause burns: let it cool before opening the oil circuit.
  suggested checks: oil_level, oil_filter_dp, cooler_fins

[tech.a] check cooler_fins   (tries the isolating check first)
  forbidden: 'Cooler fin condition' needs the machine isolated. Apply lockout/tagout first (command: loto). Lock out and tag out the machine and release all stored energy before opening any guard, housing, line or filter.

[tech.a] check oil_level
  NORMAL - Level at three quarters of the glass.

[tech.a] check fan_motor
  NORMAL - Fan turns the right way; current normal.

[tech.a] loto

[tech.a] check cooler_fins
  ABNORMAL - Fins about 70 % blocked with dust and fibres.

[tech.a] note Dust and fibres everywhere in the hall.

[tech.a] coach
coach: The evidence supports 'Cooler fins clogged with dust' and no other candidate is supported. If you agree, submit your diagnosis.

[tech.a] diagnose clogged_cooler
diagnosis CORRECT: you said 'Cooler fins clogged with dust'
actual cause: Cooler fins clogged with dust
recommended fix: Clean the cooler with dry air and a gentle wash; fit a pre-filter on the air inlet of the room.

--- what the technician can reach ---

[tech.a] assessment (as tech.a)
  forbidden: tech.a (technician) is not allowed to assess

[tech.a] technician_view of tech.a's session (as tech.b)
  forbidden: session S-0001 belongs to tech.a

--- what the supervisor sees ---
--- SUPERVISOR ONLY ---
score 80/100 (diagnosis 50/50, safety 10/20, efficiency 10/20, evidence 10/10)
checks: 3, minutes: 12 (shortest path 6), blocked attempts: 1, avoidable cost: 4,200,000 rials
rubric: Expect the technician to check the cheap items (oil level, filter, fan) before opening the cooler, and to apply lockout before the cooler guard is opened. CANARY-RUBRIC-001
narrative (mock, not used for the score): The diagnosis was correct. 3 check(s), 12 minutes against a shortest path of 6 minutes.
  + Reached the correct diagnosis.
  + The diagnosis was backed by the deciding reading.
  - 1 attempt(s) to start an isolating check before lockout/tagout; revisit the lockout procedure with this technician.
  - 6 minutes more checking than the shortest path; look at which quick checks could have come first.
```

(The seed rubrics end with a `CANARY-RUBRIC-00x` marker on purpose: the tests use it to detect leaks, see below.)

The technician gets exactly one thing back about their own score: whether the diagnosis was correct,
the actual cause and the recommended fix (a debrief, only after submitting). The numbers, the rubric,
the case notes and the cost are supervisor-only.

## Who can do what

| action | technician | supervisor |
|---|---|---|
| start a session, list own sessions | yes | yes |
| perform checks, lockout, notes, ask the coach, diagnose | own session only | own session only |
| read the technician view | own session only | any session |
| read the assessment (score, rubric, notes, cost, transcript) | **no** | yes, finished sessions only |

A supervisor cannot play a technician's session; they can read it.

## How the separation is enforced

1. **Least privilege for the model.** The coaching context (`coach.build_context`) is built from the
   public knowledge base plus what *this technician* has already done. It does not contain the root cause,
   the readings of checks that have not been performed, the rubric, the supervisor notes or any cost.
   A model cannot leak what it was never given.
2. **Allowlist views.** `views.py` builds each response from named fields. A new attribute on a session
   or case stays invisible until someone adds it on purpose (there is a test for exactly this).
3. **Permissions before models.** `assessment()` checks the role in code first; for a technician no model
   call happens at all (a test asserts zero calls).
4. **Judgement in code, language in the model.** The score is computed by `scoring.py` from the
   transcript. The model only writes the narrative, which is labelled as such, length-capped and not used
   for any number. A cheerful or hostile model reply cannot change a score.
5. **Output validation.** Replies must be JSON; check ids, cause ids and text are validated against the
   context. Unknown ids are dropped, the lockout warning is added in code if the model forgot it, text is
   cleaned and capped.
6. **Injection resistance.** Technician notes are data inside a JSON block; `<` is escaped so a note cannot
   close the block. Even a model that obeys an injected instruction has no secret to reveal and no way to
   unlock another role.

The tests prove these with **canary strings** planted in every secret field. They then scan the prompts
sent to the model, the technician views (before and after the diagnosis), the coach replies and the CLI
output for a technician. A positive control checks that the same canaries do appear in the supervisor
view, so the scan cannot pass because it looks in the wrong place.

## Mock mode and the real model

`MockLLM` receives the same prompt a real model would (a task tag and a JSON context) and answers from
a small fault-tree rule set: it ranks unperformed checks by likely causes covered per minute, rules
causes out when their deciding check is normal, names a leading cause when exactly one is supported and
suggests escalation when nothing fits. It is **not** a language model. A test follows the coach step by
step through all five cases and checks that it always reaches the true cause.

`AnthropicHTTP` talks to the Messages API with `urllib` (no SDK): `x-api-key` and `anthropic-version`
headers, retry with exponential backoff and jitter on timeouts, connection errors, 429 and 5xx
(honouring `retry-after`, capped at 30 s), no retry on other 4xx, error text scrubbed of the key,
plain `http` refused except for localhost.

```bash
export ANTHROPIC_API_KEY=...        # never committed, never printed
uv run technician-coach --llm anthropic --model <model-id> demo
```

Failure behaviour: an invalid reply gets one repair attempt, then the technician receives the rule-based
advice marked as degraded; an unavailable service does the same without a second call; a rejected
request (wrong key or model) is **not** hidden.

## Tests

137 test cases (`uv run pytest`): knowledge-base and case validation (including "every case has exactly
one answer"), the simulator and lockout gate, scoring, the rule-based coach and solvability of every
case, coach reply validation and repair/fallback, the role matrix, the leak and injection tests above,
the HTTP client against a **local fake server** (request shape, retry, backoff, timeout, key scrubbing,
bad bodies), assessment and the CLI.

Mutation check: 37 deliberate bugs (a secret added to the coaching context, the view, or the technician
debrief; role and ownership checks removed; lockout not enforced; scoring changed; model-output validation
removed; retry or key-scrubbing broken; ...) were each caught by at least one test. One real bug was found
along the way by a test: the lockout warning was inserted in the wrong order.

## Limitations

* **Not evaluated against a real model.** The prompts are untested with Claude or any other LLM, so no
  claim is made about coaching quality. The HTTP client is tested only against a local fake server and
  has never called the real API. The default model id `claude-sonnet-5-5` is a placeholder you can
  override with `--model` or `COACH_MODEL`.
* **No authentication.** `--as tech.a` is a claimed identity from a fixed user table. A real system must
  take the identity from the login and keep the permission checks exactly where they are.
* **In memory.** Sessions disappear when the process ends; there is no storage, so a supervisor cannot
  review a session later in this demo. Persistence belongs behind `CoachService`.
* The scoring formula is deliberately simple: floor division for efficiency, half the safety weight per
  blocked attempt. Weights can be set per case.
* The mock's wording is templated; the knowledge base has three machine types, five cases, one deciding
  check per cause.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
```

Tests were run here with a small stand-in for `pytest` (the sandbox had no network); run the real
`uv run pytest` and `uv run ruff check` on your machine.

## License

MIT, see `LICENSE`.
