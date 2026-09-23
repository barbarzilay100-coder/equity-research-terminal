#!/usr/bin/env python3
"""Equity Research Terminal - data pipeline.
Pulls fundamentals via yfinance for a universe of tickers and writes data.json.
Runs locally or in GitHub Actions (daily). No API key required.
"""
import json, sys, time, datetime, os
try:
    import yfinance as yf
except ImportError:   # pure helpers stay importable (and unit-testable) without yfinance
    yf = None

UNIVERSE = [
    # Mega-cap tech
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META","AVGO","ORCL","CRM","ADBE","AMD","INTC",
    "QCOM","TXN","CSCO","IBM","NOW","INTU","AMAT","MU","ADI","LRCX","KLAC","SNPS","CDNS",
    "PLTR","PANW","CRWD","SNOW","DDOG","NET","MDB","ZS","TEAM","WDAY","ANET","MRVL","SMCI",
    # Fintech / payments / crypto-adjacent (relevant to finance & crypto focus)
    "V","MA","PYPL","COIN","HOOD","SOFI","AXP","FIS","FI","GPN","AFRM","NU","MELI",
    # Financials
    "JPM","BAC","WFC","GS","MS","C","SCHW","BLK","BX","KKR","SPGI","ICE","CME","MCO","BRK-B",
    # Consumer / internet
    "TSLA","NFLX","DIS","UBER","ABNB","BKNG","SBUX","MCD","NKE","COST","WMT","TGT","HD","LOW",
    "PG","KO","PEP","PM","CMG","LULU","ROST",
    # Healthcare
    "UNH","JNJ","LLY","PFE","MRK","ABBV","TMO","ABT","DHR","AMGN","ISRG","VRTX","GILD",
    # Industrial / energy / other
    "XOM","CVX","CAT","BA","GE","HON","UPS","RTX","DE","LMT","UNP","LIN","NEE",
    "T","VZ","TMUS","CMCSA",
    # Israeli dual-listed (NASDAQ/NYSE + TASE)
    "TEVA","NICE","CHKP","WIX","MNDY","CYBR","ESLT",
    # Personal watchlist -- small caps; tagged watch=True and hidden from the public views by default
    "TE","ONDS","NBIS","LAES",
]
UNIVERSE = list(dict.fromkeys(UNIVERSE))  # guard against accidental duplicates

# Personal watchlist: these get the full analysis pipeline but are tagged watch=True so the
# front end keeps them off the public overview / leaderboards / screener unless opted in.
WATCHLIST = {"TE", "ONDS", "NBIS", "LAES"}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root: data.json lives there, this script in pipeline/

def num(v):
    try:
        f=float(v)
        if f!=f: return None
        return f
    except Exception: return None

def to_b(v, nd=3):
    v=num(v)
    return round(v/1e9,nd) if v is not None else None

EXACT=6  # $B decimals ($1K) for the fields validate_data.py reconciles, so input rounding never trips a check

def pct(v):
    v=num(v)
    return round(v*100,2) if v is not None else None

RATING={"strong_buy":"Strong Buy","buy":"Buy","hold":"Hold","underperform":"Underperform","sell":"Sell","none":None}

def rev_history(t):
    try:
        fin=t.income_stmt
        if fin is None or fin.empty or "Total Revenue" not in fin.index: return None
        row=fin.loc["Total Revenue"].dropna()
        cols=sorted(row.index)  # ascending by date
        pts=[]
        prev=None
        for c in cols[-4:]:
            r=num(row[c])
            if r is None: continue
            y="FY"+str(getattr(c,"year",c))[2:]
            g=round((r-prev)/prev*100,1) if prev else None
            end=getattr(c,"date",lambda:None)()
            pts.append({"y":y,"r":round(r/1e9,2),"g":g,"end":end.isoformat() if end else None})
            prev=r
        return pts if len(pts)>=2 else None
    except Exception:
        return None

