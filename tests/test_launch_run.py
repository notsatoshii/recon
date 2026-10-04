"""scripts/launch_run.py (phase-c-spec §15.2a): a replay is launched only when the checkout equals origin/v2
after a pull that works for root on a recon-owned checkout, under the next run id after the highest c<N>,
never the first gap. 2026-09-11-c3 ran on stale 8b08a95: 'git pull --ff-only' as root died with 'dubious
ownership' and c3 was the gap left by deleted runs. GIT_TEST_ASSUME_DIFFERENT_OWNER=1 makes git treat every
repo as foreign-owned, as on the droplet."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import launch_run  # noqa: E402

ID = ("-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=v2")


def g(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *ID, *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f"git {args}: {r.stderr}")
    return r.stdout.strip()


class NextRunIdTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="recon_launch_"))
        (self.home / "briefs").mkdir()
        (self.home / "logs").mkdir()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def mk(self, *names: str):
        for n in names:
            (self.home / "briefs" / n).mkdir()

    def test_highest_not_first_gap(self):
        # the droplet on 2026-10-04: c3-c5 and c7 deleted, c13 the newest
        self.mk("2026-09-11", "2026-09-11-c1", "2026-09-11-c1-r0", "2026-09-11-c2", "2026-09-11-c6",
                "2026-09-11-c8", "2026-09-11-c8t1", "2026-09-11-c9", "2026-09-11-c13", "2026-10-04-c20")
        (self.home / "briefs" / "replay_summary_c12.md").write_text("x")
        self.assertEqual(launch_run.next_run_id(self.home, "2026-09-11"), "2026-09-11-c14")

    def test_log_and_launch_file_count(self):
        # a launched run whose folder is not there yet, or a folder deleted with its log kept
        self.mk("2026-09-11-c2")
        (self.home / "logs" / "2026-09-11-c7.log").write_text("x")
        self.assertEqual(launch_run.next_run_id(self.home, "2026-09-11"), "2026-09-11-c8")
        (self.home / "logs" / "2026-09-11-c9.launch.out").write_text("x")
        self.assertEqual(launch_run.next_run_id(self.home, "2026-09-11"), "2026-09-11-c10")

    def test_suffixes_and_none(self):
        self.assertEqual(launch_run.next_run_id(self.home, "2026-09-11"), "2026-09-11-c1")
        self.mk("2026-09-11-c1s", "2026-09-11-c1t2", "2026-09-11-p3h")
        self.assertEqual(launch_run.next_run_id(self.home, "2026-09-11"), "2026-09-11-c2")
        self.assertEqual(launch_run.next_run_id(self.home, "2026-09-11", "p"), "2026-09-11-p4")


class ResampleTests(unittest.TestCase):
    """09-11 c15 (phase C): a consensus draw with q3 one under GAP_MIN, where c14 drew 34 on the same package. The
    report does not count such a run (replay_report NOT COUNTED) until two take-only resamples of its triage say
    whether the day splits; launch_run --resample stages them (copy_run as in phase_c_validate step 5: c<N>t1,
    c<N>t2 on the run's triage.json, run.json and started.txt removed) and runs them one after the other,
    RECON_STOP_AFTER=takes, --from-phase takes."""
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="recon_resample_"))
        ph = self.home / "briefs" / "2026-09-11-c15" / "phases"
        ph.mkdir(parents=True)
        (ph / "triage.json").write_text("{}")
        (ph / "started.txt").write_text("x")
        (self.home / "briefs" / "2026-09-11-c15" / "run.json").write_text("{}")
        (self.home / "logs").mkdir()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_two_take_only_copies_run_in_order(self):
        with mock.patch.object(launch_run, "sync", return_value=(True, "abc1234")),                 mock.patch.object(launch_run, "launch_chain", return_value=77) as chain, mock.patch("builtins.print"):
            rc = launch_run.main(["--resample", "2026-09-11-c15", "--home", str(self.home)])
        self.assertEqual(rc, 0)
        b = self.home / "briefs"
        for t in ("t1", "t2"):
            self.assertTrue((b / f"2026-09-11-c15{t}" / "phases" / "triage.json").exists())
            self.assertFalse((b / f"2026-09-11-c15{t}" / "phases" / "started.txt").exists())
            self.assertFalse((b / f"2026-09-11-c15{t}" / "run.json").exists())
        (home, cmds, env), _ = chain.call_args
        self.assertEqual(env, {"RECON_STOP_AFTER": "takes"})
        self.assertEqual([c[c.index("--run-id") + 1] for c in cmds], ["2026-09-11-c15t1", "2026-09-11-c15t2"])
        for c in cmds:
            self.assertEqual(c[c.index("--replay") + 1:c.index("--replay") + 4], ["briefs/2026-09-11", "--as-of", "2026-09-11"])
            self.assertEqual(c[-2:], ["--from-phase", "takes"])

    def test_next_free_suffix_and_missing_triage(self):
        (self.home / "briefs" / "2026-09-11-c15t1").mkdir()
        self.assertEqual(launch_run.resample_ids(self.home, "2026-09-11-c15", 2), ["2026-09-11-c15t2", "2026-09-11-c15t3"])
        with mock.patch.object(launch_run, "sync", return_value=(True, "abc1234")),                 mock.patch.object(launch_run, "launch_chain") as chain, mock.patch("sys.stderr"):
            rc = launch_run.main(["--resample", "2026-09-11-c99", "--home", str(self.home)])
        self.assertEqual(rc, 2)
        chain.assert_not_called()


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="recon_sync_"))
        origin, seed = self.tmp / "origin.git", self.tmp / "seed"
        g(self.tmp, "init", "-q", "--bare", str(origin))
        g(self.tmp, "init", "-q", str(seed))
        (seed / "a.txt").write_text("1")
        g(seed, "add", "a.txt")
        g(seed, "commit", "-q", "-m", "one")
        g(seed, "push", "-q", str(origin), "HEAD:v2")
        g(self.tmp, "clone", "-q", "-b", "v2", str(origin), "checkout")
        self.seed, self.origin, self.co = seed, origin, self.tmp / "checkout"
        (self.seed / "a.txt").write_text("2")   # origin moves on after the droplet's last pull
        g(seed, "commit", "-q", "-am", "two")
        g(seed, "push", "-q", str(origin), "HEAD:v2")
        self.new = g(seed, "rev-parse", "HEAD")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def foreign(self):
        """Every repo foreign-owned, except the local bare origin (on the droplet it is GitHub; git also checks a
        local origin's owner in upload-pack, and clears GIT_CONFIG_* env config for it, so the origin is trusted
        in a scratch global config)."""
        cfg = self.tmp / "global.cfg"
        cfg.write_text(f"[safe]\n\tdirectory = {self.origin.resolve().as_posix()}\n", encoding="utf-8")
        return mock.patch.dict(os.environ, {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1", "GIT_CONFIG_GLOBAL": str(cfg)})

    def test_foreign_owned_checkout_is_pulled(self):
        with self.foreign():
            plain = subprocess.run(["git", "-C", str(self.co), "pull", "--ff-only"], capture_output=True, text=True)
            self.assertNotEqual(plain.returncode, 0)
            self.assertIn("dubious ownership", plain.stderr)
            ok, msg = launch_run.sync(self.co)
        self.assertTrue(ok, msg)
        self.assertEqual(g(self.co, "rev-parse", "HEAD"), self.new)

    def test_stale_checkout_is_not_launched(self):
        (self.co / "a.txt").write_text("local")   # a local commit: --ff-only fails, HEAD != origin/v2
        g(self.co, "commit", "-q", "-am", "local")
        ok, msg = launch_run.sync(self.co)
        self.assertFalse(ok)
        (self.co / "briefs").mkdir()
        with mock.patch.object(launch_run, "launch") as launch:
            rc = launch_run.main(["2026-09-11", "--home", str(self.co)])
        self.assertEqual(rc, 2)
        launch.assert_not_called()

    def test_ahead_of_origin_is_refused(self):
        # fast-forward works but leaves a local-only commit on top: HEAD != origin/v2
        g(self.co, "pull", "-q", "--ff-only", "origin", "v2")
        (self.co / "b.txt").write_text("x")
        g(self.co, "add", "b.txt")
        g(self.co, "commit", "-q", "-m", "local only")
        ok, msg = launch_run.sync(self.co)
        self.assertFalse(ok)
        self.assertIn("is not origin/v2", msg)

    def test_launch_after_sync(self):
        (self.co / "briefs" / "2026-09-11-c2").mkdir(parents=True)
        with mock.patch.object(launch_run, "launch", return_value=4242) as launch, mock.patch("builtins.print"):
            rc = launch_run.main(["2026-09-11", "--home", str(self.co), "--", "--budget", "18"])
        self.assertEqual(rc, 0)
        launch.assert_called_once_with(self.co, "2026-09-11", "2026-09-11-c3", ["--budget", "18"])
        self.assertEqual(g(self.co, "rev-parse", "HEAD"), self.new)

    def test_print_only_after_sync(self):
        (self.co / "briefs" / "2026-09-11-c13").mkdir(parents=True)
        (self.co / "briefs" / "2026-09-11-c2").mkdir()
        with self.foreign(), mock.patch("builtins.print") as pr:
            rc = launch_run.main(["2026-09-11", "--home", str(self.co), "--print-only"])
        self.assertEqual(rc, 0)
        pr.assert_called_with("2026-09-11-c14")


if __name__ == "__main__":
    unittest.main()
