#!/usr/bin/env bash
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

clear

# Animated intro
sleep 0.3
echo -e "${DIM}"
echo "    Initializing secure channels..."
sleep 0.5
echo "    Establishing data links..."
sleep 0.4
echo "    Activating agent protocols..."
sleep 0.5
echo -e "${NC}"
clear

# Main banner
echo -e "${CYAN}"
cat << 'BANNER'

    ██████╗ ███████╗ ██████╗ ██████╗ ███╗   ██╗
    ██╔══██╗██╔════╝██╔════╝██╔═══██╗████╗  ██║
    ██████╔╝█████╗  ██║     ██║   ██║██╔██╗ ██║
    ██╔══██╗██╔══╝  ██║     ██║   ██║██║╚██╗██║
    ██║  ██║███████╗╚██████╗╚██████╔╝██║ ╚████║
    ╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝

BANNER
echo -e "${NC}"
echo -e "${DIM}    Multi-Agent Intelligence System${NC}"
echo -e "${DIM}    500+ sources. 9 analysts. 3 products.${NC}"
echo ""
echo -e "${WHITE}    ┌─────────────────────────────────────────┐${NC}"
echo -e "${WHITE}    │${NC}  ${GREEN}■${NC} Daily Brief    ${GREEN}■${NC} AI Digest    ${GREEN}■${NC} VC Radar  ${WHITE}│${NC}"
echo -e "${WHITE}    └─────────────────────────────────────────┘${NC}"
echo ""

sleep 1

# Check what's already configured
echo -e "${BOLD}${WHITE}  SYSTEM CHECK${NC}"
echo -e "  ${DIM}─────────────────────────────────────────${NC}"

# Python
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 --version 2>&1 | cut -d' ' -f2)
    echo -e "  ${GREEN}✓${NC} Python $PY_VER"
else
    echo -e "  ${RED}✗${NC} Python 3 not found"
fi

# Playwright
if python3 -c "import playwright" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Playwright installed"
else
    echo -e "  ${YELLOW}○${NC} Playwright not installed"
fi

# Chromium
if command -v chromium-browser &>/dev/null || command -v chromium &>/dev/null || [ -d "$HOME/.cache/ms-playwright" ]; then
    echo -e "  ${GREEN}✓${NC} Chromium available"
else
    echo -e "  ${YELLOW}○${NC} Chromium not found"
fi

# Claude CLI
if command -v claude &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Claude CLI"
else
    echo -e "  ${RED}✗${NC} Claude CLI not found (required)"
fi

# feedparser
if python3 -c "import feedparser" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} feedparser"
else
    echo -e "  ${YELLOW}○${NC} feedparser not installed"
fi

# PyYAML
if python3 -c "import yaml" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} PyYAML"
else
    echo -e "  ${YELLOW}○${NC} PyYAML not installed"
fi

# Docker
if command -v docker &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Docker"
else
    echo -e "  ${DIM}○${NC} Docker (optional, for World Monitor)"
fi

# World Monitor
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q worldmonitor; then
    echo -e "  ${GREEN}✓${NC} World Monitor running"
else
    echo -e "  ${DIM}○${NC} World Monitor not running (optional)"
fi

# Telegram
ENV_FILE="${HOME}/.recon.env"
if [ -f "$ENV_FILE" ] && grep -q "RECON_TELEGRAM_TOKEN=." "$ENV_FILE" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Telegram configured"
    TG_CONFIGURED=true
else
    echo -e "  ${YELLOW}○${NC} Telegram not configured"
    TG_CONFIGURED=false
fi

echo ""

# Determine what needs to be done
NEEDS_INSTALL=false
NEEDS_CONFIG=false

if ! python3 -c "import playwright" 2>/dev/null || ! python3 -c "import feedparser" 2>/dev/null || ! python3 -c "import yaml" 2>/dev/null; then
    NEEDS_INSTALL=true
fi

if [ "$TG_CONFIGURED" = false ]; then
    NEEDS_CONFIG=true
fi

