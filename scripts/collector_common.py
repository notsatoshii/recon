#!/usr/bin/env python3
"""
Shared helpers for the Phase E keyless collectors (docs/v2/phase-e-collectors-spec.md).

Standard library only. Used by scripts/collect_polymarket.py, collect_kalshi.py,
collect_changelogs.py and collect_zdnet_kr.py, which import it from their own folder.

    python3 scripts/collector_common.py --fresh data-sources/kalshi/latest.md
        prints "ok|stale <age_h> <stamp>" and exits 0 when fresh, 1 otherwise.

Environment:
    RECON_HOME                 repo root (default: the parent of this file's folder)
    RECON_FRESH_HOURS          freshness window in hours (default 72)
    RECON_COLLECTOR_FIXTURES   folder of recorded responses; when set, no network is used
"""
from __future__ import annotations

import gzip
import hashlib
import html
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock

RECON_HOME = Path(os.environ.get("RECON_HOME") or Path(__file__).resolve().parent.parent)
DATA_DIR = RECON_HOME / "data-sources"
CONFIG_DIR = RECON_HOME / "config"
FRESH_HOURS = float(os.environ.get("RECON_FRESH_HOURS", "72"))
UA = "RECON/2.0 (+https://github.com/notsatoshii/recon)"
KST = timezone(timedelta(hours=9))
TIMEOUT = 20


# ---------------------------------------------------------------------------
# Result, budget
# ---------------------------------------------------------------------------

@dataclass
class SourceResult:
    name: str
    ok: bool = False
    items: int = 0
    fetched_at: str = ""
    text: str = ""
    error: str | None = None
    requests: int = 0
    seconds: float = 0.0
    bytes_in: int = 0
    stale_items_dropped: int = 0
    notes: list[str] = field(default_factory=list)


class BudgetExceeded(Exception):
    pass


class HTTPFailure(Exception):
    def __init__(self, url: str, status: int | None, msg: str, headers: dict | None = None):
        super().__init__(f"{msg} ({url})")
        self.url = url
        self.status = status
        self.msg = msg
        self.headers = headers or {}


class Budget:
    """Request, time and byte budget shared by all threads of one collector."""

    def __init__(self, seconds: float, requests: int, mbytes: float):
        self.max_seconds = seconds
        self.max_requests = requests
        self.max_bytes = int(mbytes * 1024 * 1024)
        self.start = time.monotonic()
        self.requests = 0
        self.bytes_in = 0
        self.exhausted = False
        self._lock = Lock()

    def elapsed(self) -> float:
        return time.monotonic() - self.start

    def take(self) -> None:
        with self._lock:
            if (self.requests >= self.max_requests or self.elapsed() >= self.max_seconds
                    or self.bytes_in >= self.max_bytes):
                self.exhausted = True
                raise BudgetExceeded(f"budget reached after {self.requests} requests")
            self.requests += 1

    def add_bytes(self, n: int) -> None:
        with self._lock:
            self.bytes_in += n

    def remaining_seconds(self) -> float:
        return max(0.0, self.max_seconds - self.elapsed())


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def build_url(base: str, params: dict | None = None) -> str:
    if not params:
        return base
    return base + ("&" if "?" in base else "?") + urllib.parse.urlencode(params)


def _fixture_path(url: str) -> Path | None:
    root = os.environ.get("RECON_COLLECTOR_FIXTURES")
    if not root:
        return None
    return Path(root) / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".body")


