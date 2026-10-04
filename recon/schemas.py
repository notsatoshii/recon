"""Output schemas for the orchestrator's typed LLM calls (Codex --output-schema), and the Phase C
artifact schemas.

Codex validates against the call schemas in strict mode, so every object lists all its properties as
required and sets additionalProperties false; "unknown" is an empty string, not null. `validate()`
re-checks the parsed reply here, so a provider without schema support (the dry-run, the claude CLI)
still produces artifacts of the same shape.

Phase C (docs/v2/phase-c-spec.md §12): the call schemas are committed as schemas/debate/*.json and
tests/test_schemas.py checks that `ALL` produces the same JSON. Phase B's CHALLENGE, RESPONSE and
DEEP_DIVE stay under LEGACY for readers of old run folders. `ARTIFACTS` holds the programmatic
artifact schemas (nullable types and free-key objects are allowed there, never in `ALL`).
"""
from __future__ import annotations

import json
from pathlib import Path

AGENTS = ["trader", "narrator", "builder", "analyst", "skeptic", "policy_analyst",
          "user_agent", "macro_strategist", "ai_engineer"]
ENVIRONMENTS = ["MARKET-DRIVEN", "NARRATIVE-DRIVEN", "PRODUCT-DRIVEN", "RISK-DRIVEN", "QUIET"]


def obj(**props) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def s(desc: str = "") -> dict:
    return {"type": "string", "description": desc} if desc else {"type": "string"}


def num(desc: str = "") -> dict:
    return {"type": "number", "description": desc} if desc else {"type": "number"}


def enum(values: list[str], desc: str = "") -> dict:
    d = {"type": "string", "enum": values}
    if desc:
        d["description"] = desc
    return d


def b(desc: str = "") -> dict:
    return {"type": "boolean", **({"description": desc} if desc else {})}


def i(desc: str = "") -> dict:
    return {"type": "integer", **({"description": desc} if desc else {})}


def nullable(sch: dict) -> dict:
    return {**sch, "type": [sch["type"], "null"]}


def free(desc: str = "") -> dict:
    """An object with free keys (artifacts only, never sent to Codex)."""
    return {"type": "object", **({"description": desc} if desc else {})}


DATE = s("YYYY-MM-DD or empty")
QIDS = ["q1", "q2", "q3", "q4", "q5"]
# The brief's sections, in order (§11.4: moved here from the orchestrator; the orchestrator, the checks
# and llm.py's dry-run brief import it). Section 4 is WHERE THE VIEWS SPLIT since Phase C.
BRIEF_SECTIONS = ["WHAT HAPPENED", "WHAT IT MEANS", "MARKET MOOD", "WHERE THE VIEWS SPLIT", "AI NEWSLETTER",
                  "FUNDRAISING", "KOREA", "AI EDUCATION", "RISKS", "WHAT TO WATCH", "SCORECARD"]

EVIDENCE = obj(
    section=s("package section name, e.g. ON-CHAIN & MARKET DATA"),
    quote=s("text copied verbatim from the package, at most 200 characters"),
)

# ── Phase C call schemas (§12.1–12.2), committed as schemas/debate/*.json ──
QUESTION = obj(
    id=s("q1, q2, ... in order"),
    text=s("a yes/no question about today's package, at most 200 characters, ending with ?"),
    kind=enum(["threshold", "event", "direction", "judgment"]),
    domain=enum(["markets_crypto", "macro_policy", "ai_product", "korea", "prediction_markets"]),
    metric=s("the series that settles it, with its source; empty for event and judgment"),
    comparator=enum([">", ">=", "<", "<=", ""]),
    threshold=s("the level as written, e.g. 86.61B; empty when not a threshold"),
    baseline_quote=s("the package line giving today's value, copied verbatim; empty for judgment"),
    resolves_on=DATE,
    settles_with=s("the observable or event and where it is published"),
    lenses=arr(enum(AGENTS)),
    weight=i("1, 2 or 3"),
    carried_from=s("ledger id such as 2026-09-11-q2 if this re-asks an open question, else empty"),
)

TRIAGE = obj(
    environment=enum(ENVIRONMENTS, "what dominates today's data"),
    depth=enum(["quiet", "normal", "risk"]),
    reason=s("one sentence: why this environment and depth"),
    weight_agents=arr(enum(AGENTS)),
    questions=arr(QUESTION),
)

