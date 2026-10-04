"""Prompt templates for the Phase C debate (docs/v2/phase-c-spec.md §13).

Templates live in config/prompts/debate/<name>.md. Each starts with a <!-- ... --> header naming its
schema and inputs; placeholders are {{key}} (templates contain JSON-like braces, so str.format is not
used). render() raises on a placeholder without a value and on a value the template does not use, so
code and templates cannot drift silently.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

RECON_HOME = Path(os.environ.get("RECON_HOME") or Path(__file__).resolve().parent.parent)
PROMPT_DIR = RECON_HOME / "config" / "prompts" / "debate"
_PH = re.compile(r"\{\{(\w+)\}\}")


def template(name: str) -> str:
    text = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    return re.sub(r"\A\s*<!--.*?-->\s*\n", "", text, count=1, flags=re.S)


def placeholders(name: str) -> set[str]:
    return set(_PH.findall(template(name)))


def render(name: str, **vars) -> str:
    """Load config/prompts/debate/<name>.md, drop the leading <!-- ... --> header, replace {{key}}
    placeholders. KeyError on a placeholder without a value, ValueError on an unused value."""
    body = template(name)
    used = set(_PH.findall(body))
    missing = used - set(vars)
    if missing:
        raise KeyError(f"template {name}: no value for {sorted(missing)}")
    extra = set(vars) - used
    if extra:
        raise ValueError(f"template {name}: unused values {sorted(extra)}")
    return _PH.sub(lambda m: str(vars[m.group(1)]), body)
