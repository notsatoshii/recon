"""launch_run.py -- start a tagged replay on the droplet only on current code, under the next free run id
(docs/v2/phase-c-spec.md §15.2a).

    python3 scripts/launch_run.py 2026-09-11 [--prefix c] [--print-only] [--check] [-- <orchestrator args>]

1. Sync: `git -c safe.directory=<repo> fetch` + `pull --ff-only` of <remote>/<branch> (default origin/v2). The
   droplet checkout is owned by recon and pulled as root; without the -c a plain pull dies with 'dubious
   ownership' and the run went ahead on stale code (2026-09-11-c3 on 8b08a95, Phase C triage).
2. Refuse to launch (exit 2) unless HEAD equals <remote>/<branch> after the sync.
3. Run id: <day>-<prefix><N+1>, N the highest number among briefs/<day>-<prefix><N>* folders and
   logs/<day>-<prefix><N>* files (suffixes like c8t1, c1-r0, c1s count as their N), never the first gap: a gap is
   usually a killed and deleted run, and reusing it mixes two runs under one id in the reports.
4. Launch `recon/orchestrator.py --replay briefs/<day> --as-of <day> --run-id <id> --no-telegram <args>`
   detached (new session); prints `<run id> <pid> <short HEAD>`.

--check stops after step 2 (phase_c_validate.sh uses it); --print-only after step 3 (prints the id).

    python3 scripts/launch_run.py --resample 2026-09-11-c15 [--n 2]

--resample <run id>: after steps 1-2, the take-only resamples of a run's triage (§15.2 c1t1/c1t2, §15.5 g): <n>
copies <run>t<K> (K after the highest existing t<K>; run.json and phases/started.txt removed, as copy_run in
phase_c_validate.sh), run one after the other in one detached chain with RECON_STOP_AFTER=takes and
`--from-phase takes` (~9 calls each). A run that staged no pair is NOT COUNTED by replay_report.py until these
say whether its day splits (09-11 c15: consensus, q3 one under GAP_MIN where c14 drew 34)."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """git in repo, trusting it explicitly: safe.directory given on the command line is protected config, so it
    holds for root on a checkout another user owns."""
    return subprocess.run(["git", "-c", f"safe.directory={Path(repo).resolve().as_posix()}", "-C", str(repo), *args],
                          capture_output=True, text=True)


def sync(repo: Path, remote: str = "origin", branch: str = "v2") -> tuple[bool, str]:
    """Fetch and fast-forward; (ok, message). ok only when HEAD == <remote>/<branch> afterwards."""
    for args in (("fetch", remote, branch), ("pull", "--ff-only", remote, branch)):
        r = git(repo, *args)
        if r.returncode != 0:
            return False, f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}"
    head = git(repo, "rev-parse", "HEAD")
    want = git(repo, "rev-parse", f"{remote}/{branch}")
    if head.returncode != 0 or want.returncode != 0:
        return False, f"rev-parse failed: {(head.stderr or want.stderr).strip()}"
    h, w = head.stdout.strip(), want.stdout.strip()
    if h != w:
        return False, f"HEAD {h[:7]} is not {remote}/{branch} {w[:7]}"
    return True, h[:7]


def next_run_id(home: Path, day: str, prefix: str = "c") -> str:
    """<day>-<prefix><max N + 1> over run folders and logs; <prefix>1 when there are none."""
    rx = re.compile(rf"^{re.escape(day)}-{re.escape(prefix)}(\d+)(?!\d)")
    names = [p.name for p in (home / "briefs").glob(f"{day}-{prefix}*") if p.is_dir()]
    names += [p.name for p in (home / "logs").glob(f"{day}-{prefix}*")]   # <id>.log, <id>.launch.out
    nums = [int(m.group(1)) for n in names if (m := rx.match(n))]
    return f"{day}-{prefix}{max(nums, default=0) + 1}"


def resample_ids(home: Path, run_id: str, n: int) -> list[str]:
    """<run_id>t<K+1> .. t<K+n>, K the highest t<K> among existing <run_id>t<K> folders and logs."""
    rx = re.compile(rf"^{re.escape(run_id)}t(\d+)(?!\d)")
    names = [p.name for p in (home / "briefs").glob(f"{run_id}t*") if p.is_dir()]
    names += [p.name for p in (home / "logs").glob(f"{run_id}t*")]
    k = max((int(m.group(1)) for x in names if (m := rx.match(x))), default=0)
    return [f"{run_id}t{k + i}" for i in range(1, n + 1)]


def copy_run(home: Path, src: str, dst: str) -> None:
    """A rerun on src's triage (--from-phase takes): the folder copied, run.json and phases/started.txt removed."""
    b = home / "briefs"
    shutil.rmtree(b / dst, ignore_errors=True)
    shutil.copytree(b / src, b / dst, symlinks=True)
    for f in (b / dst / "run.json", b / dst / "phases" / "started.txt"):
        f.unlink(missing_ok=True)