TAKE = obj(  # same fields as Phase B; positions first, take last (§3 item 2)
    positions=arr(obj(
        question_id=s(),
        probability=num("0-100: your probability that the answer is yes"),
        reason=s("at most 25 words, from your lens"),
        evidence=arr(EVIDENCE),
    )),
    claims=arr(obj(
        claim=s(),
        section=s(),
        quote=s("verbatim from the package"),
        confidence=enum(["low", "medium", "high"]),
    )),
    summary=s("your main call in one or two sentences, at most 50 words"),
    prediction=obj(
        text=s("one testable prediction"),
        probability=num("0-100"),
        resolves_on=s("YYYY-MM-DD"),
        metric=s("the number or event that decides it"),
    ),
    novel=s("one thing you expect the other analysts to miss"),
    watching=arr(s("an item to track next session")),
    take=s("your analysis in your persona's own voice, at most 150 words, written last"),
)

CRUX_TYPES = ["factual", "causal", "definitional", "timing"]

DEBATE_CHALLENGE = obj(
    question_id=enum(QIDS),
    steelman=s("the other view's best case in at most two sentences, in terms it would accept"),
    crux=obj(
        claim=s("the single factual or causal claim the disagreement turns on"),
        type=enum(CRUX_TYPES),
        observable=s("what you would look at to settle it"),
        by_date=DATE,
    ),
    rebuttal=s("at most 120 words, plain prose, no headings"),
    evidence=arr(EVIDENCE),
    would_change_my_mind=obj(observable=s(), level=s("level or event"), by_date=DATE),
)

DEBATE_RESPONSE = obj(
    question_id=enum(QIDS),
    steelman_fair=obj(verdict=enum(["yes", "partly", "no"]), correction=s("empty when yes")),
    crux_agreed=obj(verdict=enum(["yes", "no"]), own_crux=s("empty when yes")),
    verdict=enum(["hold", "narrow", "concede"]),
    new_probability=i("integer 0-100: your probability now"),
    reason=s("at most 60 words; what changed and why, or why nothing did"),
    new_evidence=arr(EVIDENCE),
)

RED_TEAM = obj(
    question_id=enum(QIDS),
    consensus_view=s("the view you argue against, one sentence"),
    case=s("the strongest case against it, at most 150 words"),
    crux=obj(claim=s(), type=enum(CRUX_TYPES), observable=s(), by_date=DATE),
    evidence=arr(EVIDENCE),
    probability=i("integer 0-100: your own probability that the answer is yes"),
    would_change_my_mind=obj(observable=s(), level=s(), by_date=DATE),
)

CRUX_CHECK = obj(
    resolved=enum(["yes", "no", "partly"]),
    what_the_data_says=s("at most 60 words"),
    quote=s("verbatim from the data block, at most 200 characters; empty if none"),
    section=s(),
    remaining_uncertainty=s("at most 40 words"),
    settles_on=obj(observable=s(), by_date=DATE),
    leans=enum(["higher", "lower", "neither"]),
)

# ── Phase B schemas, kept for readers of old run folders (export.py) ──
CHALLENGE = obj(
    text=s("your challenge, at most 220 words, plain prose"),
    crux=s("the single factual or causal claim the disagreement turns on"),
    question_id=s("the question this is about, or empty"),
    evidence=arr(EVIDENCE),
)

RESPONSE = obj(
    text=s("your answer to the challenges, at most 220 words, plain prose"),
    verdict=enum(["hold", "narrow", "concede"]),
    final_positions=arr(obj(
        question_id=s(),
        probability=num("0-100"),
        reason=s("at most 25 words; say what changed if it moved"),
    )),
    new_evidence=arr(EVIDENCE),
    vote=obj(
        act_on=s("the most important thing to act on today, 1-3 sentences"),
        market_wrong_about=s("what the market is wrong about, 1-3 sentences"),
        unseen_risk=s("the risk nobody is discussing, 1-3 sentences"),
    ),
)

DEEP_DIVE = obj(
    text=s("your final position on the point, at most 200 words"),
    final_probability=num("0-100"),
    would_change_my_mind=obj(observable=s(), by_date=s("YYYY-MM-DD")),
    evidence=arr(EVIDENCE),
)

ALL = {"triage": TRIAGE, "take": TAKE, "debate_challenge": DEBATE_CHALLENGE,
       "debate_response": DEBATE_RESPONSE, "red_team": RED_TEAM, "crux_check": CRUX_CHECK}
LEGACY = {"challenge": CHALLENGE, "response": RESPONSE, "deep_dive": DEEP_DIVE}

# ── Phase C artifacts (§12.3): programmatic, validated before save() and in tests ──
P = i("integer 0-100")
EV_STATUS = ["verified", "partial", "unverified", "empty"]
EV_CHECKED = obj(section=s(), quote=s(), status=enum(EV_STATUS),
                 cls=enum(["data", "social", ""]), doc=nullable(s()), line=nullable(i()))
