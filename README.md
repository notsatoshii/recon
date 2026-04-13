# RECON: Multi-Agent Intelligence System

An 8-agent AI intelligence cell that independently analyzes real-time data across crypto, DeFi, prediction markets, macro economics, geopolitics, AI, and regulation — then debates in structured rounds with persistent memory, prediction scoring, and calibration feedback loops. A synthesizer (Claude Opus 4.6) reads the full debate and produces a Daily Intelligence Brief delivered via Telegram.

## How It Works

```
Phase -1: Score Yesterday's Predictions
    ├── Extract predictions from agent state files
    ├── Fetch current market data for scoring
    └── Produce scorecard for agents to review
         │
         ▼
Phase 0: Data Collection (7 parallel sources)
    ├── Reddit (RSS, 40+ subreddits)
    ├── Twitter/X (Playwright + Nitter, 200+ accounts)
    ├── On-chain (DeFiLlama, CoinGecko, Blockchain.info, Fear & Greed)
    ├── Polymarket (live prediction markets via Gamma API)
    ├── News (7 RSS feeds + CryptoPanic)
    ├── World Monitor (Docker, 79 sources via GDELT/Redis)
    └── BettaFish (Claude-powered sentiment analysis)
         │
         ▼
Phase 0.5: Processing
    ├── Deduplication (cross-source signal detection)
    ├── Historical Context (knowledge DB, 7-day lookback)
    └── Relevance Filter (score 3+/10 passes)
         │
         ▼
Phase 2: Agent Activation
    └── Each agent checks if today's data is relevant to their domain
        Agents with nothing to say sit out. Skeptic always active.
         │
         ▼
Phase 3: Independent Takes (parallel, MAX_PARALLEL=3)
    └── Each active agent produces 200-400 word analysis
        with sector context, historical continuity, and prediction scorecard
         │
         ▼
Phase 4: Structured Debate
    ├── 4A: Tension Pairs
    │   ├── Trader ↔ Narrator (data vs narrative)
    │   ├── Builder ↔ Policy Analyst (product vs compliance)
    │   ├── Analyst ↔ Skeptic (thesis vs risk)
    │   └── Macro Strategist ↔ User Agent (macro vs micro)
    ├── 4C: Wildcard Cross-Examination (unexpected pairing)
    │
    ▼
Phase 5: Defend or Concede
    ├── Agents respond to challenges with evidence
    └── 5.5: Synthesizer-directed Deep Dive on unresolved disagreements
         │
         ▼
Phase 6: Convergence
    ├── Each agent votes: most important action, what market is wrong about, hidden risk
    └── 6.5: Agent Memory + State Update (parallel)
         │
         ▼
Phase 7: Synthesis (Opus 4.6, two-pass with self-critique)
    ├── Environment classification (market/narrative/product/risk/quiet-driven)
    ├── Dynamic agent weighting based on environment
    ├── Draft brief → self-critique → final brief
    └── Knowledge DB index + archive + Telegram delivery
```

## Agents

| Agent | Role | Lens |
|-------|------|------|
| **Trader** | Quantitative trader | Price action, volume, risk/reward, positioning |
| **Narrator** | CT content strategist | Narrative lifecycle, memes, timing, social momentum |
| **Builder** | Product lead | Competitors, shipping, PMF, moats, technical depth |
| **Analyst** | Research analyst | Structural models, TAM, trendlines, thesis testing |
| **Skeptic** | Investigative journalist | Risks, fraud, what everyone ignores (always active) |
| **Policy Analyst** | Regulatory analyst | Multi-jurisdiction regulatory risk, compliance |
| **User Agent** | Real DeFi trader | UX, friction, opportunity cost, trust, on-the-ground |
| **Macro Strategist** | Macro economist | Rates, liquidity, geopolitics, cross-asset flows |
| **Synthesizer** | Chief Intelligence Officer | Produces the final brief (Opus 4.6, two-pass) |

Each agent has:
- **Persistent memory** (`config/agent_memory/*.md`) — tracked items, predictions, recurring themes
- **State log** (`config/agent_state/*_state.md`) — dated positions, predictions, concessions

