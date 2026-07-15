# Equity Research Terminal

A live, single-page **equity research terminal** covering ~117 major US companies. It has four modes:

- **Market Overview** — a GARP-quality heatmap by sector plus leaderboards (Highest Quality, Most Undervalued, Quality on Sale).
- **Screener & Leaderboard** — sort and filter the whole universe by any metric or GARP score.
- **Compare** — put 2–3 companies side by side, best value per row highlighted.
- **Research** — type any company or ticker for a full report, organized into sub-tabs: a graded GARP
  scorecard + investment memo, financials, valuation & analyst targets, a technicals tab (50/200-day
  moving averages + RSI signals), and a **Smart Money** tab (institutional ownership, 13F position
  changes, and insider buying/selling).

**[ Live demo → enable GitHub Pages on this repo ]**

## How it works

The app is fully static (hosts on GitHub Pages), yet always current, thanks to a three-part design:

1. **`build_data.py`** — a Python pipeline that pulls fundamentals for a universe of major US companies
   via `yfinance`, computes every metric, and writes **`data.js`** (and `data.json`). No API key required.
2. **`build_prices.py`** — enriches each company with a compact `tech` block: a one-year weekly price
   series, 50- and 200-day moving averages, and a 14-day RSI (used for the price chart and signals).
3. **`build_flow.py`** — adds a `flow` block of "smart money" signals: institutional ownership,
   quarter-over-quarter 13F position changes, and insider (Form 4) buying/selling.
4. **GitHub Actions** (`.github/workflows/update-data.yml`) — runs all three pipelines automatically every
   weekday morning and commits the refreshed `data.json`/`data.js`, so the site never goes stale.
5. **`index.html`** — a zero-dependency front end that loads `data.js` and renders every view,
   including a deterministic **GARP scorecard** (pass/fail against defined thresholds).

Because the numbers are pre-computed and committed, the site loads instantly, works offline, exposes
no secrets, and never hits an API rate limit during a demo.

## The GARP scorecard

Each company is scored on eight quality-and-value criteria: revenue growth, Rule of 40, FCF margin,
net margin, return on equity, PEG, earnings re-rating (forward vs. trailing P/E), and balance-sheet
leverage. The verdict band is derived from the pass rate.

## Optional AI layer

The Investment Memo is auto-generated from the data and scorecard. Paste an Anthropic API key on the
report to upgrade it to a full analyst thesis written by Claude — the numbers stay the deterministic core.

## Run locally / refresh manually

```bash
pip install -r requirements.txt
python build_data.py                 # fundamentals -> data.js (+ data.json)
python build_prices.py fetch 0 200   # price history + technicals for the universe
python build_prices.py merge         # fold tech into data.js / data.json
python build_flow.py fetch 0 200     # smart money: ownership + insider flow
python build_flow.py merge           # fold flow into data.js / data.json
```

Then just **double-click `index.html`** — it loads `data.js` via a script tag, so it works
directly from the file system (no local server needed) and on GitHub Pages alike.

To expand coverage, edit the `UNIVERSE` list in `build_data.py`.

## Stack

Python · yfinance · GitHub Actions · vanilla JS · Chart.js · Anthropic API
