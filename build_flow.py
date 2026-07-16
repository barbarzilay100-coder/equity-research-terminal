#!/usr/bin/env python3
"""Equity Research Terminal - smart-money enrichment.

Adds a compact `flow` block to each company in data.json (no re-pull of
fundamentals), covering three free "smart money" signals from yfinance:

  1. Institutional ownership   (major_holders)         -> instPct, insiderPct, instCount
  2. 13F position changes      (institutional_holders)  -> topHolders[] with pctChange
  3. Insider activity (Form 4) (insider_purchases +     -> insider6m{} + recentInsider[]
                                insider_transactions)

Two phases so it survives short shells / rate limits:
    python build_flow.py fetch START END   # slice -> /tmp/flow_partials
    python build_flow.py merge             # fold into data.json / data.js
"""
import json, sys, os, glob, datetime
try:
    import yfinance as yf
except ImportError:   # pure helpers stay importable (and unit-testable) without yfinance
    yf = None

HERE = os.path.dirname(os.path.abspath(__file__))
PARTIAL_DIR = os.environ.get("FLOW_PARTIAL_DIR", "/tmp/flow_partials")


def _round(v, n=2):
    try:
        f = float(v)
        if f != f:
            return None
        return round(f, n)
    except Exception:
        return None


def _int(v):
    try:
        f = float(v)
        if f != f:
            return None
        return int(round(f))
    except Exception:
        return None


def classify_insider(blob):
    """Map a raw Form-4 transaction description to a display type.
    Order matters: sale wording wins over the 'stock' in 'stock option', and
    gifts/exercises/grants are separated out so they never read as real buys."""
    blob = blob.lower()
    if "sale" in blob or "sold" in blob or "sell" in blob:
        return "Sell"
    if "purchase" in blob or "bought" in blob or "buy" in blob:
        return "Buy"
    if "gift" in blob:
        return "Gift"
    if "exercise" in blob or "option" in blob:
        return "Exercise"
    if "award" in blob or "grant" in blob or "acqui" in blob:
        return "Grant"
    return "Other"


def open_market_summary(rows):
    """Count/value of genuine open-market Purchases vs Sales from (text, value)
    pairs — grants, awards, gifts and option exercises are excluded, since they
    pollute the raw buy count. Returns None when no open-market activity."""
    buyN = sellN = 0
    buyV = sellV = 0.0
    for text, val in rows:
        low = str(text or "").lower()
        val = float(val) if val is not None and val == val else 0.0
        if "purchase" in low:
            buyN += 1; buyV += val
        elif "sale" in low:
            sellN += 1; sellV += val
    if buyN or sellN:
        return {"buyN": buyN, "buyVal": int(buyV), "sellN": sellN, "sellVal": int(sellV)}
    return None


