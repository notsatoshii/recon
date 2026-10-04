#!/usr/bin/env python3
"""Print the per-lens diet sizes on the committed package fixtures (no LLM, no network).

Usage: tests/diet_probe.py [--dump AGENT] [--day YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_agent_package as bap  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "package"


def diets_for(day: str) -> tuple[dict, Path]:
    tmp = Path(tempfile.mkdtemp())
    for f in (FIX / day).glob("00_*"):
        shutil.copy(f, tmp)
    pkg = (tmp / "00_data_package.md").read_text(encoding="utf-8")
    _, secs = bap.split_sections(pkg)
    return bap.build_diets(tmp, secs, bap.package_date(pkg)), tmp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump")
    ap.add_argument("--day")
    a = ap.parse_args()
    days = [a.day] if a.day else sorted(p.name for p in FIX.iterdir() if p.is_dir())
    for day in days:
        rep, tmp = diets_for(day)
        print(day)
        for agent, r in rep.items():
            print(f"  {agent:17} {r['bytes']:6} B  {', '.join(r['sections'])}")
        if a.dump:
            print((tmp / "01_diets" / f"{a.dump}.md").read_text(encoding="utf-8"))
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
