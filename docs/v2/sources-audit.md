# Sources audit

Running record of what each collector actually returns. First pass 2026-09-10 (v1 collectors,
run unchanged from the v2 worktree on the droplet). Update on every audit run.

## 2026-09-10 — first fresh collection since June

Wall time 27 min 39 s (15:27:44 → 15:55:23 KST). Package: 61,066 bytes, 970 lines.

| Source | Result | Verdict | Action (Phase 3) |
|---|---|---|---|
| Reddit RSS (40 subs) | 8 of 40 returned; 32 rate-limited (HTTP 429), 15 s wait each → 8 min | **Degraded** | Fetch via `old.reddit.com` with a real UA, 2–3 s spacing, retry once; cache per sub for 6 h; parallelise with a small pool. Target: ≥35/40 in <2 min. |
| X via nitter.cz + Playwright (164 handles) | 6 of 164 accounts, 39 tweets, 17 min. nitter.cz redirects (302) at root; Cloudflare warm-up mostly fails | **Broken** | Try 2–3 other public instances with health check at start; if none pass, drop X from the package and rely on BettaFish/Reddit for social signal. Never spend >5 min on it. |
| RootData fundraising (Playwright) | Captcha; 0 rows | **Broken** | Replace with DeFiLlama raises API (`/raises`) which is free and structured; keep RootData link-only. |
| On-chain (DeFiLlama, CoinGecko, Polymarket gamma, Fear&Greed) | 265 lines in 8 s | OK | Extend Polymarket: new markets, 24 h movers, liquidity, resolution dates (Lever desk). Add Kalshi public API. |
| News RSS | 108 lines in 3 s | OK | Add vendor changelog feeds and Korean AI media (InnovLabs desk); verify each feed. |
| AI/Tools (HN + GitHub Trending) | 65 lines in 10 s | OK | Add arXiv cs.AI/cs.CL RSS. |
| World Monitor (Redis via docker exec) | 95 lines in 2 s despite container "unhealthy" | OK (optional) | Keep optional; freshness stamp from Redis key timestamps. |
| BettaFish sentiment | 127 lines; LLM call 37 s on FAST tier (20 K in / 1.7 K out tokens) | OK | Fine as is; move to the merged package digest later. |
| Dedup | 268 → 226 items, 42 merged, 21 cross-source | OK | Keep. |
| Subreddit/X discovery (background) | launched | not audited | Low priority. |

Notes
- The collector logs progress only through a `while read` pipe, so long steps look silent; v2
  collectors report per-source `{ok, items, seconds}` as they finish.
- Total collection time is dominated by two broken sources (Reddit waits + X timeouts): ~25 of
  28 minutes. Fixing those alone brings collection under 5 minutes.

## 2026-10-04 — Phase E collectors, first live pulls (droplet)

Live smoke (phase-e spec §5): the four collectors started in parallel from the repo root on the
droplet, as `collect_data.sh` will start them (§4.1), at 2026-10-03 20:55 UTC (05:55 KST), after
the second-review fixes (Polymarket BY TOPIC 2 a topic, ZDNet descriptions 60 characters, ZDNet
budget per feed). Wall time 17.7 s for all four; each within its budget.

```
  Changelogs: ok, 10 items, 15 requests, 0.8 s, 1.4 MB
  Kalshi: ok, 36 items, 73 requests, 17.5 s, 0.9 MB
  Polymarket: ok, 60 items, 19 requests, 3.2 s, 1.6 MB
  ZDNet Korea: ok, 20 items, 2 requests, 1.2 s, 0.1 MB
```

| Source | Result | Output | Verdict | Notes |
|---|---|---|---|---|
| Polymarket (Gamma + CLOB, sports excluded) | 60 items: TOP 12, BY TOPIC 12, MOVERS 10, NEW 8, RESOLVING 10, BOOK DEPTH 8 | 15,932 B (19,102 B at 05:43 KST with 4 events a topic) | OK | top 100 events, 107 in all; 9 of 50 new events kept after the recurring/up-or-down filter; books 8 of 8. Droplet only (HTTP 451 from Korea). |
| Kalshi (public market data) | 36 items: TOP 12, MOVERS 10, CLOSING 10, BTC/ETH ladders, MACRO 2 | 8,923 B | OK | 36 series kept, 55 events. World kept 0 of 12 probed series (the top World series by lifetime volume have no open event); Politics 3 of 12. The local recording saw an occasional 429 on a probe, retried. |
| Changelogs (GitHub REST + 3 RSS) | 10 items, 9 sources with a release in 72 h | 3,534 B | OK | quiet: Gemini CLI, Cursor, Claude Agent SDK, MCP spec, Ollama, vLLM. 15 of 60 anonymous GitHub calls an hour. 293 older entries dropped by the 72 h window. |
| ZDNet Korea + 디지털애셋 | 20 items: AI 12, 가상자산 8 | 6,465 B (9,228 B with 120-character descriptions) | OK | 14 AI and 45 가상자산 matches in 72 h; the 8 가상자산 lines shown all came from 디지털애셋. 2 items older than 72 h dropped. |

Output sizes were 3 to 4 times the first estimates; the agent-view caps were re-derived from
these numbers (phase-e spec §4.4, §10.1). The endpoint fixtures for the tests were recorded in the
same minute (`tests/record_collector_fixtures.py`, 107 responses, 1.8 MB after trimming).
