# Pole Position — your morning market paper

A personal newspaper for the Indian markets. It **prints itself** in the cloud every morning and you just open the link, on your phone or laptop.

| Section | What's in it |
|---|---|
| **Front page** | The lead story, the 20-second "Before the bell" read and a market snapshot |
| **01 India** | The Editor's Briefing (Gemini takeaways, a "watch today" sheet and India in brief), then Nifty/Sensex/Bank Nifty/VIX/USD-INR tiles, a Nifty chart, sector bars, Nifty 50 breadth and top movers, **Corporate India** (the day's major company stories, one per company, large caps first), FII/DII flows, and ranked market and economy stories from ET, Mint, Business Standard, Moneycontrol, BusinessLine, FE, NDTV Profit, CNBC-TV18, Bloomberg and Reuters |
| **02 World** | An editor's note, a global index heat-map, US futures, FX, commodities, bond yields, and stories from WSJ, Bloomberg, FT, Reuters, CNBC, MarketWatch, The Economist, Nikkei Asia and BBC |
| **03 AI & Tech** | An editor's note, tech bellwethers (incl. Indian IT), a trending-topics chart, AI videos on YouTube, Reddit/Bluesky chatter, and stories from TechCrunch, The Verge, MIT Tech Review, Ars, Wired, VentureBeat, OpenAI, Google, Bloomberg/WSJ/FT tech, ET Tech, Inc42 and Hacker News |

## How it works
```
GitHub Actions (free)                     every hour, around the clock
   └─ python build.py
        ├─ feeds.py    → ~56 RSS feeds, in parallel
        ├─ process.py  → de-duplicate, cluster the same story across outlets, classify, rank
        ├─ markets.py  → ~65 instruments (Yahoo Finance; Stooq as a backup) + NSE FII/DII
        ├─ ai.py       → Gemini morning brief + "why it matters" (falls back to an auto-digest)
        └─ template.html → site/index.html (one self-contained page) → GitHub Pages
```
The code uses only the Python standard library, with no servers and no databases. It doesn't depend on any Claude subscription. Past editions are kept in `archive/` for 30 days and can be picked from the "Today" menu.

## What gets ranked up (analyst lens, `analyst.json`)
- **Pushed up:** earnings (Q results, margins, guidance), broker rating and target changes, deals and capital raises (M&A, stake sales, block deals, QIPs, buybacks), orders and capex, regulation and policy (SEBI, RBI, GST, tariffs), macro prints (CPI, IIP, GDP, PMI), rates and central banks, FII/DII flows, index events (MSCI, rebalancing), credit ratings, market-wide moves, and stories naming large-cap companies. Those companies also appear as tags.
- **Pushed down:** SME IPO subscription and GMP chatter, small DRHP filings, "multibagger" and stock-tip listicles, live blogs and recaps, explainers and opinion, and gadget reviews in Tech.
- **Dropped:** personal finance (FD rates, ITR, credit cards), lifestyle, sports, entertainment and astrology.
- Edit `analyst.json` to change the weights or add companies.

## Social & video sources
These appear in the AI & Tech section only.
- **YouTube:** the latest videos from OpenAI, Google DeepMind, Anthropic, Bloomberg Tech, Karpathy, Two Minute Papers, AI Explained, Dwarkesh Patel, Fireship and Matthew Berman.
- **Reddit:** top posts of the day from r/MachineLearning, r/LocalLLaMA, r/OpenAI, r/singularity and r/artificial.
- **Bluesky:** a few AI voices.
- Social posts and videos appear in their own **Social Pulse** and **On Air** panels, marked *unverified*. They never count as confirmation of a story and are never given to the AI.

## Accuracy safeguards
- **Trusted outlets only:** Google News items from publishers outside the trusted list are dropped.
- **Confirmation count:** each story shows how many independent outlets carry it ("✓ 3 outlets" or "Single source").
- **AI fact-check:** every number in the AI briefing is checked against the source data it was given. Sentences and items with figures that can't be traced are deleted, and the Editor's Briefing shows the result.
- **Market data checks:** implausible one-day moves are blanked, and old prints are marked *stale*.

## Daily use
- **Open your link** (`https://<your-username>.github.io/<repo>/`). Add it to your phone's home screen for a one-tap app.
- **Refresh now:** tap *Refresh* in the top bar, then **Run workflow**. The new edition appears in about 2 minutes.
- **Keyboard:** `/` search · `j`/`k` next/previous story · `o` open · `s` save · `t` theme · `?` help.
- **Ask the Editor:** the red ✦ Ask button (bottom-right) opens it. Tap ⚙ and paste your Gemini key once per device. The key is stored only in that browser.

## One-time setup (already done for you, kept here for reference)
1. Create a GitHub repository and upload these files, including `.github/workflows/daily.yml`.
2. **Settings → Secrets and variables → Actions → New repository secret**: name `GEMINI_API_KEY`, value = your key from https://aistudio.google.com/apikey. You can use `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` instead.
3. **Settings → Pages → Source: GitHub Actions.**
4. **Actions → Print the paper → Run workflow.**

## Customise
- `feeds.json`: add or remove sources (`section`: india / world / tech; `tier` 1–3 sets how much the source weighs in the ranking; `enabled`).
- `markets.json`: instruments shown (Yahoo Finance symbols).
- `site.json`: title, window, section keywords, ranking keywords, AI models.
- Schedule: the `cron` lines in `.github/workflows/daily.yml`, which are in UTC (IST − 5:30).

## Troubleshooting
- The footer lists every source with a green/red dot. A red source is skipped, and the rest of the paper still prints.
- Full logs: **Actions → latest run → build → "Build today's edition"**.
- If the AI step fails (quota or a bad key), the paper prints with an *Auto-digest* instead of the AI brief.
- The site is public but hidden from search engines (`noindex`). It shows only headlines and links to the publishers.

Development notes: see `CONTRACT.md`. Offline test: `python dev/integration.py` (in the full project zip).
