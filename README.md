# RECON: Multi-Agent Intelligence System

A 7-agent AI intelligence cell that independently analyzes real-time crypto, DeFi, and prediction market data, then debates in structured rounds. A synthesizer (Claude Opus 4.6) reads the full debate and produces a Daily Intelligence Brief delivered via Telegram.

Built for [LEVER Protocol](https://lever.protocol) — leveraged prediction market perpetuals on Base.

## How It Works

```
Data Collection (6 sources)
    │
    ▼
Relevance Filter (Analyst agent)
    │
    ▼
Agent Activation Check ──── agents with nothing to say sit out
    │
    ▼
Independent Takes (parallel, 3 concurrent) ──── each agent analyzes from their lens
    │
    ▼
Tension Debates ──── natural opponents challenge each other
    │
    ├── Trader ↔ Narrator (data vs narrative)
    ├── Builder ↔ User (product vs adoption)
    └── Analyst ↔ Skeptic (thesis vs risk)
    │
    ▼
Regulator Audit ──── compliance review of all takes
    │
    ▼
Wildcard Cross-Examination ──── unexpected agent pairing
    │
    ▼
Defend or Concede ──── agents respond to challenges
    │
    ▼
Convergence Votes ──── each agent votes on key questions
    │
    ▼
Synthesis (Opus 4.6) ──── produces the Daily Intelligence Brief
    │
    ▼
Telegram Delivery ──── @ReconSentinel_bot
```

## Agents

| Agent | Role | Lens |
|-------|------|------|
| **Trader** | Quantitative trader | Data, volume, risk/reward, positioning |
| **Narrator** | CT content strategist | Narrative lifecycle, memes, timing |
| **Builder** | Product lead | Competitors, shipping, PMF, moats |
| **Analyst** | Research analyst | Structural models, TAM, trendlines |
| **Skeptic** | Investigative journalist | Risks, fraud, what everyone ignores |
| **Regulator** | Compliance officer | Multi-jurisdiction regulatory risk |
| **User** | Real DeFi trader | UX, friction, opportunity cost, trust |
| **Synthesizer** | Chief Intelligence Officer | Produces the final brief (Opus 4.6) |

## Data Sources

### Live (no API key required)
- **DeFiLlama** — TVL, DEX volumes, fees, yields, stablecoins, chain data, protocol details
- **CoinGecko** — Prices, trending, global market data, DeFi sector, Fear & Greed Index
- **Blockchain.info** — BTC network health (hash rate, transactions, difficulty)
- **RSS Feeds** — CoinDesk, Decrypt, CoinTelegraph, DeFiant, Blockworks, Unchained
- **USGS** — Earthquake data (geopolitical signal)

### Requires free API key
- **Reddit (PRAW)** — 40+ subreddits across crypto, AI, politics, economics
- **Twitter/X (twscrape)** — 55+ seed accounts, no X API key needed (uses account sessions)
- **CryptoPanic** — Aggregated news with sentiment scoring
- **World Monitor** — Geopolitical intelligence dashboard (65+ sources, self-hosted via Docker)

### Integrated analysis
- **BettaFish** — Multi-agent sentiment analysis adapted from [666ghj/BettaFish](https://github.com/666ghj/BettaFish). Runs QueryEngine (topic extraction), MediaEngine (sentiment scoring), InsightEngine (trend/anomaly detection), and ReportEngine (structured output).

## On-Chain Data Points

The on-chain collection pulls 13 data sections per run:

1. Market overview (total crypto market cap, 24h volume, BTC/ETH dominance)
2. Fear & Greed Index (current + 7-day trend)
3. Key prices with volume + market cap (BTC, ETH, SOL, BNB)
4. CoinGecko trending coins
5. Total DeFi TVL (7d + 30d change)
6. Chain TVLs (Base, BSC, Ethereum, Solana, Arbitrum, Polygon, Optimism)
7. Prediction market protocols (Polymarket, Azuro, Kalshi) — TVL, chains, trends, tokens
8. DEX volumes — total + prediction/derivatives breakdown + top 10
9. Fee revenue — top earners + prediction/derivatives fees
10. Competitors detailed (Synthetix, dYdX, Hyperliquid, GMX)
11. Stablecoin supply (total + per-chain for Base/BSC)
12. Top stablecoin yields (opportunity cost benchmarks)
13. Base + BNB chain ecosystem top protocols
14. BTC network health + DeFi sector overview

## Project Structure

```
recon/
├── personas/                  # Agent identity files
│   ├── trader.md
│   ├── narrator.md
│   ├── builder.md
│   ├── analyst.md
│   ├── skeptic.md
│   ├── regulator.md
│   ├── user_agent.md
│   └── synthesizer.md
├── scripts/
│   ├── ask_hermes.sh          # LLM interface (claude -p primary, hermes fallback)
│   ├── collect_data.sh        # Main data collection orchestrator
│   ├── collect_twitter.py     # Twitter/X scraping via twscrape
│   ├── collect_worldmonitor.py # World Monitor data extraction
│   ├── collect_bettafish.py   # Sentiment analysis (BettaFish adaptation)
│   ├── discover_twitter.py    # Social graph discovery for new accounts
│   ├── discover_subreddits.py # Subreddit discovery via Reddit API
│   └── run_recon.sh           # Main orchestration (7-phase debate)
├── config/
│   ├── analyst_model.md       # Persistent structural model (updated each run)
│   └── twitter_seeds.yaml     # Twitter seed accounts by category
├── data-sources/
│   ├── reddit/                # Reddit intelligence
│   ├── twitter/               # Twitter/X intelligence
│   ├── onchain/               # DeFiLlama + CoinGecko + Blockchain.info
│   ├── news/                  # RSS feeds + CryptoPanic
│   ├── worldmonitor/          # Geopolitical intelligence
│   └── bettafish/             # Sentiment analysis reports
├── briefs/                    # Daily output (gitignored)
│   └── YYYY-MM-DD/
│       ├── 00_data_package.md
│       ├── 01_filtered.md
│       ├── 03_take_*.md
│       ├── 04a_*_vs_*.md
│       ├── 04b_audit.md
│       ├── 05_resp_*.md
│       ├── 06_vote_*.md
│       ├── 07_full_record.md
│       └── 07_daily_brief.md
└── logs/                      # Run logs (gitignored)
```

## Setup

### Prerequisites
- Linux server with 4GB+ RAM
- Python 3.11+ with venv
- Node.js 22+ (for World Monitor seeders)
- Docker + Docker Compose (for World Monitor)
- Claude Code CLI (`claude`) or Hermes Agent

### 1. Clone and configure

```bash
git clone git@github.com:notsatoshii/recon.git
cd recon

# Create Python venv
python3 -m venv ~/recon-venv
source ~/recon-venv/bin/activate
pip install praw feedparser requests twscrape pyyaml
```

### 2. Set up environment

```bash
cp ~/.recon.env.example ~/.recon.env
nano ~/.recon.env
```

Required variables:
```bash
# Telegram bot (create via @BotFather)
export RECON_TELEGRAM_TOKEN=<bot token>
export RECON_TELEGRAM_CHAT_ID=<your chat id>

# Reddit API (https://reddit.com/prefs/apps → create "script" app)
export REDDIT_CLIENT_ID=<client id>
export REDDIT_CLIENT_SECRET=<client secret>
export REDDIT_USER_AGENT=RECON/1.0

# Optional
export CRYPTOPANIC_API_KEY=<free key from cryptopanic.com/developers/api/>
export DUNE_API_KEY=<free key from dune.com/settings/api>
```

### 3. Set up Twitter/X scraping

```bash
# Add a throwaway X account for scraping (no API key needed)
python3 scripts/collect_twitter.py --add-account YOUR_X_USERNAME YOUR_X_PASSWORD

# Verify
python3 scripts/collect_twitter.py --check
```

### 4. Install World Monitor (optional)

```bash
cd /home/recon
git clone https://github.com/koala73/worldmonitor.git
cd worldmonitor
npm install
docker compose up -d --build
./scripts/run-seeders.sh
```

Add free API keys in `docker-compose.override.yml` for more data:
- GROQ_API_KEY → https://console.groq.com
- FRED_API_KEY → https://fred.stlouisfed.org/docs/api/api_key.html
- FINNHUB_API_KEY → https://finnhub.io

### 5. Test

```bash
# Test data collection only
cd ~/recon && ./scripts/collect_data.sh

# Check output
cat data-sources/onchain/latest.md
cat data-sources/news/latest.md
cat data-sources/bettafish/latest.md

# Full RECON run (takes 15-30 min)
./scripts/run_recon.sh

# Read the brief
cat briefs/$(date +%Y-%m-%d)/07_daily_brief.md
```

### 6. Schedule daily runs

```bash
# Already configured if using the deploy script:
crontab -l

# Manual setup:
# Data at 20:30 UTC, full run at 21:00 UTC (6:00 AM KST)
(crontab -l 2>/dev/null; echo "30 20 * * * source ~/.bashrc && source ~/.recon.env && source ~/recon-venv/bin/activate && cd ~/recon && ./scripts/collect_data.sh >> logs/cron.log 2>&1") | crontab -
(crontab -l 2>/dev/null; echo "0 21 * * * source ~/.bashrc && source ~/.recon.env && source ~/recon-venv/bin/activate && cd ~/recon && ./scripts/run_recon.sh >> logs/cron.log 2>&1") | crontab -
```

## Quick Reference

| Action | Command |
|--------|---------|
| Run manually | `cd ~/recon && ./scripts/run_recon.sh` |
| Data only | `cd ~/recon && ./scripts/collect_data.sh` |
| Today's brief | `cat ~/recon/briefs/$(date +%Y-%m-%d)/07_daily_brief.md` |
| Full debate | `cat ~/recon/briefs/$(date +%Y-%m-%d)/07_full_record.md` |
| Check logs | `cat ~/recon/logs/$(date +%Y-%m-%d).log` |
| Edit persona | `nano ~/recon/personas/<agent>.md` |
| Check cron | `crontab -l` |
| Twitter account status | `python3 scripts/collect_twitter.py --check` |
| Discover new Twitter accounts | `python3 scripts/discover_twitter.py` |
| Discover new subreddits | `python3 scripts/discover_subreddits.py` |
| World Monitor health | `curl localhost:3080/api/health` |
| World Monitor reseed | `cd ~/worldmonitor && ./scripts/run-seeders.sh` |

## Architecture Decisions

**Why Claude, not fine-tuned models?** Each agent needs to reason about novel data combinations daily. Fine-tuned models overfit to training distributions. Claude's general reasoning + strong persona prompts produces better cross-domain analysis.

**Why parallel with MAX_PARALLEL=3?** Balances speed against API rate limits and server memory. 7 sequential agent calls take ~25 min; parallelized takes ~10 min.

**Why BettaFish adaptation instead of full install?** The original BettaFish requires PyTorch, Chinese NLP models, PostgreSQL, and Streamlit — ~4GB RAM for Chinese social media analysis. Our adaptation uses the same architecture (QueryEngine → MediaEngine → InsightEngine → ReportEngine) but runs on existing data sources with lexicon-based sentiment + Claude for deep analysis. ~50MB RAM.

**Why World Monitor via Docker?** It aggregates 65+ sources into a unified API. Even without API keys, it provides earthquake, weather, conflict, and displacement data. With free keys (GROQ, FRED, Finnhub), it adds economic indicators and market intelligence.

## Extending

### Add a new agent
1. Create `personas/newagent.md` following the existing format
2. Add `newagent` to the `AGENTS` array in `scripts/run_recon.sh`
3. Optionally add tension pairs in the `TENSIONS` array

### Add a new data source
1. Create `scripts/collect_newsource.py` outputting to `data-sources/newsource/latest.md`
2. Add the collection call to `scripts/collect_data.sh`
3. Add `newsource` to the `for src in ...` assembly loop

### Add Twitter seed accounts
Edit `config/twitter_seeds.yaml` — accounts are organized by category. Run `python3 scripts/discover_twitter.py` to automatically find new accounts from the social graphs of existing seeds.

## Credits

- [DeFiLlama](https://defillama.com) — DeFi data
- [CoinGecko](https://coingecko.com) — Market data
- [Alternative.me](https://alternative.me) — Fear & Greed Index
- [World Monitor](https://github.com/koala73/worldmonitor) — Geopolitical intelligence
- [BettaFish](https://github.com/666ghj/BettaFish) — Multi-agent sentiment analysis architecture
- [twscrape](https://github.com/vladkens/twscrape) — Twitter scraping without API
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — AI agent framework
- Built with [Claude Code](https://claude.ai/claude-code) by Anthropic

## License

MIT
