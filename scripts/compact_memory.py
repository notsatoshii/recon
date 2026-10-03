#!/usr/bin/env python3
"""One-time cleanup of config/agent_memory/*.md after the append bug (improvement plan F5).

Until 2026-10-04 each run appended a full rewritten memory under "### Last updated: <date>",
so a file held several copies of the same sections. Since then run_recon.sh replaces the
file with the latest copy. This script does the same to the existing files: it keeps the
two header lines and the newest "### Last updated" copy, and backs up each original to
<backup_dir>/<name>.md first. Files with a single copy are left alone.

Usage: compact_memory.py [--backup-dir DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
from pathlib import Path

RECON_HOME = Path(os.environ.get("RECON_HOME") or Path(__file__).resolve().parent.parent)


def compact(text: str) -> str | None:
    marks = [m.start() for m in re.finditer(r"^### Last updated: .*$", text, re.M)]
    copies = len(re.findall(r"^### Active Tracking", text, re.M))
    if len(marks) == 0 or copies <= 1:
        return None
    header = "\n".join(text.split("\n")[:2])
    latest = text[marks[-1]:].strip("\n")
    if "### Active Tracking" not in latest:
        return None
    return f"{header}\n\n{latest}\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup-dir", default=str(RECON_HOME / "archive" / "agent_memory-before-compact"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    mem_dir = RECON_HOME / "config" / "agent_memory"
    backup = Path(args.backup_dir)
    for f in sorted(mem_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        new = compact(text)
        if new is None:
            print(f"{f.name}: {len(text.splitlines())} lines, one copy, unchanged")
            continue
        print(f"{f.name}: {len(text.splitlines())} -> {len(new.splitlines())} lines")
        if args.dry_run:
            continue
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, backup / f.name)
        f.write_text(new, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