## Intelligence Features

### Prediction Scoring & Calibration
`score_yesterday.py` extracts testable predictions from agent state/memory files, fetches current market data, and produces a scorecard. Agents read this before making today's takes — closing the feedback loop and improving calibration over time.

### Alert Monitor
`alert_monitor.sh` runs between daily briefs (via cron every 15-30 min) and fires Telegram alerts when thresholds are crossed:
- BTC/ETH/SOL large moves (>5/7/10%)
- Fear & Greed extremes (<15 or >85)
- Stablecoin depegs (>1% deviation)
- Polymarket volume surges (>$5M/day on a single market)
- DeFi TVL crashes (>5% in 24h)

Alerts have a 60-minute cooldown to prevent spam.

### Knowledge Database
SQLite FTS5 database (`config/knowledge.db`) indexes every daily brief and debate record. Agents receive 7-day historical context each run, enabling trend tracking and prediction verification.

### Deduplication
Cross-source signal detection identifies when the same event appears across Reddit, Twitter, news, and on-chain data simultaneously — surfacing high-conviction signals.

### Synthesizer-Directed Deep Dive
After the debate, the synthesizer reviews all challenges and responses. If there's one unresolved disagreement that would materially change the brief's conclusions, it sends both agents back for a focused second round.

### Dynamic Agent Weighting
The synthesizer classifies each day's environment (market/narrative/product/risk/quiet-driven) and weights agent contributions accordingly.

## Data Sources

### Live (no API key required)
- **Reddit RSS** — 40+ subreddits across crypto, DeFi, AI, politics, macro, prediction markets
- **Twitter/X** — 200+ seed accounts via Playwright + Nitter (no X API key). Discovery script expands the seed list automatically via social graph mapping.
- **Polymarket** — Top prediction markets by 24h volume with prices and liquidity (Gamma API)
- **DeFiLlama** — TVL, DEX volumes, fees, yields, stablecoins, chain data, protocol details
- **CoinGecko** — Prices, trending coins, global market data, DeFi sector, Fear & Greed Index
- **Blockchain.info** — BTC network health (hash rate, transactions, difficulty)
- **News RSS** — CoinDesk, Decrypt, CoinTelegraph, DeFiant, Blockworks, Unchained, CryptoSlate

### Self-hosted (Docker)
- **World Monitor** — 79 sources via Redis. GDELT events, prediction markets, economic calendar, conflict/unrest tracking, cyber threats, stablecoin flows, maritime data. 4 containers on port 3080.

### Integrated analysis
- **BettaFish** — Claude-powered multi-agent sentiment analysis across Reddit, Twitter, and news. QueryEngine → MediaEngine → InsightEngine → ReportEngine pipeline.

### Optional (free API key)
- **CryptoPanic** — Aggregated news with community sentiment scoring

## Brief Output Format

```
RECON DAILY INTELLIGENCE BRIEF
├── Executive Summary (3-4 sentences)
├── High Conviction Signals (5+ agents converged)
├── Active Debates (meaningful splits between agents)
├── Emerging Patterns (multi-day trends from agent state history)
├── Prediction Scorecard (yesterday's predictions scored)
├── Blind Spots (single agent flags, coverage gaps)
├── Risk Register (probability/impact assessment)
├── Structural Model Update (analyst thesis changes)
├── What We Don't Know (explicit intelligence gaps)
└── Implications (1-4 week outlook)
```

## Project Structure