def rev_growth_q(t):
    """Latest reported quarter vs the year-ago quarter, in percent.

    Recomputed rather than taken from info["revenueGrowth"], so the period is the
    pipeline's own definition. The year-ago quarter is found by date, not by
    position: Yahoo's quarterly frame can open with an empty placeholder column or
    skip a quarter, and either one shifts positions. A trailing-twelve-month growth
    read is NOT available — Yahoo caps the quarterly statement at five quarters, so
    the year-ago TTM window cannot be summed; the longer read is the fiscal year."""
    try:
        row = t.quarterly_income_stmt.loc["Total Revenue"].dropna()
        if row.empty: return None
        newest = max(row.index)
        ago = [c for c in row.index if 350 <= (newest - c).days <= 380]
        if len(ago) != 1: return None
        new, old = num(row[newest]), num(row[ago[0]])
        return round((new - old) / old * 100, 2) if (new is not None and old) else None
    except Exception:
        return None

def ttm_window(q, rows):
    """The four newest consecutive quarter-end dates on which every one of `rows`
    is reported, newest first — or None.

    Skips the empty placeholder column Yahoo adds for a quarter it has not filled
    yet. Refuses a window with a hole in it: four quarters spread over fifteen
    months are not a year. Quarters may run 12 to 16 weeks (a 12/12/12/16-week
    fiscal calendar, or a 53-week year), so consecutive means 75-125 days apart;
    a missing quarter leaves a gap of about 180."""
    if q is None or q.empty or any(r not in q.index for r in rows): return None
    cols = [c for c in sorted(q.columns, reverse=True)
            if all(num(q.loc[r, c]) is not None for r in rows)][:4]
    if len(cols) < 4: return None
    if any(not 75 <= (a - b).days <= 125 for a, b in zip(cols, cols[1:])): return None
    return cols

def fcf_ttm(t):
    """Trailing-twelve-month free cash flow, the two lines it is built from, and the
    revenue of the same four quarters.

    Operating cash flow plus capital expenditure (capex is negative on the
    statement), summed here rather than read from a ready-made field:
    info["freeCashflow"] is Yahoo's "levered free cash flow", which deducts far more
    than capex, and the statement's own Free Cash Flow row is operating cash flow
    relabelled whenever the capex row is missing. Revenue is summed over the exact
    quarter dates of the cash flow, so a margin's numerator and denominator cover
    the same twelve months.

    Returns (fcf, ocf, capex, revenue) in dollars. All None unless four consecutive
    quarters of both cash-flow lines are reported — a partial year is worse than no
    number. Revenue alone is None if any of those quarters lacks it."""
    try:
        cf = t.quarterly_cashflow
        cols = ttm_window(cf, ["Operating Cash Flow", "Capital Expenditure"])
        if cols is None: return None, None, None, None
        ocf = sum(num(cf.loc["Operating Cash Flow", c]) for c in cols)
        capex = sum(num(cf.loc["Capital Expenditure", c]) for c in cols)
    except Exception:
        return None, None, None, None
    rev = None
    try:
        inc = t.quarterly_income_stmt
        revs = [num(inc.loc["Total Revenue", c]) if c in inc.columns else None for c in cols]
        if all(v is not None for v in revs): rev = sum(revs)
    except Exception:
        pass
    return ocf + capex, ocf, capex, rev

def fcf_fy(t, fy_end):
    """Free cash flow and revenue for the fiscal year that ends on fy_end (ISO date).

    fy_end comes from revHist, so the margin's numerator, its denominator and the
    "FY ended" date shown beside it are the same year by construction; the newest
    cash-flow column is not assumed to be that year. Same rule as fcf_ttm:
    operating cash flow plus capex, None when either line is missing.
    Returns (fcf, revenue) in dollars."""
    try:
        cf, inc = t.cashflow, t.income_stmt
        col = next(c for c in cf.columns if c.date().isoformat() == fy_end)
        ocf = num(cf.loc["Operating Cash Flow", col])
        capex = num(cf.loc["Capital Expenditure", col])
        if ocf is None or capex is None: return None, None
        return ocf + capex, num(inc.loc["Total Revenue", col])
    except Exception:
        return None, None

