#!/usr/bin/env python3
"""Equity Research Terminal - SEC filings / Deal Radar enrichment.

Adds a compact `events` block to each company in data.json (no re-pull of
fundamentals), built from the free SEC EDGAR submissions API:

  https://data.sec.gov/submissions/CIK##########.json

Every recent filing is classified DETERMINISTICALLY from its form type and,
for 8-Ks, the item codes EDGAR lists for the filing (no text parsing, no AI):

  8-K item 2.01          -> M&A / disposition completed
  8-K item 1.01 / 1.02   -> material agreement signed / terminated
  8-K item 5.02          -> leadership change
  8-K item 2.02          -> results announced          (and 2.03 debt, 3.01 listing)
  S-4 / F-4 / 425 / DEFM14A / PREM14A / SC TO-* / SC 14D9 -> merger filing
  SC 13D (+ amendments)  -> activist stake
  SC 13G (initial only)  -> passive >5% stake
  10-K / 10-Q / 20-F     -> periodic report

Everything else (Form 4/144/424B2/FWP/6-K/...) is deliberately skipped as noise.

Two phases so it survives short shells / rate limits (same pattern as
build_prices.py / build_flow.py):
    python build_events.py fetch START END   # slice -> /tmp/events_partials
    python build_events.py merge             # fold into data.json / data.js

SEC fair-access policy: identify yourself via User-Agent (SEC_UA env to
override) and stay under 10 requests/second (we sleep between requests).
"""
import datetime
import glob
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root: data.json lives there
PARTIAL_DIR = os.environ.get("EVENTS_PARTIAL_DIR", "/tmp/events_partials")
UA = os.environ.get("SEC_UA", "equity-research-terminal (barbarzilay100@gmail.com)")
WINDOW_DAYS = int(os.environ.get("EVENTS_WINDOW_DAYS", "183"))   # ~6 months
MAX_EVENTS = int(os.environ.get("EVENTS_MAX_PER_COMPANY", "12"))
SLEEP = float(os.environ.get("SLEEP", "0.15"))                   # <10 req/s per SEC policy

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# 8-K item code -> (category, label). Order = priority when a filing has several items.
ITEM_PRIORITY = [
    ("2.01", ("ma",      "M&A / disposition completed")),
    ("1.01", ("agmt",    "Material agreement")),
    ("1.02", ("agmt",    "Agreement terminated")),
    ("5.02", ("mgmt",    "Leadership change")),
    ("2.03", ("debt",    "New financial obligation")),
    ("3.01", ("listing", "Listing notice")),
    ("2.02", ("results", "Results announced")),
]

MERGER_FORMS = {"S-4", "S-4/A", "F-4", "F-4/A", "425", "DEFM14A", "DEFM14C",
                "PREM14A", "SC TO-T", "SC TO-T/A", "SC TO-I", "SC TO-I/A",
                "SC 14D9", "SC 14D9/A"}
PERIODIC_FORMS = {"10-K", "10-Q", "20-F"}


def classify_filing(form, items):
    """(form, 8-K items string) -> (category, label) or None to skip.

    Pure + deterministic: driven only by EDGAR's own form type and item codes.
    """
    form = (form or "").strip().upper()
    if form in ("8-K", "8-K/A"):
        codes = [i.strip() for i in (items or "").split(",") if i.strip()]
        for code, cat in ITEM_PRIORITY:
            if code in codes:
                return cat
        return None                    # 7.01 / 8.01 / 5.07-only 8-Ks: unclassifiable noise
    if form in MERGER_FORMS:
        return ("merger", "Merger-related filing")
    if form in ("SC 13D", "SC 13D/A"):
        return ("activist", "Activist stake (13D)")
    if form == "SC 13G":               # initial only; 13G/A amendments are noise
        return ("stake", "New passive stake >5% (13G)")
    if form in PERIODIC_FORMS:
        return ("periodic", form + " filed")
    return None


