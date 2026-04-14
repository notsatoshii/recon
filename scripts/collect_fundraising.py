#!/usr/bin/env python3
"""
RECON Fundraising Data Collector via Playwright
Scrapes RootData.com/Fundraising for recent crypto/web3 funding rounds.

Output: /home/recon/recon/data-sources/fundraising/latest.md
"""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

RECON_HOME = Path("/home/recon/recon")
OUTPUT_FILE = RECON_HOME / "data-sources" / "fundraising" / "latest.md"
URL = "https://www.rootdata.com/Fundraising"
CF_WAIT = 5


async def main():
    from playwright.async_api import async_playwright

    print("Fundraising: Launching Playwright...")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# Fundraising Intelligence (RootData)",
        f"## {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}",
        f"## Source: https://www.rootdata.com/Fundraising",
        "",
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900},
            locale="en-US",
        )

        page = await context.new_page()

        try:
            print("  Navigating to RootData Fundraising...")
            await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(CF_WAIT * 1000)

            # Check for captcha/verification
            content = await page.content()
            if "Slide to complete" in content or "Verification" in content:
                print("  Captcha detected, waiting 8s and retrying...")
                await page.wait_for_timeout(8000)
                await page.reload(wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(5000)
                content = await page.content()
                if "Slide to complete" in content:
                    print("  Captcha still present. Using cached data if available.")
                    # Fall back to cached file
                    if OUTPUT_FILE.exists() and OUTPUT_FILE.stat().st_size > 200:
                        print(f"  Using cached fundraising data ({OUTPUT_FILE.stat().st_size} bytes)")
                        await browser.close()
                        return
                    else:
                        raise Exception("Captcha blocked and no cached data")

            # Wait for the fundraising table to load
            try:
                await page.wait_for_selector("table, .fundraising-list, .list-item, [class*='fund'], [class*='raise']", timeout=15000)
            except Exception:
                print("  No table selector found, trying to read page content directly...")

            await page.wait_for_timeout(3000)  # Extra wait for JS rendering

            # Try to extract fundraising data from the page
            data = await page.evaluate("""() => {
                const results = [];

                // Strategy 1: Look for table rows
                const rows = document.querySelectorAll('table tbody tr, .list-item, [class*="fundraising"] > div');
                for (const row of rows) {
                    const text = row.innerText.trim();
                    if (text && text.length > 10) {
                        results.push({type: 'row', text: text.replace(/\\n+/g, ' | ')});
                    }
                }

                // Strategy 2: If no table, get all text content that looks like fundraising data
                if (results.length === 0) {
                    const allText = document.body.innerText;
                    // Look for patterns like "$XM", "Series A", "Seed", "raised"
                    const sections = allText.split('\\n');
                    for (const line of sections) {
                        if (line.match(/\\$[\\d.]+[MBK]|Series [A-D]|Seed|Pre-Seed|Strategic|raised|Round/i) && line.length > 10) {
                            results.push({type: 'text', text: line.trim()});
                        }
                    }
                }

                // Strategy 3: Look for specific data attributes
                const cards = document.querySelectorAll('[class*="card"], [class*="item"], [class*="project"]');
                for (const card of cards) {
                    const links = card.querySelectorAll('a');
                    const text = card.innerText.trim();
                    if (text && text.match(/\\$|Series|Seed|Round|raised/i)) {
                        const href = links.length > 0 ? links[0].href : '';
                        results.push({type: 'card', text: text.replace(/\\n+/g, ' | '), href: href});
                    }
                }

                return results.slice(0, 50);
            }""")

            if data and len(data) > 0:
                lines.append("## RECENT FUNDRAISING ROUNDS\n")
                seen = set()
                # Filter words that indicate UI elements, not data
                ui_noise = ["All |", "Confirm", "Fundraising Rounds  Investors",
                            "< $", "≥ $", "Angel |", "Pre-Seed |", "Seed |",
                            "Series A |", "Series B |", "Private |", "ICO |",
                            "IDO |", "OTC", "Community |", "Public Sale"]
                for item in data:
                    text = item.get("text", "").strip()
                    # Deduplicate
                    key = text[:80]
                    if key in seen or len(text) < 15:
                        continue
                    # Skip UI filter elements
                    if any(noise in text for noise in ui_noise):
                        continue
                    # Skip if it's just a header
                    if text.startswith("Fundraising Rounds"):
                        continue
                    seen.add(key)

                    href = item.get("href", "")
                    if href and not href.startswith("http"):
                        href = f"https://www.rootdata.com{href}" if href.startswith("/") else ""

                    # Clean up the pipe-delimited RootData format into readable text
                    clean = text
                    # Parse: "ProjectName | Round $XM -- | Date | | Investor1 | * | Investor2 | +N"
                    parts = [p.strip() for p in text.split("|")]
                    if len(parts) >= 3:
                        project = parts[0].strip()
                        round_info = parts[1].strip().replace("\t", " ").strip()
                        date_info = ""
                        investors = []
                        for p in parts[2:]:
                            p = p.strip()
                            if not p or p == "*" or p == "--":
                                continue
                            if "Apr" in p or "Mar" in p or "Feb" in p or "Jan" in p:
                                date_info = p
                            elif p.startswith("+"):
                                investors.append(p)
                            elif len(p) > 1:
                                investors.append(p)
                        inv_str = ", ".join(investors) if investors else "undisclosed"
                        clean = f"{project} — {round_info} ({date_info}) | Investors: {inv_str}"

                    if href:
                        lines.append(f"- {clean}")
                        lines.append(f"  Source: {href}")
                    else:
                        lines.append(f"- {clean}")
                lines.append("")
                print(f"  Extracted {len(seen)} fundraising entries")
            else:
                # Fallback: screenshot the page and save raw text
                print("  No structured data found, extracting raw page text...")
                content = await page.inner_text("body")
                # Extract lines that look like fundraising data
                for line in content.split("\n"):
                    line = line.strip()
                    if line and len(line) > 10 and any(kw in line.lower() for kw in
                        ["$", "million", "series", "seed", "round", "raised", "funding",
                         "venture", "capital", "invest", "pre-seed", "strategic"]):
                        lines.append(f"- {line[:200]}")
                lines.append("")

                # Also take a screenshot for debugging
                screenshot_path = RECON_HOME / "data-sources" / "fundraising" / "rootdata_screenshot.png"
                await page.screenshot(path=str(screenshot_path), full_page=False)
                print(f"  Screenshot saved to {screenshot_path}")

        except Exception as e:
            print(f"  Error scraping RootData: {e}")
            lines.append(f"## ERROR: Could not scrape RootData ({str(e)[:100]})\n")
        finally:
            await browser.close()

    OUTPUT_FILE.write_text("\n".join(lines))
    print(f"Fundraising: {len(lines)} lines written to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
