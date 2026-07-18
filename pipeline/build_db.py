#!/usr/bin/env python3
"""Writes terminal.db — a SQLite snapshot of the company universe, so the same
dataset the site renders is queryable with plain SQL (see sql/queries.sql).
Runs in CI after the pipelines. Scalar fields only, plus an `events` table of
classified SEC filings; the nested revHist / tech / flow blocks stay in data.json."""
import json, os, sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JSON = os.path.join(ROOT, "data.json")
DB_PATH = os.environ.get("DB_OUT", os.path.join(ROOT, "terminal.db"))  # DB_OUT: build elsewhere, e.g. on a read-restricted mount

TEXT = {"ticker", "name", "sector", "industry", "rating", "impliedFrom"}
FIELDS = ["ticker", "name", "sector", "industry", "price", "marketCap", "ev", "high52",
          "distHigh", "revGrowth", "earnGrowth", "netMargin", "grossMargin", "ebitdaMargin",
          "eps", "netIncome", "fcf", "fcfMargin", "cash", "debt", "debtEquity", "currentRatio",
          "roe", "pe", "forwardPE", "peg", "evEbitda", "evFcf", "divYield", "rating",
          "numAnalysts", "ptAvg", "ptLow", "ptHigh", "upside",
          "impliedPrice", "impliedUpside", "impliedFrom"]

def main():
    payload = json.load(open(DATA_JSON))
    # rebuild in place (portable: works where unlinking the old file is not allowed)
    db = sqlite3.connect(DB_PATH)
    for t in ("companies", "meta", "events"):
        db.execute(f"DROP TABLE IF EXISTS {t}")
    cols = ", ".join(f"{f} {'TEXT' if f in TEXT else 'REAL'}" for f in FIELDS)
    db.execute(f"CREATE TABLE companies ({cols}, PRIMARY KEY (ticker))")
    db.execute("CREATE TABLE meta (generated TEXT, count INTEGER)")
    db.execute("INSERT INTO meta VALUES (?, ?)", (payload["generated"], payload["count"]))
    # OR IGNORE: keep the first row if the source ever carries a duplicate ticker
    db.executemany(
        f"INSERT OR IGNORE INTO companies VALUES ({','.join('?' * len(FIELDS))})",
        [tuple(c.get(f) for f in FIELDS) for c in payload["companies"]])
    # classified SEC filings from build_events.py (one row per filing)
    db.execute("CREATE TABLE events (ticker TEXT REFERENCES companies(ticker), "
               "filing_date TEXT, form TEXT, category TEXT, label TEXT, accession TEXT)")
    db.executemany(
        "INSERT INTO events VALUES (?,?,?,?,?,?)",
        [(c["ticker"], e["d"], e["f"], e["c"], e["l"], e["a"])
         for c in payload["companies"]
         for e in ((c.get("events") or {}).get("list") or [])])
    db.commit()
    db.execute("VACUUM")   # compact: keep the committed artifact free of dead pages
    n = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    n_ev = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    dropped = len(payload["companies"]) - n
    print(f"wrote terminal.db: {n} companies, {len(FIELDS)} columns, {n_ev} filing events"
          + (f" ({dropped} duplicate tickers ignored)" if dropped else ""))

if __name__ == "__main__":
    main()