def build(tk):
    t=yf.Ticker(tk)
    i=t.info
    if not i or not i.get("shortName"): return None
    price=num(i.get("currentPrice") or i.get("regularMarketPrice"))
    if price is None: return None
    high=num(i.get("fiftyTwoWeekHigh"))
    fcf_raw,ocf_raw,capex_raw,rev_raw=fcf_ttm(t)
    fcf,rev_ttm=to_b(fcf_raw,EXACT),to_b(rev_raw,EXACT)
    rh=rev_history(t)
    fy_end=rh[-1]["end"] if rh else None
    fy_fcf_raw,fy_rev_raw=fcf_fy(t,fy_end) if fy_end else (None,None)
    fcf_fy_b,rev_fy=to_b(fy_fcf_raw,EXACT),to_b(fy_rev_raw,EXACT)
    ev=to_b(i.get("enterpriseValue"))
    de=num(i.get("debtToEquity"))
    ptavg=num(i.get("targetMeanPrice"))
    d={
      "ticker":tk.replace("-","."),
      "name":i.get("shortName"),
      "sector":i.get("sector") or "—",
      "industry":i.get("industry") or "",
      "price":round(price,2),
      "marketCap":to_b(i.get("marketCap")),
      "ev":ev,
      "high52":round(high,2) if high else None,
      "distHigh":round((price-high)/high*100,1) if high else None,
      "revHist":rh,
      "revGrowthFY":rh[-1]["g"] if rh else None,   # latest full fiscal year vs the prior one
      "fyEnd":fy_end,                              # that year's end date — FY figures lag by up to a year
      "revGrowthQ":rev_growth_q(t),                # latest reported quarter vs the year-ago quarter
      "earnGrowth":pct(i.get("earningsGrowth")),
      "netMargin":pct(i.get("profitMargins")),
      "grossMargin":pct(i.get("grossMargins")),
      "ebitdaMargin":pct(i.get("ebitdaMargins")),
      "eps":num(i.get("trailingEps")),
      "netIncome":to_b(i.get("netIncomeToCommon")),
      # margins are computed from the stored values, so validate_data.py reproduces them exactly
      "revTTM":rev_ttm,        # revenue over the same four quarters as fcf
      "fcf":fcf,
      "ocf":to_b(ocf_raw,EXACT),     # the two statement lines fcf is built from
      "capex":to_b(capex_raw,EXACT),
      "fcfMargin":round(fcf/rev_ttm*100,2) if (fcf is not None and rev_ttm) else None,
      "revFY":rev_fy,          # revHist[-1] at full precision
      "fcfFY":fcf_fy_b,
      "fcfMarginFY":round(fcf_fy_b/rev_fy*100,2) if (fcf_fy_b is not None and rev_fy) else None,
      "cash":to_b(i.get("totalCash")),
      "debt":to_b(i.get("totalDebt")),
      "debtEquity":round(de/100,2) if de is not None else None,
      "currentRatio":num(i.get("currentRatio")),
      "roe":pct(i.get("returnOnEquity")),
      "pe":num(i.get("trailingPE")),
      "forwardPE":num(i.get("forwardPE")),
      "peg":num(i.get("trailingPegRatio")),
      "evEbitda":num(i.get("enterpriseToEbitda")),
      "evFcf":round(ev/fcf,1) if (ev and fcf and fcf>0) else None,   # a negative multiple means nothing
      "divYield":num(i.get("dividendYield")),
      "rating":RATING.get((i.get("recommendationKey") or "none"),None),
      "numAnalysts":num(i.get("numberOfAnalystOpinions")),
      "ptAvg":round(ptavg,2) if ptavg else None,
      "ptLow":num(i.get("targetLowPrice")),
      "ptHigh":num(i.get("targetHighPrice")),
      "upside":round((ptavg-price)/price*100,1) if ptavg else None,
      "summary":(i.get("longBusinessSummary") or "").strip(),
    }
    if tk in WATCHLIST:
        d["watch"] = True
    return d