```
recon/
├── personas/                  # Agent identity files (9 agents)
├── scripts/
│   ├── run_recon.sh           # Main orchestration (7-phase debate)
│   ├── collect_data.sh        # Data collection pipeline (4 layers)
│   ├── ask_hermes.sh          # LLM interface (claude -p)
│   ├── score_yesterday.py     # Prediction extraction and scoring
│   ├── alert_monitor.sh       # Threshold-based alerting (cron)
│   ├── collect_twitter.py     # Twitter/X via Playwright + Nitter
│   ├── collect_bettafish.py   # Claude-powered sentiment analysis
│   ├── collect_worldmonitor.py # World Monitor Redis extraction
│   ├── deduplicate.py         # Cross-source deduplication
│   ├── knowledge_db.py        # SQLite FTS5 knowledge base
│   ├── discover_twitter_pw.py # Social graph discovery (Playwright)
│   └── discover_subreddits.py # Reddit subreddit discovery
├── config/
│   ├── twitter_seeds.yaml     # Twitter seed accounts (~200, 14 categories)
│   ├── analyst_model.md       # Persistent structural thesis
│   ├── sector_context.md      # Sector landscape document
│   ├── knowledge.db           # Knowledge database (SQLite FTS5)
│   ├── alert_state.json       # Alert cooldown state
│   ├── agent_memory/          # Per-agent persistent memory
│   └── agent_state/           # Per-agent dated state logs
├── data-sources/              # Raw collection output
├── briefs/                    # Daily output (gitignored)
├── archive/                   # Historical briefs + data snapshots
└── logs/                      # Run logs + LLM call log (gitignored)
```

## Setup

### Prerequisites
- Linux server with 4GB+ RAM
- Python 3.11+ with venv
- Playwright + Chromium
- Docker + Docker Compose (for World Monitor)
- Claude Code CLI (`claude`)

### Install

```bash
git clone git@github.com:notsatoshii/recon.git
cd recon

# Python environment
python3 -m venv ~/recon-venv
source ~/recon-venv/bin/activate
pip install playwright pyyaml feedparser requests
playwright install chromium

# Environment
cp ~/.recon.env.example ~/.recon.env
# Edit: set RECON_TELEGRAM_TOKEN and RECON_TELEGRAM_CHAT_ID

# World Monitor (optional, adds geopolitical intelligence)
cd /home/recon && git clone https://github.com/koala73/worldmonitor.git
cd worldmonitor && npm install && docker compose up -d --build
./scripts/run-seeders.sh
```

### Schedule

```bash
# Daily RECON run at 21:00 UTC (6 AM KST)
0 21 * * * source ~/.bashrc && source ~/.recon.env && source ~/recon-venv/bin/activate && cd ~/recon && ./scripts/run_recon.sh >> logs/cron.log 2>&1

# Alert monitor every 15 minutes
*/15 * * * * source ~/.bashrc && source ~/.recon.env && source ~/recon-venv/bin/activate && cd ~/recon && ./scripts/alert_monitor.sh >> logs/alerts.log 2>&1

# World Monitor re-seed every 6 hours
0 */6 * * * cd ~/worldmonitor && ./scripts/run-seeders.sh >> ~/worldmonitor/logs/seed.log 2>&1
```

## Quick Reference

| Action | Command |
|--------|---------|
| Full run | `./scripts/run_recon.sh` |
| Data only | `./scripts/collect_data.sh` |
| Check alerts | `./scripts/alert_monitor.sh` |
| Score predictions | `python3 scripts/score_yesterday.py` |
| Today's brief | `cat briefs/$(date +%Y-%m-%d)/07_daily_brief.md` |
| Full debate | `cat briefs/$(date +%Y-%m-%d)/07_full_record.md` |
| Run log | `cat logs/$(date +%Y-%m-%d).log` |
| LLM costs | `cat logs/llm_calls.log` |
| Discover Twitter | `python3 scripts/discover_twitter_pw.py` |
| Query knowledge DB | `python3 scripts/knowledge_db.py context --days 7` |
| World Monitor health | `curl localhost:3080/api/health` |

## Cost

~$3/run at current context limits. ~$90/month for daily runs.

## Credits

- [DeFiLlama](https://defillama.com) — DeFi data
- [CoinGecko](https://coingecko.com) — Market data
- [Alternative.me](https://alternative.me) — Fear & Greed Index
- [World Monitor](https://github.com/koala73/worldmonitor) — Geopolitical intelligence
- [BettaFish](https://github.com/666ghj/BettaFish) — Multi-agent sentiment analysis architecture
- [Nitter](https://github.com/zedeus/nitter) — Twitter frontend
- Built with [Claude Code](https://claude.ai/claude-code) by Anthropic

## License

MIT
