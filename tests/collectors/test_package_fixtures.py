"""The committed package days (spec §5): trimmed copies of briefs/2026-09-11 and 2026-10-04 that
Phase C §17.1/§17.4 and the §4.5b LENS_RAW test read through --package-from."""
from __future__ import annotations

import contextlib
import io
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import sys

from helpers import PACKAGE_FIXTURES, REPO

import build_agent_package as bap

sys.path.insert(0, str(REPO / "tests"))
import lens_extras_probe as probe  # noqa: E402

DAYS = ("2026-09-11", "2026-10-04")
FILES = ("00_data_package.md", "00_raw_data.md", "00_scorecard.md", "01_filtered.md")


class PackageFixtureTests(unittest.TestCase):
    def test_files_present_and_small(self):
        for day in DAYS:
            for f in FILES:
                p = PACKAGE_FIXTURES / day / f
                with self.subTest(p=str(p)):
                    self.assertTrue(p.exists())
                    self.assertLessEqual(p.stat().st_size, 200 * 1024)

    def test_raw_and_package_headings(self):
        """The headings §4.5b relies on: raw file per collector, package-only blocks in the package."""
        for day in DAYS:
            raw = (PACKAGE_FIXTURES / day / "00_raw_data.md").read_text(encoding="utf-8")
            pkg = (PACKAGE_FIXTURES / day / "00_data_package.md").read_text(encoding="utf-8")
            with self.subTest(day=day):
                for h in ("# Reddit Intelligence", "# News Intelligence", "## STABLECOIN SUPPLY"):
                    self.assertRegex(raw, rf"(?m)^{re.escape(h)}")
                self.assertRegex(pkg, r"(?m)^# SECTION \d+: ")
                self.assertRegex(pkg, r"(?m)^# World Monitor Intelligence")
                self.assertNotRegex(raw, r"(?m)^# World Monitor Intelligence")

    def test_views_are_replay_views(self):
        """01_filtered.md is the capped view build_agent_package.py builds from the full package
        (what a --replay sees), not the old uncapped v1 view (09-11 was 169 KB, every raw line)."""
        for day in DAYS:
            view = (PACKAGE_FIXTURES / day / "01_filtered.md").read_text(encoding="utf-8")
            with self.subTest(day=day):
                self.assertIn("AGENT VIEW", view)
                self.assertLessEqual(len(view.encode("utf-8")), 80 * 1024)

    def test_lens_extras_reach_every_agent(self):
        """§4.5b / Phase C §3 item 3 on the fixtures: every agent > 0 and <= 6,000 B, nothing the
        view carries, no line given to two agents."""
        for day in DAYS:
            run = PACKAGE_FIXTURES / day
            res = probe.measure(run)
            view = probe.View(res["view"])
            seen: dict[str, str] = {}
            for agent, v in res["agents"].items():
                with self.subTest(day=day, agent=agent):
                    self.assertGreater(v["bytes"], 0)
                    self.assertLessEqual(v["bytes"], probe.AGENT_CAP)
                    for line in v["body"]:
                        self.assertFalse(view.has(line), line[:80])
                        self.assertNotIn(line, seen, f"{line[:60]} also given to {seen.get(line)}")
                        seen[line] = agent
            self.assertTrue(res["agents"]["macro_strategist"]["text"].startswith("# World Monitor Intelligence"))

    def test_agent_view_builds_from_fixture(self):
        for day in DAYS:
            with self.subTest(day=day):
                run = Path(tempfile.mkdtemp(prefix="recon-pkg-"))
                try:
                    for f in FILES:
                        shutil.copy(PACKAGE_FIXTURES / day / f, run / f)
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(bap.build(run), 0)
                    view = (run / "01_filtered.md").read_text(encoding="utf-8")
                    self.assertIn("AGENT VIEW", view)
                finally:
                    shutil.rmtree(run, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
