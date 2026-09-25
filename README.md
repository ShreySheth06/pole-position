# Pole Position — your morning market paper

A personal newspaper for the Indian markets. It **prints itself** in the cloud every morning and you just open the link, on your phone or laptop.

| Section | What's in it |
|---|---|
| **Front page** | The lead story, the 20-second "Before the bell" read and a market snapshot |
| **01 India** | Nifty/Sensex/Bank Nifty/VIX/USD-INR tiles, a Nifty chart, sector bars, FII/DII flows, and ranked stories from ET, Mint, Business Standard, Moneycontrol, BusinessLine, FE, NDTV Profit, CNBC-TV18, Bloomberg and Reuters |
| **The Editor's Desk** | The Gemini-written morning briefing (takeaways, "watch today" sheet) plus **Ask the Editor**, which streams answers about today's news |
| **02 World** | A global index heat-map, US futures, FX, commodities, bond yields, and stories from WSJ, Bloomberg, FT, Reuters, CNBC, MarketWatch, The Economist, Nikkei Asia and BBC |
| **03 AI & Tech** | Tech bellwethers (incl. Indian IT), a trending-topics chart, and stories from TechCrunch, The Verge, MIT Tech Review, Ars, Wired, VentureBeat, OpenAI, Google, Bloomberg/WSJ/FT tech, ET Tech, Inc42 and Hacker News |

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

## Social & video sources
- **YouTube:** the latest videos from 26 channels, including ET Now, CNBC-TV18, NDTV Profit, Zee Business, Moneycontrol, Bloomberg TV, CNBC, Reuters, WSJ, FT, OpenAI, DeepMind, Anthropic and Karpathy. They're filtered for market relevance, with clickbait and Shorts removed.
- **Reddit:** top posts of the day from r/IndianStockMarket, r/IndiaInvestments, r/DalalStreetTalks, r/stocks, r/investing, r/economics, r/MachineLearning, r/LocalLLaMA and others.
- **Bluesky:** a few AI voices.
- Social posts and videos appear in their own **Social Pulse** and **On Air** panels, marked *unverified*. They never count as confirmation of a story and are never given to the AI.

## Accuracy safeguards
- **Trusted outlets only:** Google News items from publishers outside the trusted list are dropped.
- **Confirmation count:** each story shows how many independent outlets carry it ("✓ 3 outlets" or "Single source").
- **AI fact-check:** every number in the AI briefing is checked against the source data it was given. Sentences and items with figures that can't be traced are deleted, and the Editor's Desk shows the result.
- **Market data checks:** implausible one-day moves are blanked, and old prints are marked *stale*.

## Daily use
- **Open your link** (`https://<your-username>.github.io/<repo>/`). Add it to your phone's home screen for a one-tap app.
- **Refresh now:** tap *Refresh* in the top bar, then **Run workflow**. The new edition appears in about 2 minutes.
- **Keyboard:** `/` search · `j`/`k` next/previous story · `o` open · `s` save · `t` theme · `?` help.
- **Ask the Editor:** tap ⚙ and paste your Gemini key once per device. The key is stored only in that browser.

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
