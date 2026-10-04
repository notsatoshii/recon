"""Phase C dry-run end to end (docs/v2/phase-c-spec.md §17.4). Each test runs recon/orchestrator.py on the
dry-run provider in a scratch copy of the repo (RECON_HOME), on the committed 2026-10-04 / 2026-09-11
package fixtures. No network, no LLM; a few seconds per run."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from recon import schemas  # noqa: E402

COPY = ("recon", "scripts", "personas", "config", "schemas")
# Patch read() so any data-sources/ path fails the run: a replay must read nothing there (§17.4).
GUARD = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from recon import orchestrator as o
_read = o.read
def guarded(p, limit=None):
    if "data-sources" in Path(p).parts:
        raise RuntimeError(f"replay read data-sources: {p}")
    return _read(p, limit)
o.read = guarded
sys.exit(o.main(sys.argv[2:]))
"""


def tree_hash(root: Path) -> str:
    h = hashlib.sha1()
    if root.exists():
        for f in sorted(root.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                h.update(str(f.relative_to(root)).encode())
                h.update(f.read_bytes())
    return h.hexdigest()


class DryRunEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="recon-dry-"))
        for d in COPY:
            shutil.copytree(REPO / d, cls.tmp / d, ignore=shutil.ignore_patterns("__pycache__", "*.db*"))
        shutil.copytree(REPO / "tests" / "fixtures" / "package", cls.tmp / "fixtures")
        ds = cls.tmp / "data-sources" / "news"
        ds.mkdir(parents=True)
        (ds / "latest.md").write_text("# News Intelligence\n## 2026-10-04 00:00 UTC\n- October news\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_orch(self, run_id, *extra, env=None, day="2026-10-04", guard=False):
        e = {**os.environ, "RECON_HOME": str(self.tmp), "PYTHONIOENCODING": "utf-8", **(env or {})}
        for k in ("RECON_DRY_SPREAD", "RECON_CALL_BUDGET", "RECON_CALL_CEILING", "RECON_STOP_AFTER", "RECON_PAIR_GAP"):
            if k not in (env or {}):
                e.pop(k, None)
        args = ["--dry-run", "--as-of", day, "--run-id", run_id, *extra]
        if not any(a == "--replay" for a in extra):
            args = ["--package-from", str(self.tmp / "fixtures" / day), *args]
        cmd = [sys.executable, "-c", GUARD, str(self.tmp), *args] if guard else \
              [sys.executable, str(self.tmp / "recon" / "orchestrator.py"), *args]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=e, timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout[-3000:] + p.stderr[-2000:])
        return p.stdout

    def rd(self, run_id) -> Path:
        return self.tmp / "briefs" / run_id

    def load(self, run_id, name):
        return json.loads((self.rd(run_id) / "phases" / f"{name}.json").read_text(encoding="utf-8"))

    def brief_heads(self, run_id):
        b = (self.rd(run_id) / "07_daily_brief.md").read_text(encoding="utf-8")
        return [l[4:].strip() for l in b.splitlines() if l.startswith("### ")], b

    def test_wide_spread(self):
        self.run_orch("w1")
        self.assertGreaterEqual(len(self.load("w1", "triage")["data"]["questions"]), 3)
        pr = self.load("w1", "pairing")
        self.assertEqual(pr["day_type"], "debate")
        self.assertGreaterEqual(len(pr["pairs"]), 1)
        for name in ("challenges", "responses", "cruxcheck", "split", "synthesis", "checks", "cruxsearch"):
            self.assertTrue((self.rd("w1") / "phases" / f"{name}.json").exists(), name)
        heads, _ = self.brief_heads("w1")
        self.assertEqual(heads, schemas.BRIEF_SECTIONS)
        run = json.loads((self.rd("w1") / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["schema_version"], 2)
        schemas.validate(run, schemas.ARTIFACTS["run"])
        self.assertEqual(run["status"], "ok")

    def test_narrow_spread_consensus(self):
        self.run_orch("n1", env={"RECON_DRY_SPREAD": "narrow"})
        self.assertEqual(self.load("n1", "pairing")["day_type"], "consensus")
        chs = list((self.rd("n1") / "phases" / "challenges").glob("*.json"))
        self.assertEqual([c.name.split("__")[0] for c in chs], ["redteam"])
        self.assertFalse(list((self.rd("n1") / "phases" / "responses").glob("*.json")))
        self.assertIn("redteam", self.load("n1", "cruxsearch"))
        self.assertEqual(self.load("n1", "split_sheet")["blocks"][0]["type"], "consensus")
        _, brief = self.brief_heads("n1")
        self.assertIn("case against", brief)

    def test_budget_and_ceiling(self):
        self.run_orch("b1", env={"RECON_CALL_BUDGET": "18"})
        pr = self.load("b1", "pairing")
        self.assertLess(len(pr["pairs"]), 3)
        run = json.loads((self.rd("b1") / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(run["usage"]["budget_skips"])
        self.run_orch("c1", env={"RECON_CALL_CEILING": "12"})
        run = json.loads((self.rd("c1") / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(any(s["reason"] == "ceiling" for s in run["usage"]["budget_skips"]))
        self.assertTrue((self.rd("c1") / "07_daily_brief.md").exists())
        self.assertLessEqual(run["usage"]["calls_logged"], 12 + 2)   # + the two synthesis calls, never skipped

    def test_resume_alias_and_ledger(self):
        self.run_orch("r1")
        calls = (self.rd("r1") / "phases" / "calls.jsonl").read_text(encoding="utf-8").splitlines()
        before = [c for c in calls if json.loads(c)["phase"] in ("triage", "takes", "challenges")]
        out = self.run_orch("r1", "--from-phase", "responses")
        self.assertIn("[challenges] reused", out)
        after = (self.rd("r1") / "phases" / "calls.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([c for c in after if json.loads(c)["phase"] in ("triage", "takes", "challenges")], before)
        out = self.run_orch("r1", "--from-phase", "deepdive")
        self.assertIn("--from-phase deepdive → cruxcheck", out)
        self.run_orch("r1", "--from-phase", "split")
        self.run_orch("r1", "--from-phase", "split")
        led = (self.rd("r1") / "state" / "questions" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        ids = [json.loads(l)["ledger_id"] for l in led]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), len(self.load("r1", "triage")["data"]["questions"]))

    def test_replay_cold_state_nothing_from_data_sources(self):
        cfg, ds = tree_hash(self.tmp / "config"), tree_hash(self.tmp / "data-sources")
        self.run_orch("p1", "--replay", str(self.tmp / "fixtures" / "2026-09-11"), day="2026-09-11", guard=True)
        self.assertEqual(tree_hash(self.tmp / "config"), cfg)
        self.assertEqual(tree_hash(self.tmp / "data-sources"), ds)
        st = self.rd("p1") / "state"
        self.assertTrue((st / "questions" / "ledger.jsonl").exists())
        self.assertEqual((self.rd("p1") / "00_historical_context.md").read_text(encoding="utf-8").strip(), "")
        raw = json.loads((self.rd("p1") / "07_raw_sections.json").read_text(encoding="utf-8"))
        self.assertNotIn("October news", json.dumps(raw))
        tri = self.load("p1", "triage")["data"]
        self.assertTrue(all(q["resolves_on"] in ("", "2026-09-18") for q in tri["questions"]))

    def test_skip_collect_assembly_writes_the_raw_file(self):
        """phase-e §4.1a / §4.5a: --skip-collect assembles 00_raw_data.md with the Phase E files (a stale one
        becomes a SOURCE STALE line), and the synthesizer's raw blocks carry the changelogs and ZDNet."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        ds = self.tmp / "data-sources"
        files = {"changelogs": f"# Changelogs Intelligence\n## {now} UTC\n- [openai/codex] v1.2 released https://github.com/openai/codex/releases/tag/v1.2\n",
                 "zdnet_kr": f"# ZDNet Korea Intelligence\n## {now} UTC\n- [2026-10-04 01:00 UTC] 네이버, 새 AI 모델 공개 https://zdnet.co.kr/view/?no=1\n",
                 "kalshi": "# Kalshi Intelligence\n## 2026-09-01 20:05 UTC\n- an old market\n",
                 "ai_tools": f"# AI & Tools Intelligence\n## {now} UTC\n- repo one 1,234 stars\n"}
        for name, text in files.items():
            (ds / name).mkdir(parents=True, exist_ok=True)
            (ds / name / "latest.md").write_text(text, encoding="utf-8")
        e = {**os.environ, "RECON_HOME": str(self.tmp), "PYTHONIOENCODING": "utf-8"}
        p = subprocess.run([sys.executable, str(self.tmp / "recon" / "orchestrator.py"), "--dry-run", "--run-id", "a1",
                            "--as-of", datetime.now().strftime("%Y-%m-%d")],
                           capture_output=True, text=True, encoding="utf-8", env=e, timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout[-2000:])
        raw = (self.rd("a1") / "00_raw_data.md").read_text(encoding="utf-8")
        self.assertIn("# Changelogs Intelligence", raw)
        self.assertIn("- Kalshi: SOURCE STALE", raw)
        self.assertNotIn("an old market", raw)
        pkg = (self.rd("a1") / "00_data_package.md").read_text(encoding="utf-8")
        self.assertIn("# ZDNet Korea Intelligence", pkg)
        rs = json.loads((self.rd("a1") / "07_raw_sections.json").read_text(encoding="utf-8"))
        self.assertIn("# Changelogs Intelligence", rs["AI_RAW"])
        self.assertIn("# ZDNet Korea Intelligence", rs["KR_RAW"])
        fx = json.loads((self.rd("w1") / "07_raw_sections.json").read_text(encoding="utf-8")) \
            if (self.rd("w1") / "07_raw_sections.json").exists() else None
        if fx:
            self.assertNotIn("# Changelogs Intelligence", fx["AI_RAW"])
            self.assertNotIn("# ZDNet Korea Intelligence", fx["KR_RAW"])

    def test_stop_after_takes(self):
        out = self.run_orch("s1", env={"RECON_STOP_AFTER": "takes"})
        self.assertIn("RECON_STOP_AFTER=takes", out)
        self.assertFalse((self.rd("s1") / "phases" / "pairing.json").exists())


if __name__ == "__main__":
    unittest.main()
