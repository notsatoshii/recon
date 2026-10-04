#!/usr/bin/env python3
"""Live schema smoke (docs/v2/phase-c-spec.md §17.4b): one FAST call per call schema in schemas.ALL (6 calls),
each with a tiny prompt and slim Codex, before any replay spends calls on a schema Codex might reject.

    RECON_CODEX_SLIM=1 python3 scripts/schema_smoke.py      # exit 0 when every reply parses
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.setdefault("RECON_CODEX_SLIM", "1")
from recon import llm, schemas  # noqa: E402

PROMPT = ("Fill every field of the JSON schema with a short placeholder. Question ids are q1. Dates are "
          "2026-10-11. Probabilities and weights are small whole numbers. Reply with one JSON object only.")


def main() -> int:
    fails = 0
    with tempfile.TemporaryDirectory(prefix="recon-smoke-") as tmp:
        paths = schemas.write_all(Path(tmp))
        for name in schemas.ALL:
            try:
                res = llm.ask_ex(PROMPT, tier="fast", schema_path=paths[name], agent="schema-smoke", note=f"smoke={name}")
                schemas.parse(res["text"], name)
                u = res.get("usage") or {}
                print(f"  {name}: ok ({u.get('input_tokens', 0)} in, {u.get('output_tokens', 0)} out, {res['seconds']} s)")
            except (llm.LLMError, schemas.SchemaError) as e:
                fails += 1
                print(f"  {name}: FAILED ({str(e)[:300]})")
    print(f"schema smoke: {len(schemas.ALL) - fails}/{len(schemas.ALL)} ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
