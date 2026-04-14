# RECON

**Autonomous multi-agent intelligence system.** 9 AI analysts independently analyze 500+ data sources across world events, financial markets, crypto, AI/ML, and fundraising — then debate each other in structured rounds before a synthesizer produces publishable intelligence briefs delivered via Telegram.

Three products. Zero API keys required for data collection. Free on Claude Max.

---

## What It Produces

### Daily Intelligence Brief
A 600-1000 word morning brief covering geopolitics, markets, crypto, and AI. Written like a senior analyst's report — no AI jargon, no agent references. Includes market sentiment quotes from Twitter/Reddit, a contrarian case, risk assessment, and a prediction scorecard that tracks accuracy over time.

### AI/Tools Digest (2-3x/week)
Curated AI/ML developments: GitHub trending repos with links, new model releases, infrastructure shifts, tools you can actually use. Written for practitioners, not hype watchers.

### Fundraising Radar (weekly)
Web3/crypto funding rounds scraped from [RootData](https://www.rootdata.com/Fundraising), combined with VC Twitter activity and market sentiment. Who raised, how much, from whom, and what it signals.

---

## How It Works

```
Data Collection (500+ sources, parallel)
    ├── Reddit RSS (40+ subreddits, no API key)
    ├── Twitter/X (400+ accounts via Playwright, no API key)
    ├── On-chain data (DeFiLlama, CoinGecko, Blockchain.info, Polymarket)
    ├── News RSS (CoinDesk, Decrypt, CoinTelegraph, Blockworks, +3 more)
    ├── AI/Tech (GitHub Trending, Hacker News, TechCrunch AI, Verge AI, Ars Technica)
    ├── Fundraising (RootData via Playwright scraping)
    ├── World Monitor (435+ geopolitical feeds via GDELT/Redis)
    └── BettaFish (Claude-powered sentiment analysis on all social data)
         │
    Deduplication ── cross-source signal detection
         │
    Historical Context ── 30-day knowledge DB lookback
         │
    9 Agents Analyze Independently (parallel)
         │
    Structured Debate
    ├── 5 tension pairs challenge each other
    ├── Wildcard cross-examination
    ├── Defend or concede with evidence
    └── Deep dive on unresolved disagreements
         │
    Agent Memory Update ── persistent memory + prediction tracking
         │
    Synthesis (Claude Opus, two-pass with hallucination filter)
         │
    Telegram Delivery (HTML formatted)
```

---

## Data Sources

RECON pulls from **500+ sources** across 8 collection layers. No API keys required for any data collection — everything uses RSS feeds, public APIs, and headless browser scraping.

| Layer | Sources | Method |
|-------|---------|--------|
| **Reddit** | 40+ subreddits (crypto, DeFi, AI, politics, macro, prediction markets) | RSS feeds |
| **Twitter/X** | 400+ accounts across 15 categories (trading, VCs, AI, geopolitics, regulation) | [Playwright](https://playwright.dev/) + [Nitter](https://github.com/zedeus/nitter) |
| **On-chain** | DeFiLlama (TVL, DEX volumes, fees, yields, stablecoins), CoinGecko (prices, trending), Blockchain.info (BTC network), Polymarket (prediction markets via [Gamma API](https://gamma-api.polymarket.com/)), AI token prices (RNDR, TAO, FET, etc.) | Public APIs |
| **News** | CoinDesk, Decrypt, CoinTelegraph, DeFiant, Blockworks, Unchained, CryptoSlate, TechCrunch AI, The Verge AI, Ars Technica | RSS feeds |
| **AI/Tools** | GitHub Trending (new + hot AI/ML repos), Hacker News (top stories filtered for AI/tech/crypto) | GitHub API + HN Firebase API |
| **Fundraising** | [RootData](https://www.rootdata.com/Fundraising) (recent rounds, amounts, investors, sectors) | Playwright scraping |
| **Geopolitics** | [World Monitor](https://github.com/koala73/worldmonitor) — 435+ feeds: GDELT events, conflicts, economic calendars, cyber threats, sanctions, disease outbreaks, Hormuz tracker, regional intelligence | Docker (4 containers, Redis-backed) |
| **Sentiment** | [BettaFish](https://github.com/666ghj/BettaFish) — multi-agent sentiment analysis across Reddit, Twitter, and news. Detects narratives, divergences, and controversy clusters. | Claude-powered (adapted from [666ghj/BettaFish](https://github.com/666ghj/BettaFish)) |

---

## The 9 Agents

Each agent has a distinct analytical lens, persistent memory that accumulates across runs, and a prediction log that gets scored for accuracy.

| Agent | Perspective |
|-------|-------------|
| **Trader** | Quantitative: price action, volume, risk/reward, positioning signals |
| **Narrator** | Social: narrative lifecycle, CT sentiment, timing, what's trending and why |
| **Builder** | Product: competitive landscape, shipping velocity, moats, technical feasibility |
| **Analyst** | Structural: models, TAM, trendlines, fundraising flows, sector rotation |
| **Skeptic** | Risk: fraud detection, what everyone ignores, pre-mortem analysis (always active) |
| **Policy Analyst** | Regulatory: multi-jurisdiction risk, enforcement patterns, compliance signals |
| **User Agent** | Ground-level: real trader UX, friction, opportunity cost, trust assessment |
| **Macro Strategist** | Macro: geopolitics, central banks, capital flows, cross-asset correlations |
| **AI Engineer** | Technical: model capabilities, inference costs, agentic patterns, what's real vs. hype |

Agents debate in structured tension pairs, challenge each other's assumptions, and must defend or concede with evidence. Concessions are tracked — when an agent changes their mind, that's signal.

---

## Intelligence Features

### Prediction Scoring
Agents make testable predictions with dates. `score_yesterday.py` scores them against outcomes each morning. Agents see their own track record before analyzing — creating a calibration feedback loop.

### Alert Monitor
Real-time threshold alerting between daily briefs (every 15 min via cron):
- Large BTC/ETH/SOL moves
- Fear & Greed extremes
- Stablecoin depegs
- Polymarket volume surges
- DeFi TVL crashes

### Knowledge Database
SQLite FTS5 database indexing every brief, agent take, and debate record. 30-day lookback provides historical continuity — agents can reference what they said last week and whether they were right.

### Hallucination Filter
Two-pass synthesis: the second pass cross-references every claim against the raw data package. Numbers from social media get attributed ("reportedly", "per @handle"). Fabricated statistics get flagged `[unverified]` or dropped.

### Agent Memory
Each agent accumulates memory across runs (~150 lines): active tracking items, predictions (never deleted until scored), recurring themes, and lessons learned (what they got wrong and why).

---

## Quick Start

```bash
git clone git@github.com:notsatoshii/recon.git && cd recon
./scripts/setup.sh
```

The setup wizard checks dependencies, installs what's missing, and walks you through Telegram configuration:

```
    ██████╗ ███████╗ ██████╗  ██████╗ ███╗   ██╗
    ██╔══██╗██╔════╝██╔════╝██╔═●══██╗████╗  ██║
    ██████╔╝█████╗  ██║     ██║   ██║██╔██╗ ██║
    ██╔══██╗██╔══╝  ██║     ██║   ██║██║╚██╗██║
    ██║  ██║███████╗╚██████╗ ╚█████╔╝██║ ╚████║
    ╚═╝  ╚═╝╚══════╝ ╚═════╝  ╚════╝ ╚═╝  ╚═══╝

    SYSTEM CHECK
    ✓ Python 3.12        ✓ Playwright
    ✓ Claude CLI          ✓ Chromium
    ✓ Docker              ○ Telegram (setup below)
```

After setup:
```bash
./scripts/run_recon.sh                           # Full run (~20 min)
./scripts/run_recon.sh --skip-collect            # Reuse existing data
./scripts/run_recon.sh --mode ai-digest          # AI Digest (~1 min)
./scripts/run_recon.sh --mode fundraising        # Fundraising Radar (~1 min)
```

### Optional: World Monitor (adds 435+ geopolitical sources)
```bash
git clone https://github.com/koala73/worldmonitor.git
cd worldmonitor && npm install && docker compose up -d --build
./scripts/run-seeders.sh
```

### Cron Schedule
```bash
# Morning Brief: 6:00 AM daily
0 6 * * * source ~/.recon.env && cd ~/recon && ./scripts/run_recon.sh >> logs/cron.log 2>&1

# AI Digest: Mon/Wed/Fri 7:00 AM
0 7 * * 1,3,5 source ~/.recon.env && cd ~/recon && ./scripts/run_recon.sh --mode ai-digest --skip-collect >> logs/ai-digest.log 2>&1

# Fundraising Radar: Monday 7:30 AM
30 7 * * 1 source ~/.recon.env && cd ~/recon && ./scripts/run_recon.sh --mode fundraising --skip-collect >> logs/fundraising.log 2>&1

# Alerts: every 15 minutes
*/15 * * * * source ~/.recon.env && cd ~/recon && ./scripts/alert_monitor.sh >> logs/alerts.log 2>&1

# World Monitor reseed: every 6 hours
0 */6 * * * cd ~/worldmonitor && bash scripts/run-seeders.sh >> logs/wm-seeders.log 2>&1
```

---

## Output Format

### Daily Brief
```
RECON DAILY BRIEF
├── What Happened (world → markets → crypto → AI)
├── What It Means (key insights with analysis)
├── Market Mood (real Twitter/Reddit quotes)
├── The Contrarian Case (strongest counter-argument)
├── Risks (probability + impact)
├── What To Watch (specific items with dates)
└── Scorecard (prior predictions scored: RIGHT/WRONG/PENDING)
```

### AI Digest
```
RECON AI DIGEST
├── Top Picks (2-3 most significant with analysis)
├── New Tools & Repos (with GitHub links)
├── Model Updates (releases, benchmarks, pricing)
├── Infrastructure (GPU costs, serving, deployment)
└── What It Means (trends + what to build)
```

### Fundraising Radar
```
RECON FUNDRAISING RADAR
├── Biggest Rounds (project, amount, investors, sector)
├── VC Activity (who's deploying, who's quiet)
├── Sector Trends (hot vs cooling categories)
├── Fundraising Climate (valuations, timelines)
└── Signals (what smart money tells us about 3-6 months out)
```

---

## Architecture

```
recon/
├── personas/                  # 10 agent identity files + 3 synthesizer variants
├── scripts/
│   ├── run_recon.sh           # Main orchestration (7-phase debate + mode system)
│   ├── collect_data.sh        # Data collection pipeline (8 layers)
│   ├── ask_hermes.sh          # LLM interface (Claude CLI)
│   ├── score_yesterday.py     # Prediction scoring + calibration
│   ├── alert_monitor.sh       # Threshold alerting (cron)
│   ├── collect_twitter.py     # Twitter/X via Playwright + Nitter
│   ├── collect_fundraising.py # RootData via Playwright
│   ├── collect_bettafish.py   # Claude-powered sentiment analysis
│   ├── collect_worldmonitor.py # World Monitor Redis extraction
│   ├── deduplicate.py         # Cross-source signal detection
│   └── knowledge_db.py        # SQLite FTS5 knowledge base
├── config/
│   ├── twitter_seeds.yaml     # 400+ Twitter accounts (15 categories)
│   ├── sector_context.md      # Landscape document all agents read
│   ├── analyst_model.md       # Persistent structural thesis
│   ├── agent_memory/          # Per-agent accumulated memory
│   └── agent_state/           # Per-agent dated state logs
├── data-sources/              # Collection output (gitignored)
├── briefs/                    # Daily output (gitignored)
├── archive/                   # Historical briefs + snapshots
└── logs/                      # Run logs + LLM cost tracking (gitignored)
```

---

## Cost

RECON uses the Claude CLI (`claude -p`) for all LLM calls. On **Claude Max**, all runs are included in your subscription — no additional costs. All data collection uses free public APIs, RSS feeds, and headless browser scraping — no API keys required. World Monitor is self-hosted via Docker.

For users running with **API keys** instead of Claude Max, estimated per-run costs are ~$3 for the Morning Brief and ~$0.50 for AI Digest / Fundraising Radar.

---

## Credits

- [World Monitor](https://github.com/koala73/worldmonitor) — 435+ geopolitical intelligence feeds
- [BettaFish](https://github.com/666ghj/BettaFish) — Multi-agent sentiment analysis architecture
- [DeFiLlama](https://defillama.com) — DeFi protocol data
- [CoinGecko](https://coingecko.com) — Market data
- [RootData](https://www.rootdata.com) — Crypto fundraising data
- [Nitter](https://github.com/zedeus/nitter) — Twitter frontend for scraping
- [Playwright](https://playwright.dev) — Browser automation
- Built with [Claude Code](https://claude.ai/claude-code) by Anthropic

## License

MIT
