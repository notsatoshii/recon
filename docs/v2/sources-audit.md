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
