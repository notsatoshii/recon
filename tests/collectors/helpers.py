"""Shared setup for the Phase E collector tests (docs/v2/phase-e-collectors-spec.md §5).

Every test runs offline: collector_common reads the recorded responses in
tests/fixtures/collectors/ (RECON_COLLECTOR_FIXTURES), "now" is frozen to the recording time
(RECON_COLLECTOR_NOW, from manifest.json), and output goes to a temporary data-sources/ folder.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
FIXTURES = REPO / "tests" / "fixtures" / "collectors"
PACKAGE_FIXTURES = REPO / "tests" / "fixtures" / "package"
for p in (str(SCRIPTS), str(REPO / "recon")):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.pop("RECON_FRESH_HOURS", None)
import collector_common as cc  # noqa: E402
import collect_changelogs  # noqa: E402
import collect_kalshi  # noqa: E402
import collect_polymarket  # noqa: E402
import collect_zdnet_kr  # noqa: E402

MODULES = {"polymarket": collect_polymarket, "kalshi": collect_kalshi,
           "changelogs": collect_changelogs, "zdnet_kr": collect_zdnet_kr}
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
NOW = cc.to_utc(MANIFEST["now"])
ENV_KEYS = ("RECON_COLLECTOR_FIXTURES", "RECON_COLLECTOR_NOW", "RECON_COLLECTOR_RECORD")


def url_like(*parts: str) -> str:
    """The one recorded URL containing every part (fails loudly when there is not exactly one)."""
    hits = [v["url"] for v in MANIFEST["files"].values() if all(p in v["url"] for p in parts)]
    if len(hits) != 1:
        raise AssertionError(f"{len(hits)} recorded URLs match {parts}")
    return hits[0]


def fixture_body(url: str) -> bytes:
    return (FIXTURES / f"{cc.fixture_name(url)}.body").read_bytes()


class FakeResp:
    """What urllib.request.urlopen returns, enough for collector_common.http_get."""

    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CollectorCase(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ENV_KEYS}
        self.tmp = Path(tempfile.mkdtemp(prefix="recon-test-"))
        self._data_dir = cc.DATA_DIR
        cc.DATA_DIR = self.tmp / "data-sources"
        cc.DATA_DIR.mkdir()
        os.environ.pop("RECON_COLLECTOR_RECORD", None)
        os.environ["RECON_COLLECTOR_FIXTURES"] = str(FIXTURES)
        os.environ["RECON_COLLECTOR_NOW"] = MANIFEST["now"]

    def tearDown(self):
        cc.DATA_DIR = self._data_dir
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # fixtures ---------------------------------------------------------------
    def overlay(self, bodies: dict[str, bytes | str | object] | None = None,
                statuses: dict[str, int] | None = None, empty: bool = False) -> Path:
        """A copy of the recorded fixtures (or an empty folder) with some URLs replaced."""
        d = self.tmp / f"fx{len(list(self.tmp.glob('fx*')))}"
        if empty:
            d.mkdir()
        else:
            shutil.copytree(FIXTURES, d)
        for url, body in (bodies or {}).items():
            if not isinstance(body, (bytes, str)):
                body = json.dumps(body)
            (d / f"{cc.fixture_name(url)}.body").write_bytes(body.encode() if isinstance(body, str) else body)
            st = d / f"{cc.fixture_name(url)}.status"
            if st.exists():
                st.unlink()
        for url, code in (statuses or {}).items():
            stem = cc.fixture_name(url)
            if not (d / f"{stem}.body").exists():
                (d / f"{stem}.body").write_bytes(b"")
            (d / f"{stem}.status").write_text(str(code))
        os.environ["RECON_COLLECTOR_FIXTURES"] = str(d)
        return d

    # running ----------------------------------------------------------------
    def run_collector(self, name: str) -> tuple[int, str, dict, str]:
        """(exit code, latest.md text or "", status.json, printed status line)."""
        mod = MODULES[name]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cc.run_main(mod.NAME, mod.LABEL, mod.collect)
        d = cc.DATA_DIR / mod.NAME
        latest = d / "latest.md"
        text = latest.read_text(encoding="utf-8") if latest.exists() else ""
        status = json.loads((d / "status.json").read_text(encoding="utf-8"))
        return code, text, status, out.getvalue()

    def latest_bytes(self, name: str) -> bytes:
        return (cc.DATA_DIR / MODULES[name].NAME / "latest.md").read_bytes()


def item_lines(text: str) -> list[str]:
    return [l for l in text.splitlines() if l.startswith("- ")]


def section(text: str, heading_prefix: str) -> list[str]:
    """The '- ' lines under the first '## ' heading starting with heading_prefix."""
    out, on = [], False
    for l in text.splitlines():
        if l.startswith("## "):
            on = l.startswith(heading_prefix)
            continue
        if on and l.startswith("- "):
            out.append(l)
    return out
