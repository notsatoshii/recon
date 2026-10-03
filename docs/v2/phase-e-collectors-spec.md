# RECON v2 — Phase E build spec, collector half: four keyless collectors

Date: 2026-10-04. Status: build spec. Source plan: `06-improvement-plan.md` §4 item 10 and §7
row E; `01-plan.md` §4 (desk sources); `sources-audit.md` (2026-09-10 actions);
`04-implementation-plan.md` §2.3 (`SourceResult`). The other half of Phase E (the Lever and
InnovLabs desks, the three new personas, Reddit/X follow-ups) is not in this file.

Every endpoint below was called on 2026-10-04 (from Eric's desktop in Korea, and from the droplet
for Polymarket) and the result is recorded next to it. Eric's instruction: decide, do not ask;
decisions are listed in §9.

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
recon/collectors/
  __init__.py
  common.py        http_json, http_text, parse_rss, parse_atom, to_utc, write_latest, write_status,
                   fresh, SourceResult, Budget
  polymarket.py    collect() -> SourceResult
  kalshi.py        collect() -> SourceResult
  changelogs.py    collect() -> SourceResult
  zdnet_kr.py      collect() -> SourceResult
  run.py           python3 -m recon.collectors.run [names...] — runs them in parallel, prints one line each
config/
  changelog_sources.json     the tracked repos and feeds (§3.3)
  kalshi_series.json         the watchlist series (§3.2)
```

Each module also runs alone: `python3 -m recon.collectors.kalshi` (writes the file, prints the
status line, exit 0 when `ok`, 1 otherwise).

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
   A shared helper `recon/collectors/common.py:fresh(path, now) -> (ok, age_h, stamp)` is used by
   both the bash assembly (via `python3 -m recon.collectors.common --fresh <path>`) and Python.
3. **Run record (`export.source_record`).** A source whose stamp is older than 72 h at the run's
   finish gets `ok: false, error: "stale (74 h)"`, so the RUBRIC page's source health shows it.

The rule applies to the four new collectors from day one. Applying it to the v1 sources (Reddit,
X, news, on-chain …) is a one-line change in the same assembly helper and is switched on at the
same time; X already has its own 72 h filter (`RECON_TWITTER_MAX_AGE_H`) and keeps it.

---

## 3. The collectors

### 3.1 Polymarket — `data-sources/polymarket/latest.md`

**Endpoints** (public, no key), base `https://gamma-api.polymarket.com` and
`https://clob.polymarket.com`:

| Call | Verified 2026-10-04 (from the droplet) |
|---|---|
| `GET /events?closed=false&order=volume24hr&ascending=false&limit=50&offset=0` | 200; event fields include `title`, `slug`, `volume24hr`, `volume`, `liquidity`, `openInterest`, `endDate`, `tags[]`, `markets[]` |
| `GET /events?tag_slug=<slug>&closed=false&order=volume24hr&ascending=false&limit=8` | 200 for `crypto`, `economy`, `fed-rates`, `politics`, `geopolitics`, `ai`, `tech` |
| `GET /events?closed=false&order=createdAt&ascending=false&limit=50` | 200 (new markets) |
| `GET /markets?closed=false&end_date_min=<now>&end_date_max=<now+7d>&order=volume24hr&ascending=false&limit=100` | 200 (resolution calendar; sports dominate, filtered out below) |
| market fields used | `question`, `outcomes`, `outcomePrices`, `oneDayPriceChange`, `oneWeekPriceChange`, `volume24hr`, `liquidityNum`, `bestBid`, `bestAsk`, `spread`, `endDate`, `clobTokenIds`, `slug` |
| `GET https://clob.polymarket.com/book?token_id=<clobTokenIds[0]>` | 200; `bids[]`, `asks[]` (`price`, `size` strings), `tick_size`, `last_trade_price` |
| page size | `limit` above 100 is silently capped at 100; page with `offset` |
| **from Eric's desktop (Korea)** | **HTTP 451, Cloudflare error 1026: Polymarket geo-blocks Korean IPs.** The collector runs on the droplet only (it already does for the v1 block); tests use recorded fixtures. |

