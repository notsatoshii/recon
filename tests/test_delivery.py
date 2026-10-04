"""Telegram delivery (Phase C review 2026-10-04): the brief was cut into chunks only after a chunk passed 3800
characters, so one 300-900 character paragraph line (normal in WHAT IT MEANS / WHERE THE VIEWS SPLIT) pushed a
chunk past Telegram's 4096 limit; it failed in HTML and plain mode and was dropped, and run_status ignored
delivery, so run.json said 'ok' and cron_run.sh neither resumed nor alerted."""
from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recon.orchestrator import Run, run_status, tg_chunks  # noqa: E402

LIMIT = 4096


def brief(bullets: int = 40, para: int = 900) -> str:
    # 40 bullets (~3500 characters) and then a paragraph line in the same section: no header cut in between
    lines = ["# RECON DAILY BRIEF -- 2026-10-04", "", "## WHAT IT MEANS"]
    lines += [f"- Bullet {i}: rates and flows moved on the day, with a short note on why it mattered."
              for i in range(bullets)]
    lines += ["", ("The market read this as a slower path for cuts, and the long end " * 40)[:para]]
    return "\n".join(lines)


class TelegramDeliveryTests(unittest.TestCase):
    def send(self, text: str) -> tuple[bool, list[str]]:
        got: list[str] = []

        def urlopen(req, timeout=10):
            body = json.loads(req.data.decode())
            if len(body["text"]) > LIMIT:
                raise urllib.error.HTTPError(req.full_url, 400, "message is too long", {}, None)
            got.append(body["text"])

        r = Run.__new__(Run)
        r.log = lambda *a, **k: None
        env = {"RECON_TELEGRAM_TOKEN": "t", "RECON_TELEGRAM_CHAT_ID": "c"}
        with mock.patch.dict(os.environ, env), mock.patch("urllib.request.urlopen", urlopen), \
                mock.patch("time.sleep", lambda s: None):
            ok = r.telegram(text)
        return ok, got

    def test_long_paragraph_does_not_push_a_chunk_past_the_limit(self):
        text = brief()
        ok, got = self.send(text)
        self.assertTrue(ok)
        self.assertGreaterEqual(len(got), 2)
        sent = "\n".join(got)
        self.assertIn("Bullet 39", sent)
        self.assertIn("slower path for cuts", sent)
        self.assertTrue(all(len(c) <= LIMIT for c in got), [len(c) for c in got])

    def test_single_line_over_4000_is_hard_split(self):
        line = "word " * 1500   # 7500 characters, one line
        chunks = tg_chunks("intro\n" + line.strip())
        self.assertTrue(all(len(c) <= 3800 for c in chunks), [len(c) for c in chunks])
        self.assertEqual("".join(chunks).replace(" ", "").replace("\n", ""), ("intro" + "word" * 1500))
        nospace = "x" * 9000
        chunks = tg_chunks(nospace)
        self.assertEqual("".join(chunks), nospace)
        self.assertTrue(all(len(c) <= 3800 for c in chunks))

    def test_short_brief_is_one_chunk(self):
        self.assertEqual(tg_chunks("a\nb\n\nc"), ["a\nb\n\nc"])

    def test_failed_send_makes_run_partial(self):
        checks = {"sections_ok": True}
        self.assertEqual(run_status("brief", checks, {"telegram": False, "telegram_on": True}), "partial")
        self.assertEqual(run_status("brief", checks, {"telegram": True, "telegram_on": True}), "ok")
        self.assertEqual(run_status("brief", checks, {"telegram": False, "telegram_on": False}), "ok")  # --no-telegram
        self.assertEqual(run_status("brief", checks), "ok")
        self.assertEqual(run_status("", checks, {"telegram": False, "telegram_on": True}), "failed")


if __name__ == "__main__":
    unittest.main()