# Setup flow
if [ "$NEEDS_INSTALL" = true ] || [ "$NEEDS_CONFIG" = true ]; then
    echo -e "${BOLD}${WHITE}  SETUP${NC}"
    echo -e "  ${DIM}─────────────────────────────────────────${NC}"
    echo ""

    if [ "$NEEDS_INSTALL" = true ]; then
        echo -e "  ${CYAN}Installing dependencies...${NC}"
        echo ""

        # Create venv if it doesn't exist
        if [ ! -d "${HOME}/recon-venv" ]; then
            echo -e "  ${DIM}Creating Python virtual environment...${NC}"
            python3 -m venv "${HOME}/recon-venv" 2>/dev/null || true
        fi

        # Try to activate venv
        if [ -f "${HOME}/recon-venv/bin/activate" ]; then
            source "${HOME}/recon-venv/bin/activate"
        fi

        # Install Python deps
        pip install playwright pyyaml feedparser requests 2>/dev/null | while read -r line; do
            echo -e "  ${DIM}  $line${NC}"
        done

        # Install Chromium
        if ! [ -d "$HOME/.cache/ms-playwright" ]; then
            echo -e "  ${DIM}Installing Chromium for Twitter/fundraising scraping...${NC}"
            playwright install chromium 2>/dev/null || true
        fi

        echo ""
        echo -e "  ${GREEN}✓${NC} Dependencies installed"
        echo ""
    fi

    if [ "$NEEDS_CONFIG" = true ]; then
        echo -e "  ${CYAN}Telegram Setup${NC}"
        echo -e "  ${DIM}RECON delivers briefs via Telegram bot.${NC}"
        echo ""
        echo -e "  ${WHITE}1.${NC} Message ${BOLD}@BotFather${NC} on Telegram"
        echo -e "  ${WHITE}2.${NC} Send ${BOLD}/newbot${NC} and follow the prompts"
        echo -e "  ${WHITE}3.${NC} Copy the bot token"
        echo ""

        read -p "  Bot token (or press Enter to skip): " TG_TOKEN
        if [ -n "$TG_TOKEN" ]; then
            echo ""
            echo -e "  ${DIM}Now send any message to your new bot, then:${NC}"
            echo -e "  ${DIM}Visit: https://api.telegram.org/bot${TG_TOKEN}/getUpdates${NC}"
            echo -e "  ${DIM}Find your chat_id in the response.${NC}"
            echo ""
            read -p "  Chat ID: " TG_CHAT_ID

            if [ -n "$TG_CHAT_ID" ]; then
                cat > "$ENV_FILE" << ENVFILE
# RECON Environment
export RECON_TELEGRAM_TOKEN=${TG_TOKEN}
export RECON_TELEGRAM_CHAT_ID=${TG_CHAT_ID}

# Optional
export CRYPTOPANIC_API_KEY=
ENVFILE
                echo ""
                echo -e "  ${GREEN}✓${NC} Telegram configured (saved to ~/.recon.env)"

                # Test it
                echo -e "  ${DIM}Sending test message...${NC}"
                source "$ENV_FILE"
                curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
                    -H "Content-Type: application/json" \
                    -d "{\"chat_id\":\"${TG_CHAT_ID}\",\"text\":\"RECON connected. Intelligence system online.\",\"parse_mode\":\"HTML\"}" > /dev/null 2>&1 \
                    && echo -e "  ${GREEN}✓${NC} Test message sent — check Telegram" \
                    || echo -e "  ${YELLOW}!${NC} Couldn't send test message — check token/chat ID"
            fi
        else
            echo -e "  ${DIM}Skipped. Run this script again to configure later.${NC}"
        fi
        echo ""
    fi
fi

# Ready state
echo -e "${BOLD}${WHITE}  READY${NC}"
echo -e "  ${DIM}─────────────────────────────────────────${NC}"
echo ""
echo -e "  ${WHITE}Run commands:${NC}"
echo ""
echo -e "  ${GREEN}Morning Brief${NC}    ./scripts/run_recon.sh"
echo -e "  ${GREEN}AI Digest${NC}        ./scripts/run_recon.sh --mode ai-digest"
echo -e "  ${GREEN}VC Radar${NC}         ./scripts/run_recon.sh --mode fundraising"
echo -e "  ${GREEN}Quick run${NC}        ./scripts/run_recon.sh --skip-collect"
echo -e "  ${GREEN}Alerts${NC}           ./scripts/alert_monitor.sh"
echo ""
echo -e "  ${DIM}Add --skip-collect to any mode to reuse cached data.${NC}"
echo ""
echo -e "${CYAN}"
cat << 'FOOTER'
    ┌─────────────────────────────────────────┐
    │         Intelligence is ready.          │
    └─────────────────────────────────────────┘
FOOTER
echo -e "${NC}"