**Sports filter.** An event is dropped when any tag slug is in
`{sports, esports, games, soccer, football, basketball, baseball, hockey, tennis, mma, golf, cricket,
nfl, nba, mlb, nhl}`. (The top event by 24 h volume on 2026-10-04 was a Dota 2 match tagged
`esports, dota-2, games, sports`.)

**Sections and selection** (each `- ` line ends with `https://polymarket.com/event/<event slug>`):

| Section | Rule | Lines |
|---|---|---|
| `## TOP EVENTS BY 24H VOLUME (sports excluded)` | first 100 events by `volume24hr` (2 pages), filter, top 12 | `- <title> — 24h vol $X \| total vol $Y \| liq $Z \| OI $W \| ends YYYY-MM-DD \| leading: "<outcome or groupItemTitle>" YES n% (1d ±a pts) …up to 3 \| <url>` |
| `## BY TOPIC` | `tag_slug` in crypto, fed-rates, economy, politics, geopolitics, ai, tech; top 4 each by 24 h volume, events already listed above skipped | same line, prefixed `[crypto]` etc. |
| `## 24H MOVERS` | all markets of the events fetched above with `liquidityNum ≥ 25,000` and `volume24hr ≥ 10,000`; sorted by \|`oneDayPriceChange`\|; ≥ 5 points; top 10 | `- [topic] <question> YES n% (1d ±a pts, 1w ±b pts) \| 24h vol $X \| liq $Y \| <url>` |
| `## NEW MARKETS (created in the last 48 h)` | `order=createdAt` page, filter sports, `createdAt` within 48 h, `volume24hr ≥ 5,000`; top 8 | `- <title> — created YYYY-MM-DD HH:MM UTC \| 24h vol $X \| liq $Y \| ends … \| <url>` |
| `## RESOLVING IN THE NEXT 7 DAYS` | the `end_date_min/max` call, filter sports, top 10 by 24 h volume | `- <question> — ends YYYY-MM-DD HH:MM UTC \| YES n% \| 24h vol $X \| liq $Y \| <url>` |
| `## BOOK DEPTH (top non-sports markets)` | the leading market of the top 8 events; CLOB book for its YES token | `- <question>: mid n.n¢ \| spread n.n¢ \| depth within 2¢: $B bid / $A ask \| within 5¢: $B5 / $A5 \| 24h vol ÷ liquidity r.r \| <url>` |

Depth in dollars = Σ `size × price` over levels within ±2¢ (±5¢) of the mid. The volume ÷
liquidity ratio is the structural point the 09-11 debate raised and the brief dropped
($1.63 M volume against $238 K liquidity, F14): it is now one quotable line.

**Budget**: ≤ 30 requests (2 + 7 + 1 + 1 + 8 + 1 spare), ≤ 45 s, ≤ 25 MB read (single events can be
~170 KB; a 50-event page several MB). **Size**: ~5–7 KB of output on a normal day.

**Failure modes**: 451 or 403 (wrong host) → `ok: false`, previous file kept. `outcomePrices`
and `clobTokenIds` arrive as JSON strings inside JSON (`"[\"0.87\", \"0.13\"]"`): decode both;
skip a market whose decode fails, never the run.

### 3.2 Kalshi — `data-sources/kalshi/latest.md`

**Endpoints** (public market data, no key), base `https://api.elections.kalshi.com/trade-api/v2`
(the host serves every category, not only elections):

| Call | Verified 2026-10-04 (from Korea; Kalshi does not block) |
|---|---|
| `GET /series?category=<C>&include_volume=true` | 200, ~1–2 s each; Economics 889 series, Crypto 276, Financials 1,129, Politics 2,523, Companies 429, Science and Technology 429; each with `ticker`, `title`, `frequency`, `volume_fp` (lifetime) |
| `GET /events?series_ticker=<T>&status=open&with_nested_markets=true&limit=10` | 200; e.g. `KXBTCD`: "BTC price on Oct 4, 2026 at 5pm EDT?" with 80 strike markets |
| market fields used | `ticker`, `title`, `yes_sub_title`, `yes_bid_dollars`, `yes_ask_dollars`, `last_price_dollars`, `previous_price_dollars`, `volume_24h_fp`, `volume_fp`, `open_interest_fp`, `close_time`, `floor_strike`/`cap_strike`, `status` (prices are dollar strings, volumes fixed-point strings) |
| why not scan all events | `GET /events?status=open&limit=200` paged to the end = 69 pages, 13,760 events, 54 s without nested markets (half are sports). Too slow and the wrong ranking; the series list ranks by volume in 7 calls. |

