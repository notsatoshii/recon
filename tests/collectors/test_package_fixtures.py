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

from helpers import PACKAGE_FIXTURES

import build_agent_package as bap

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
