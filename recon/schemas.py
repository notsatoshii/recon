"""Output schemas for the orchestrator's typed LLM calls (Codex --output-schema).

Codex validates against these in strict mode, so every object lists all its properties as
required and sets additionalProperties false; "unknown" is an empty string, not null.
`validate()` re-checks the parsed reply here, so a provider without schema support (the
dry-run, the claude CLI) still produces artifacts of the same shape.
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


EVIDENCE = obj(
    section=s("package section name, e.g. ON-CHAIN & MARKET DATA"),
    quote=s("text copied verbatim from the package, at most 200 characters"),
)

TRIAGE = obj(
    environment=enum(ENVIRONMENTS, "what dominates today's data"),
    depth=enum(["quiet", "normal", "risk"]),
    reason=s("one sentence: why this environment and depth"),
    weight_agents=arr(enum(AGENTS)),
    questions=arr(obj(
        id=s("q1, q2, ... in order"),
        text=s("a question the lenses can disagree on, resolvable where possible"),
        resolves_on=s("YYYY-MM-DD when it can be settled, or empty"),
        settles_with=s("the observable or data series that settles it"),
        lenses=arr(enum(AGENTS)),
    )),
)

TAKE = obj(
    take=s("your analysis in your persona's own output format, 200-400 words, markdown"),
    summary=s("your main call in one or two sentences, at most 50 words"),
    positions=arr(obj(
        question_id=s(),
        probability=num("0-100: your probability that the answer is yes"),
        reason=s("at most 25 words"),
        evidence=arr(EVIDENCE),
    )),
    claims=arr(obj(
        claim=s(),
        section=s(),
        quote=s("verbatim from the package"),
        confidence=enum(["low", "medium", "high"]),
    )),
    prediction=obj(
        text=s("one testable prediction"),
        probability=num("0-100"),
        resolves_on=s("YYYY-MM-DD"),
        metric=s("the number or event that decides it"),
    ),
    novel=s("one thing you expect the other analysts to miss"),
    watching=arr(s("an item to track next session")),
)

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

ALL = {"triage": TRIAGE, "take": TAKE, "challenge": CHALLENGE, "response": RESPONSE, "deep_dive": DEEP_DIVE}


def write_all(directory: Path) -> dict[str, str]:
    """Write every schema as <dir>/<name>.json; returns name -> path."""
    directory.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, sch in ALL.items():
        p = directory / f"{name}.json"
        p.write_text(json.dumps(sch, indent=1), encoding="utf-8")
        out[name] = str(p)
    return out


class SchemaError(ValueError):
    pass


def validate(value, sch: dict, path: str = "$"):
    """Check `value` against the subset of JSON schema used above. Coerces numeric strings and
    clamps nothing; raises SchemaError with the first problem."""
    t = sch.get("type")
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
        return [validate(v, sch.get("items", {}), f"{path}[{i}]") for i, v in enumerate(value)]
    if t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            try:
                return float(str(value).strip().rstrip("%"))
            except ValueError:
                raise SchemaError(f"{path}: expected number")
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
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        raise SchemaError("no JSON object in the reply")
    try:
        value = json.loads(t[i:j + 1])
    except json.JSONDecodeError as e:
        raise SchemaError(f"invalid JSON: {e}")
    return validate(value, ALL[name])
