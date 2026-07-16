#!/usr/bin/env python3
"""Equity Research Terminal - data pipeline.
Pulls fundamentals via yfinance for a universe of tickers and writes data.json.
Runs locally or in GitHub Actions (daily). No API key required.
"""
import json, sys, time, datetime, os
import yfinance as yf

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
]
UNIVERSE = list(dict.fromkeys(UNIVERSE))  # guard against accidental duplicates

def num(v):
    try:
        f=float(v)
        if f!=f: return None
        return f
    except: return None

def to_b(v):
    v=num(v)
    return round(v/1e9,3) if v is not None else None

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
            pts.append({"y":y,"r":round(r/1e9,2),"g":g})
            prev=r
        return pts if len(pts)>=2 else None
    except Exception:
        return None

def build(tk):
    t=yf.Ticker(tk)
    i=t.info
    if not i or not i.get("shortName"): return None
    price=num(i.get("currentPrice") or i.get("regularMarketPrice"))
    if price is None: return None
    high=num(i.get("fiftyTwoWeekHigh"))
    rev=num(i.get("totalRevenue"))
    fcf=to_b(i.get("freeCashflow"))
    de=num(i.get("debtToEquity"))
    ptavg=num(i.get("targetMeanPrice"))
    d={
      "ticker":tk.replace("-","."),
      "name":i.get("shortName"),
      "sector":i.get("sector") or "—",
      "industry":i.get("industry") or "",
      "price":round(price,2),
      "marketCap":to_b(i.get("marketCap")),
      "ev":to_b(i.get("enterpriseValue")),
      "high52":round(high,2) if high else None,
      "distHigh":round((price-high)/high*100,1) if high else None,
      "revHist":rev_history(t),
      "revGrowth":pct(i.get("revenueGrowth")),
      "earnGrowth":pct(i.get("earningsGrowth")),
      "netMargin":pct(i.get("profitMargins")),
      "grossMargin":pct(i.get("grossMargins")),
      "ebitdaMargin":pct(i.get("ebitdaMargins")),
      "eps":num(i.get("trailingEps")),
      "netIncome":to_b(i.get("netIncomeToCommon")),
      "fcf":fcf,
      "fcfMargin":round(fcf*1e9/rev*100,2) if (fcf is not None and rev) else None,
      "cash":to_b(i.get("totalCash")),
      "debt":to_b(i.get("totalDebt")),
      "debtEquity":round(de/100,2) if de is not None else None,
      "currentRatio":num(i.get("currentRatio")),
      "roe":pct(i.get("returnOnEquity")),
      "pe":num(i.get("trailingPE")),
      "forwardPE":num(i.get("forwardPE")),
      "peg":num(i.get("trailingPegRatio")),
      "evEbitda":num(i.get("enterpriseToEbitda")),
      "evFcf":round(num(i.get("enterpriseValue"))/ (fcf*1e9),1) if (fcf and i.get("enterpriseValue")) else None,
      "divYield":num(i.get("dividendYield")),
      "rating":RATING.get((i.get("recommendationKey") or "none"),None),
      "numAnalysts":num(i.get("numberOfAnalystOpinions")),
      "ptAvg":round(ptavg,2) if ptavg else None,
      "ptLow":num(i.get("targetLowPrice")),
      "ptHigh":num(i.get("targetHighPrice")),
      "upside":round((ptavg-price)/price*100,1) if ptavg else None,
      "summary":(i.get("longBusinessSummary") or "").strip(),
    }
    return d

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
    payload={
      "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%b %d, %Y"),
      "count": len(out),
      "companies": out,
    }
    outpath=os.environ.get("OUT","data.json")
    with open(outpath,"w") as f:
        json.dump(payload,f,separators=(",",":"))
    with open(outpath.replace("data.json","data.js"),"w") as f:
        f.write("window.DATA = "+json.dumps(payload,separators=(",",":"))+";\n")
    print(f"\nDONE  wrote {len(out)} companies -> data.json")

if __name__=="__main__":
    main()