def select_events(recent, today=None, window_days=WINDOW_DAYS, cap=MAX_EVENTS):
    """filings.recent (dict of parallel arrays) -> compact classified event list.

    Pure: no network. Keeps classified filings from the last `window_days`,
    newest first, at most `cap` per company.
    """
    today = today or datetime.date.today()
    cutoff = (today - datetime.timedelta(days=window_days)).isoformat()
    out = []
    forms = recent.get("form", [])
    for i in range(len(forms)):
        d = recent["filingDate"][i]
        if d < cutoff:
            continue
        cat = classify_filing(forms[i], (recent.get("items") or [""] * len(forms))[i])
        if not cat:
            continue
        ev = {
            "d": d,
            "f": forms[i],
            "c": cat[0],
            "l": cat[1],
            "a": recent["accessionNumber"][i].replace("-", ""),
            "p": recent.get("primaryDocument", [""] * len(forms))[i] or "",
        }
        items = (recent.get("items") or [""] * len(forms))[i]
        if items:
            ev["i"] = items
        out.append(ev)
    out.sort(key=lambda e: e["d"], reverse=True)
    return out[:cap]


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def load_cik_map():
    """ticker -> CIK from EDGAR's official mapping (cached per fetch run)."""
    cache = os.path.join(PARTIAL_DIR, "cik_map.json")
    if os.path.exists(cache):
        with open(cache) as f:
            return json.load(f)
    raw = _get_json(TICKER_MAP_URL)
    m = {row["ticker"].upper(): row["cik_str"] for row in raw.values()}
    os.makedirs(PARTIAL_DIR, exist_ok=True)
    with open(cache, "w") as f:
        json.dump(m, f)
    return m


def load_data():
    with open(os.path.join(ROOT, "data.json")) as f:
        return json.load(f)


def phase_fetch(a, b):
    os.makedirs(PARTIAL_DIR, exist_ok=True)
    cik_map = load_cik_map()
    data = load_data()
    tickers = [c["ticker"] for c in data["companies"]]
    sl = tickers[a:b]
    out = {}
    for n, tk in enumerate(sl, 1):
        cik = cik_map.get(tk.upper().replace(".", "-")) or cik_map.get(tk.upper())
        if cik is None:
            print(f"[{a+n}/{len(tickers)}] SKIP {tk}: no CIK in EDGAR map")
            continue
        try:
            sub = _get_json(SUBMISSIONS_URL.format(cik=cik))
            events = select_events(sub.get("filings", {}).get("recent", {}))
            out[tk] = {"cik": cik, "list": events}
            print(f"[{a+n}/{len(tickers)}] OK  {tk:6} cik={cik} events={len(events)}")
        except Exception as e:
            print(f"[{a+n}/{len(tickers)}] ERR {tk}: {e}")
        time.sleep(SLEEP)
    path = os.path.join(PARTIAL_DIR, f"events_{a}_{b}.json")
    with open(path, "w") as f:
        json.dump(out, f)
    print(f"wrote {len(out)} -> {path}")


def phase_merge():
    data = load_data()
    ev_map = {}
    for p in sorted(glob.glob(os.path.join(PARTIAL_DIR, "events_*.json"))):
        with open(p) as f:
            ev_map.update(json.load(f))
    hit = 0
    for c in data["companies"]:
        if c["ticker"] in ev_map:
            c["events"] = ev_map[c["ticker"]]   # NOT "ev" — that key is enterprise value
            hit += 1
    data["evGenerated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%b %d, %Y")
    with open(os.path.join(ROOT, "data.json"), "w") as f:
        json.dump(data, f, separators=(",", ":"))
    with open(os.path.join(ROOT, "data.js"), "w") as f:
        f.write("window.DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print(f"MERGED events into {hit}/{len(data['companies'])} companies -> data.json / data.js")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "merge"
    if cmd == "fetch":
        phase_fetch(int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "merge":
        phase_merge()
    else:
        sys.exit(f"usage: {sys.argv[0]} fetch START END | merge")