def compute_flow(ticker):
    """Return a compact smart-money dict for one ticker, or None."""
    try:
        t = yf.Ticker(ticker)
        out = {}

        # --- 1. institutional / insider ownership snapshot ---
        try:
            mh = t.major_holders
            if mh is not None and hasattr(mh, "columns") and "Value" in mh.columns:
                vd = mh["Value"].to_dict()
                ip_ = vd.get("institutionsPercentHeld")
                is_ = vd.get("insidersPercentHeld")
                ic_ = vd.get("institutionsCount")
                out["instPct"] = _round(ip_ * 100, 1) if ip_ is not None else None
                out["insiderPct"] = _round(is_ * 100, 2) if is_ is not None else None
                out["instCount"] = _int(ic_)
        except Exception:
            pass

        # --- 2. top institutional holders + quarter-over-quarter change (13F) ---
        try:
            ih = t.institutional_holders
            if ih is not None and not ih.empty:
                holders = []
                for _, r in ih.head(8).iterrows():
                    p = r.get("pctHeld"); chg = r.get("pctChange")
                    p = float(p) if p is not None and p == p else None
                    chg = float(chg) if chg is not None and chg == chg else None
                    holders.append({
                        "h": str(r.get("Holder"))[:28],
                        "p": _round(p * 100, 2) if p is not None else None,
                        "chg": _round(chg * 100, 1) if chg is not None else None,
                    })
                out["topHolders"] = holders
                try:
                    dts = ih["Date Reported"].dropna().astype(str).tolist()
                    out["asof"] = max(dts)[:10] if dts else None
                except Exception:
                    pass
        except Exception:
            pass

        # --- 3a. insider buy/sell summary, last 6 months ---
        try:
            ip = t.insider_purchases
            if ip is not None and not ip.empty:
                lab = ip.columns[0]
                m = {str(r[lab]): r for _, r in ip.iterrows()}

                def g(key, col="Shares"):
                    r = m.get(key)
                    if r is None:
                        return None
                    return _round(r.get(col), 4) if col.startswith("%") or key.startswith("%") else _int(r.get(col))

                buypct = m.get("% Buy Shares")
                sellpct = m.get("% Sell Shares")
                netpct = m.get("% Net Shares Purchased (Sold)")
                out["insider6m"] = {
                    "buys": _int(g("Purchases")), "buysT": _int(g("Purchases", "Trans")),
                    "sells": _int(g("Sales")), "sellsT": _int(g("Sales", "Trans")),
                    "net": _int(g("Net Shares Purchased (Sold)")),
                    "buyPct": _round(float(buypct["Shares"]) * 100, 1) if buypct is not None and buypct["Shares"] == buypct["Shares"] else None,
                    "sellPct": _round(float(sellpct["Shares"]) * 100, 1) if sellpct is not None and sellpct["Shares"] == sellpct["Shares"] else None,
                    "netPct": _round(float(netpct["Shares"]) * 100, 1) if netpct is not None and netpct["Shares"] == netpct["Shares"] else None,
                }
        except Exception:
            pass

        # --- 3b. recent individual insider transactions ---
        try:
            it = t.insider_transactions
            if it is not None and not it.empty:
                recent = []
                for _, r in it.head(6).iterrows():
                    blob = str(r.get("Transaction") or "") + " " + str(r.get("Text") or "")
                    typ = classify_insider(blob)
                    recent.append({
                        "i": str(r.get("Insider"))[:24],
                        "pos": (str(r.get("Position"))[:22] if r.get("Position") is not None else ""),
                        "t": typ,
                        "sh": _int(r.get("Shares")),
                        "v": _int(r.get("Value")),
                        "d": (str(r.get("Start Date"))[:10] if r.get("Start Date") is not None else ""),
                    })
                out["recentInsider"] = recent

                om = open_market_summary((r.get("Text"), r.get("Value")) for _, r in it.iterrows())
                if om:
                    out["om"] = om
        except Exception:
            pass

        if not out or all(k not in out for k in ("instPct", "topHolders", "insider6m")):
            return None
        return out
    except Exception as e:
        print(f"    flow ERR {ticker}: {e}")
        return None


def load_data():
    with open(os.path.join(HERE, "data.json")) as f:
        return json.load(f)


def phase_fetch(a, b):
    os.makedirs(PARTIAL_DIR, exist_ok=True)
    data = load_data()
    tickers = [c["ticker"] for c in data["companies"]]
    sl = tickers[a:b]
    out = {}
    for n, tk in enumerate(sl, 1):
        yf_tk = tk.replace(".", "-")
        flow = compute_flow(yf_tk)
        if flow:
            out[tk] = flow
            inst = flow.get("instPct"); net = (flow.get("insider6m") or {}).get("net")
            print(f"[{a+n}/{len(tickers)}] OK  {tk:6} inst={inst}%  insiderNet={net}")
        else:
            print(f"[{a+n}/{len(tickers)}] SKIP {tk}")
    path = os.path.join(PARTIAL_DIR, f"flow_{a}_{b}.json")
    with open(path, "w") as f:
        json.dump(out, f)
    print(f"wrote {len(out)} -> {path}")


def phase_merge():
    data = load_data()
    flow_map = {}
    for p in sorted(glob.glob(os.path.join(PARTIAL_DIR, "flow_*.json"))):
        with open(p) as f:
            flow_map.update(json.load(f))
    hit = 0
    for c in data["companies"]:
        if c["ticker"] in flow_map:
            c["flow"] = flow_map[c["ticker"]]
            hit += 1
    data["flowGenerated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%b %d, %Y")
    with open(os.path.join(HERE, "data.json"), "w") as f:
        json.dump(data, f, separators=(",", ":"))
    with open(os.path.join(HERE, "data.js"), "w") as f:
        f.write("window.DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print(f"MERGED flow into {hit}/{len(data['companies'])} companies -> data.json / data.js")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "merge"
    if cmd == "fetch":
        phase_fetch(int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "merge":
        phase_merge()
    else:
        print("usage: build_flow.py fetch START END | merge")
