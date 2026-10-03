"""Changelogs (spec §3.3)."""
from __future__ import annotations

import unittest
from datetime import timedelta

from helpers import NOW, CollectorCase, cc, item_lines, url_like
import collect_changelogs as cl


class RuleTests(unittest.TestCase):
    def test_prerelease_fallback_regex(self):
        for name in ("0.162.0-alpha.11", "rust-v0.162.0-alpha.11", "v0.9.0-nightly.20261003", "2026-07-28 RC",
                     "1.0.92-3", "v1.0.0-beta.2", "v3.0.0-rc1"):
            self.assertTrue(cl.is_prerelease_name(name), name)
        for name in ("v2.1.288", "rust-v0.160.0", "resources", "search", "v0.11.0", "Devstral support"):
            self.assertFalse(cl.is_prerelease_name(name), name)

    def test_include_exclude(self):
        cline = {"exclude": "^(sdk/|SDK |Desktop )"}
        entries = [{"version": v, "title": v} for v in ("sdk/v1.2.0", "SDK 1.0", "Desktop 0.3", "v3.30.0")]
        self.assertEqual([e["version"] for e in cl.apply_filters(cline, entries)], ["v3.30.0"])
        gh = {"include": "Copilot"}
        entries = [{"version": t, "title": t, "categories": c} for t, c in (
            ("Copilot code review is now in public preview", []),
            ("Actions runner update", []),
            ("New model picker", ["Copilot"]))]
        self.assertEqual(len(cl.apply_filters(gh, entries)), 2)

    def test_release_notes(self):
        body = ("## What's Changed\n* Add `--json` flag by @dev in https://github.com/o/r/pull/1\n"
                "* Fix **crash** on start in #2\n- [Docs](https://x) update\n- Fourth item\n"
                "## New Contributors\n* @new made their first contribution in #3\n"
                "**Full Changelog**: https://github.com/o/r/compare/a...b")
        self.assertEqual(cl.md_notes(body), "Add --json flag; Fix crash on start; Docs update")
        self.assertLessEqual(len(cl.md_notes("- " + "x" * 400)), 280)
        self.assertEqual(cl.md_notes("<p>First sentence. Second one! Third? Fourth.</p>"),
                         "First sentence.; Second one!; Third?")


class ReplayTests(CollectorCase):
    def test_stable_found_behind_alphas_and_counted(self):
        url = url_like("api.github.com/repos/openai/codex/")
        t = lambda h: cc.iso_z(NOW - timedelta(hours=h))  # noqa: E731
        rel = [{"id": i, "tag_name": f"rust-v0.162.0-alpha.{i}", "name": f"0.162.0-alpha.{i}", "draft": False,
                "prerelease": True, "published_at": t(i), "html_url": f"https://github.com/openai/codex/a{i}",
                "body": "- alpha"} for i in range(1, 11)]
        rel += [{"id": 50, "tag_name": "rust-v0.161.0", "name": "0.161.0", "draft": False, "prerelease": False,
                 "published_at": t(30), "html_url": "https://github.com/openai/codex/s1", "body": "- Stable fix"},
                {"id": 51, "tag_name": "rust-v0.160.0", "name": "0.160.0", "draft": False, "prerelease": False,
                 "published_at": t(60), "html_url": "https://github.com/openai/codex/s0", "body": "- Older"},
                {"id": 52, "tag_name": "rust-v0.159.0", "name": "", "draft": False, "prerelease": False,
                 "published_at": t(80), "html_url": "https://github.com/openai/codex/old", "body": "- Too old"},
                {"id": 53, "tag_name": "rust-v0.163.0", "name": "", "draft": True, "prerelease": False,
                 "published_at": t(1), "html_url": "https://github.com/openai/codex/draft", "body": "- Draft"}]
        self.overlay(bodies={url: rel})
        code, text, _, _ = self.run_collector("changelogs")
        self.assertEqual(code, 0)
        [line] = [l for l in item_lines(text) if "Codex CLI" in l]
        self.assertIn("Codex CLI rust-v0.161.0 (new) (+1 earlier release in 72 h): Stable fix", line)
        self.assertTrue(line.endswith("| https://github.com/openai/codex/s1"))
        self.assertNotIn("alpha", line)

    def test_new_only_on_first_appearance(self):
        _, first, _, _ = self.run_collector("changelogs")
        _, second, _, _ = self.run_collector("changelogs")
        self.assertIn("(new)", first)
        self.assertNotIn("(new)", second)
        self.assertEqual(first.replace(" (new)", ""), second)

    def test_quiet_line(self):
        _, text, status, _ = self.run_collector("changelogs")
        quiet = [l for l in item_lines(text) if l.startswith("- No release in the last 72 h:")]
        self.assertEqual(len(quiet), 1)
        self.assertIn("quiet", status["notes"][-1])

    def test_rate_limited_source_does_not_stop_the_others(self):
        self.overlay(statuses={url_like("api.github.com/repos/ollama/ollama/"): 403})
        code, text, _, _ = self.run_collector("changelogs")
        self.assertEqual(code, 0)
        self.assertIn("- Not reachable this run: Ollama (HTTP 403).", text)


if __name__ == "__main__":
    unittest.main()
