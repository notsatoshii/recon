# RECON v2 — Phase E build spec, collector half: four keyless collectors

Date: 2026-10-04. Status: build spec. Source plan: `06-improvement-plan.md` §4 item 10 and §7
row E; `01-plan.md` §4 (desk sources); `sources-audit.md` (2026-09-10 actions);
`04-implementation-plan.md` §2.3 (`SourceResult`). The other half of Phase E (the Lever and
InnovLabs desks, the three new personas, Reddit/X follow-ups) is not in this file.

Every endpoint below was called on 2026-10-04 (from Eric's desktop in Korea, and from the droplet
for Polymarket) and the result is recorded next to it. Eric's instruction: decide, do not ask;
decisions are listed in §9.

**Revision 2026-10-04 (review fixes).** A review measured every call again from the droplet and
found dead Kalshi series picks, a 20 MB Polymarket page, an always-empty NEW MARKETS section,
Atom feeds that hide stable releases behind pre-releases, and Phase C `LENS_RAW` headings that do
not exist in the raw file. All fixes are folded into the sections below; §10 lists them.

**Revision 2026-10-04, third review (measured lens bytes).** The §4.5b table still gave trader,
builder and analyst 0 bytes on the real 10-04 package, and the committed 09-11 fixture view was the
old uncapped view. The table is re-picked from what the views leave out and measured per agent on
09-10, 09-11, 10-04 and 10-04 with the Phase E files; the fixture views are the views a replay
builds. §10.2 lists the changes.

**Revision 2026-10-04, second review (measured output).** The collectors' real output was 3 to 4
times the size estimates the §4 caps were set from, the §5 tests did not exist, and ZDNet's shared
2-request budget let one retry on the first feed stop the second. Polymarket BY TOPIC now keeps 2
events a topic and ZDNet descriptions 60 characters; sizes, caps and cost below are the measured
numbers of 2026-10-04 (§3, §4.4, §4.5a, §6); the tests and fixtures exist (§5); ZDNet has a budget
per feed (§3.4). §10.1 lists the changes.

---

## 0. Scope and rules

Four standalone collectors, each one file, each runnable alone:

| Name | Writes | For | Replaces |
|---|---|---|---|
| `polymarket` | `data-sources/polymarket/latest.md` | Lever desk, prediction-market questions | the 15-line `## POLYMARKET LIVE MARKETS` block inside the on-chain collector |
| `kalshi` | `data-sources/kalshi/latest.md` | Lever desk, macro questions | nothing (new) |
| `changelogs` | `data-sources/changelogs/latest.md` | AI NEWSLETTER, ai_engineer and builder lenses | nothing (new; the news collector has OpenAI News only) |
| `zdnet_kr` | `data-sources/zdnet_kr/latest.md` | KOREA section, Korea AI and 가상자산 | nothing (adds a fifth Korean feed next to AI타임스, 전자신문, 블록미디어, 토큰포스트) |

Rules for all four:

- **No API keys, no accounts, no login.** Public endpoints only.
- **Python standard library only** (`urllib.request`, `json`, `xml.etree.ElementTree`,
  `email.utils`, `concurrent.futures`). No `feedparser`, no Playwright, so they run in any venv
  and in tests without installs.
- **v1 file layout**, so `export.source_record`, `build_agent_package.py` and the RUBRIC page read
  them unchanged:
  ```
  # <Name> Intelligence
  ## YYYY-MM-DD HH:MM UTC          ← real UTC: datetime.now(timezone.utc)
  
  ## <SUBSECTION>
  
  - <one item per line>
  ```
  `export.header_time` reinterprets a "UTC" stamp later than the run's end as Seoul time (a fix for
  five v1 collectors before 2026-10-04). New collectors must never trigger it: the stamp is taken
  from `timezone.utc`, never `datetime.now()`.
- **One item, one line.** Every number, date and link that belongs to an item is on its `- ` line,
  so an agent's evidence quote (≤ 200 characters, copied from one line) verifies with
  `evidence.verify_quote`. Continuation lines are not used.
- **72-hour freshness** (§2).
- **Never overwrite a good file with a failure.** `latest.md` is replaced (write to `.tmp`, then
  `os.replace`) only when the collector produced at least one item. Every run writes
  `data-sources/<name>/status.json` (§1.3).

---

## 1. Common contract

### 1.1 Layout

```
scripts/
  collector_common.py     http_json, http_text, parse_rss, parse_atom, to_utc, write_latest,
                          write_status, fresh, SourceResult, Budget, run_main
  collect_polymarket.py   collect() -> SourceResult
  collect_kalshi.py       collect() -> SourceResult
  collect_changelogs.py   collect() -> SourceResult
  collect_zdnet_kr.py     collect() -> SourceResult
config/
  changelog_sources.json     the tracked repos and feeds (§3.3)
  kalshi_series.json         the watchlist series (§3.2)
```

The collectors sit next to the v1 collectors (`collect_twitter.py`, `collect_worldmonitor.py`) as
plain scripts, not a `recon/collectors/` package: `recon/` has no `__init__.py`, so
`python3 -m recon.collectors.x` only resolves when the working directory is the repo root, and a
script run by path imports `collector_common` from its own folder wherever it is started from.
Each script runs alone: `python3 scripts/collect_kalshi.py` (writes the file, prints the status
line, exit 0 when `ok`, 1 otherwise). Output paths never depend on the working directory:
`Path(os.environ.get("RECON_HOME", <repo root from __file__>)) / "data-sources" / <name>`.

### 1.2 `SourceResult` (from `04-implementation-plan.md` §2.3, extended)

```python
@dataclass
class SourceResult:
    name: str                 # "polymarket"
    ok: bool                  # at least one item and no fatal error
    items: int                # number of "- " lines written
    fetched_at: str           # ISO UTC, the header stamp
    text: str                 # the whole latest.md
    error: str | None
    requests: int             # HTTP requests made
    seconds: float
    bytes_in: int             # response bytes read
    stale_items_dropped: int  # items older than the freshness window
```

### 1.3 `status.json`

Written on every run, success or not (gitignored with the rest of `data-sources/`):

```json
{"name": "kalshi", "ok": true, "items": 41, "error": null, "fetched_at": "2026-10-04T20:05:11Z",
 "last_good_at": "2026-10-04T20:05:11Z", "requests": 52, "seconds": 23.4, "bytes_in": 1830211,
 "stale_items_dropped": 0}
```

`last_good_at` carries over from the previous status when the run fails, so the package builder
can say how old the file it is about to use is without parsing it.

### 1.4 HTTP behaviour (`common.http_json`, `common.http_text`)

- User-Agent `RECON/2.0 (+https://github.com/notsatoshii/recon)`; `Accept` set per call.
- Timeout 20 s per request. Retries: 2, only on timeouts, 5xx and 429; wait `Retry-After` if
  given (capped at 30 s), else 2 s then 6 s.
- A per-collector `Budget(seconds, requests, bytes)`; when it runs out, the collector stops
  fetching and writes what it has, noting `(budget reached after N requests)` in the header block.
  Retries count against it. A collector that reads independent feeds whose failures must stay
  separate gives each feed its own budget (ZDNet, §3.4), so one feed's retries cannot use up
  another's share. `http_get(..., timeout=)` caps one attempt below the 20 s default.
- 4xx other than 429 is not retried. HTTP 451 (Polymarket from a Korean IP, §3.1) is reported as
  `error: "geo-blocked (HTTP 451)"`.

### 1.5 Time

`common.to_utc(value)` accepts RFC 822 (`Sat, 03 Oct 2026 23:17:01 +0900`), ISO 8601 with `Z` or an
offset, and the naive `YYYY-MM-DD HH:MM:SS` some Korean CMS feeds use (read as KST, `+09:00`).
Items print their time as `[YYYY-MM-DD HH:MM UTC]`.

---

## 2. The 72-hour freshness rule

`RECON_FRESH_HOURS` (default 72) is applied in three places:

1. **Item level (collector).** Feed items (changelogs, ZDNet Korea) older than 72 h at collection
   time are dropped and counted in `stale_items_dropped`. Markets are live state, not items with an
   age; Polymarket and Kalshi items have no age filter, but their "new markets" lists use 48 h.
2. **File level (package assembly).** When `collect_data.sh` / the orchestrator's `--skip-collect`
   assembly reads a `latest.md`, it compares the header stamp with the package time. Older than
   72 h, or no parseable stamp: the file's body is not included, and the section shows one line
   instead:
   `- <Name>: SOURCE STALE — last good pull 2026-10-01 20:05 UTC (74 h old); not shown.`
   A shared helper `scripts/collector_common.py:fresh(path, now) -> (ok, age_h, stamp)` is used by
   both the bash assembly (via `python3 scripts/collector_common.py --fresh <path>`, exit 0 fresh,
   1 stale, one line on stdout) and Python.
