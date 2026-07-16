"""Unit tests for the pure computation inside the data pipelines.

The pipeline modules guard their yfinance import, so these tests run anywhere
Python + pandas exist (locally and in CI) with no network access.

Run: python -m pytest -q
"""
import pandas as pd

import build_data
import build_flow
import build_prices


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