def resample(home: Path, run_id: str, n: int) -> tuple[list[str], int]:
    """Stage n take-only copies of run_id and launch them as one sequential chain; (run ids, pid)."""
    day = run_id[:10]
    ids = resample_ids(home, run_id, n)
    for rid in ids:
        copy_run(home, run_id, rid)
    cmds = [orchestrator_cmd(day, rid, ["--from-phase", "takes"]) for rid in ids]
    return ids, launch_chain(home, cmds, {"RECON_STOP_AFTER": "takes"})


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[:argv.index("--")] if "--" in argv else argv
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("day", nargs="?", help="YYYY-MM-DD: the briefs/<day> package to replay")
    ap.add_argument("--prefix", default="c")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", default="v2")
    ap.add_argument("--home", default=os.environ.get("RECON_HOME") or str(REPO))
    ap.add_argument("--check", action="store_true", help="sync and verify HEAD only")
    ap.add_argument("--print-only", action="store_true", help="sync, verify, print the next run id")
    ap.add_argument("--resample", metavar="RUN_ID", help="take-only resamples of this run's triage (t1, t2, ...)")
    ap.add_argument("--n", type=int, default=2, help="number of --resample copies (default 2)")
    a = ap.parse_args(argv)
    home = Path(a.home)
    if a.resample and not re.fullmatch(r"\d{4}-\d{2}-\d{2}-[\w-]+", a.resample):
        ap.error("--resample needs a run id <YYYY-MM-DD>-<tag>")
    ok, msg = sync(home, a.remote, a.branch)
    if not ok:
        print(f"NOT LAUNCHED: {msg}", file=sys.stderr)
        return 2
    if a.check:
        print(f"HEAD {msg} == {a.remote}/{a.branch}")
        return 0
    if a.resample:
        if not (home / "briefs" / a.resample / "phases" / "triage.json").is_file():
            print(f"NOT LAUNCHED: briefs/{a.resample}/phases/triage.json is missing", file=sys.stderr)
            return 2
        ids, pid = resample(home, a.resample, max(1, a.n))
        print(f"{' '.join(ids)} {pid} {msg}")
        return 0
    if not a.day or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.day):
        ap.error("day YYYY-MM-DD is required")
    rid = next_run_id(home, a.day, a.prefix)
    if a.print_only:
        print(rid)
        return 0
    print(f"{rid} {launch(home, a.day, rid, extra)} {msg}")
    return 0


def orchestrator_cmd(day: str, rid: str, extra: list[str]) -> list[str]:
    return [sys.executable, "recon/orchestrator.py", "--replay", f"briefs/{day}", "--as-of", day,
            "--run-id", rid, "--no-telegram", *extra]


def launch(home: Path, day: str, rid: str, extra: list[str]) -> int:
    """The replay, detached in its own session (survives the ssh logout); stdout to logs/<id>.launch.out. Pid."""
    (home / "logs").mkdir(exist_ok=True)
    with open(home / "logs" / f"{rid}.launch.out", "ab") as out:
        return subprocess.Popen(orchestrator_cmd(day, rid, extra), cwd=home, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True).pid


# Runs each command in turn (one finishing before the next starts, as phase_c_validate.sh does), env added.
_CHAIN = ("import json, os, subprocess, sys\n"
          "cmds, env = json.loads(sys.argv[1]), dict(os.environ, **json.loads(sys.argv[2]))\n"
          "for c in cmds:\n"
          "    print('chain:', ' '.join(c), 'exit', subprocess.run(c, env=env).returncode, flush=True)\n")


def launch_chain(home: Path, cmds: list[list[str]], env: dict[str, str]) -> int:
    """cmds one after the other in one detached session; stdout to logs/<first id>.launch.out. Pid."""
    (home / "logs").mkdir(exist_ok=True)
    first = cmds[0][cmds[0].index("--run-id") + 1]
    with open(home / "logs" / f"{first}.launch.out", "ab") as out:
        return subprocess.Popen([sys.executable, "-c", _CHAIN, json.dumps(cmds), json.dumps(env)], cwd=home,
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True).pid


if __name__ == "__main__":
    sys.exit(main())