3. **Run record (`export.source_record`).** A source whose stamp is older than 72 h at the run's
   finish gets `ok: false, error: "stale (74 h)"`, so the RUBRIC page's source health shows it.

The rule applies to the four new collectors from day one. For the v1 sources (Reddit, X, news,
on-chain …) the same helper is called behind `RECON_FRESH_V1` (default `0`: not applied), and the
flag is flipped to `1` after one week of daily runs with no false `SOURCE STALE` line (§8). X
already has its own 72 h filter (`RECON_TWITTER_MAX_AGE_H`) and keeps it.

---

## 3. The collectors

### 3.1 Polymarket — `data-sources/polymarket/latest.md`

**Endpoints** (public, no key), base `https://gamma-api.polymarket.com` and
`https://clob.polymarket.com`. **Every `/events` call carries `exclude_tag_id=1`** (tag 1 is
Sports, from `GET /tags/slug/sports`; esports events carry it too). Without it the top-50 page is
20,034,571 bytes (sports events have 200+ nested markets, single events reach 1 MB); with it,
`limit=50` is 4.5 MB and `offset=50` 3.5 MB (measured on the droplet 2026-10-04).

| Call | Verified 2026-10-04 (from the droplet) |
|---|---|
| `GET /events?closed=false&exclude_tag_id=1&order=volume24hr&ascending=false&limit=50&offset=0` (and `offset=50`) | 200, 4.5 MB / 3.5 MB; event fields include `title`, `slug`, `volume24hr`, `volume`, `liquidity`, `openInterest`, `endDate`, `startDate`, `createdAt`, `tags[]`, `markets[]` |
| `GET /events?tag_slug=<slug>&closed=false&exclude_tag_id=1&order=volume24hr&ascending=false&limit=8` | 200 for `crypto`, `economy`, `fed-rates`, `politics`, `geopolitics`, `ai`, `tech` |
| `GET /events?closed=false&exclude_tag_id=1&start_date_min=<now−48h ISO>&order=volume24hr&ascending=false&limit=50` | 200 (new markets); 8 real markets kept after the filters below |
| `GET /events?closed=false&exclude_tag_id=1&end_date_min=<now ISO>&end_date_max=<now+7d ISO>&order=volume24hr&ascending=false&limit=20` | 200, 1.3 MB (resolution calendar: Brazil election, "Bitcoin above ___ on October 4?", the Musk tweets market) |
| market fields used | `question`, `groupItemTitle`, `outcomes`, `outcomePrices`, `oneDayPriceChange`, `oneWeekPriceChange`, `volume24hr`, `liquidityNum`, `bestBid`, `bestAsk`, `spread`, `endDate`, `clobTokenIds`, `slug`, `sportsMarketType`, `gameId` |
| `GET https://clob.polymarket.com/book?token_id=<clobTokenIds[0]>` | 200 for a live market; `bids[]`, `asks[]` (`price`, `size` strings), `tick_size`, `last_trade_price`; asks arrive sorted from 0.999 down; **404 for a resolved market's token** |
| page size | `limit` above 100 is silently capped at 100; page with `offset` |
| **from Eric's desktop (Korea)** | **HTTP 451, Cloudflare error 1026: Polymarket geo-blocks Korean IPs.** The collector runs on the droplet only (it already does for the v1 block); tests use recorded fixtures. |

`/markets` is not used: market objects carry no `tags`, so a sports filter cannot work on them
(the old resolution-calendar call returned Dota 2, Braves vs Dodgers, Packers vs Buccaneers and
Colts spreads at the top).

**Sports filter (backstop).** `exclude_tag_id=1` does the work server side. Client side, an event
is still dropped when any tag slug is in `{sports, esports, games, soccer, football, basketball,
baseball, hockey, tennis, mma, golf, cricket, nfl, nba, mlb, nhl}`, and a market is dropped when
its `sportsMarketType` or `gameId` is set. (The top event by 24 h volume on 2026-10-04 without the
exclusion was a Dota 2 match tagged `esports, dota-2, games, sports`.)

**Leading markets.** For a mutually exclusive event (`negRisk`) the leading markets are the open
markets with the highest YES price; for any other multi-market event (strike ladders such as
"Bitcoin above ___ on October 4?") they are the open markets with the most 24 h volume, since the
highest YES there is always the lowest strike.

**Field rules.** `oneDayPriceChange` and `oneWeekPriceChange` are 0–1 fractions (−0.3995 seen):
points = 100 × the change, so "≥ 5 points" means |change| ≥ 0.05. `liquidityNum`, `bestBid` and
`oneWeekPriceChange` can be null: null counts as 0 in thresholds and prints as `n/a`; a market whose
`outcomePrices` is null or does not decode is skipped. `outcomePrices` and `clobTokenIds` arrive as
JSON strings inside JSON (`"[\"0.87\", \"0.13\"]"`): decode both.

**Sections and selection** (each `- ` line ends with `https://polymarket.com/event/<event slug>`):

| Section | Rule | Lines |
|---|---|---|
| `## TOP EVENTS BY 24H VOLUME (sports excluded)` | first 100 events by `volume24hr` (2 pages, `exclude_tag_id=1`), backstop filter, top 12 | `- <title> — 24h vol $X \| total vol $Y \| liq $Z \| OI $W \| ends YYYY-MM-DD \| leading: "<outcome or groupItemTitle>" YES n% (1d ±a pts) …up to 3 \| <url>` |
| `## BY TOPIC` | `tag_slug` in crypto, fed-rates, economy, politics, geopolitics, ai, tech; top 2 each by 24 h volume (4 made this section 7 KB of a 19 KB file), events already listed above skipped | same line, prefixed `[crypto]` etc. |
| `## 24H MOVERS` | all markets of the events fetched above with `liquidityNum ≥ 25,000` and `volume24hr ≥ 10,000`; \|points\| ≥ 5; sorted by \|points\|; top 10 | `- [topic] <question> YES n% (1d ±a pts, 1w ±b pts) \| 24h vol $X \| liq $Y \| <url>` |
| `## NEW MARKETS (started in the last 48 h)` | the `start_date_min` call; drop events with any tag slug in `{recurring, up-or-down, hide-from-new}` (the automatic "Dogecoin/Solana/Hyperliquid Up or Down - 5m" markets), then `volume24hr ≥ 5,000`; top 8 | `- <title> — started YYYY-MM-DD HH:MM UTC \| 24h vol $X \| liq $Y \| ends … \| <url>` |
| `## RESOLVING IN THE NEXT 7 DAYS` | the `end_date_min/max` `/events` call; per event, the market with the most 24 h volume among its open markets ending inside the window; top 10 by 24 h volume | `- <title>: "<leading market>" — ends YYYY-MM-DD HH:MM UTC \| YES n% \| 24h vol $X \| liq $Y \| <url>` |
| `## BOOK DEPTH (top non-sports markets)` | the most-traded (24 h) open market of each top event, CLOB book for its YES token, until 8 books are in; a 404 (resolved market) skips to the next event's leading market and is not an error | `- <question>: mid n.n¢ \| spread n.n¢ \| depth within 2¢: $B bid / $A ask \| within 5¢: $B5 / $A5 \| 24h vol ÷ liquidity r.r \| <url>` |

Mid = (best bid + best ask) / 2, where best bid = max bid price and best ask = min ask price over
all levels (the book is not assumed to be sorted). Depth in dollars = Σ `size × price` over all
levels within ±2¢ (±5¢) of the mid, order-free. The volume ÷ liquidity ratio is the structural
point the 09-11 debate raised and the brief dropped ($1.63 M volume against $238 K liquidity,
F14): it is now one quotable line.

**Budget**: ≤ 30 requests (2 + 7 + 1 + 1 + up to 12 books + spare), ≤ 45 s, ≤ 25 MB read
(16 MB decompressed with `exclude_tag_id=1`; the budget counts bytes on the wire, 1.6 MB gzipped).
Measured 2026-10-04 on the droplet: 19 requests, 3–7 s. **Size**: ~16 KB: 15,932 B, 60 lines of
~265 B (TOP 3.7 KB, BY TOPIC 3.9 KB, MOVERS 2.1 KB, NEW 1.7 KB, RESOLVING 2.1 KB, BOOK DEPTH
2.0 KB). With 4 events a topic it was 19,102 B.

**Failure modes**: 451 or 403 (wrong host) → `ok: false`, previous file kept. A market whose decode
fails is skipped, never the run.

