"""Phase C schema tests (docs/v2/phase-c-spec.md §17.2). Run on the droplet after every pull of v2:
python3 -m unittest tests.test_schemas"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from recon import debate, evidence, llm, schemas  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "package"


def walk(sch, path="$"):
    """Yield (path, problem) for anything Codex strict mode rejects."""
    t = sch.get("type")
    if isinstance(t, list) or t == "null":
        yield path, "nullable type"
    if t == "object":
        props = sch.get("properties")
        if props is None:
            yield path, "free-key object"
            return
        if sorted(sch.get("required", [])) != sorted(props):
            yield path, "required != properties"
        if sch.get("additionalProperties") is not False:
            yield path, "additionalProperties not false"
        for k, v in props.items():
            yield from walk(v, f"{path}.{k}")
    if t == "array":
        yield from walk(sch.get("items", {}), f"{path}[]")


GOLDEN = {
    "triage": {"environment": "QUIET", "depth": "normal", "reason": "r", "weight_agents": ["trader"],
               "questions": [{"id": "q1", "text": "Will TVL hold 86B by 10-11?", "kind": "threshold",
                              "domain": "markets_crypto", "metric": "TVL", "comparator": ">=", "threshold": "86B",
                              "baseline_quote": "- Current: $86,610,000,000", "resolves_on": "2026-10-11",
                              "settles_with": "DeFiLlama", "lenses": ["trader", "analyst"], "weight": 2,
                              "carried_from": ""}]},
    "take": {"positions": [{"question_id": "q1", "probability": 60, "reason": "r",
                            "evidence": [{"section": "S", "quote": "q"}]}],
             "claims": [{"claim": "c", "section": "s", "quote": "q", "confidence": "low"}], "summary": "s",
             "prediction": {"text": "t", "probability": 55, "resolves_on": "2026-10-11", "metric": "m"},
             "novel": "n", "watching": ["w"], "take": "t"},
    "debate_challenge": {"question_id": "q1", "steelman": "s", "crux": {"claim": "c", "type": "factual",
                         "observable": "o", "by_date": ""}, "rebuttal": "r", "evidence": [],
                         "would_change_my_mind": {"observable": "o", "level": "l", "by_date": "2026-10-11"}},
    "debate_response": {"question_id": "q1", "steelman_fair": {"verdict": "yes", "correction": ""},
                        "crux_agreed": {"verdict": "no", "own_crux": "x"}, "verdict": "hold", "new_probability": 55,
                        "reason": "r", "new_evidence": []},
    "red_team": {"question_id": "q1", "consensus_view": "v", "case": "c", "crux": {"claim": "c", "type": "causal",
                 "observable": "o", "by_date": ""}, "evidence": [], "probability": 30,
                 "would_change_my_mind": {"observable": "o", "level": "l", "by_date": ""}},
    "crux_check": {"resolved": "partly", "what_the_data_says": "w", "quote": "", "section": "",
                   "remaining_uncertainty": "u", "settles_on": {"observable": "o", "by_date": ""}, "leans": "neither"},
}


class SchemaTests(unittest.TestCase):
    def test_strict_mode_clean(self):
        for name, sch in schemas.ALL.items():
            with self.subTest(name=name):
                self.assertEqual(list(walk(sch)), [])

    def test_committed_json_matches(self):
        for name in schemas.ALL:
            with self.subTest(name=name):
                f = REPO / "schemas" / "debate" / f"{name}.json"
                self.assertEqual(schemas.ALL[name], json.loads(f.read_text(encoding="utf-8")))
        self.assertEqual(schemas.QUESTION,
                         json.loads((REPO / "schemas" / "debate" / "question.json").read_text(encoding="utf-8")))

    def test_golden_and_missing_field(self):
        for name, gold in GOLDEN.items():
            with self.subTest(name=name):
                schemas.validate(copy.deepcopy(gold), schemas.ALL[name])
                bad = copy.deepcopy(gold)
                k = next(iter(bad))
                del bad[k]
                with self.assertRaisesRegex(schemas.SchemaError, rf"\$\.{k}: missing"):
                    schemas.validate(bad, schemas.ALL[name])
        with self.assertRaises(schemas.SchemaError):
            schemas.validate(0.5, schemas.i())
        self.assertEqual(schemas.validate("7", schemas.i()), 7)

    def test_write_all_keeps_legacy_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = schemas.write_all(Path(tmp))
            for name in list(schemas.ALL) + list(schemas.LEGACY):
                self.assertTrue(Path(paths[name]).exists(), name)


def fixture_prompt(day: str, task: str) -> str:
    view = (FIX / day / "01_filtered.md").read_text(encoding="utf-8")
    return f"TODAY'S INTELLIGENCE PACKAGE ({day}).\n{view}\n--- END PACKAGE ---\n{task}"


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.env = os.environ.get("RECON_DRY_SPREAD")
        os.environ.pop("RECON_DRY_SPREAD", None)

    def tearDown(self):
        if self.env is not None:
            os.environ["RECON_DRY_SPREAD"] = self.env

    def test_dry_json_validates(self):
        prompt = fixture_prompt("2026-10-04", "QUESTION [q2]: x\n[q1] a\n[q2] b\nResolves: 2026-10-11")
        for name, sch in schemas.ALL.items():
            with self.subTest(name=name):
                out = schemas.parse(llm._dry_json(sch, prompt, "trader"), name)
                if "question_id" in sch.get("properties", {}):
                    self.assertIn(out["question_id"], ("q1", "q2"))
                if name == "debate_response":
                    self.assertIsInstance(out["new_probability"], int)

    def test_dry_triage_passes_the_gate(self):
        for day in ("2026-09-11", "2026-10-04"):
            with self.subTest(day=day):
                prompt = fixture_prompt(day, f"TASK: TRIAGE AND QUESTIONS OF THE DAY ({day}).")
                tri = schemas.parse(llm._dry_json(schemas.TRIAGE, prompt, "synthesizer"), "triage")
                d = FIX / day
                loc = evidence.Locator({"package": (d / "00_data_package.md").read_text(encoding="utf-8"),
                                        "raw": (d / "00_raw_data.md").read_text(encoding="utf-8"),
                                        "view": (d / "01_filtered.md").read_text(encoding="utf-8")})
                res = debate.gate_questions(tri["questions"], day, loc, depth="risk")
                self.assertGreaterEqual(len(res["kept"]), 3, res["dropped"])


class ArtifactTests(unittest.TestCase):
    def test_pairing_with_inactive_agent(self):
        p = {"trader": {"q1": 20}, "analyst": {"q1": 50}, "skeptic": {"q1": 80}}
        ev = [{"section": "", "quote": "q", "status": "verified", "cls": "data", "doc": "package", "line": 3}]
        evq = {a: {"q1": ev} for a in p}
        res = debate.pair([{"id": "q1", "text": "t", "kind": "threshold", "weight": 2, "lenses": []}], p, evq,
                          list(p), "normal", 3)
        res["positions_evidence"] = evq           # builder, narrator ... inactive: absent
        res["budget"] = {"used": 10, "budget": 24, "ceiling": 32, "target_before_budget": 3, "crux_check_planned": False}
        res["debate"] = {"enabled": True, "reason": ""}
        schemas.validate(json.loads(json.dumps(res)), schemas.ARTIFACTS["pairing"])
        cons = debate.pair([{"id": "q1", "text": "t", "kind": "threshold", "weight": 2, "lenses": []}],
                           {"trader": {"q1": 60}, "analyst": {"q1": 62}, "skeptic": {"q1": 65}}, evq, list(p), "normal", 3)
        cons["positions_evidence"], cons["budget"], cons["debate"] = evq, res["budget"], res["debate"]
        schemas.validate(json.loads(json.dumps(cons)), schemas.ARTIFACTS["pairing"])

    def test_stats_and_scores(self):
        schemas.validate(debate.question_stats([10, 50, 90]), schemas.ARTIFACTS["stats"])
        schemas.validate(debate.question_stats([]), schemas.ARTIFACTS["stats"])
        sc = debate.agent_run_score("trader", "r", "2026-10-04", {"trader": {"q1": 40}, "skeptic": {"q1": 60}}, [], [],
                                    {"trader": {"1"}, "skeptic": {"2"}}, [], "n")
        schemas.validate(sc, schemas.ARTIFACTS["agent_run_score"])


if __name__ == "__main__":
    unittest.main()
