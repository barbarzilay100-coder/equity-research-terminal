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
import build_sec


# ---------- build_data.rev_history ----------

class FakeTicker:
    def __init__(self, income_stmt):
        self.income_stmt = income_stmt


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


def test_equity_of_prefers_book_value_and_falls_back_to_the_ratio():
    """Yahoo has no equity field: book value per share x shares is the direct
    route, debt / debt-to-equity the fallback when either input is missing."""
    direct = build_data.equity_of({"bookValue": 5.0, "sharesOutstanding": 2e9,
                                   "totalDebt": 1e9, "debtToEquity": 50.0})
    assert direct == 10.0                      # 5 x 2bn shares, not 1bn / 0.5
    fallback = build_data.equity_of({"totalDebt": 1e9, "debtToEquity": 50.0})
    assert fallback == 2.0
    assert build_data.equity_of({"bookValue": 5.0}) is None


# ---------- build_sec: choosing the right fact ----------

def facts_of(*rows):
    """rows: (tag, val, start, end, form, filed, accn) -> a companyfacts-shaped dict."""
    out = {}
    for tag, val, start, end, form, filed, accn in rows:
        f = {"val": val, "end": end, "form": form, "filed": filed, "accn": accn}
        if start:
            f["start"] = start
        out.setdefault("us-gaap", {}).setdefault(tag, {"units": {"USD": []}})
        out["us-gaap"][tag]["units"]["USD"].append(f)
    return out


def test_annual_fact_prefers_newest_period_over_tag_priority():
    """A tag a filer abandoned still holds real values; taking the first tag that
    has data would report a years-old revenue as current (NVDA, XOM both do this)."""
    facts = facts_of(
        ("RevenueFromContractWithCustomerExcludingAssessedTax", 26_914e6,
         "2021-01-25", "2022-01-30", "10-K", "2022-03-18", "a-22"),
        ("Revenues", 215_938e6, "2025-01-27", "2026-01-25", "10-K", "2026-02-26", "a-26"),
    )
    fact, tag = build_sec.annual_fact(facts, build_sec.DURATION_TAGS["revenue"])
    assert fact["val"] == 215_938e6 and tag[1] == "Revenues"


def test_annual_fact_rejects_a_quarter_tagged_as_full_year():
    """`fp: FY` is not evidence of an annual period -- the period length is."""
    facts = facts_of(
        ("Revenues", 1_500e6, "2025-10-01", "2025-12-31", "10-K", "2026-02-01", "q4"),
        ("Revenues", 17_258e6, "2025-01-01", "2025-12-31", "10-K", "2026-02-01", "fy"),
    )
    fact, _ = build_sec.annual_fact(facts, build_sec.DURATION_TAGS["revenue"])
    assert fact["val"] == 17_258e6


def test_annual_fact_lets_a_restatement_supersede_the_original():
    facts = facts_of(
        ("Revenues", 100e9, "2024-01-01", "2024-12-31", "10-K", "2025-02-01", "orig"),
        ("Revenues", 98e9, "2024-01-01", "2024-12-31", "10-K", "2026-02-01", "restated"),
    )
    fact, _ = build_sec.annual_fact(facts, build_sec.DURATION_TAGS["revenue"])
    assert fact["val"] == 98e9


def test_extract_labels_the_year_from_the_period_not_the_filing():
    """A comparative carries the filing's `fy`, which is a year ahead of its period."""
    facts = facts_of(("Revenues", 2e9, "2025-01-01", "2025-12-31", "20-F", "2026-04-01", "x"))
    facts["us-gaap"]["Revenues"]["units"]["USD"][0]["fy"] = 2026
    blk = build_sec.extract(facts, 1234)
    assert blk["fy"] == "FY2025"


def test_extract_keeps_every_figure_inside_one_filing():
    """Balance-sheet rows must come from the same filing as the revenue row."""
    facts = facts_of(
        ("Revenues", 10e9, "2025-01-01", "2025-12-31", "10-K", "2026-02-01", "new"),
        ("StockholdersEquity", 5e9, None, "2025-12-31", "10-K", "2026-02-01", "new"),
        ("StockholdersEquity", 4e9, None, "2024-12-31", "10-K", "2025-02-01", "old"),
    )
    blk = build_sec.extract(facts, 1234)
    assert blk["accn"] == "new" and blk["equity"] == 5.0


def test_extract_omits_debt_when_only_the_current_portion_is_tagged():
    """Filers who tag debt per instrument expose only the current slice at entity
    level; summing it alone understates total debt, so no row is emitted."""
    facts = facts_of(
        ("Revenues", 17e9, "2025-01-01", "2025-12-31", "20-F", "2026-02-01", "x"),
        ("LongTermDebtCurrent", 1.8e9, None, "2025-12-31", "20-F", "2026-02-01", "x"),
    )
    assert "debt" not in build_sec.extract(facts, 1234)


def test_extract_adds_the_current_portion_to_a_noncurrent_balance():
    facts = facts_of(
        ("Revenues", 275e9, "2025-01-01", "2025-12-31", "10-K", "2026-02-01", "x"),
        ("LongTermDebtNoncurrent", 5.0e9, None, "2025-12-31", "10-K", "2026-02-01", "x"),
        ("LongTermDebtCurrent", 0.8e9, None, "2025-12-31", "10-K", "2026-02-01", "x"),
    )
    assert build_sec.extract(facts, 1234)["debt"] == 5.8


def test_extract_computes_fcf_only_with_both_legs():
    base = [("Revenues", 10e9, "2025-01-01", "2025-12-31", "10-K", "2026-02-01", "x"),
            ("NetCashProvidedByUsedInOperatingActivities", 3e9, "2025-01-01", "2025-12-31",
             "10-K", "2026-02-01", "x")]
    assert "fcf" not in build_sec.extract(facts_of(*base), 1234)   # bank: no capex tag
    with_capex = base + [("PaymentsToAcquirePropertyPlantAndEquipment", 1e9,
                          "2025-01-01", "2025-12-31", "10-K", "2026-02-01", "x")]
    assert build_sec.extract(facts_of(*with_capex), 1234)["fcf"] == 2.0