### 3.2 Kalshi — `data-sources/kalshi/latest.md`

**Endpoints** (public market data, no key), base `https://api.elections.kalshi.com/trade-api/v2`
(the host serves every category, not only elections):

| Call | Verified 2026-10-04 (from Korea; Kalshi does not block) |
|---|---|
| `GET /series?category=<C>&include_volume=true` | 200, ~1–2 s each; Economics 889 series, Crypto 276, Financials 1,129, Politics 2,523, Companies 429, Science and Technology 429; each with `ticker`, `title`, `frequency`, `volume_fp` (lifetime) |
| `GET /events?series_ticker=<T>&status=open&with_nested_markets=true&limit=10` | 200; e.g. `KXBTCD`: "BTC price on Oct 4, 2026 at 5pm EDT?" with 80 strike markets |
| market fields used | `ticker`, `title`, `yes_sub_title`, `yes_bid_dollars`, `yes_ask_dollars`, `last_price_dollars`, `previous_price_dollars`, `volume_24h_fp`, `volume_fp`, `open_interest_fp`, `close_time`, `floor_strike`/`cap_strike`, `status` (prices are dollar strings, volumes fixed-point strings) |
| why not scan all events | `GET /events?status=open&limit=200` paged to the end = 69 pages, 13,760 events, 54 s without nested markets (half are sports). Too slow and the wrong ranking; the series list ranks by volume in 7 calls. |

Every query string is built with `urllib.parse.urlencode` (`category=Science and Technology`
with a raw space makes `urllib` raise `InvalidURL`).

**Selection**:

1. **Watchlist first.** `config/kalshi_series.json` (desk-chosen, deduped, always kept whatever
   their frequency): `KXFEDDECISION`, `KXFED`, `KXRATECUTCOUNT`, `KXCPI`, `KXPAYROLLS`, `KXBTCD`,
   `KXETHD`, `KXBTCMAXY`, `KXLLM1`, `KXTOPMODEL` (each checked 2026-10-04 with `GET /series/<T>`
   and ≥ 1 open event; `KXBTCD`/`KXETHD` are `hourly` series, which is why the frequency filter in
   step 2 does not apply to the watchlist; `KXFEDCHAIRNOM` and `KXGOVSHUTLENGTH` exist but had no
   open events and are left out). Unknown tickers or series without open events are logged and
   skipped, so the file can be edited without code changes. The watchlist's events calls are
   submitted to the pool before any category probe, so a budget cut never removes MACRO or CRYPTO
   PRICE LADDERS.
2. **Category picks by probing, not by lifetime volume alone.** Lifetime `volume_fp` ranks dead
   series first: on 2026-10-04 the Politics top 5 (`PRES`, `KXFEDCHAIRNOM`, `KXMAYORNYCPARTY`,
   `KXGOVSHUTLENGTH`, `POPVOTE`) had 0 open events, World's top 2 (`KXRAINNOSB`,
   `KXMAMDANIBOROUGHS`) had 0, Companies had 1 of 5. So, for each category in `Economics,
   Financials, Crypto, Politics, Science and Technology, Companies, World`: take the series list
   with volume; walk it in lifetime-volume order; skip `frequency` in `{fifteen_min, hourly}`
   (values seen: annual, custom, one_off, monthly, daily, weekly, hourly, fifteen_min, quarterly),
   tickers matching `15M|1H$` (the BTC 15-minute series has the largest volume of all and says
   nothing for a daily brief) and watchlist tickers; probe
   `GET /events?series_ticker=T&status=open&with_nested_markets=true&limit=10`; keep the series
   only when it returns at least one event with `close_time > now`. Stop at 5 kept or 12 probes per
   category. The probe's response is the series' event data (no second call).
3. **Events per series**: from the probe (or the watchlist call), the open events with
   `close_time > now` (`status=open` still returns events awaiting settlement), ≤ 3 per series,
   nearest `close_time` first.

**Concurrency**: the events calls run in a `ThreadPoolExecutor(4)` (each takes 0.9–1.9 s from the
droplet; sequential, ~52 calls ran 70–90 s). Categories are probed in parallel, the probes within a
category in order (so the 5-kept / 12-probe stop is exact). Expected wall time about 30 s.

A market opened less than 24 h ago, or with `previous_price_dollars` of 0, has no 24 h change
(Kalshi reports 0 for it, which would read as a +99-point move); it prints `24h n/a, new` and is
left out of the movers. Event lines show the event's next market close (`next close`), since
multi-date events such as "Before Nov 1 / Dec 1 / Jan 1" close market by market.

