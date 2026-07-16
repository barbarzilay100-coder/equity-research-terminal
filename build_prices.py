#!/usr/bin/env python3
"""Equity Research Terminal - price & technicals enrichment.

Adds a compact `tech` block (weekly price series + 50/200-day moving averages
+ latest RSI) to each company already present in data.json, WITHOUT re-pulling
fundamentals. Runs in two phases so it survives short-lived shells / rate limits:

    python build_prices.py fetch START END   # fetch a slice -> /tmp/tech_partials
    python build_prices.py merge              # fold all partials into data.json/js

The same compute_tech() function is imported by build_data.py so a full rebuild
also produces technicals.
"""
import json, sys, os, glob, datetime
try:
    import yfinance as yf
except ImportError:   # pure helpers stay importable (and unit-testable) without yfinance
    yf = None

HERE = os.path.dirname(os.path.abspath(__file__))
PARTIAL_DIR = os.environ.get("PARTIAL_DIR", "/tmp/tech_partials")
WEEKS = 52  # weekly points kept for the chart (~1 year)


def _round(v, n=2):
    try:
        f = float(v)
        if f != f:
            return None
        return round(f, n)
    except Exception:
        return None


def compute_tech(ticker, period="2y"):
    """Fetch daily history for one ticker and return a compact technicals dict, or None."""
    try:
        t = yf.Ticker(ticker)
        h = t.history(period=period, interval="1d")
        if h is None or h.empty or "Close" not in h.columns:
            return None
        return tech_from_close(h["Close"].dropna())
    except Exception as e:
        print(f"    tech ERR {ticker}: {e}")
        return None


def tech_from_close(close):
    """Pure computation: weekly series + 50/200-day MAs + Wilder RSI from a
    daily Close series. Split from compute_tech so it is unit-testable."""
    import pandas as pd  # local import so build_data.py stays light
    if close is None or len(close) < 30:
        return None

    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()

    # Wilder RSI(14) on daily closes
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - 100 / (1 + rs)

    # weekly downsample (last trading day of each week) for the chart
    df = pd.DataFrame({"c": close, "m50": ma50, "m200": ma200})
    wk = df.resample("W-FRI").last().dropna(subset=["c"]).tail(WEEKS)
    if len(wk) < 8:
        return None

    c_arr = [_round(v, 2) for v in wk["c"].tolist()]
    m50_arr = [_round(v, 2) for v in wk["m50"].tolist()]
    m200_arr = [_round(v, 2) for v in wk["m200"].tolist()]

    return {
        "t0": wk.index[0].strftime("%Y-%m-%d"),
        "c": c_arr,
        "m50": m50_arr,
        "m200": m200_arr,
        "rsi": _round(rsi.iloc[-1], 1),
        "px": _round(close.iloc[-1], 2),
        "ma50": _round(ma50.iloc[-1], 2),
        "ma200": _round(ma200.iloc[-1], 2),
    }


def load_tickers():
    with open(os.path.join(HERE, "data.json")) as f:
        data = json.load(f)
    return data, [c["ticker"] for c in data["companies"]]


def phase_fetch(a, b):
    os.makedirs(PARTIAL_DIR, exist_ok=True)
    _, tickers = load_tickers()
    sl = tickers[a:b]
    out = {}
    for n, tk in enumerate(sl, 1):
        # data.json stores BRK.B; yfinance wants BRK-B
        yf_tk = tk.replace(".", "-")
        tech = compute_tech(yf_tk)
        status = "OK " if tech else "SKIP"
        px = tech["px"] if tech else "-"
        print(f"[{a+n}/{len(tickers)}] {status} {tk:6} px={px}")
        if tech:
            out[tk] = tech
    path = os.path.join(PARTIAL_DIR, f"tech_{a}_{b}.json")
    with open(path, "w") as f:
        json.dump(out, f)
    print(f"wrote {len(out)} -> {path}")


def phase_merge():
    data, _ = load_tickers()
    tech_map = {}
    for p in sorted(glob.glob(os.path.join(PARTIAL_DIR, "tech_*.json"))):
        with open(p) as f:
            tech_map.update(json.load(f))
    hit = 0
    for c in data["companies"]:
        if c["ticker"] in tech_map:
            c["tech"] = tech_map[c["ticker"]]
            hit += 1
    data["techGenerated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%b %d, %Y")
    with open(os.path.join(HERE, "data.json"), "w") as f:
        json.dump(data, f, separators=(",", ":"))
    with open(os.path.join(HERE, "data.js"), "w") as f:
        f.write("window.DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print(f"MERGED tech into {hit}/{len(data['companies'])} companies -> data.json / data.js")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "merge"
    if cmd == "fetch":
        a = int(sys.argv[2]); b = int(sys.argv[3])
        phase_fetch(a, b)
    elif cmd == "merge":
        phase_merge()
    else:
        print("usage: build_prices.py fetch START END | merge")