def add_valuations(companies):
    """Sector-relative valuation: reprice each company at its sector peers'
    median EV/EBITDA and forward P/E (peers exclude the company itself),
    blend the implied prices, and store impliedPrice / impliedUpside.
    Self-contained on purpose — it must run on an already-built company list
    without touching the network."""
    MIN_PEERS=4
    def median(xs):
        xs=sorted(xs); n=len(xs)
        return xs[n//2] if n%2 else (xs[n//2-1]+xs[n//2])/2
    by_sector={}
    for c in companies:
        by_sector.setdefault(c.get("sector"),[]).append(c)
    for peers in by_sector.values():
        for c in peers:
            ee=[p["evEbitda"] for p in peers if p is not c and p.get("evEbitda") and p["evEbitda"]>0]
            fp=[p["forwardPE"] for p in peers if p is not c and p.get("forwardPE") and p["forwardPE"]>0]
            implied=[]; used=[]
            if len(ee)>=MIN_PEERS and c.get("evEbitda") and c["evEbitda"]>0 and c.get("ev") and c.get("marketCap") and c.get("price"):
                ebitda=c["ev"]/c["evEbitda"]                                   # $B
                eq=median(ee)*ebitda-(c.get("debt") or 0)+(c.get("cash") or 0) # implied equity, $B
                if eq>0 and c["marketCap"]>0:
                    implied.append(eq/c["marketCap"]*c["price"]); used.append("EV/EBITDA")
            if len(fp)>=MIN_PEERS and c.get("forwardPE") and c["forwardPE"]>0 and c.get("price"):
                implied.append(median(fp)*c["price"]/c["forwardPE"]); used.append("Fwd P/E")
            if implied:
                ip=sum(implied)/len(implied)
                c["impliedPrice"]=round(ip,2)
                c["impliedUpside"]=round((ip-c["price"])/c["price"]*100,1)
                c["impliedFrom"]=" + ".join(used)

def main():
    if len(sys.argv)>1:
        tickers=sys.argv[1:]
    else:
        a=int(os.environ.get("START","0")); b=int(os.environ.get("END","9999"))
        tickers=UNIVERSE[a:b]
    out=[]
    for n,tk in enumerate(tickers,1):
        try:
            d=build(tk)
            if d:
                out.append(d)
                print(f"[{n}/{len(tickers)}] OK  {tk:6} {d['name'][:30]:30} {d['price']}")
            else:
                print(f"[{n}/{len(tickers)}] SKIP {tk} (no data)")
        except Exception as e:
            print(f"[{n}/{len(tickers)}] ERR  {tk}: {e}")
        time.sleep(float(os.environ.get("SLEEP","0.15")))
    # Safety guard: if the source rate-limited or failed mid-run, don't overwrite
    # good committed data with a shrunken universe. Tunable via MIN_COUNT.
    min_count=int(os.environ.get("MIN_COUNT","100"))
    if len(sys.argv)<=1 and len(tickers)>=min_count and len(out)<min_count:
        print(f"\nABORT  only {len(out)}/{len(tickers)} companies fetched (< MIN_COUNT={min_count}); not writing output.")
        sys.exit(1)
    add_valuations(out)
    payload={
      "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%b %d, %Y"),
      "count": len(out),
      "companies": out,
    }
    outpath=os.environ.get("OUT",os.path.join(ROOT,"data.json"))
    with open(outpath,"w") as f:
        json.dump(payload,f,separators=(",",":"))
    with open(outpath.replace("data.json","data.js"),"w") as f:
        f.write("window.DATA = "+json.dumps(payload,separators=(",",":"))+";\n")
    print(f"\nDONE  wrote {len(out)} companies -> data.json")

if __name__=="__main__":
    main()
