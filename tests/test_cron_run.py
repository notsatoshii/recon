"""cron_run.sh success rule (sixth review 2026-10-04): for the orchestrator, a run is done when run.json (written
by record, after deliver) is newer than the start, not when 07_daily_brief.md (written in synthesis, before checks
and deliver) exists. A fake orchestrator stands in for the real one. Linux only (flock, timeout, stat -c): runs on
the droplet (phase_c_validate.sh step 1), skipped on Windows."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

FAKE = r'''
import json, os, sys
from pathlib import Path
home = Path(os.environ["RECON_HOME"])
args = sys.argv[1:]
run = args[args.index("--run-id") + 1]
d = home / "briefs" / run
(d / "phases").mkdir(parents=True, exist_ok=True)
with (home / "calls.log").open("a") as f:
    f.write(" ".join(args) + "\n")
mode = os.environ["FAKE_MODE"]
if "--resume" in args:
    (d / "run.json").write_text("{}")
    sys.exit(0)
(d / "07_daily_brief.md").write_text("# RECON DAILY BRIEF -- test\n\nbody\n")
(d / "phases" / "takes.json").write_text("{}")
if mode == "ok":
    (d / "run.json").write_text("{}")
    sys.exit(0)
sys.exit(1)   # 'after_synthesis': checks or deliver raised after the brief was written
'''


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("flock") and shutil.which("timeout"),
                     "cron_run.sh needs Linux (flock, timeout, stat -c)")
class CronRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="recon_cron_"))
        (self.tmp / "scripts").mkdir()
        (self.tmp / "recon").mkdir()
        shutil.copy2(REPO / "scripts" / "cron_run.sh", self.tmp / "scripts" / "cron_run.sh")
        (self.tmp / "recon" / "orchestrator.py").write_text(FAKE, encoding="utf-8")
        (self.tmp / "empty.env").write_text("", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cron(self, mode: str) -> tuple[int, str, list[str]]:
        env = {**os.environ, "RECON_HOME": str(self.tmp), "RECON_ENV": str(self.tmp / "empty.env"), "FAKE_MODE": mode}
        p = subprocess.run(["/bin/bash", str(self.tmp / "scripts" / "cron_run.sh"), "--run-id", "t1", "--no-telegram"],
                           env=env, capture_output=True, text=True, timeout=120)
        log = (self.tmp / "logs" / "cron.log").read_text(encoding="utf-8")
        calls = (self.tmp / "calls.log").read_text(encoding="utf-8").splitlines()
        return p.returncode, log, calls

    def test_brief_without_run_json_is_resumed(self):
        rc, log, calls = self.run_cron("after_synthesis")
        self.assertEqual(len(calls), 2, log)
        self.assertIn("--resume", calls[1])
        self.assertIn("brief written but run not finished", log)
        self.assertEqual(rc, 0, log)
        self.assertIn("brief landed", log)

    def test_finished_run_is_not_resumed(self):
        rc, log, calls = self.run_cron("ok")
        self.assertEqual((rc, len(calls)), (0, 1), log)


if __name__ == "__main__":
    unittest.main()
