"""scripts/deploy.sh and the deployed_sha it leaves for run.json. A bare repo stands in for GitHub and a clone for
the droplet checkout; the test command is replaced by `true` so the suite does not run itself. Linux only."""
from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from recon import orchestrator  # noqa: E402

ID = ("-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=v2")


def g(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *ID, *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f"git {args}: {r.stderr}")
    return r.stdout.strip()


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("git"), "Linux with git only")
class DeployScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="recon_deploy_"))
        self.origin = self.tmp / "origin.git"
        g(self.tmp, "init", "-q", "--bare", str(self.origin))
        work = self.tmp / "work"
        g(self.tmp, "clone", "-q", str(self.origin), str(work))
        (work / "a.txt").write_text("one\n")
        g(work, "add", "a.txt")
        g(work, "commit", "-q", "-m", "one")
        g(work, "push", "-q", "origin", "HEAD:v2")
        self.checkout = self.tmp / "recon-v2"
        g(self.tmp, "clone", "-q", "-b", "v2", str(self.origin), str(self.checkout))
        self.old = g(self.checkout, "rev-parse", "HEAD")
        (work / "a.txt").write_text("two\n")
        g(work, "commit", "-q", "-am", "two")
        g(work, "push", "-q", "origin", "HEAD:v2")
        self.new = g(work, "rev-parse", "HEAD")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def deploy(self, now: str, *args: str, tests: str = "true") -> subprocess.CompletedProcess:
        env = {**os.environ, "RECON_DEPLOY_DIR": str(self.checkout), "RECON_DEPLOY_USER": getpass.getuser(),
               "RECON_DEPLOY_NOW": now, "RECON_DEPLOY_TESTS": tests}
        return subprocess.run(["bash", str(REPO / "scripts" / "deploy.sh"), *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def head(self) -> str:
        return g(self.checkout, "rev-parse", "HEAD")

    def test_refuses_inside_the_cron_window(self):
        for now in ("04:30", "05:00", "05:29"):
            r = self.deploy(now)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("inside the cron window", r.stderr)
        self.assertEqual(self.head(), self.old)

    def test_dry_run_changes_nothing(self):
        r = self.deploy("12:00", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"would move {self.checkout} from {self.old[:7]} to {self.new[:7]}", r.stdout)
        self.assertEqual(self.head(), self.old)
        self.assertFalse((self.checkout / "deploy").exists())

    def test_deploy_pulls_and_records_the_sha(self):
        r = self.deploy("05:30")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.head(), self.new)
        rec = json.loads((self.checkout / "deploy" / "last-deploy.json").read_text())
        self.assertEqual((rec["deployed_sha"], rec["previous_sha"], rec["branch"]), (self.new, self.old, "v2"))
        info = orchestrator.deploy_info(self.checkout)
        self.assertEqual(info["deployed_sha"], self.new)
        self.assertEqual(info["deployed_at"], rec["deployed_at"])
        self.assertIn("Already on origin/v2", self.deploy("12:00").stdout)

    def test_failing_tests_leave_the_checkout_alone(self):
        r = self.deploy("12:00", tests="echo boom; exit 3")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Tests failed", r.stderr)
        self.assertEqual(self.head(), self.old)
        self.assertFalse((self.checkout / "deploy").exists())

    def test_local_edits_stop_the_deploy(self):
        (self.checkout / "a.txt").write_text("edited on the droplet\n")
        r = self.deploy("12:00")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("local edits", r.stderr)
        self.assertEqual(self.head(), self.old)


class DeployInfoTests(unittest.TestCase):
    def test_absent_or_broken_file_gives_none(self):
        home = Path(tempfile.mkdtemp(prefix="recon_deployinfo_"))
        try:
            self.assertEqual(orchestrator.deploy_info(home), {"deployed_sha": None, "deployed_at": None})
            (home / "deploy").mkdir()
            (home / "deploy" / "last-deploy.json").write_text("not json")
            self.assertEqual(orchestrator.deploy_info(home), {"deployed_sha": None, "deployed_at": None})
        finally:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