Probability of a market = mid of `yes_bid`/`yes_ask` when both exist and the spread ≤ 10¢, else
`last_price`. 24 h change = `last_price − previous_price` in points. **Build-time check**: confirm
on the first live pull that `previous_price_dollars` is the price 24 h earlier (Kalshi documents
it as the previous day's price); if not, the movers section uses the change since the last pull,
read from `data-sources/kalshi/prev.json` (written each run).

**Sections** (lines end with `https://kalshi.com/markets/<series ticker, lower case>`):

| Section | Rule |
|---|---|
| `## TOP EVENTS BY 24H VOLUME` | events from step 3 (`close_time > now`) ranked by Σ `volume_24h_fp`; top 12; line: `- [Category] <event title> — 24h vol N contracts \| OI N \| closes YYYY-MM-DD \| top: "<yes_sub_title>" n% (24h ±a pts) …up to 3 \| <url>` |
| `## 24H MOVERS` | markets with `volume_24h_fp ≥ 500` and \|change\| ≥ 5 points; top 10 |
| `## CLOSING IN THE NEXT 7 DAYS` | events with `now < close_time ≤ now + 7 d` (events at or past `close_time` are dropped); top 10 by 24 h volume |
| `## CRYPTO PRICE LADDERS` | for `KXBTCD` and `KXETHD`, the nearest daily (5 pm EDT) expiry whose `close_time ≥ collection time + 12 h`, not the hourly ones. Cron runs at 05:00 KST = 20:00 UTC, so the same-day 5 pm EDT event (e.g. `KXBTCD-26OCT0317`, close 21:00Z) closes an hour after collection with a degenerate ladder (0.99/0.01); on 2026-10-04 the rule picks `KXBTCD-26OCT0417`. The strike where the YES probability crosses 50 % (linear interpolation between the two neighbouring strikes), plus the 25 % and 75 % strikes: `- BTC on 2026-10-04 17:00 EDT: market-implied median $X (25–75 %: $A–$B), from N strikes \| <url>` |
| `## MACRO` | `KXFEDDECISION` and `KXFED` nearest meeting: each outcome with its probability on one line |

**Budget**: ≤ 110 requests (7 series lists + 10 watchlist + ≤ 84 probes), ≤ 90 s, ≤ 40 MB read
(the series lists alone are ~6.5 MB; Politics is 3.4 MB and takes 2.2 s). Kalshi's public read
limit is far above 4 parallel requests. Measured 2026-10-04: 73 requests, 17.5 s, 0.9 MB on the
wire; a 429 now and then on a probe is retried. **Size**: ~9 KB: 8,923 B, 36 lines of ~250 B (TOP
3.4 KB, CLOSING 2.7 KB, MOVERS 1.6 KB, LADDERS and MACRO 0.4 KB each).

**Not in this collector**: matching the same question across Polymarket and Kalshi. Titles do not
line up and a wrong match is worse than none; it is a Lever-desk feature (desk half of Phase E).

### 3.3 Changelogs — `data-sources/changelogs/latest.md`

Header `# Changelogs Intelligence`. Release notes for the AI tools the brief tracks. The news
collector's Google News queries name OpenAI, Anthropic, Google DeepMind, Meta AI, Mistral, xAI,
Codex, Claude Code, Cursor and Copilot (`collect_data.sh`, `## AI NEWSLETTER SOURCES`), so the
list starts there.

**Why not the Atom feeds.** `github.com/<repo>/releases.atom` lists only the 10 newest
releases and carries no pre-release flag. On 2026-10-04: Codex's 10 were all alphas, so the stable
`rust-v0.160.0` (published 2026-10-01, inside 72 h) was invisible; Gemini CLI's 10 were all
nightly or preview; Cline's 10 were SDK and Desktop releases only; Atom titles differ from tags
(Codex title `0.162.0-alpha.11`, tag `rust-v0.162.0-alpha.11`).

**GitHub repos: the REST API.** `GET https://api.github.com/repos/{owner}/{repo}/releases?per_page=30`,
no key (anonymous limit 60 per hour per IP, `x-ratelimit-limit: 60` checked from the droplet; this
collector makes 12 calls a day). Keep entries with `draft == false` and `prerelease == false`; the
time is `published_at`, the version is `tag_name`, the notes come from `body` (markdown), the link
is `html_url`. A 403/429 with `x-ratelimit-remaining: 0` marks that source `rate-limited` and the
run goes on with the others.

**Sources** (`config/changelog_sources.json`; every entry verified 2026-10-04):

| Source | Fetch | Verified |
|---|---|---|
| Codex CLI | REST `openai/codex` | 200; stable `rust-v0.160.0` 2026-10-01 behind 10 alphas |
| Claude Code | REST `anthropics/claude-code` | 200, latest 2026-10-02 (`v2.1.288`); `body` holds the release notes |
| Gemini CLI | REST `google-gemini/gemini-cli` | 200; nightly/preview flagged `prerelease` |
| GitHub Copilot CLI | REST `github/copilot-cli` | 200, latest 2026-10-02; `1.0.92-3`-style builds flagged `prerelease` (and caught by the fallback regex) |
| OpenAI Agents SDK (Python) | REST `openai/openai-agents-python` | 200, latest 2026-10-02 |
| Claude Agent SDK (Python) | REST `anthropics/claude-agent-sdk-python` | 200, latest 2026-09-30 |
| Anthropic SDK (Python) | REST `anthropics/anthropic-sdk-python` | 200, latest 2026-09-30 |
| OpenAI SDK (Python) | REST `openai/openai-python` | 200, latest 2026-10-02 |
| Model Context Protocol spec | REST `modelcontextprotocol/modelcontextprotocol` | 200, latest 2026-07-28 (an `RC`) |
| Ollama | REST `ollama/ollama` | 200, latest 2026-10-02 |
| vLLM | REST `vllm-project/vllm` | 200, latest 2026-10-02 |
| Cline | REST `cline/cline`, exclude `^(sdk/\|SDK \|Desktop )` on tag or name | 200; the 30 newest include the extension releases |
| Cursor changelog | RSS `https://cursor.com/changelog/rss.xml` | 200, RSS, 50 items, latest 2026-09-23 |
| GitHub Changelog (Copilot only) | RSS `https://github.blog/changelog/feed/` | 200, RSS, 10 items; kept when title or category contains "Copilot" |
| Google AI blog | RSS `https://blog.google/innovation-and-ai/technology/ai/rss/` | 200, RSS (the old `/technology/ai/rss/` URL returns 301) |

Checked and left out: Anthropic has no RSS (`/rss.xml` 404; the docs release-notes URL returns
HTML); its product changes arrive through the Claude Code and SDK feeds and Google News.
`raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md` works but is 900 KB.
`langchain-ai/langchain` publishes a release per sub-package (noise). `Aider-AI/aider` (last
release 2026-02) and `continuedev/continue` (2026-06) are stale. OpenAI News RSS is already in the
news collector (no duplicate).

**Rules**:

- Window: entries published within 72 h (§2). Entries are matched against
  `data-sources/changelogs/seen.json` (ids already written); first appearances are marked `new`.
- Pre-releases: for GitHub repos the API's `prerelease` flag decides. The title/tag regex is a
  fallback only, for GitHub repos (a release some project forgot to flag), case-insensitive and
  anchored on separators:
  `(?i)(?:^|[\s._-])(alpha|beta|rc\d*|nightly|preview|canary|dev|pre)(?:[\s._\d-]|$)|\d+\.\d+\.\d+-\d+$`
  (drops `0.162.0-alpha.11`, `v0.9.0-nightly.20261003`, `2026-07-28 RC`, `1.0.92-3`; keeps
  `v2.1.288`, `resources`, `search`). It is **never** applied to Cursor, GitHub Changelog or the
  Google blog, whose titles are prose ("public preview" is news there).
- Per-source `include`/`exclude` regexes in the JSON (Cline: exclude `^(sdk/|SDK |Desktop )`;
  GitHub Changelog: include `Copilot`).
- One line per source: the newest stable release in the window, plus `(+N earlier releases in
  72 h)` counted from the same filtered REST list (for feeds, from the feed).
- Release notes: `body` markdown (REST) or the entry's HTML content (feeds), tags and markdown
  markup stripped, headings and boilerplate (`What's Changed`, `Full Changelog`, contributor lines)
  skipped, the first three bullet points or sentences joined with `; `, cut to 280 characters.
- Line: `- [YYYY-MM-DD HH:MM UTC] <Product> <version> (new): <notes> | <release url>`.
- Sources with nothing in the window are summarised in one last line:
  `- No release in the last 72 h: MCP spec, Cursor, …` (so a quiet day still writes a valid file).
- Section order: `## CODING AGENTS & CLIS` (Codex, Claude Code, Gemini CLI, Copilot CLI, Cursor,
  Cline, GitHub Changelog), `## SDKS & PROTOCOLS` (Agents SDK, Claude Agent SDK, Anthropic SDK,
  OpenAI SDK, MCP), `## MODEL SERVING` (Ollama, vLLM), `## VENDOR BLOGS` (Google AI).

**Budget**: 20 requests (12 REST + 3 feeds + spare), ≤ 40 s, 4 in parallel. Measured 2026-10-04:
15 requests, 1–3 s, 1.4 MB. **Size**: ~3.5 KB: 3,534 B, 10 lines of up to ~430 B (9 sources with a
release, 6 quiet). A day with a release from all 15 sources is ~6 KB.

### 3.4 Korean source: ZDNet Korea — `data-sources/zdnet_kr/latest.md`

Header `# ZDNet Korea Intelligence`. The news collector already reads AI타임스 and 전자신문
(KOREA — AI) and 블록미디어 and 토큰포스트 (KOREA — CRYPTO & MARKETS). ZDNet Korea covers both
Korean AI and 가상자산 policy and industry, with proper timezones and categories.

| Candidate | Result 2026-10-04 |
|---|---|
| **ZDNet Korea** `https://feeds.feedburner.com/zdkorea` | **chosen**: 200, RSS 2.0, 30 items, `pubDate` RFC 822 with `+0900`, `<category>` tags, full-text `description`; robots.txt allows `/` |
| 디지털애셋 `https://www.digitalasset.works/rss/allArticle.xml` | 200, 50 items, crypto only, includes a daily 김치 프리미엄 by exchange; naive `pubDate` (KST). **Read as the second request** (below) |
| 바이라인네트워크 `https://byline.network/feed/` | 200, 20 items, IT/AI |
| 한국경제 IT `https://www.hankyung.com/feed/it`, 매일경제 `https://www.mk.co.kr/rss/50300009/`, 연합뉴스 경제 | 200 but general-interest; more noise per useful item |
| `zdnet.co.kr/rss/newsall.xml`, decenter.kr, coinreaders, ddaily (HTML, no items) | 404 or not a feed |

**Two requests, not one.** The feedburner feed holds only the 30 newest items, 6–21 h old at
05:19 KST on 2026-10-04, so a once-a-day pull misses about 9 h of items and the 72 h window never
bites; that day it had 14 AI matches and 1 가상자산 match. The 디지털애셋 feed is therefore read
from the start as the second request (same parser; its naive `pubDate` is read as KST), and its
items count toward `[가상자산]` only. Either feed failing alone is not a failed run.

**Rules**: items within 72 h; an item is kept when its title, categories or the first 300
characters of its description match one of two keyword sets, and tagged by the set it matched:

- `[AI]`: `AI`, `인공지능`, `생성형`, `LLM`, `에이전트`, `GPU`, `데이터센터`, `오픈AI`, `앤트로픽`,
  `챗GPT`, `클로드`, `제미나이`, `AI 기본법`, `AI 교육`;
- `[가상자산]`: `가상자산`, `암호화폐`, `비트코인`, `이더리움`, `블록체인`, `스테이블코인`, `거래소`,
  `업비트`, `빗썸`, `토큰증권`, `STO`, `디지털자산`.

Matching: Latin keywords (`AI`, `LLM`, `GPU`, `STO`) match on ASCII word boundaries
(`re.compile(r"\bAI\b", re.ASCII)`), so `AI` no longer matches inside `MAIN` and `STO` inside
`STORY`; with `re.ASCII`, a Hangul letter next to `AI` counts as a boundary, so `AI가` and `오픈AI`
still match. Hangul keywords stay plain substrings. An item matching both sets is tagged `[AI]`
once, unless it came from 디지털애셋.

Line: `- [YYYY-MM-DD HH:MM UTC] [AI] <title> | <link>`, with up to 60 characters of the description
appended after ` — ` when it adds information (not when it repeats the title). Max 12 `[AI]` and 8
`[가상자산]` items, newest first, under `## AI` and `## 가상자산·블록체인`; each 가상자산 line names its
feed (`ZDNet` or `디지털애셋`). Titles stay in Korean (the KOREA section is written in English from
them, as today).

**Budget**: one per feed, 3 requests (1 + 2 retries), ≤ 20 s, 15 s per attempt; ≤ 6 requests and
≤ 40 s for the run. A shared 2-request budget let a single 5xx/429 retry, or one 20 s timeout, on
ZDNet stop the 디지털애셋 request, which carries most `[가상자산]` lines (all 8 on 2026-10-04).
Measured 2026-10-04: 2 requests, 1 s, 0.1 MB. **Size**: ~6.5 KB: 6,465 B, 20 lines of ~320 B
(Korean is 3 bytes a character; with 120-character descriptions it was 9,228 B).

---

## 4. Wiring into the package (Phase B's commit, after the collectors land)

The collectors are committed and live-tested first, standalone; nothing below is wired in the
same commit, because `collect_data.sh`, `run_recon.sh` and the orchestrator are Phase B's files.

1. **Collection.** `collect_data.sh` starts the four scripts in the background right after it
   starts Reddit and X (Phase A runs those side by side), from the repo root and with a 120 s
   total timeout, and waits for them before assembling the package:
   ```bash
   (cd "$RECON_HOME" && for c in polymarket kalshi changelogs zdnet_kr; do
       timeout 120 "$PY" "scripts/collect_$c.py" & done; wait) >> "$DAY_LOG" 2>&1 &
   NEW_COLLECTORS_PID=$!
   ```
   Each collector prints `  Kalshi: ok, 41 items, 52 requests, 23.4 s` into the day log.
   Collection time grows by ~0 s (the slowest new collector, Kalshi at ~30 s with 4 workers, is far
   shorter than Reddit's pacing). The scripts resolve `data-sources/` from `RECON_HOME`, never
   from the working directory.
1a. **Raw file.** The `00_raw_data.md` loop (`collect_data.sh` line ~883, today `for src in reddit
   twitter onchain news ai_tools fundraising`) becomes `for src in reddit twitter onchain news
   ai_tools fundraising polymarket kalshi changelogs zdnet_kr`, each file through the §2 freshness
   check. The orchestrator's `--skip-collect` assembly writes `00_raw_data.md` too, from the same
   list (today it writes no raw file at all), so Phase C's `LENS_RAW` and `evidence.locate` see
   the same raw text on a normal run and a skip-collect run.
2. **Package sections.**
   - New `# SECTION 8: PREDICTION MARKETS`: Polymarket, then Kalshi.
   - Changelogs go into `# SECTION 6: AI & TOOLS` after the GitHub/HN block.
   - ZDNet Korea goes into `# SECTION 4: NEWS INTELLIGENCE` after the news file.
   - Every file passes the §2 freshness check first.
3. **Remove the duplicate.** The `## POLYMARKET LIVE MARKETS` block in the on-chain collector is
   deleted (the DeFiLlama `## PREDICTION MARKET PROTOCOLS` block stays: TVL and fees are different
   data).
4. **Agent view caps** (`scripts/build_agent_package.py` `CAPS`): `PREDICTION MARKETS` 20,000 (new),
   `AI & TOOLS` 6,000 → 9,000, `NEWS INTELLIGENCE` 13,000 → 18,000. `FALLBACK_NAMES` gains
   `polymarket`, `kalshi` → PREDICTION MARKETS, `changelogs` → AI & TOOLS, `zdnet korea` → NEWS
   INTELLIGENCE. The view grows from ~65 KB to ~92 KB.

   The caps come from running `fair_share` on the measured 2026-10-04 files (Polymarket 15.9 KB +
   Kalshi 8.9 KB; news section 47.3 KB + ZDNet 6.5 KB; ai_tools 4.2 KB + changelogs 3.5 KB):

   | Section | Cap | Lines kept in the agent view |
   |---|---|---|
   | PREDICTION MARKETS | 7,000 (first estimate) | Polymarket 10 of 60, Kalshi 10 of 36 |
   | | 16,000 | 36 of 60, 24 of 36 |
   | | **20,000** | **50 of 60, 29 of 36** |
   | NEWS INTELLIGENCE | 13,000 (news alone, today) | news 44 of 146 |
   | | 14,000 (first estimate) | news 36, ZDNet 10 of 20: the news lines are squeezed |
   | | **18,000** | **news 44, ZDNet 13 of 20** |
   | AI & TOOLS | 8,000 | ai_tools 23 of 23, changelogs 10 of 10 (7.7 KB, no headroom) |
   | | **9,000** | all, with room for a busy changelog day |

   The ZDNet lines the view drops still reach the draft through `KR_RAW` (5a) and the
   policy_analyst lens (5b); the Polymarket lines it drops reach trader's lens and the Kalshi lines
   macro_strategist's (5b, measured on 10-04: 1,953 B and 965 B).
5. **Orchestrator.** `shared()` lists `SECTION 8 (PREDICTION MARKETS)` in its header and reads the
   view up to 100 KB (was 80 KB; the view is ~92 KB with the caps in item 4); the `--skip-collect` assembly list gains the four names (package
   and raw file, item 1a). The Phase E entries of `LENS_RAW` are in the corrected table in 5b; they
   only match once item 1a puts the files into `00_raw_data.md`.
5a. **Synthesizer raw blocks.** The draft's AI NEWSLETTER and KOREA material comes from
   `orchestrator.raw_sections()` (`AI_RAW` = ai_tools + the news `## AI & TECH NEWS` section;
   `KR_RAW` = the news `## KOREA — …` sections), not from the agent view, so without this item the
   changelogs and ZDNet never reach the draft they exist for. `AI_RAW` gains the
   `# Changelogs Intelligence` block (≤ 5,000 B; measured 3,534 B, so 3,000 cut its end) and `KR_RAW`
   the `# ZDNet Korea Intelligence` block (≤ 7,000 B; measured 6,465 B, so 3,000 dropped about
   half of the Korean items, which are what the source is for), both read from the run folder's `00_raw_data.md` (item 1a), not from
   `data-sources/`, so a replay reads the day it replays. Test: `07_raw_sections.json` contains
   both headers when the files exist, and neither when they do not.
5b. **`LENS_RAW` (replaces the table in Phase C §3; revised a second time 2026-10-04 from
   measured bytes).** The rules, table and measurements below are the same text as Phase C §3
   item 3; the two must stay identical.
   Phase A's raw file holds only the reddit, twitter, onchain, news, ai_tools and fundraising
   files (`collect_data.sh` line ~883); World Monitor, the economic calendar and BettaFish exist
   only in the package. Two earlier tables failed when measured. The first named headings that do
   not exist (`## RECENT HACKS`; `## STABLECOINS` is not a prefix of `## STABLECOIN SUPPLY`), so
   macro_strategist and skeptic got 0 bytes. The second (first 2026-10-04 review) gave trader,
   builder and analyst 0 bytes on the real 10-04 package: every on-chain, AI & Tools and World
   Monitor line is already in the ~60 KB view (on 09-10, 09-11 and 10-04 the view caps cut nothing
   from those sections), so `## DEX VOLUMES`, `## FEE REVENUE`, `## CHAIN TVLs`, `## STABLECOIN
   SUPPLY`, `## DERIVATIVES PROTOCOLS`, `## GITHUB TRENDING`, `## HACKER NEWS` and the like add
   nothing, and with the phase-e §4.4 caps the changelogs and Polymarket's `## BOOK DEPTH` and
   `## RESOLVING` are entirely in the view too. The table below is picked from what the views
   really leave out (fundraising, the news sub-blocks, X and Reddit lines, the KOREA and AI
   EDUCATION blocks) and measured per agent. The reference implementation is
   `tests/lens_extras_probe.py` (no LLM, no network); `debate.lens_extras` implements the same
   rules and table, and the unit test compares the two.
   - **Search order**: `00_raw_data.md`, then `00_data_package.md`; the first heading line that
     starts with the entry. A `# ` heading takes its block up to the next `# ` line; a `## `
     heading up to the next `# ` or `## ` line. The Phase E files are named by their `# ` heading
     only, because their `## ` subheadings collide (`## TOP EVENTS BY 24H VOLUME` is in both
     Polymarket and Kalshi; Kalshi's `## MACRO` is a prefix of X's `## MACRO ECONOMICS`).
   - **Subtract the view**: a body line is dropped when `01_filtered.md` already carries it: its
     stripped text is a view line; or it is an X line whose text after the engagement bracket
     (first 120 characters) occurs in the view (the view re-renders X lines as
     `- @who [Mon DD HH:MM] (…) text`, so a text match alone never removes a tweet, and most of
     narrator's X bytes under the old rule were tweets already in the view); or its last URL
     (over 24 characters) occurs in the view. An indented continuation line (a snippet under an
     item) is kept only when its item line is kept.
   - **One lens per line**: a line already given by an earlier entry of the same agent, or to an
     earlier agent in table order, is dropped, so "the other lenses were not given it" in
     `take.md` holds. The table runs from the narrow lenses to the broad ones (whole Reddit and X
     blocks last); macro_strategist precedes trader so Kalshi goes to macro and Polymarket to
     trader. All nine agents are computed in table order whether or not they are active that day,
     so a lens's block never depends on who else runs.
   - **Headings** are kept only when a body line under them survives.
   - **Line filters**: `news~<regex>` takes the matching `- ` lines (case-insensitive) of the raw
     file's `# News Intelligence` and `# Twitter/X Intelligence` blocks, under the heading
     `## News and X lines matching /<regex>/`.
   - **Caps**: whole lines, in order; each entry at most 3,000 B and each agent at most 6,000 B,
     headings and blank separators included.

   | # | agent | entries (in order) |
   |---|---|---|
   | 1 | macro_strategist | `# World Monitor Intelligence` (package), `# Kalshi Intelligence`, `news~` MACRO, `## ECONOMICS` and `## POLITICS` (Reddit) |
   | 2 | trader | `# Polymarket Intelligence`, `# Kalshi Intelligence`, `news~` TRADER |
   | 3 | analyst | `## CRYPTO / WEB3 ROUNDS`, `## AI ROUNDS` (fundraising), `## CROSS-SOURCE SIGNALS` (package), `news~` ANALYST |
   | 4 | skeptic | `news~` SKEPTIC, `## 5. Controversy & Risk Flags`, `## 2. Narrative Analysis`, `## 3. Divergences` (package, BettaFish) |
   | 5 | policy_analyst | `## KOREA — CRYPTO & MARKETS`, `news~` POLICY, `# ZDNet Korea Intelligence` |
   | 6 | ai_engineer | `## AI NEWSLETTER SOURCES`, `news~` AI, `# Changelogs Intelligence`, `## GITHUB TRENDING` |
   | 7 | builder | `## AI & TECH NEWS`, `news~` BUILD, `# Changelogs Intelligence`, `## GITHUB TRENDING`, `## HACKER NEWS` |
   | 8 | user_agent | `## KOREA — AI`, `## AI EDUCATION & WORKFORCE`, `# ZDNet Korea Intelligence`, `# Reddit Intelligence` |
   | 9 | narrator | `# Reddit Intelligence`, `# Twitter/X Intelligence` |

   ```
   MACRO    \bFed\b|FOMC|\bCPI\b|inflation|payroll|jobs report|tariff|treasur|\byields?\b|\bdollar|\bDXY\b|recession|\bGDP\b|rate cut|rate hike|\bECB\b|\bBOJ\b|Powell|\boil\b|sanction|shutdown|election|midterm|geopolit|China|Iran|Russia|Ukraine|Israel
   TRADER   liquidat|funding rate|open interest|short squeeze|whale|leverag|\boptions\b|\bperps?\b|ETF.{0,12}(in|out)flow|\bsupport\b|\bresistance\b|\bshorts?\b|\blongs?\b
   ANALYST  \bTVL\b|stablecoin|market share|revenue|earnings|valuation|inflows?\b|outflows?\b|\bETFs?\b
   SKEPTIC  \bhack|exploit|depeg|breach|drain|\brug|scam|fraud|lawsuit|outage|insolven|bankrupt|delist|vulnerab
   POLICY   regulat|\bSEC\b|\bCFTC\b|\bESMA\b|CLARITY|\bMiCA\b|lawmaker|Congress|\bsenat|금융위|금감원
   AI       OpenAI|Anthropic|Claude|Gemini|\bGPT|\bLLM|Llama|DeepSeek|Qwen|Mistral|inference|benchmark|\bGPU|Nvidia
   BUILD    launch|\bships?\b|shipped|\breleas|open.?source|\bSDK|\bAPI\b|mainnet|testnet|upgrade|github|developer|\bprotocol
   ```

   Entries that do not exist in a package are skipped silently. Kept although they add 0 B on
   10-04 and 09-11, because they fill when a view cap cuts them: `# Changelogs Intelligence`,
   `## GITHUB TRENDING` and `## HACKER NEWS` (1.4 KB each on 09-10). BettaFish is named by its
   `## ` blocks because its `# ` headings differ by day (on 10-04 `# BettaFish Sentiment
   Intelligence` holds only the market signals, all in the view; the report has its own `# `
   heading); its narrative analysis adds 1.7 KB to skeptic on 09-10.

   **Measured** with `tests/lens_extras_probe.py --rebuild-view` on the droplet's real packages
   (2026-10-04): the view is rebuilt by the current `scripts/build_agent_package.py`, as a
   `--replay` does (for 10-04 it is byte-identical to the run's own view). Bytes of the YOUR LENS
   DATA block:

   | agent | 09-10 | 09-11 | 10-04 | 10-04 + Phase E (simulated) | fixture 09-11 | fixture 10-04 |
   |---|---|---|---|---|---|---|
   | macro_strategist | 1,842 | 5,320 | 3,123 | 4,090 | 3,788 | 3,259 |
   | trader | 1,216 | 3,008 | 2,036 | 3,991 | 3,008 | 1,547 |
   | analyst | 915 | 5,701 | 5,542 | 5,542 | 5,701 | 5,924 |
   | skeptic | 2,589 | 2,394 | 3,688 | 3,688 | 2,138 | 3,682 |
   | policy_analyst | 878 | 4,008 | 5,548 | 5,875 | 4,008 | 5,827 |
   | ai_engineer | 4,288 | 4,757 | 5,842 | 5,842 | 4,757 | 5,758 |
   | builder | 2,699 | 3,468 | 3,481 | 3,481 | 3,403 | 3,542 |
   | user_agent | 2,807 | 5,909 | 5,877 | 5,877 | 5,909 | 5,877 |
   | narrator | 3,694 | 5,845 | 5,819 | 5,819 | 5,792 | 4,235 |
   | **agents ≥ 2 KB** | 5/9 | **9/9** | **9/9** | 9/9 | 9/9 | 8/9 |

   For comparison, the previous table on the real 10-04 package: narrator 6,000, policy_analyst
   6,000, user_agent 6,000, skeptic 3,432, macro_strategist 3,074, ai_engineer 3,000, trader,
   builder and analyst 0 (narrator's and user_agent's were the same Reddit lines). 09-10
   predates the KOREA, AI EDUCATION and fundraising blocks (its view is 38 KB), so four lenses get
   under 2 KB there. The fixture columns are lower where the trimmed fixture raw file (40 lines a
   block) cuts the lines the view leaves out. "10-04 + Phase E" appends the four Phase E files of
   the same morning (stamped 2026-10-03 20:55 UTC; the 10-04 collection ran at 20:00 UTC) to the
   raw file and the package as phase-e §4.1a and §4.2 do, and rebuilds the view with the §4.4 caps
   (87.7 KB): Polymarket adds 1,953 B to trader (the TOP EVENTS and BY TOPIC lines the 20,000 cap
   cuts), Kalshi 965 B to macro_strategist, ZDNet 325 B to policy_analyst; the changelogs are all
   in the view.

   **phase-e §4.1a does not change the probe or the replays.** `--replay` and `--package-from`
   copy each day's `00_*.md` as collected, and 09-10, 09-11 and 10-04 were collected before the
   Phase E files existed, so the Phase E entries are inert on them and columns 09-10 to 10-04 are
   what the spread probe (§15.0 of Phase C) and the replays see; trader's bytes there come from
   its `news~` line. The wiring (Phase B's commit, phase-e §4) matters from the first package
   collected after it, and the "10-04 + Phase E" column is the measured expectation for such a day.

   The bytes added per agent are recorded in `takes/<agent>.json → fed.lens_extra_bytes` and per
   entry in `fed.lens_extra_headings`, and the takes phase logs a warning when an active agent
   gets less than 2 KB. When an agent's extras are empty the block reads "(no lens data today:
   cite the package)" and the lens-data rule in `take.md` does not apply. **Unit test**
   (`tests/test_debate.py`, Phase C §17.1): on the committed 2026-09-11 and 2026-10-04 package
   fixtures every `LENS_RAW` agent gets more than 0 bytes and at most 6,000 B; `debate.lens_extras`
   returns the same text as `tests/lens_extras_probe.py`; no added line is in that day's
   `01_filtered.md` by the view rule above, and no line goes to two agents (the same checks run
   today in `tests/collectors/test_package_fixtures.py` against the probe). **Replay pass-bar
   item** (Phase C §15.5): `lens_extra_bytes ≥ 2 KB` for at least 7 of 9 agents on 09-11 and on
   10-04, every agent above 0 on 09-10, and `citation_overlap` lower than the old run's. The point
   (F1, F6) is that lenses stop starting from the identical text.
6. **Run record.** `export.LAYER` gains `polymarket`, `kalshi`, `changelogs`, `zdnet_kr`;
   `SECTION_LAYER` gains `("polymarket", "polymarket"), ("kalshi", "kalshi"), ("changelogs",
   "changelogs"), ("zdnet korea", "zdnet_kr")` (none of the existing substring keys matches these
   headers, so order does not matter); `TITLE_SOURCES` (added in `f11607c`) gains
   `("prediction", ["polymarket", "kalshi"])`. (`export.package_sections` turns the title
   `PREDICTION MARKETS` into `prediction_markets` and `section_sources` matches with
   `title.startswith(k)`, so a key with a space would never match.) `section_sources()` then attributes SECTION 8
   to Polymarket and Kalshi and the new blocks inside SECTIONS 4 and 6 to `zdnet_kr` and
   `changelogs` by their `# <Name> Intelligence` headers, which is how the RUBRIC recon page links
   sources to sections; checked on the page when Phase F starts.
7. **Archive.** `ph_deliver` copies the four new `latest.md` files into `archive/<date>/`.
8. **`.gitignore`**: `data-sources/*/status.json`, `data-sources/*/seen.json`,
   `data-sources/*/prev.json`.
9. **`sources-audit.md`**: one row per new source after the first live pull (done 2026-10-04).

---

## 5. Test plan

All `unittest`, no network, in `tests/collectors/`. Run from the repo root:
`python3 -m unittest discover -s tests/collectors` (65 tests, ~2 s, desktop and droplet).

**Status 2026-10-04.** Built: fixtures, header, items, export compatibility, quote round trip,
freshness (collector level), failure keeps file, Polymarket, Kalshi, changelogs, ZDNet Korea,
budget, package fixtures, live smoke. A golden test compares each collector's replay with the replay
recorded on the droplet (`tests/fixtures/collectors/replay_<name>.md`), so any change in output
shows up. Left for Phase B's wiring commit, since they test the wiring: the package-level
`SOURCE STALE` line and `source_record`'s `stale (73 h)` (freshness rows 2 and 3) and the `package`
row (`--dry-run --package-from …`).

**Recording.** `tests/record_collector_fixtures.py`, run on the droplet (Polymarket answers 451 from
Korea), runs the four collectors live in a scratch `RECON_HOME` with `RECON_COLLECTOR_NOW` frozen
and `RECON_COLLECTOR_RECORD` set, so each response is saved under the name `_fixture_path` expects
(the first 16 hex digits of the URL's sha1); trims each to the fields the collectors read and
≤ 200 KB; writes `manifest.json` (frozen `now`, every URL, live and replay status lines); and replays
the trimmed set to check all four still produce an ok file. The set recorded 2026-10-03 20:55 UTC
(05:55 KST on 10-04) is 107 responses, 1.8 MB (39 MB live). Re-record when an endpoint or a
collector's output changes.

Package fixtures: `briefs/` is gitignored and exists only on the droplet, so trimmed copies of two
real days are committed to `tests/fixtures/package/2026-09-11/` and `tests/fixtures/package/2026-10-04/`
(`00_data_package.md`, `00_raw_data.md`, `00_scorecard.md`, `01_filtered.md`, each ≤ 200 KB, pulled
from `/home/recon/recon-v2/briefs`; the package and raw file trimmed by cutting each `##` block to
its first 40 lines, so every heading survives). `01_filtered.md` is not trimmed: it is the full view
the current `scripts/build_agent_package.py` builds from that day's full package, which is what a
`--replay` sees (09-11: 63,240 B, regenerated on the droplet 2026-10-04, replacing the old uncapped
169 KB v1 view in which every raw line appeared; 10-04: the run's own 60,260 B, byte-identical to a
rebuild). Trimming a view would make view lines look like lens extras. Every test and dry run that names a package day points at these folders
through `--package-from`, never at `briefs/`, so the suite runs on the desktop and in CI. Phase C's
§17.1 (the 09-11 raw-file crux fixture) and §17.4 (`--dry-run --package-from …/2026-10-04`) use the
same folders.

| Test | What |
|---|---|
| fixtures | One recorded response per endpoint in `tests/fixtures/collectors/` (Polymarket recorded on the droplet: events page, tag page, new-markets page, resolution-calendar page, one CLOB book, one CLOB 404; Kalshi: one series list, `KXBTCD` and `KXFEDDECISION` events, one empty events reply; three GitHub REST release lists incl. Codex (alphas plus a stable) and Cline; Cursor and GitHub Changelog RSS; ZDNet Korea and 디지털애셋 RSS). Trimmed to ≤ 200 KB each. `collector_common.http_*` reads fixtures when `RECON_COLLECTOR_FIXTURES=<dir>` is set. |
| header | every collector's output matches `^# .+ Intelligence\n## \d{4}-\d\d-\d\d \d\d:\d\d UTC\n` and the stamp equals a frozen `now` given in UTC (a test sets `now` to 23:30 UTC, when Seoul is already the next day, to catch a local-time stamp) |
| items | every non-heading, non-blank line starts with `- `; no line over 600 bytes; each item line has a URL |
| export compatibility | `export.source_record(name, layer, text, finished)` returns `ok: true`, the right item count and `fetched_at` equal to the stamp converted to KST (the Seoul-time heuristic not triggered) |
| quote round trip | 20 random substrings (≤ 200 chars) of item lines verify with `evidence.Corpus(...).verify_quote` after the file goes through `build_agent_package.py` |
| freshness | items dated 73 h before `now` dropped and counted; a file stamped 73 h old renders the `SOURCE STALE` line in the package; `source_record` reports `stale (73 h)` |
| failure keeps file | a fixture HTTP 451 / 500 / timeout: `latest.md` unchanged (byte-identical), `status.json` has `ok: false`, the error and the old `last_good_at`; exit code 1 |
| Polymarket | every `/events` URL carries `exclude_tag_id=1`; backstop sports filter (the Dota 2 event and a market with `gameId` set are dropped); `outcomePrices` string decoding; null `liquidityNum`/`bestBid`/`oneWeekPriceChange`; points = 100 × fraction; depth arithmetic on a hand-made, unsorted book (±2¢, ±5¢); CLOB 404 skips to the next event; movers threshold; `recurring`/`up-or-down`/`hide-from-new` events dropped from NEW MARKETS |
| Kalshi | 15-minute and hourly series excluded from the category picks but `KXBTCD` kept from the watchlist; a series whose probe returns no event with `close_time > now` is skipped and the walk goes on (stop at 5 kept / 12 probes); watchlist submitted first; `Science and Technology` URL-encoded; ladder event = nearest daily with `close_time ≥ now + 12 h`; events with `close_time ≤ now` dropped from CLOSING and TOP; mid vs last price rule; ladder interpolation on a 5-strike fixture (median between strikes) |
| changelogs | `prerelease: true` dropped and the stable `rust-v0.160.0` found behind ten alphas; fallback regex drops `0.162.0-alpha.11`, `2026-07-28 RC`, `1.0.92-3`, keeps `v2.1.288`, and is not applied to GitHub Changelog titles (`… in public preview`, `resources`); Cline `sdk/`, `SDK `, `Desktop ` exclusion; `(+N earlier releases)` count; `new` marking with `seen.json`; quiet-day summary line |
| ZDNet Korea | `+0900` → UTC; 디지털애셋 naive `pubDate` read as KST; keyword tagging in both sets; `MAIN`/`STORY` do not match `AI`/`STO`, `오픈AI`/`AI가` do; an item matching both is tagged `[AI]` once and listed under AI; description not appended when it repeats the title; one feed failing still writes the other |
| budget | a fixture server that answers slowly: the collector stops at its request or time budget and still writes what it has |
| live smoke (droplet, not CI) | each `scripts/collect_<name>.py` once on the droplet: all four `ok`, each within its budget, total < 120 s; output pasted into `sources-audit.md` |
| package | `--dry-run --package-from tests/fixtures/package/2026-10-04` plus the four collector fixtures: SECTION 8 present in `01_filtered.md`, the four blocks present in `00_raw_data.md`, all caps respected, `build_agent_package.py` exit code 0 |
| package fixture views and lens bytes | both fixture views are capped views (≤ 80 KB, `AGENT VIEW` header); `tests/lens_extras_probe.py` on each fixture day gives every `LENS_RAW` agent more than 0 bytes and at most 6,000 B, no added line in the view, no line to two agents (§4.5b) |

---

## 6. Cost

No LLM calls. HTTP per day, measured 2026-10-04: 19 (Polymarket) + 73 (Kalshi) + 15 (changelogs) +
2 (ZDNet, up to 6 with retries) ≈ 110 requests, ~4 MB on the wire (~40 MB decompressed), 17.7 s wall
for the four in parallel, inside the rest of collection.

What the agents pay: the view grows ~27 KB (PREDICTION MARKETS ~20 KB, NEWS +5 KB, AI & TOOLS
+2 KB) ≈ 7.5 K tokens per call that reads the shared block (triage + takes = 10 calls). The block
is byte-identical across them, so after the first call it is mostly cached: ~75 K input tokens a
day, ~68 K of them cached. The synthesizer's `AI_RAW` and `KR_RAW` grow by up to 12 KB ≈ 3.4 K
tokens on one call.

---

## 7. Build order and effort

1. `collector_common.py` with tests (HTTP, time, RSS/Atom parsing, freshness, atomic write, status) — 0.25 day.
2. `collect_changelogs.py` and `collect_zdnet_kr.py` (testable from any machine) — 0.25 day.
3. `collect_kalshi.py` (testable from Korea) — 0.25 day.
4. `collect_polymarket.py` (fixtures recorded on the droplet; live only from the droplet) — 0.25 day.
5. Wiring (§4) and one live collection on the droplet with `RECON_RUN_DIR` set to a tagged run
   folder, so the daily run is untouched — 0.25 day.

About 1.25 days of Claude time, no model quota. It does not depend on Phase C or D and can be
built in parallel with them; Phase C's `LENS_RAW` entries for these sources are inert until the
files exist.

---

## 8. Later (not in this spec)

- Polymarket ↔ Kalshi same-question matching (Lever desk).
- arXiv cs.AI / cs.CL RSS (in `01-plan.md` §4; an InnovLabs-desk source, not a tool changelog).
- 디지털애셋 김치 프리미엄 line as a Korea market number (the feed itself is read from day one, §3.4).
- Freshness rule for the v1 sources: `RECON_FRESH_V1=1` after the first week of daily runs shows no
  false `SOURCE STALE` line (§2).

---

## 9. Decisions made in this spec

| # | Decision |
|---|---|
| 1 | Collectors are Python stdlib only, `scripts/collect_<name>.py` plus `scripts/collector_common.py`, each runnable alone from any directory |
| 2 | v1 layout kept; header stamps in real UTC; one item per line with its numbers and URL |
| 3 | 72 h freshness at item, file and run-record level; a failed run never overwrites a good file |
| 4 | Polymarket runs only on the droplet (Korea is geo-blocked, HTTP 451); it replaces the on-chain collector's Polymarket block |
| 5 | Kalshi walks each category's series in volume order and keeps the first 5 with an open event (≤ 12 probes), plus a desk watchlist fetched first, instead of scanning 13,760 events; 4 parallel workers |
| 6 | The 15-minute and hourly series are excluded from the category picks |
| 7 | Changelogs come from the GitHub REST releases API (the `prerelease` flag decides) plus Cursor, GitHub Changelog (Copilot only) and Google AI RSS |
| 8 | The Korean addition is ZDNet Korea (feedburner) plus 디지털애셋 for 가상자산, both read every run |
| 9 | New package SECTION 8: PREDICTION MARKETS; changelogs into AI & TOOLS; ZDNet into NEWS; view ~92 KB (caps from measured sizes, §4.4) |
| 10 | Built in parallel with Phase C/D; no quota needed |
| 11 | Every Polymarket `/events` call excludes tag 1 (Sports); `/markets` is not used |
| 12 | The collectors land standalone; wiring (§4) is Phase B's commit |
| 13 | `LENS_RAW` is corrected here (§4.5b) because Phase C's table was built on headings the raw file lacks |

---

## 10. Review fixes folded in (2026-10-04)

| Where | Fix |
|---|---|
| Phase C §3 `LENS_RAW` → §4.5b | rebuilt from the real headings, raw file then package, minus lines already in `01_filtered.md`; < 2 KB warning; replay pass-bar item; unit test on both fixture days |
| §3.2 Kalshi selection | probe series in volume order, keep those with an open event (5 kept / 12 probes), watchlist first |
| §3.1 Polymarket budget | `exclude_tag_id=1` on every `/events` call (20 MB → 4.5 MB per page) |
| §3.1 NEW MARKETS | `start_date_min` + volume order; `recurring`/`up-or-down`/`hide-from-new` dropped |
| §3.3 Changelogs | GitHub REST releases API with the `prerelease` flag; anchored case-insensitive fallback regex, never on prose feeds; Cline SDK/Desktop excluded; new Google AI URL |
| §4.1a, §4.5 | raw loop gains the four sources; `--skip-collect` writes `00_raw_data.md` |
| §3.2 Kalshi timing | `ThreadPoolExecutor(4)`, `urlencode`, 110 requests / 90 s / 40 MB |
| §3.2 ladders | nearest daily event closing ≥ 12 h after collection; `close_time ≤ now` dropped |
| §3.1 RESOLVING | `/events` with end-date bounds and `exclude_tag_id=1` instead of `/markets` |
| §4.5a | changelogs → `AI_RAW`, ZDNet → `KR_RAW`, from the run folder's raw file |
| §4.6 | `TITLE_SOURCES` key `prediction` |
| §3.1 fields | points = 100 × fraction; nulls; CLOB 404 skip; order-free depth |
| §2 vs §8 | v1 freshness behind `RECON_FRESH_V1=0`, flipped after a week |
| §5 (and Phase C §17) | committed package fixtures for 2026-09-11 and 2026-10-04 |
| §3.4 ZDNet | Latin keywords on word boundaries; 디지털애셋 read every run |
| §1.1, §4.1 | scripts layout, run from `RECON_HOME`, output paths from `RECON_HOME` |

### 10.1 Second review (2026-10-04, after the first live pull)

| Where | Fix |
|---|---|
| §3.1, §3.4 | Output was 3–4× the estimates (Polymarket 19,102 B, ZDNet 9,228 B). Trimmed at the source: BY TOPIC 2 events a topic (15,932 B), ZDNet descriptions 60 characters (6,465 B) |
| §3, §6 | Size, request, time and cost figures are the measured 2026-10-04 numbers |
| §4.4, §4.5 | Caps re-derived by running `fair_share` on the measured files: PREDICTION MARKETS 20,000, NEWS 18,000, AI & TOOLS 9,000; view ~92 KB; the orchestrator reads up to 100 KB |
| §4.5a | `AI_RAW` changelogs ≤ 5,000 B, `KR_RAW` ZDNet ≤ 7,000 B (3,000 cut both) |
| §3.4, §1.4 | ZDNet: a budget per feed (3 requests, 20 s, 15 s per attempt), so ZDNet's retries or a timeout never leave 디지털애셋 unread; tested for both cases |
| §5 | Tests, recorded endpoint fixtures (droplet) and the 09-11 / 10-04 package fixtures committed; `sources-audit.md` has the live-smoke numbers |

### 10.2 Third review (2026-10-04, measured lens bytes)

| Where | Fix |
|---|---|
| §4.5b (= Phase C §3 item 3) | Measured with the spec's rules on the droplet's real packages, the corrected table gave trader, builder and analyst 0 bytes on 10-04: every on-chain, AI & Tools and World Monitor line is already in the view, and with the §4.4 caps so are the changelogs and Polymarket BOOK DEPTH / RESOLVING. Re-picked from what the views leave out (fundraising for analyst, `news~` filters per lens, AI EDUCATION for user_agent, Reddit ECONOMICS / POLITICS for macro); view subtraction matches re-rendered X lines and URLs; one lens per line in table order; per-agent bytes recorded for 09-10, 09-11, 10-04 and 10-04 + Phase E (9/9 agents ≥ 2 KB on 09-11 and 10-04); reference implementation `tests/lens_extras_probe.py` |
| §4.5b, Phase C §0.1 | §4.1a cannot help the spread probe or the replays: they copy each day's `00_*.md` as collected, and those days predate the Phase E files. The table works without them; the Phase E entries fill from the first package collected after the wiring |
| §5 | 09-11 fixture view regenerated with the current `build_agent_package.py` on the droplet (63,240 B; was the old uncapped 169 KB view), 10-04 fixture view replaced by the run's untrimmed view; new fixture test for capped views and lens bytes |

Path: `docs/v2/phase-e-collectors-spec.md`.
