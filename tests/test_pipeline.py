"""Unit tests for the pure computation inside the data pipelines.

The pipeline modules guard their yfinance import, so these tests run anywhere
Python + pandas exist (locally and in CI) with no network access.

Run: python -m pytest -q
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline"))

import build_data
import build_events
import build_flow
import build_prices


# ---------- build_data.rev_history ----------

class FakeTicker:
    def __init__(self, income_stmt=None, quarterly_cashflow=None,
                 quarterly_income_stmt=None, cashflow=None):
        self.income_stmt = income_stmt
        self.quarterly_cashflow = quarterly_cashflow
        self.quarterly_income_stmt = quarterly_income_stmt
        self.cashflow = cashflow


def fin_frame(revs_by_year):
    cols = [pd.Timestamp(f"{y}-06-30") for y in revs_by_year]
    return pd.DataFrame([list(revs_by_year.values())], index=["Total Revenue"], columns=cols)


def test_rev_history_years_growth_and_units():
    fin = fin_frame({2022: 100e9, 2023: 110e9, 2024: 121e9, 2025: 133.1e9})
    pts = build_data.rev_history(FakeTicker(fin))
    assert [p["y"] for p in pts] == ["FY22", "FY23", "FY24", "FY25"]
    assert pts[0]["g"] is None                 # no growth for the first year
    assert pts[1]["g"] == 10.0                 # 100 -> 110
    assert pts[3]["r"] == 133.1                # dollars converted to $B


def test_rev_history_keeps_last_four_years():
    fin = fin_frame({y: (100 + y - 2019) * 1e9 for y in range(2019, 2026)})
    pts = build_data.rev_history(FakeTicker(fin))
    assert [p["y"] for p in pts] == ["FY22", "FY23", "FY24", "FY25"]


def test_rev_history_needs_two_points():
    assert build_data.rev_history(FakeTicker(fin_frame({2025: 5e9}))) is None


def test_rev_history_survives_garbage():
    assert build_data.rev_history(FakeTicker(None)) is None
    assert build_data.rev_history(FakeTicker(pd.DataFrame())) is None


# ---------- build_data: quarterly helpers (rev_growth_q, ttm_window, fcf_ttm) ----------

NAN = float("nan")
# Real quarter-end dates, newest first. A 52/53-week year shifts these by a few days,
# which the helpers must tolerate; the tests use calendar quarters.
QTRS = [pd.Timestamp(d) for d in
        ("2026-07-31", "2026-04-30", "2026-01-31", "2025-10-31", "2025-07-31", "2025-04-30")]


def q_frame(rows, dates=QTRS):
    """A quarterly statement. `rows` maps a line to its values, newest first; NAN is a
    quarter Yahoo left empty. Columns come back oldest-first — the opposite of what the
    helpers want — so every test also proves they sort by date."""
    n = len(next(iter(rows.values())))
    df = pd.DataFrame([list(v) for v in rows.values()], index=list(rows), columns=list(dates)[:n])
    return df[sorted(df.columns)]


def test_rev_growth_q_compares_the_latest_quarter_to_the_year_ago_quarter():
    # 2026-07-31 (40) vs 2025-07-31 (10); the older 2025-04-30 (1) must not be used
    inc = q_frame({"Total Revenue": [40e9, 30e9, 20e9, 10e9, 10e9, 1e9]})
    assert build_data.rev_growth_q(FakeTicker(quarterly_income_stmt=inc)) == 300.0


def test_rev_growth_q_skips_an_empty_placeholder_quarter():
    # Yahoo opens the frame with the not-yet-reported quarter: compare 2026-04-30 with 2025-04-30
    inc = q_frame({"Total Revenue": [NAN, 40e9, 30e9, 20e9, 10e9, 10e9]})
    assert build_data.rev_growth_q(FakeTicker(quarterly_income_stmt=inc)) == 300.0


def test_rev_growth_q_is_none_without_the_year_ago_quarter():
    short = q_frame({"Total Revenue": [40e9, 30e9, 20e9, 10e9]})
    assert build_data.rev_growth_q(FakeTicker(quarterly_income_stmt=short)) is None
    # year-ago quarter empty: must not fall back to the quarter fifteen months back
    holed = q_frame({"Total Revenue": [40e9, 30e9, 20e9, 10e9, NAN, 5e9]})
    assert build_data.rev_growth_q(FakeTicker(quarterly_income_stmt=holed)) is None


def test_rev_growth_q_survives_garbage():
    assert build_data.rev_growth_q(FakeTicker(quarterly_income_stmt=None)) is None
    assert build_data.rev_growth_q(FakeTicker(quarterly_income_stmt=pd.DataFrame())) is None


def cash_and_revenue(ocf, capex, rev, cf_dates=QTRS, rev_dates=QTRS, **extra_cf_rows):
    return FakeTicker(quarterly_cashflow=q_frame({"Operating Cash Flow": ocf, "Capital Expenditure": capex,
                                                 **extra_cf_rows}, cf_dates),
                      quarterly_income_stmt=q_frame({"Total Revenue": rev}, rev_dates))


def test_fcf_ttm_is_ocf_plus_capex_not_the_vendor_free_cash_flow_row():
    # the statement's Free Cash Flow row is deliberately wrong — it must be ignored
    t = cash_and_revenue([44e9, 33e9, 22e9, 11e9], [-4e9, -3e9, -2e9, -1e9], [100e9, 90e9, 80e9, 70e9],
                         **{"Free Cash Flow": [44e9, 33e9, 22e9, 11e9]})
    assert build_data.fcf_ttm(t) == (100e9, 110e9, -10e9, 340e9)


def test_fcf_ttm_sums_only_the_four_newest_quarters():
    # a fifth, older quarter with large values must stay out of the sum
    t = cash_and_revenue([44e9, 33e9, 22e9, 11e9, 900e9], [-4e9, -3e9, -2e9, -1e9, -90e9],
                         [100e9, 90e9, 80e9, 70e9, 900e9])
    assert build_data.fcf_ttm(t) == (100e9, 110e9, -10e9, 340e9)


def test_fcf_ttm_skips_an_empty_placeholder_quarter():
    t = cash_and_revenue([NAN, 44e9, 33e9, 22e9, 11e9], [NAN, -4e9, -3e9, -2e9, -1e9],
                         [NAN, 100e9, 90e9, 80e9, 70e9])
    assert build_data.fcf_ttm(t) == (100e9, 110e9, -10e9, 340e9)


def test_fcf_ttm_revenue_covers_the_cash_flow_quarters_not_the_newest_revenue():
    # revenue is already reported for 2026-07-31, cash flow is not: the margin's
    # denominator must be the four quarters the numerator covers, without 2026-07-31
    t = cash_and_revenue([NAN, 44e9, 33e9, 22e9, 11e9], [NAN, -4e9, -3e9, -2e9, -1e9],
                         [999e9, 100e9, 90e9, 80e9, 70e9])
    assert build_data.fcf_ttm(t)[3] == 340e9


def test_fcf_ttm_is_none_when_the_source_carries_no_capex_row():
    t = FakeTicker(quarterly_cashflow=q_frame({"Free Cash Flow": [44e9, 33e9, 22e9, 11e9],
                                               "Operating Cash Flow": [44e9, 33e9, 22e9, 11e9]}))
    assert build_data.fcf_ttm(t) == (None, None, None, None)


def test_fcf_ttm_is_none_when_capex_is_missing_inside_the_year():
    t = cash_and_revenue([44e9, 33e9, 22e9, 11e9, 10e9], [-4e9, NAN, NAN, NAN, -1e9],
                         [100e9, 90e9, 80e9, 70e9, 60e9])
    assert build_data.fcf_ttm(t) == (None, None, None, None)


def test_fcf_ttm_refuses_four_quarters_with_a_hole_in_them():
    # 2026-01-31 never reported: the four newest dates span fifteen months, not a year
    holed = [QTRS[0], QTRS[1], QTRS[3], QTRS[4], QTRS[5]]
    t = cash_and_revenue([44e9, 33e9, 22e9, 11e9, 10e9], [-4e9, -3e9, -2e9, -1e9, -1e9],
                         [100e9, 90e9, 80e9, 70e9, 60e9], cf_dates=holed, rev_dates=holed)
    assert build_data.fcf_ttm(t) == (None, None, None, None)


def test_fcf_ttm_accepts_a_sixteen_week_fiscal_quarter():
    # a 12/12/12/16-week fiscal calendar: 84-, 84- and 112-day gaps are consecutive quarters
    weeks = [pd.Timestamp("2026-05-10"), pd.Timestamp("2026-02-15"), pd.Timestamp("2025-11-23"),
             pd.Timestamp("2025-08-03")]
    t = cash_and_revenue([44e9, 33e9, 22e9, 11e9], [-4e9, -3e9, -2e9, -1e9], [100e9, 90e9, 80e9, 70e9],
                         cf_dates=weeks, rev_dates=weeks)
    assert build_data.fcf_ttm(t) == (100e9, 110e9, -10e9, 340e9)


def test_fcf_ttm_keeps_fcf_when_revenue_is_missing_for_one_of_its_quarters():
    t = cash_and_revenue([44e9, 33e9, 22e9, 11e9], [-4e9, -3e9, -2e9, -1e9], [100e9, NAN, 80e9, 70e9])
    assert build_data.fcf_ttm(t) == (100e9, 110e9, -10e9, None)


def test_fcf_ttm_returns_the_lines_validate_data_reconciles_against():
    fcf, ocf, capex, _ = build_data.fcf_ttm(cash_and_revenue(
        [44e9, 33e9, 22e9, 11e9], [-4e9, -3e9, -2e9, -1e9], [100e9, 90e9, 80e9, 70e9]))
    assert ocf + capex == fcf
    assert capex < 0


def test_fcf_ttm_survives_garbage():
    assert build_data.fcf_ttm(FakeTicker(quarterly_cashflow=None)) == (None, None, None, None)
    assert build_data.fcf_ttm(FakeTicker(quarterly_cashflow=pd.DataFrame())) == (None, None, None, None)


# ---------- build_data.fcf_fy ----------

FY = [pd.Timestamp("2026-01-31"), pd.Timestamp("2025-01-31")]


def annual(rows):
    return pd.DataFrame([list(v) for v in rows.values()], index=list(rows), columns=FY)


def test_fcf_fy_reads_the_year_revhist_is_labelled_with():
    # FY26 revenue not reported yet, so revHist ends at FY25 — while the cash-flow
    # statement already has FY26. The margin must be FY25 cash over FY25 revenue.
    years = FY + [pd.Timestamp("2024-01-31")]
    t = FakeTicker(income_stmt=pd.DataFrame([[NAN, 100e9, 80e9]], index=["Total Revenue"], columns=years),
                   cashflow=pd.DataFrame([[50e9, 20e9, 10e9], [-10e9, -5e9, -2e9]],
                                         index=["Operating Cash Flow", "Capital Expenditure"], columns=years))
    fy_end = build_data.rev_history(t)[-1]["end"]
    assert fy_end == "2025-01-31"
    assert build_data.fcf_fy(t, fy_end) == (15e9, 100e9)


def test_fcf_fy_is_none_without_a_capex_row():
    t = FakeTicker(income_stmt=annual({"Total Revenue": [200e9, 100e9]}),
                   cashflow=annual({"Free Cash Flow": [96e9, 60e9], "Operating Cash Flow": [102e9, 64e9]}))
    assert build_data.fcf_fy(t, "2026-01-31") == (None, None)


def test_fcf_fy_is_none_when_that_year_is_absent():
    t = FakeTicker(income_stmt=annual({"Total Revenue": [200e9, 100e9]}),
                   cashflow=annual({"Operating Cash Flow": [102e9, 64e9], "Capital Expenditure": [-6e9, -3e9]}))
    assert build_data.fcf_fy(t, "2024-01-31") == (None, None)


def test_fcf_fy_survives_garbage():
    assert build_data.fcf_fy(FakeTicker(cashflow=None), "2026-01-31") == (None, None)
    assert build_data.fcf_fy(FakeTicker(cashflow=pd.DataFrame()), "2026-01-31") == (None, None)


# ---------- build_prices.tech_from_close ----------

def noisy_uptrend(n=320):
    steps = [1.0 if i % 2 == 0 else -0.3 for i in range(n)]
    prices = [100.0]
    for s in steps[1:]:
        prices.append(prices[-1] + s)
    return pd.Series(prices, index=pd.bdate_range("2024-01-01", periods=n))


def test_tech_from_close_uptrend():
    close = noisy_uptrend()
    tech = build_prices.tech_from_close(close)
    assert tech["px"] == round(close.iloc[-1], 2)
    assert tech["ma50"] < tech["px"]           # uptrend: price above its MAs
    assert tech["ma200"] < tech["ma50"]
    assert 60 < tech["rsi"] <= 100             # gains dominate
    assert 8 <= len(tech["c"]) <= build_prices.WEEKS
    assert len(tech["c"]) == len(tech["m50"]) == len(tech["m200"])


def test_tech_from_close_rejects_short_series():
    short = pd.Series(range(10), index=pd.bdate_range("2024-01-01", periods=10), dtype=float)
    assert build_prices.tech_from_close(short) is None
    assert build_prices.tech_from_close(None) is None


# ---------- build_flow: insider classification + open-market summary ----------

def test_classify_insider_types():
    assert build_flow.classify_insider("Sale at price 842.10 per share") == "Sell"
    assert build_flow.classify_insider("Purchase at price 101.10") == "Buy"
    assert build_flow.classify_insider("Stock Gift") == "Gift"
    assert build_flow.classify_insider("Exercise of employee stock option") == "Exercise"
    assert build_flow.classify_insider("Restricted Stock Award (grant)") == "Grant"
    assert build_flow.classify_insider("Conversion of derivative security") == "Other"


def test_classify_insider_sale_wins_over_option_wording():
    # a same-day "sale ... acquired via option" line must read as a Sell
    assert build_flow.classify_insider("Sale of shares acquired upon option exercise") == "Sell"


def test_open_market_summary_excludes_grants_gifts_exercises():
    rows = [
        ("Purchase at price 10.00", 1000.0),
        ("Purchase at price 11.00", float("nan")),   # NaN value counts as 0, still a buy
        ("Sale at price 12.00", 2000.0),
        ("Restricted Stock Award (grant)", 99999.0),  # excluded
        ("Gift of shares", 500.0),                    # excluded
        ("Exercise of stock option", 700.0),          # excluded
        (None, 300.0),                                # excluded
    ]
    om = build_flow.open_market_summary(rows)
    assert om == {"buyN": 2, "buyVal": 1000, "sellN": 1, "sellVal": 2000}


def test_open_market_summary_none_when_no_trades():
    assert build_flow.open_market_summary([("Gift of shares", 1.0)]) is None
    assert build_flow.open_market_summary([]) is None


# ---------- build_events: SEC filing classification + event selection ----------

def test_classify_8k_item_priority():
    # a deal completion outranks the routine results item on the same filing
    assert build_events.classify_filing("8-K", "2.01,2.02,9.01") == ("ma", "M&A / disposition completed")
    assert build_events.classify_filing("8-K", "2.02,9.01") == ("results", "Results announced")
    assert build_events.classify_filing("8-K/A", "5.02") == ("mgmt", "Leadership change")
    assert build_events.classify_filing("8-K", "1.02") == ("agmt", "Agreement terminated")


def test_classify_skips_unclassifiable_8ks():
    assert build_events.classify_filing("8-K", "7.01") is None      # Reg FD
    assert build_events.classify_filing("8-K", "8.01,9.01") is None  # Other Events
    assert build_events.classify_filing("8-K", "") is None


def test_classify_forms():
    assert build_events.classify_filing("425", "")[0] == "merger"
    assert build_events.classify_filing("S-4", "")[0] == "merger"
    assert build_events.classify_filing("SC 13D/A", "")[0] == "activist"
    assert build_events.classify_filing("SC 13G", "")[0] == "stake"
    assert build_events.classify_filing("SC 13G/A", "") is None      # amendments = noise
    assert build_events.classify_filing("10-Q", "") == ("periodic", "10-Q filed")
    for noise in ("4", "144", "424B2", "FWP", "6-K", "DEF 14A"):
        assert build_events.classify_filing(noise, "") is None


def fake_recent(rows):
    """rows: list of (date, form, items) -> EDGAR filings.recent parallel arrays."""
    return {
        "filingDate":      [r[0] for r in rows],
        "form":            [r[1] for r in rows],
        "items":           [r[2] for r in rows],
        "accessionNumber": [f"0000000000-26-{i:06d}" for i in range(len(rows))],
        "primaryDocument": ["doc.htm"] * len(rows),
    }


def test_select_events_window_sort_and_cap():
    import datetime
    today = datetime.date(2026, 7, 18)
    rows = [("2026-07-01", "8-K", "2.02,9.01"),
            ("2026-07-10", "425", ""),
            ("2025-01-01", "8-K", "2.01"),      # outside the window -> dropped
            ("2026-06-01", "4", ""),            # noise form -> dropped
            ("2026-07-15", "10-Q", "")]
    evs = build_events.select_events(fake_recent(rows), today=today)
    assert [e["d"] for e in evs] == ["2026-07-15", "2026-07-10", "2026-07-01"]  # newest first
    assert [e["c"] for e in evs] == ["periodic", "merger", "results"]
    assert all("-" not in e["a"] for e in evs)   # accession stored dash-less for URLs
    assert evs[2]["i"] == "2.02,9.01"            # 8-K keeps its item codes
    assert "i" not in evs[1]                     # non-8-K rows carry no items key
    capped = build_events.select_events(
        fake_recent([("2026-07-%02d" % (1 + i % 9), "8-K", "2.02") for i in range(20)]),
        today=today, cap=5)
    assert len(capped) == 5