**Selection**:

1. For each category in `Economics, Financials, Crypto, Politics, Science and Technology,
   Companies, World`, take the series list with volume, drop `frequency` in
   `{fifteen_min, hourly}` (values seen: annual, custom, one_off, monthly, daily, weekly, hourly,
   fifteen_min, quarterly) and tickers matching `15M|1H$` (the BTC 15-minute series has the
   largest volume of all and says nothing for a daily brief), keep the top 5 by `volume_fp`.
2. Add the watchlist `config/kalshi_series.json` (desk-chosen, deduped, always kept whatever
   their frequency): `KXFEDDECISION`, `KXFED`, `KXRATECUTCOUNT`, `KXCPI`, `KXPAYROLLS`, `KXBTCD`,
   `KXETHD`, `KXBTCMAXY`, `KXLLM1`, `KXTOPMODEL` (each checked 2026-10-04 with `GET /series/<T>`
   and ≥ 1 open event; `KXBTCD`/`KXETHD` are `hourly` series, which is why the frequency filter in
   step 1 does not apply to the watchlist; `KXFEDCHAIRNOM` and `KXGOVSHUTLENGTH` exist but had no
   open events and are left out). Unknown tickers or series without open events are logged and
   skipped, so the file can be edited without code changes.
3. For each series, its open events with nested markets (≤ 3 events per series, nearest
   `close_time` first). ~45 series → ~45 calls.

