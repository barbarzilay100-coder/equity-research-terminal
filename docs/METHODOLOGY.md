# Methodology — thresholds, sources, and limitations

This document defends every number the GARP scorecard uses, and lists the model's known
limitations. The scorecard is deliberately simple — eight pass/fail checks — because a
transparent heuristic that a reviewer can audit beats an opaque composite score.

## Scorecard thresholds

| # | Criterion | Threshold | Rationale & source |
|---|-----------|-----------|--------------------|
| 1 | Revenue growth | fiscal year **or** latest quarter, YoY > 10% | GARP looks for growth well above nominal GDP (~4–5% in the US). 10% ≈ 2× nominal GDP growth — a bar that separates genuine growers from mature compounders inside a large-cap universe. Two year-over-year reads are shown and the check passes if either clears the bar: the latest full fiscal year, and the latest reported quarter against the same quarter a year earlier. There is no trailing-twelve-month read — see "Definitions and periods". |
| 2 | Rule of 40 | FY revenue growth + FY FCF margin ≥ 40 | A venture/SaaS heuristic popularized by Brad Feld ("The Rule of 40% for a Healthy SaaS Company", 2015): a software business is healthy if growth plus profitability exceeds 40. Both terms are measured over the same fiscal year, so the sum never adds a quarter to a year. Because it encodes a *software* growth/margin trade-off, it is only applied to growth sectors (Technology, Communication Services) — see "Sector applicability" below. |
| 3 | FCF margin | TTM > 15% | Free-cash-flow conversion is the quality-of-earnings test; free cash flow here is operating cash flow minus capital expenditure, built from the cash-flow statement (see "Definitions and periods"). 15%+ of revenue turning into FCF indicates real cash economics, not accrual accounting. Large-cap software/pharma typically clear it; capital-heavy businesses don't — which is informative. Not meaningful for banks (see below). |
| 4 | Net margin | TTM > 15% | The long-run S&P 500 aggregate net margin sits around 11–12% (S&P Dow Jones Indices data); > 15% indicates above-market pricing power or operating leverage. |
| 5 | Return on equity | TTM > 15% | A classic quality bar (Buffett's shareholder letters repeatedly use mid-teens ROE as the mark of a good business). The long-run S&P 500 median is roughly 14–15%, so this asks for above-median capital productivity. Yahoo's figure is consolidated rather than common-shareholder ROE — see "Data limitations". |
| 6 | PEG | < 2 | Canon (Peter Lynch, *One Up on Wall Street*, 1989) is PEG < 1. That bar is intentionally relaxed to < 2 here: at the premium multiples large-cap growth companies typically command, < 1 is a deep-value bar, while < 2 keeps the growth-at-a-reasonable-price spirit and still separates reasonably priced growth from expensive growth. Note Yahoo's `trailingPegRatio` divides trailing P/E by an ~5-year *expected* growth estimate — a forward-looking, analyst-dependent input. |
| 7 | Forward multiple discount | forward P/E < trailing P/E | A forward multiple below the trailing one means consensus expects EPS to grow over the next 12 months. It is labeled a *discount*, not a "re-rating": nothing guarantees the market re-prices the stock — the signal is expected earnings growth only. |
| 8 | Balance sheet | debt/equity < 1.5 | A conventional prudence threshold for non-financial corporates (≤ 1.5–2.0 is the common textbook range). Meaningless for banks and insurers, whose leverage *is* the business model — so it is skipped for Financial Services. The ratio is Yahoo's; see "Data limitations" for what its debt and equity include. |

## Definitions and periods

Every figure the scorecard reads, and the period it covers. *Statement* figures are computed
by the pipeline (`pipeline/build_data.py`) from Yahoo's income and cash-flow statement lines;
*vendor* figures are Yahoo's ready-made fields, used as delivered.

| Figure | Definition | Period | Source |
|---|---|---|---|
| Free cash flow | Operating cash flow + capital expenditure (capex is reported as a negative number) | Trailing twelve months | Statement |
| FCF margin (TTM) | Free cash flow ÷ revenue of the same four quarters | Trailing twelve months | Statement |
| FCF margin (FY) | (Operating cash flow + capex) ÷ revenue, all for the latest fiscal year in the revenue history | Latest full fiscal year | Statement |
| Revenue growth (FY) | Latest full fiscal year's revenue vs. the preceding fiscal year's | Fiscal year | Statement |
| Revenue growth (latest quarter) | Latest reported quarter's revenue vs. the quarter one year earlier | One quarter, year over year | Statement |
| Net margin, net income | `profitMargins`, `netIncomeToCommon` | Trailing twelve months | Vendor |
| Return on equity | `returnOnEquity`: net income including non-controlling interests ÷ average of opening and closing total equity, also including them | Trailing twelve months | Vendor |
| Debt / equity | `debtToEquity` ÷ 100 | Latest balance sheet | Vendor |
| EV / FCF | Enterprise value ÷ free cash flow, shown only when both are positive | Trailing twelve months | Vendor ÷ statement |

**Why free cash flow is computed rather than read.** Yahoo's `freeCashflow` field is a
"levered free cash flow" whose derivation is undocumented: it is often well below operating
cash flow minus capex, sometimes above operating cash flow itself, and blank for many banks.
The statement's own Free Cash Flow row is operating cash flow minus capex when a capex line is
reported, but equals operating cash flow alone when it is not, and the row does not say which.
So the pipeline builds free cash flow from the two lines, stores them, and every refresh checks
the result against them.

**How periods are chosen.** Statement columns are selected by date, never by position:

- Trailing-twelve-month free cash flow sums the four newest consecutive quarters in which both
  operating cash flow and capital expenditure are reported. Quarters are consecutive when
  75–125 days apart, which covers 12- to 17-week fiscal quarters; a missing quarter leaves a gap
  of about 180 days and voids the figure. The FCF margin divides by revenue summed over those
  same four quarters, and is withheld if any of them lacks revenue.
- The year-ago quarter is the one dated 350–380 days before the latest, which covers 52- and
  53-week fiscal calendars and leap years.
- Every fiscal-year figure comes from the latest fiscal year in the revenue history, so a
  margin's numerator, its denominator and the "FY ended" date shown beside it are the same year.
- Fiscal-year growth compares the latest fiscal year with the one before it, only when their
  year-ends are 350–380 days apart; a missing year or a change of fiscal year-end produces no
  figure.

**Why there is no trailing-twelve-month growth rate.** Yahoo returns at most five quarters of
statements. A trailing-twelve-month growth rate compares four quarters with the four before
them — eight quarters — so the longer growth read is the fiscal year instead. That keeps Rule
of 40 on a single period, at a cost: the latest full fiscal year can have ended many months
before the snapshot, so fiscal-year figures describe the last reported year rather than the
current run-rate. On a company's report, the scorecard, the header and the free-cash-flow card
show the date the fiscal year ended. The screener, the comparison table, the sector overview,
the Rule of 40 tile and the auto-generated memo label these figures as fiscal-year but show no
date. Companies close their fiscal years on different dates, so figures placed side by side in
those views can cover different twelve-month periods.

**A figure is withheld rather than approximated.** A company with no capex line — typically a
bank — gets no free cash flow; its operating cash flow is never shown under that name. Three
quarters, four quarters with a gap, a missing year-ago quarter, or two fiscal years that are
not a year apart produce no figure. A withheld
figure shows as a dash, its scorecard criterion is marked N/A, and it drops out of the
scorecard's denominator (see "Scoring mechanics").

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

The pipeline computes a sector-relative implied value per company (`pipeline/build_data.py`,
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

## SEC filings classification (Deal Radar)

The `events` block is built from the free SEC EDGAR submissions API
(`data.sec.gov/submissions/`), which lists every filing with its form type and — for 8-Ks —
the item codes the filer declared. Classification uses only those two fields; no filing text
is parsed and no AI is involved, so the result is fully deterministic and reproducible:

| Signal | Source |
|---|---|
| M&A / disposition completed | 8-K item 2.01 |
| Material agreement signed / terminated | 8-K item 1.01 / 1.02 |
| Leadership change | 8-K item 5.02 |
| Results announced | 8-K item 2.02 (2.03 debt, 3.01 listing) |
| Merger-related filing | S-4, F-4, 425, DEFM14A/PREM14A, SC TO-*, SC 14D9 |
| Activist stake | SC 13D + amendments |
| New passive >5% stake | SC 13G (initial filings only) |
| Periodic report | 10-K, 10-Q, 20-F |

Known limits, by construction: an 8-K item code says *that* something happened, not *what*
(item 1.01 covers any material agreement, not only deal-related ones); 8-Ks filed only under
items 7.01/8.01 are skipped even when the underlying news is significant; 13G **amendments**
are excluded as noise, so stake *increases* by passive holders don't surface; foreign private
issuers (most of the Israeli dual-listed names) report material news on 6-K, which carries no
item codes and is skipped, so they show fewer events; and the window is ~6 months with a per-company cap, both
set in `pipeline/build_events.py`.

## Data limitations

- **Single source, two kinds of field.** All fundamentals come from Yahoo Finance via
  `yfinance`, with no second vendor to reconcile against. Free cash flow and revenue growth are
  rebuilt from statement lines (see "Definitions and periods"): Yahoo's ready-made free cash
  flow is not operating cash flow minus capex, and its revenue-growth field covers a single
  quarter. The other fields are used as Yahoo delivers them, and some of their definitions are
  undocumented:
  - *Debt / equity* is Yahoo's total debt over total equity **including** non-controlling
    interests. Its debt figure can be larger than the balance sheet's Total Debt line and cannot
    be rebuilt from the balance-sheet lines. Where non-controlling interests are large, the
    equity base alone can move the ratio across the 1.5 threshold. The largest cases are asset
    managers, which the scorecard skips because it does not score debt / equity for Financial
    Services, but the effect also reaches scored sectors such as telecoms.
  - *Return on equity* is consolidated: net income including non-controlling interests over
    average total equity including them. Common-shareholder ROE (net income to common
    shareholders over their equity) differs where non-controlling interests are large, and the
    two can fall on opposite sides of the 15% bar.
  - *Net income and net margin* can disagree with the statements, and for some financial
    companies Yahoo computes net margin on a different revenue base than the statement's Total
    Revenue. The growth and FCF figures on the scorecard use the statement throughout.
  - *Gross margin* is Yahoo's `grossMargins` field and does not always match the statement's
    Gross Profit over Total Revenue. The gaps are largest in energy but also appear elsewhere,
    for example at card networks. For financial companies with no Gross Profit line, Yahoo
    supplies a placeholder of 0% or 100%, which is not a real margin. Gross margin is displayed,
    not scored.
- **What every refresh checks.** `pipeline/validate_data.py` runs before any data is committed,
  and a hard failure blocks the commit. Hard failures are:
  - a derived figure that does not reproduce from its stored inputs: analyst and implied
    upside, distance from the 52-week high, EV/FCF, free cash flow from operating cash flow and
    capex, and each FCF margin from its own period's revenue;
  - a free cash flow or fiscal-year FCF margin stored without the inputs needed to reproduce
    it, a positive capex figure (a broken sign convention), or an EV/FCF multiple stored with a
    non-positive EV or FCF;
  - a fiscal-year figure whose revenue is not the revenue history's latest year;
  - trailing or fiscal-year free cash flow, or either revenue-growth read, missing for more than
    a fifth of the universe (a failed fetch, not a data gap);
  - a duplicate ticker, a non-positive price, a company count that does not match the list, or
    a shrunken universe.

  Bounds checks and source-data oddities — including a vendor net margin that disagrees with
  statement revenue — are listed as warnings in `docs/validation-report.md` and do not block the
  commit. A wrong but internally consistent vendor figure still flows through.
- **Statement lag.** Yahoo sometimes lists a company's newest quarter before it fills in the
  statement lines. The pipeline then uses the newest complete quarter, so trailing-twelve-month
  figures and the latest-quarter growth read can trail the company's latest report by a quarter
  while vendor fields already reflect it.
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

A criterion with missing data is shown as N/A and excluded from the denominator, same as a
sector exclusion. Data is missing when the source has no value, or when the pipeline withholds
a figure it cannot compute over a complete period (see "Definitions and periods"). Revenue
growth is N/A only when both of its reads are missing; Rule of 40 is N/A when either
fiscal-year term is. The verdict bands are: pass rate ≥ 0.85
Strong, ≥ 0.6 Solid, ≥ 0.4 Mixed, otherwise Weak.