def http_get(url: str, budget: Budget, accept: str = "*/*", retries: int = 2) -> tuple[bytes, dict]:
    """GET with retries on timeouts, 5xx and 429. Returns (body, headers). Raises HTTPFailure."""
    fx = _fixture_path(url)
    if fx is not None:
        budget.take()
        meta = fx.with_suffix(".status")
        status = int(meta.read_text().strip()) if meta.exists() else 200
        if not fx.exists():
            raise HTTPFailure(url, 404, "no fixture")
        body = fx.read_bytes()
        budget.add_bytes(len(body))
        if status >= 400:
            raise HTTPFailure(url, status, f"HTTP {status}")
        return body, {}

    waits = [2, 6]
    attempt = 0
    while True:
        budget.take()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept,
                                                   "Accept-Encoding": "gzip"})
        try:
            timeout = max(3.0, min(TIMEOUT, budget.remaining_seconds()))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    budget.add_bytes(len(body))
                    body = gzip.decompress(body)
                else:
                    budget.add_bytes(len(body))
                return body, {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as e:
            headers = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
            try:
                budget.add_bytes(len(e.read() or b""))
            except Exception:
                pass
            if e.code == 451:
                raise HTTPFailure(url, 451, "geo-blocked (HTTP 451)", headers)
            retryable = e.code == 429 or e.code >= 500
            if not retryable or attempt >= retries:
                raise HTTPFailure(url, e.code, f"HTTP {e.code}", headers)
            wait = waits[min(attempt, len(waits) - 1)]
            ra = headers.get("retry-after")
            if ra and ra.strip().isdigit():
                wait = min(30, int(ra.strip()))
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            if attempt >= retries:
                raise HTTPFailure(url, None, f"network error: {getattr(e, 'reason', e)}")
            wait = waits[min(attempt, len(waits) - 1)]
        attempt += 1
        if wait >= budget.remaining_seconds():
            raise HTTPFailure(url, None, "no time left to retry")
        time.sleep(wait)


def http_json(url: str, budget: Budget):
    body, _ = http_get(url, budget, accept="application/json")
    return json.loads(body.decode("utf-8"))


def http_text(url: str, budget: Budget, accept: str = "application/rss+xml, application/xml, text/xml, */*") -> str:
    body, _ = http_get(url, budget, accept=accept)
    return body.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def now_utc() -> datetime:
    override = os.environ.get("RECON_COLLECTOR_NOW")  # tests freeze time with an ISO stamp
    if override:
        return to_utc(override) or datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


def to_utc(value) -> datetime | None:
    """RFC 822, ISO 8601 (Z or offset), or naive 'YYYY-MM-DD HH:MM[:SS]' (read as KST)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=KST)).astimezone(timezone.utc)
    v = str(value).strip()
    if not v:
        return None
    if re.match(r"^[A-Za-z]{3},", v) or re.search(r"\d{1,2} [A-Za-z]{3} \d{4}", v):
        try:
            d = parsedate_to_datetime(v)
            if d.tzinfo is None:
                d = d.replace(tzinfo=KST)
            return d.astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
    iso = v.replace("Z", "+00:00")
    if re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d(:\d\d)?$", iso):
        try:
            return datetime.fromisoformat(iso).replace(tzinfo=KST).astimezone(timezone.utc)
        except ValueError:
            return None
    # Trim fractional seconds beyond 6 digits, which fromisoformat rejects on old Pythons.
    iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def fmt_utc(d: datetime | None, with_time: bool = True) -> str:
    if d is None:
        return "n/a"
    d = d.astimezone(timezone.utc)
    return d.strftime("%Y-%m-%d %H:%M UTC") if with_time else d.strftime("%Y-%m-%d")


def iso_z(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------

_ATOM = "{http://www.w3.org/2005/Atom}"


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def parse_rss(text: str) -> list[dict]:
    """RSS 2.0 (and Atom, detected) into [{title, link, published, summary, categories, id}]."""
    root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    if _strip_ns(root.tag) == "feed":
        return parse_atom(text)
    items = []
    for it in root.iter():
        if _strip_ns(it.tag) != "item":
            continue
        d: dict = {"categories": []}
        for ch in it:
            t = _strip_ns(ch.tag)
            val = (ch.text or "").strip()
            if t == "title":
                d["title"] = val
            elif t == "link":
                d["link"] = val
            elif t in ("pubDate", "date", "published", "updated") and not d.get("published"):
                d["published"] = val
            elif t == "description" and not d.get("summary"):
                d["summary"] = val
            elif t == "encoded" and not d.get("summary"):
                d["summary"] = val
            elif t == "category" and val:
                d["categories"].append(val)
            elif t == "guid":
                d["id"] = val
        d.setdefault("id", d.get("link", ""))
        items.append(d)
    return items


def parse_atom(text: str) -> list[dict]:
    root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    items = []
    for e in root.findall(f"{_ATOM}entry"):
        link = ""
        for l in e.findall(f"{_ATOM}link"):
            if l.get("rel", "alternate") == "alternate":
                link = l.get("href", "")
        content = e.findtext(f"{_ATOM}content") or e.findtext(f"{_ATOM}summary") or ""
        items.append({
            "title": (e.findtext(f"{_ATOM}title") or "").strip(),
            "link": link,
            "published": e.findtext(f"{_ATOM}published") or e.findtext(f"{_ATOM}updated") or "",
            "summary": content,
            "categories": [c.get("term", "") for c in e.findall(f"{_ATOM}category")],
            "id": e.findtext(f"{_ATOM}id") or link,
        })
    return items


def strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "\n- ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def one_line(s: str, limit: int | None = None) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    if limit and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def source_dir(name: str) -> Path:
    d = DATA_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def header(title: str, stamp: datetime, extra: list[str] | None = None) -> list[str]:
    lines = [f"# {title} Intelligence", f"## {stamp.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"]
    for e in extra or []:
        lines.append(f"## {e}")
    return lines


def count_items(text: str) -> int:
    return sum(1 for l in text.splitlines() if l.startswith("- "))


def write_latest(name: str, text: str) -> Path:
    d = source_dir(name)
    path = d / "latest.md"
    tmp = d / "latest.md.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def write_status(res: SourceResult) -> None:
    d = source_dir(res.name)
    path = d / "status.json"
    last_good = None
    try:
        last_good = json.loads(path.read_text(encoding="utf-8")).get("last_good_at")
    except Exception:
        pass
    if res.ok:
        last_good = res.fetched_at
    data = {"name": res.name, "ok": res.ok, "items": res.items, "error": res.error,
            "fetched_at": res.fetched_at, "last_good_at": last_good, "requests": res.requests,
            "seconds": round(res.seconds, 1), "bytes_in": res.bytes_in,
            "stale_items_dropped": res.stale_items_dropped}
    if res.notes:
        data["notes"] = res.notes[:20]
    tmp = d / "status.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(tmp, path)


def fresh(path, now: datetime | None = None, hours: float | None = None):
    """(ok, age_h, stamp) for a latest.md: ok when its '## YYYY-MM-DD HH:MM UTC' header is recent."""
    now = now or datetime.now(timezone.utc)
    hours = FRESH_HOURS if hours is None else hours
    try:
        head = Path(path).read_text(encoding="utf-8")[:600]
    except OSError:
        return False, None, None
    m = re.search(r"^## (\d{4}-\d\d-\d\d \d\d:\d\d) UTC\s*$", head, re.M)
    if not m:
        return False, None, None
    stamp = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    age = (now - stamp).total_seconds() / 3600
    return age <= hours, round(age, 1), stamp


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_main(name: str, label: str, collect) -> int:
    """Run collect(budget-less wrapper) -> (text, SourceResult); write files; print one status line."""
    t0 = time.monotonic()
    stamp = now_utc()
    res = SourceResult(name=name, fetched_at=iso_z(stamp))
    try:
        collect(res, stamp)
    except Exception as e:  # never let a crash overwrite the good file
        res.ok = False
        res.error = res.error or f"{type(e).__name__}: {e}"
    res.seconds = time.monotonic() - t0
    if res.text:
        res.items = count_items(res.text)
    if res.ok and res.items > 0:
        write_latest(name, res.text)
    else:
        res.ok = False
        if not res.error:
            res.error = "no items"
        prev = DATA_DIR / name / "latest.md"
        prev_head = prev.read_text(encoding="utf-8", errors="ignore")[:400] if prev.exists() else ""
        if not prev_head or "SOURCE UNAVAILABLE" in prev_head:
            # No good pull on disk: leave a stub so readers see why (as collect_twitter.py does).
            # A good previous file is never replaced; the package's 72 h check ages it out.
            stub = "\n".join(header(label, stamp) + [
                "", "## SOURCE UNAVAILABLE", "",
                f"- {label}: SOURCE UNAVAILABLE — {res.error} [{fmt_utc(stamp)}]", ""])
            write_latest(name, stub)
    write_status(res)
    state = "ok" if res.ok else f"FAILED ({res.error})"
    print(f"  {label}: {state}, {res.items} items, {res.requests} requests, "
          f"{res.seconds:.1f} s, {res.bytes_in / 1e6:.1f} MB", flush=True)
    for n in res.notes[:12]:
        print(f"    note: {n}", flush=True)
    return 0 if res.ok else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--fresh":
        ok, age, stamp = fresh(sys.argv[2])
        print(f"{'ok' if ok else 'stale'} {age if age is not None else 'n/a'} "
              f"{stamp.strftime('%Y-%m-%d %H:%M UTC') if stamp else 'no-stamp'}")
        sys.exit(0 if ok else 1)
    print(__doc__)
    sys.exit(2)