Probability of a market = mid of `yes_bid`/`yes_ask` when both exist and the spread ≤ 10¢, else
`last_price`. 24 h change = `last_price − previous_price` in points. **Build-time check**: confirm
on the first live pull that `previous_price_dollars` is the price 24 h earlier (Kalshi documents
it as the previous day's price); if not, the movers section uses the change since the last pull,
read from `data-sources/kalshi/prev.json` (written each run).

**Sections** (lines end with `https://kalshi.com/markets/<series ticker, lower case>`):

| Section | Rule |
|---|---|
| `## TOP EVENTS BY 24H VOLUME` | events from step 3 ranked by Σ `volume_24h_fp`; top 12; line: `- [Category] <event title> — 24h vol N contracts \| OI N \| closes YYYY-MM-DD \| top: "<yes_sub_title>" n% (24h ±a pts) …up to 3 \| <url>` |
| `## 24H MOVERS` | markets with `volume_24h_fp ≥ 500` and \|change\| ≥ 5 points; top 10 |
| `## CLOSING IN THE NEXT 7 DAYS` | events with `close_time` within 7 days; top 10 by 24 h volume |
| `## CRYPTO PRICE LADDERS` | for `KXBTCD` and `KXETHD`, the nearest daily (5 pm EDT) expiry, not the hourly ones: the strike where the YES probability crosses 50 % (linear interpolation between the two neighbouring strikes), plus the 25 % and 75 % strikes: `- BTC on 2026-10-04 17:00 EDT: market-implied median $X (25–75 %: $A–$B), from N strikes \| <url>` |
| `## MACRO` | `KXFEDDECISION` and `KXFED` nearest meeting: each outcome with its probability on one line |

**Budget**: ≤ 60 requests, ≤ 60 s, pace 0.15 s (Kalshi's public read limit is far above this).
**Size**: ~4–6 KB.

**Not in this collector**: matching the same question across Polymarket and Kalshi. Titles do not
line up and a wrong match is worse than none; it is a Lever-desk feature (desk half of Phase E).

### 3.3 Changelogs — `data-sources/changelogs/latest.md`

Header `# Changelogs Intelligence`. Release notes for the AI tools the brief tracks. The news
collector's Google News queries name OpenAI, Anthropic, Google DeepMind, Meta AI, Mistral, xAI,
Codex, Claude Code, Cursor and Copilot (`collect_data.sh`, `## AI NEWSLETTER SOURCES`), so the
list starts there.

**Sources** (`config/changelog_sources.json`; every entry verified 2026-10-04):

| Source | URL | Verified |
|---|---|---|
| Codex CLI | `https://github.com/openai/codex/releases.atom` | 200, 10 entries, latest 2026-10-03 (`rust-v0.162.0-alpha.11`) |
| Claude Code | `https://github.com/anthropics/claude-code/releases.atom` | 200, latest 2026-10-02 (`v2.1.288`); entry content holds the release notes |
| Gemini CLI | `https://github.com/google-gemini/gemini-cli/releases.atom` | 200, latest 2026-10-03 (nightly) |
| GitHub Copilot CLI | `https://github.com/github/copilot-cli/releases.atom` | 200, latest 2026-10-02 |
| OpenAI Agents SDK (Python) | `https://github.com/openai/openai-agents-python/releases.atom` | 200, latest 2026-10-02 |
| Claude Agent SDK (Python) | `https://github.com/anthropics/claude-agent-sdk-python/releases.atom` | 200, latest 2026-09-30 |
| Anthropic SDK (Python) | `https://github.com/anthropics/anthropic-sdk-python/releases.atom` | 200, latest 2026-09-30 |
| OpenAI SDK (Python) | `https://github.com/openai/openai-python/releases.atom` | 200, latest 2026-10-02 |
| Model Context Protocol spec | `https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom` | 200, latest 2026-07-28 |
| Ollama | `https://github.com/ollama/ollama/releases.atom` | 200, latest 2026-10-02 |
| vLLM | `https://github.com/vllm-project/vllm/releases.atom` | 200, latest 2026-10-02 |
| Cline | `https://github.com/cline/cline/releases.atom` | 200, latest 2026-10-02 (many `sdk/…` sub-releases: filtered, below) |
| Cursor changelog | `https://cursor.com/changelog/rss.xml` | 200, RSS, 50 items, latest 2026-09-23 |
| GitHub Changelog (Copilot only) | `https://github.blog/changelog/feed/` | 200, RSS, 10 items; kept when title or category contains "Copilot" |
| Google AI blog | `https://blog.google/technology/ai/rss/` | 200, RSS |

Checked and left out: Anthropic has no RSS (`/rss.xml` 404; the docs release-notes URL returns
HTML); its product changes arrive through the Claude Code and SDK feeds and Google News.
`raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md` works but is 900 KB.
`langchain-ai/langchain` publishes a release per sub-package (noise). `Aider-AI/aider` (last
release 2026-02) and `continuedev/continue` (2026-06) are stale. OpenAI News RSS is already in the
news collector (no duplicate).

**Rules**:

- Window: entries updated within 72 h (§2). Entries are matched against
  `data-sources/changelogs/seen.json` (ids already written); first appearances are marked `new`.
- Pre-releases dropped by title: `alpha|beta|rc\d*|nightly|preview|canary|dev|-pre`; per-source
  `include`/`exclude` regexes in the JSON (Cline: exclude `^sdk/`; GitHub Changelog: include
  `Copilot`).
- One line per source: the newest stable release in the window, plus `(+N earlier releases in
  72 h)` when there are more.
- Release notes: the entry's HTML content, tags stripped, the first three bullet points or
  sentences joined with `; `, cut to 280 characters.
- Line: `- [YYYY-MM-DD HH:MM UTC] <Product> <version> (new): <notes> | <release url>`.
- Sources with nothing in the window are summarised in one last line:
  `- No release in the last 72 h: MCP spec, Cursor, …` (so a quiet day still writes a valid file).
- Section order: `## CODING AGENTS & CLIS` (Codex, Claude Code, Gemini CLI, Copilot CLI, Cursor,
  Cline, GitHub Changelog), `## SDKS & PROTOCOLS` (Agents SDK, Claude Agent SDK, Anthropic SDK,
  OpenAI SDK, MCP), `## MODEL SERVING` (Ollama, vLLM), `## VENDOR BLOGS` (Google AI).

**Budget**: 15 requests, ≤ 40 s, 4 in parallel. github.com Atom feeds are not the REST API and
do not use its 60-per-hour anonymous quota. **Size**: ~2–3 KB.

### 3.4 Korean source: ZDNet Korea — `data-sources/zdnet_kr/latest.md`

Header `# ZDNet Korea Intelligence`. The news collector already reads AI타임스 and 전자신문
(KOREA — AI) and 블록미디어 and 토큰포스트 (KOREA — CRYPTO & MARKETS). ZDNet Korea covers both
Korean AI and 가상자산 policy and industry, with proper timezones and categories.

| Candidate | Result 2026-10-04 |
|---|---|
| **ZDNet Korea** `https://feeds.feedburner.com/zdkorea` | **chosen**: 200, RSS 2.0, 30 items, `pubDate` RFC 822 with `+0900`, `<category>` tags, full-text `description`; robots.txt allows `/` |
| 디지털애셋 `https://www.digitalasset.works/rss/allArticle.xml` | 200, 50 items, crypto only, includes a daily 김치 프리미엄 by exchange; naive `pubDate` (KST). **Fallback** if the feedburner feed dies, and the next addition if KOREA crypto runs thin |
| 바이라인네트워크 `https://byline.network/feed/` | 200, 20 items, IT/AI |
| 한국경제 IT `https://www.hankyung.com/feed/it`, 매일경제 `https://www.mk.co.kr/rss/50300009/`, 연합뉴스 경제 | 200 but general-interest; more noise per useful item |
| `zdnet.co.kr/rss/newsall.xml`, decenter.kr, coinreaders, ddaily (HTML, no items) | 404 or not a feed |

**Rules**: items within 72 h; an item is kept when its title, categories or the first 300
characters of its description match one of two keyword sets, and tagged by the set it matched:

- `[AI]`: `AI`, `인공지능`, `생성형`, `LLM`, `에이전트`, `GPU`, `데이터센터`, `오픈AI`, `앤트로픽`,
  `챗GPT`, `클로드`, `제미나이`, `AI 기본법`, `AI 교육`;
- `[가상자산]`: `가상자산`, `암호화폐`, `비트코인`, `이더리움`, `블록체인`, `스테이블코인`, `거래소`,
  `업비트`, `빗썸`, `토큰증권`, `STO`, `디지털자산`.

Line: `- [YYYY-MM-DD HH:MM UTC] [AI] <title> | <link>`, with up to 120 characters of the description
appended after ` — ` when it adds information (not when it repeats the title). Max 12 `[AI]` and 8
`[가상자산]` items, newest first, under `## AI` and `## 가상자산·블록체인`. Titles stay in Korean (the
KOREA section is written in English from them, as today).

**Budget**: 1 request, ≤ 15 s. **Size**: ~2–3 KB.

---

## 4. Wiring into the package (same commit as the collectors)

1. **Collection.** `collect_data.sh` runs `python3 -m recon.collectors.run polymarket kalshi
   changelogs zdnet_kr` in the background right after it starts Reddit and X (Phase A runs those
   side by side), with a 120 s total timeout, and waits for it before assembling the package. Each
   collector logs `  Kalshi: ok, 41 items, 52 requests, 23.4 s` into the day log. Collection time
   grows by ~0 s (the slowest new collector, ~60 s, is far shorter than Reddit's pacing).
2. **Package sections.**
   - New `# SECTION 8: PREDICTION MARKETS`: Polymarket, then Kalshi.
   - Changelogs go into `# SECTION 6: AI & TOOLS` after the GitHub/HN block.
   - ZDNet Korea goes into `# SECTION 4: NEWS INTELLIGENCE` after the news file.
   - Every file passes the §2 freshness check first.
3. **Remove the duplicate.** The `## POLYMARKET LIVE MARKETS` block in the on-chain collector is
   deleted (the DeFiLlama `## PREDICTION MARKET PROTOCOLS` block stays: TVL and fees are different
   data).
4. **Agent view caps** (`scripts/build_agent_package.py` `CAPS`): `PREDICTION MARKETS` 7,000 (new),
   `AI & TOOLS` 6,000 → 8,000, `NEWS INTELLIGENCE` 13,000 → 14,000. `FALLBACK_NAMES` gains
   `polymarket`, `kalshi` → PREDICTION MARKETS, `changelogs` → AI & TOOLS, `zdnet korea` → NEWS
   INTELLIGENCE. The view grows from ~65 KB to ~75 KB.
5. **Orchestrator.** `shared()` lists `SECTION 8 (PREDICTION MARKETS)` in its header and reads the
   view up to 90 KB (was 80 KB); the `--skip-collect` assembly list gains the four names.
   `LENS_RAW` (Phase C §3) already names `# Changelogs Intelligence`; add `# Polymarket
   Intelligence` and `# Kalshi Intelligence` for trader and analyst, `# ZDNet Korea Intelligence`
   for policy_analyst and user_agent.
6. **Run record.** `export.LAYER` gains `polymarket`, `kalshi`, `changelogs`, `zdnet_kr`;
   `SECTION_LAYER` gains `("polymarket", "polymarket"), ("kalshi", "kalshi"), ("changelogs",
   "changelogs"), ("zdnet korea", "zdnet_kr")` (none of the existing substring keys matches these
   headers, so order does not matter); `TITLE_SOURCES` (added in `f11607c`) gains
   `("prediction markets", ["polymarket", "kalshi"])`. `section_sources()` then attributes SECTION 8
   to Polymarket and Kalshi and the new blocks inside SECTIONS 4 and 6 to `zdnet_kr` and
   `changelogs` by their `# <Name> Intelligence` headers, which is how the RUBRIC recon page links
   sources to sections; checked on the page when Phase F starts.
7. **Archive.** `ph_deliver` copies the four new `latest.md` files into `archive/<date>/`.
8. **`.gitignore`**: `data-sources/*/status.json`, `data-sources/*/seen.json`,
   `data-sources/*/prev.json`.
9. **`sources-audit.md`**: one row per new source after the first live pull.

---

## 5. Test plan

All `unittest`, no network, in `tests/collectors/`.

| Test | What |
|---|---|
| fixtures | One recorded response per endpoint in `tests/fixtures/collectors/` (Polymarket recorded on the droplet: events page, tag page, markets calendar, one CLOB book; Kalshi: one series list, `KXBTCD` and `KXFEDDECISION` events; three Atom feeds incl. Claude Code; Cursor and GitHub Changelog RSS; ZDNet Korea RSS). Trimmed to ≤ 200 KB each. `common.http_*` reads fixtures when `RECON_COLLECTOR_FIXTURES=<dir>` is set. |
| header | every collector's output matches `^# .+ Intelligence\n## \d{4}-\d\d-\d\d \d\d:\d\d UTC\n` and the stamp equals a frozen `now` given in UTC (a test sets `now` to 23:30 UTC, when Seoul is already the next day, to catch a local-time stamp) |
| items | every non-heading, non-blank line starts with `- `; no line over 600 bytes; each item line has a URL |
| export compatibility | `export.source_record(name, layer, text, finished)` returns `ok: true`, the right item count and `fetched_at` equal to the stamp converted to KST (the Seoul-time heuristic not triggered) |
| quote round trip | 20 random substrings (≤ 200 chars) of item lines verify with `evidence.Corpus(...).verify_quote` after the file goes through `build_agent_package.py` |
| freshness | items dated 73 h before `now` dropped and counted; a file stamped 73 h old renders the `SOURCE STALE` line in the package; `source_record` reports `stale (73 h)` |
| failure keeps file | a fixture HTTP 451 / 500 / timeout: `latest.md` unchanged (byte-identical), `status.json` has `ok: false`, the error and the old `last_good_at`; exit code 1 |
| Polymarket | sports filter (the Dota 2 event is dropped); `outcomePrices` string decoding; depth arithmetic on a hand-made book (±2¢, ±5¢); movers threshold; 48 h new-market window |
| Kalshi | 15-minute and hourly series excluded from the volume picks but `KXBTCD` kept from the watchlist; watchlist merge and dedupe; nearest 5 pm EDT event chosen over hourly ones; mid vs last price rule; ladder interpolation on a 5-strike fixture (median between strikes) |
| changelogs | pre-release filter (`rust-v0.162.0-alpha.11` dropped, `v2.1.288` kept); Cline `sdk/` exclusion; `(+N earlier releases)` count; `new` marking with `seen.json`; quiet-day summary line |
| ZDNet Korea | `+0900` → UTC; keyword tagging in both sets; an item matching both is tagged `[AI]` once and listed under AI; description not appended when it repeats the title |
| budget | a fixture server that answers slowly: the collector stops at its request or time budget and still writes what it has |
| live smoke (droplet, not CI) | `python3 -m recon.collectors.run` on the droplet: all four `ok`, each within its budget, total < 120 s; output pasted into `sources-audit.md` |
| package | `--dry-run --package-from` a run folder containing the four files: SECTION 8 present in `01_filtered.md`, all caps respected, `build_agent_package.py` exit code 0 |

---

## 6. Cost

No LLM calls. HTTP per day: ~30 (Polymarket) + ~55 (Kalshi) + 15 (changelogs) + 1 (ZDNet) ≈ 100
requests, ~10–30 MB read, ~60 s wall in parallel with the rest of collection.

What the agents pay: the view grows ~10 KB ≈ 2.8 K tokens per call that reads the shared block
(triage + takes = 10 calls). The block is byte-identical across them, so after the first call it is
mostly cached: ~28 K input tokens a day, ~25 K of them cached.

---

## 7. Build order and effort

1. `common.py` with tests (HTTP, time, RSS/Atom parsing, freshness, atomic write, status) — 0.25 day.
2. `changelogs.py` and `zdnet_kr.py` (feeds; testable from any machine) — 0.25 day.
3. `kalshi.py` (testable from Korea) — 0.25 day.
4. `polymarket.py` (fixtures recorded on the droplet; live only from the droplet) — 0.25 day.
5. Wiring (§4) and one live collection on the droplet with `RECON_RUN_DIR` set to a tagged run
   folder, so the daily run is untouched — 0.25 day.

About 1.25 days of Claude time, no model quota. It does not depend on Phase C or D and can be
built in parallel with them; Phase C's `LENS_RAW` entries for these sources are inert until the
files exist.

---

## 8. Later (not in this spec)

- Polymarket ↔ Kalshi same-question matching (Lever desk).
- arXiv cs.AI / cs.CL RSS (in `01-plan.md` §4; an InnovLabs-desk source, not a tool changelog).
- 디지털애셋 김치 프리미엄 line as a Korea market number.
- Freshness rule switched on for the v1 sources (§2, one line, same day as this lands if the
  first week shows no false stales).

---

## 9. Decisions made in this spec

| # | Decision |
|---|---|
| 1 | Collectors are Python stdlib only, under `recon/collectors/`, one module each, runnable alone |
| 2 | v1 layout kept; header stamps in real UTC; one item per line with its numbers and URL |
| 3 | 72 h freshness at item, file and run-record level; a failed run never overwrites a good file |
| 4 | Polymarket runs only on the droplet (Korea is geo-blocked, HTTP 451); it replaces the on-chain collector's Polymarket block |
| 5 | Kalshi ranks series by volume per category plus a desk watchlist, instead of scanning 13,760 events |
| 6 | The 15-minute and hourly series are excluded |
| 7 | Changelogs come from GitHub release Atom feeds plus Cursor, GitHub Changelog (Copilot only) and Google AI RSS; pre-releases dropped |
| 8 | The Korean addition is ZDNet Korea (feedburner); 디지털애셋 is the verified fallback |
| 9 | New package SECTION 8: PREDICTION MARKETS; changelogs into AI & TOOLS; ZDNet into NEWS; view ~75 KB |
| 10 | Built in parallel with Phase C/D; no quota needed |

Path: `docs/v2/phase-e-collectors-spec.md`.