PAIR = obj(question_id=s(), high=enum(AGENTS), low=enum(AGENTS), p_high=P, p_low=P,
           gap=i(), score=num(), both_lenses=b(), repeat_of_yesterday=b())
PAIRING = obj(
    day_type=enum(["debate", "split_unpaired", "consensus", "no_questions"]),
    depth=enum(["quiet", "normal", "risk"]), target=i(), gap_min=i(),
    pairs=arr(PAIR),
    candidates_considered=i(),
    unpaired=arr(obj(question_id=s(), range=i(), reason=s())),
    red_team=nullable(obj(agent=enum(AGENTS), question_id=s(), median=num(), distance=num(), reason=s())),
    eligible=obj(**{q: arr(enum(AGENTS)) for q in QIDS}),
    positions_evidence=free("{agent: {question_id: [EV_CHECKED]}}; active agents only, so §18 can retire agents"),
    budget=obj(used=i(), budget=i(), ceiling=i(), target_before_budget=i(), crux_check_planned=b()),
    debate=obj(enabled=b(), reason=s("why the debate is off (RECON_DEBATE, §0.1/§18); empty when on")),
)
EV_NEW = obj(section=s(), quote=s(), status=enum(EV_STATUS), cls=enum(["data", "social", ""]),
             new_evidence_source=enum(["crux_data", "challenger", "own", "other"]), qualifies=b())
SIDE_MOVE = obj(agent=enum(AGENTS), take=P, requested=nullable(P), gated=P, delta=i(),
                clamped=b(), rescaled=b(), new_evidence=arr(EV_NEW),
                new_evidence_verified=i(), new_data_evidence=i(),
                evidence_source=enum(["crux_data", "other", "none"]))
DEBATE_SCORE = obj(
    question_id=s(), high=enum(AGENTS), low=enum(AGENTS), status=enum(["two-sided", "one-sided", "failed"]),
    gap_before=i(), gap_after=nullable(i()),
    moves=arr(SIDE_MOVE),
    verdicts=obj(high=s(), low=s()),
    steelman_fair=obj(high=s(), low=s()),
    crux_agreed=b(),
    evidence=obj(high=obj(claimed=i(), verified=i(), data=i()), low=obj(claimed=i(), verified=i(), data=i())),
    flags=arr(obj(agent=enum(AGENTS), flag=s(), where=enum(["challenge", "response"]))),
    crux_check=nullable(obj(resolved=s(), leans=s())),
    closed_on_data=b(), narrowed_on_data=b(), closure_without_evidence=i(), effect=s(),
    in_split=b(), live_split=b(), held_split=b(), useful=b(),
)
STATS = obj(n=i(), median=nullable(num()), mean=nullable(num()), min=nullable(num()), max=nullable(num()),
            range=nullable(num()), iqr=nullable(num()), majority_side=enum(["yes", "no", "even", ""]),
            majority_count=i(), minority_count=i())
QUESTION_STATS = obj(id=s(), ledger_id=s(), text=s(), weight=i(), debated=b(),
                     take_stats=STATS, final_stats=STATS, finals=free("{agent: probability}"))
AGENT_RUN_SCORE = obj(
    run_id=s(), day=DATE, agent=enum(AGENTS), distinctness=nullable(num()), endpoint_count=i(),
    evidence_rate=nullable(num()), data_share=nullable(num()), unique_numbers=i(),
    moves_with_evidence=i(), moves_capped=i(), verbal_concessions=i(), steelmen_rejected=i(),
    leakage_flags=i(), novel=s(),
)
SPLIT_BLOCK = obj(
    type=enum(["direction", "degree", "consensus"]), debated=b(), question_id=s(), ledger_id=s(),
    question=s(), resolves_on=DATE, settles_with=s(), narrowed_on_data=b(),
    counts=obj(n=i(), majority=i(), minority=i(), median=num(), range=arr(i())),
    count_phrase=s(),
    base_case=obj(text=s(), quote=s()),
    minority_case=obj(text=s(), quote=s(), source=enum(["rebuttal", "steelman", "reason", "red_team"])),
    crux=s(), crux_check=nullable(obj(resolved=s(), what_the_data_says=s(), quote=s())),
    settles_on=obj(observable=s(), by_date=DATE),
    carried=s(),
)
SPLIT_SHEET = obj(day=DATE, run_id=s(), day_type=enum(["debate", "split_unpaired", "consensus", "no_questions"]),
                  blocks=arr(SPLIT_BLOCK), no_split_line=s(), bytes=i())
