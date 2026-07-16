# Methodology — thresholds, sources, and limitations

This document defends every number the GARP scorecard uses, and lists the model's known
limitations. The scorecard is deliberately simple — eight pass/fail checks — because a
transparent heuristic that a reviewer can audit beats an opaque composite score.

## Scorecard thresholds

| # | Criterion | Threshold | Rationale & source |
|---|-----------|-----------|--------------------|
| 1 | Revenue growth | YoY > 10% | GARP looks for growth well above nominal GDP (~4–5% in the US). 10% ≈ 2× nominal GDP growth — a bar that separates genuine growers from mature compounders inside a large-cap universe. |
| 2 | Rule of 40 | growth + FCF margin ≥ 40 | A venture/SaaS heuristic popularized by Brad Feld ("The Rule of 40% for a Healthy SaaS Company", 2015): a software business is healthy if growth plus profitability exceeds 40. Because it encodes a *software* growth/margin trade-off, it is only applied to growth sectors (Technology, Communication Services) — see "Sector applicability" below. |
| 3 | FCF margin | > 15% | Free-cash-flow conversion is the quality-of-earnings test; 15%+ of revenue turning into FCF indicates real cash economics, not accrual accounting. Large-cap software/pharma typically clear it; capital-heavy businesses don't — which is informative. Not meaningful for banks (see below). |
| 4 | Net margin | > 15% | The long-run S&P 500 aggregate net margin sits around 11–12% (S&P Dow Jones Indices data); > 15% indicates above-market pricing power or operating leverage. |
| 5 | Return on equity | > 15% | A classic quality bar (Buffett's shareholder letters repeatedly use mid-teens ROE as the mark of a good business). The long-run S&P 500 median is roughly 14–15%, so this asks for above-median capital productivity. |
| 6 | PEG | < 2 | Canon (Peter Lynch, *One Up on Wall Street*, 1989) is PEG < 1. That bar is intentionally relaxed to < 2 here: in the current rate/multiple regime almost no mega-cap trades below 1, and a screen that fails ~everything carries no information. < 2 keeps the growth-adjusted-value spirit while discriminating within this universe. Note Yahoo's `trailingPegRatio` divides trailing P/E by an ~5-year *expected* growth estimate — a forward-looking, analyst-dependent input. |
| 7 | Forward multiple discount | forward P/E < trailing P/E | A forward multiple below the trailing one means consensus expects EPS to grow over the next 12 months. It is labeled a *discount*, not a "re-rating": nothing guarantees the market re-prices the stock — the signal is expected earnings growth only. |
| 8 | Balance sheet | debt/equity < 1.5 | A conventional prudence threshold for non-financial corporates (≤ 1.5–2.0 is the common textbook range). Meaningless for banks and insurers, whose leverage *is* the business model — so it is skipped for Financial Services. |

## Sector applicability (and the denominator caveat)

Criteria that don't fit a sector's business model are **excluded** for that company rather
than counted as failures:

- **Rule of 40** — applied only to Technology and Communication Services.
- **FCF margin, debt/equity** — skipped for Financial Services (bank "FCF" and bank
  leverage are not comparable to corporate metrics; D/E of 6+ is normal for a
  broker-dealer and says nothing GARP-relevant).

**Caveat:** exclusions shrink the denominator, so scores are ratios over *different bases* —
a bank scoring 4/5 and a software company scoring 7/8 both show high pass rates, but they
were tested against different (and differently difficult) criteria sets. The verdict bands
compare pass *rates*, not identical exams. The scorecard displays "X of Y applicable
criteria" to keep this visible.

## Relative valuation (implied value)

The pipeline computes a sector-relative implied value per company (`build_data.py`,
`add_valuations`):

1. Take the company's sector peers (universe members in the same Yahoo sector, excluding
   the company itself). At least 4 peers with a valid multiple are required, else the
   method is skipped.
2. **EV/EBITDA route** — reprice the company's EBITDA (derived as EV ÷ EV/EBITDA) at the
   peer-median EV/EBITDA, subtract debt, add cash → implied equity → implied price.
3. **Forward P/E route** — reprice the company's forward EPS (price ÷ forward P/E) at the
   peer-median forward P/E → implied price.
4. Blend: the implied price is the simple average of whichever routes were available; the
   Valuation tab shows it against the market price and the analyst consensus target.

Caveats: sector is a coarse peer group (a payments network and a regional bank are both
"Financial Services"); medians inherit every Yahoo data quirk; for financials only the
forward P/E route usually exists (EV/EBITDA is undefined for banks); and a one-day multiple
snapshot is not a fairness opinion — this is a screening signal, not a price target.

## Data limitations

- **Single source.** All fundamentals come from Yahoo Finance via `yfinance`. There is no
  second vendor to reconcile against, and Yahoo's field definitions (e.g. what lands in
  `totalDebt`) are not always documented. To contain this, every refresh runs
  `validate_data.py`: derived fields are recomputed from their inputs (hard failures block
  the commit) and source-data oddities are reported in `docs/validation-report.md` — but a
  wrong-but-internally-consistent Yahoo number still flows through.
- **PEG definition.** Yahoo's `trailingPegRatio` uses an ~5-year expected earnings-growth
  estimate in the denominator — an analyst consensus input, not a reported number.
- **13F lag.** Institutional holdings (13F filings) are due 45 days after quarter end, so
  the "Smart Money" ownership and position-change data lags reality by up to a quarter.
  Insider (Form 4) data is far fresher (2 business days) but sparse.
- **Analyst targets are consensus opinions.** "Highest Analyst Upside" ranks by mean price
  target vs. price; it reflects sell-side consensus, not an independent valuation.
- **Snapshot cadence.** The pipeline refreshes on weekday mornings (UTC). Prices and
  multiples shown are the latest snapshot, not live quotes.

## Scoring mechanics

A criterion with missing data (null from the source) is shown as N/A and excluded from
the denominator, same as a sector exclusion. The verdict bands are: pass rate ≥ 0.85
Strong, ≥ 0.6 Solid, ≥ 0.4 Mixed, otherwise Weak.