LEDGER_LINE = obj(
    type=enum(["question"]), ledger_id=s("<run_id>-<qid>; <day>-<qid> for the daily run"), run_id=s(), day=DATE,
    question=QUESTION,                                   # as gated, weight clamped
    finals=free("{agent: gated probability}"), take_values=free("{agent: take probability}"),
    final_stats=STATS, debated=b(), split_type=enum(["direction", "degree", "consensus", "none"]),
)
# run.json (schema_version 2): the Phase C parts are checked; the Phase B keys around them are free.
RUN = {"type": "object", "required": ["schema_version", "date", "day", "status", "questions", "pairing", "debates",
                                      "red_team", "crux_check", "split_sheet", "agent_scores", "agents", "edges",
                                      "usage", "deep_dive"],
       "properties": {"schema_version": i(), "date": s(), "day": DATE, "status": enum(["ok", "partial", "failed"]),
                      "questions": arr(free()), "pairing": nullable(free()), "debates": arr(DEBATE_SCORE),
                      "red_team": nullable(free()), "crux_check": nullable(free()), "split_sheet": nullable(SPLIT_SHEET),
                      "agent_scores": free(), "agents": arr(free()), "edges": arr(free()), "deep_dive": {"type": "null"},
                      "usage": {"type": "object", "required": ["calls", "budget_skips"],
                                "properties": {"calls": i(), "budget_skips": arr(obj(
                                    phase=s(), item=s(), reason=enum(["budget", "ceiling"]), used=i()))}}}}
ARTIFACTS = {"pairing": PAIRING, "debate_score": DEBATE_SCORE, "question_stats": QUESTION_STATS,
             "agent_run_score": AGENT_RUN_SCORE, "split_block": SPLIT_BLOCK, "split_sheet": SPLIT_SHEET,
             "ledger_line": LEDGER_LINE, "ev_checked": EV_CHECKED, "side_move": SIDE_MOVE, "stats": STATS,
             "run": RUN}


def write_all(directory: Path) -> dict[str, str]:
    """Write every call schema as <dir>/<name>.json, the Phase B LEGACY ones too (an older checkout's
    phase code still finds its schema path); returns name -> path."""
    directory.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, sch in {**LEGACY, **ALL}.items():
        p = directory / f"{name}.json"
        p.write_text(json.dumps(sch, indent=1), encoding="utf-8")
        out[name] = str(p)
    return out


class SchemaError(ValueError):
    pass


def validate(value, sch: dict, path: str = "$"):
    """Check `value` against the subset of JSON schema used above. Coerces numeric strings (and an
    integral float or numeric string for "integer", rejecting 0.5); clamps nothing; raises
    SchemaError with the first problem. Supports nullable types (["string", "null"]) and free-key
    objects (an object schema without "properties")."""
    t = sch.get("type")
    if isinstance(t, list):
        if value is None and "null" in t:
            return None
        rest = [x for x in t if x != "null"]
        return validate(value, {**sch, "type": rest[0] if rest else None}, path)
    if t == "object":
        if not isinstance(value, dict):
            raise SchemaError(f"{path}: expected object")
        for k in sch.get("required", []):
            if k not in value:
                raise SchemaError(f"{path}.{k}: missing")
        for k, sub in sch.get("properties", {}).items():
            if k in value:
                value[k] = validate(value[k], sub, f"{path}.{k}")
        return value
    if t == "array":
        if not isinstance(value, list):
            raise SchemaError(f"{path}: expected array")
        return [validate(v, sch.get("items", {}), f"{path}[{n}]") for n, v in enumerate(value)]
    if t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            try:
                return float(str(value).strip().rstrip("%"))
            except ValueError:
                raise SchemaError(f"{path}: expected number")
        return value
    if t == "integer":
        if isinstance(value, bool):
            raise SchemaError(f"{path}: expected integer")
        if isinstance(value, int):
            return value
        try:
            f = float(str(value).strip().rstrip("%"))
        except ValueError:
            raise SchemaError(f"{path}: expected integer")
        if f != int(f):
            raise SchemaError(f"{path}: expected integer, got {value!r}")
        return int(f)
    if t == "boolean":
        if not isinstance(value, bool):
            raise SchemaError(f"{path}: expected boolean")
        return value
    if t == "string":
        if not isinstance(value, str):
            raise SchemaError(f"{path}: expected string")
        if "enum" in sch and value not in sch["enum"]:
            raise SchemaError(f"{path}: {value!r} not in {sch['enum']}")
        return value
    return value


def parse(text: str, name: str) -> dict:
    """Parse a model reply as JSON (tolerating a fenced block) and validate it."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    i0, j = t.find("{"), t.rfind("}")
    if i0 < 0 or j < i0:
        raise SchemaError("no JSON object in the reply")
    try:
        value = json.loads(t[i0:j + 1])
    except json.JSONDecodeError as e:
        raise SchemaError(f"invalid JSON: {e}")
    return validate(value, ALL.get(name) or LEGACY[name])
