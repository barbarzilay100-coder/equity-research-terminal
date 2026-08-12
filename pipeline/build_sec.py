#!/usr/bin/env python3
"""Equity Research Terminal - as-reported (SEC XBRL) enrichment.

Adds a compact `sec` block to each company in data.json: the five figures the
GARP scorecard is built on, taken from the company's own latest annual filing
via the free SEC XBRL Company Facts API:

  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json

  revenue, netIncome, fcf (operating cash flow - capex), equity, debt

Yahoo (build_data.py) is a summarised, mostly-TTM view; this block is what the
company actually filed, for one fiscal year, with the accession number kept so
every number links back to the filing. The front end shows the two side by side
and the difference is the point of the exercise, not a bug: a TTM figure and a
fiscal-year figure legitimately differ.

Three rules make the extraction defensible:
  1. One filing. The fiscal year is chosen from the revenue fact, and every other
     figure prefers a fact carrying the same accession number, so the five rows
     are internally consistent instead of assembled from different filings.
  2. Duration, not labels. An annual figure is a fact whose period really spans
     ~a year; `fp: FY` alone is not enough (TEVA tags a single quarter FY).
  3. Latest filed wins. Restatements and comparatives repeat a period across
     filings, so among candidates for one period the newest `filed` date wins.

SEC fair-access policy: identify yourself via User-Agent (SEC_UA env to
override) and stay under 10 requests/second (we sleep between requests).

Two phases so it survives short shells / rate limits:
    python build_sec.py fetch START END   # slice -> /tmp/sec_partials
    python build_sec.py merge             # fold into data.json / data.js
"""
import json, sys, os, glob, datetime, time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root: data.json lives there
PARTIAL_DIR = os.environ.get("SEC_PARTIAL_DIR", "/tmp/sec_partials")
UA = os.environ.get("SEC_UA", "equity-research-terminal (barbarzilay100@gmail.com)")
SLEEP = float(os.environ.get("SLEEP", "0.15"))            # <10 req/s per SEC policy
MIN_RATIO = float(os.environ.get("SEC_MIN_RATIO", "0.6"))  # merge aborts below this coverage

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{plain}/{accn}-index.htm"

ANNUAL_FORMS = ("10-K", "20-F", "40-F")

# EDGAR's ticker->CIK file is not exhaustive: it lags ticker changes (Fiserv still
# appears as FISV) and omits some foreign private issuers (CyberArk carries no
# ticker at all). Both are stable identifiers, so they are pinned here.
CIK_OVERRIDES = {"FI": 798354, "CYBR": 1598110}

# Tag priority per figure. First hit wins, so the most specific tag leads and the
# older/looser ones are fallbacks. us-gaap covers the Israeli dual-listed names
# too (they file 20-F in US GAAP); ifrs-full is there for true IFRS filers.
DURATION_TAGS = {
    # `Revenues` leads on purpose: it is the income statement's total line. The
    # contract-with-customer tags cover only revenue from customer contracts, so
    # for an insurer or a conglomerate they are a subset (Berkshire files both:
    # 247B of contract revenue inside 371B of total revenues) -- but for most
    # filers they are the only revenue tag present, hence the fallback.
    "revenue": [
        ("us-gaap", "Revenues"),
        # Banks and brokers report net revenues; several stopped using `Revenues`
        # years ago, so without this tag their newest annual figure is a decade old
        ("us-gaap", "RevenuesNetOfInterestExpense"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
        ("us-gaap", "SalesRevenueNet"),
        ("ifrs-full", "Revenue"),
        ("ifrs-full", "RevenueFromContractsWithCustomers"),
    ],
    "netIncome": [
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "ProfitLoss"),
        ("us-gaap", "NetIncomeLossAvailableToCommonStockholdersBasic"),
        ("ifrs-full", "ProfitLoss"),
    ],
    "ocf": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
        ("ifrs-full", "CashFlowsFromUsedInOperatingActivities"),
    ],
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipmentExcludingCapitalizedInterest"),
        ("us-gaap", "PaymentsToAcquireOtherPropertyPlantAndEquipment"),
        ("us-gaap", "PaymentsToAcquireProductiveAssets"),
        ("us-gaap", "PaymentsForCapitalImprovements"),
        ("ifrs-full", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
    ],
}

INSTANT_TAGS = {
    "equity": [
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        ("ifrs-full", "Equity"),
    ],
    # Debt is the one row that cannot be read off a single tag. Two shapes exist:
    # a tag that already carries the whole long-term balance including current
    # maturities, or a non-current balance that needs its current portion added.
    # They are kept apart so the current portion is never added twice.
    "debtTotalLong": [       # already includes current maturities
        ("us-gaap", "DebtLongtermAndShorttermCombinedAmount"),
        ("us-gaap", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"),
        ("us-gaap", "LongTermDebt"),
    ],
    "debtNoncurrent": [      # needs the current portion added
        ("us-gaap", "LongTermDebtNoncurrent"),
        ("us-gaap", "LongTermDebtAndCapitalLeaseObligationsNoncurrent"),
        ("ifrs-full", "NoncurrentPortionOfNoncurrentBorrowings"),
    ],
    "debtCurrent": [
        ("us-gaap", "LongTermDebtCurrent"),
        ("us-gaap", "DebtCurrent"),
        ("ifrs-full", "CurrentPortionOfNoncurrentBorrowings"),
    ],
    "debtShortTerm": [       # commercial paper and other short-term borrowings
        ("us-gaap", "ShortTermBorrowings"),
        ("us-gaap", "OtherShortTermBorrowings"),
    ],
}


def to_b(v):
    """USD -> billions, matching the scale build_data.py stores."""
    try:
        f = float(v)
        if f != f:
            return None
        return round(f / 1e9, 3)
    except Exception:
        return None


def _usd_facts(facts, ns, tag):
    """All USD-denominated facts for one taxonomy tag, or []."""
    node = facts.get(ns, {}).get(tag)
    if not node:
        return []
    for unit in ("USD", "usd"):
        if unit in node.get("units", {}):
            return node["units"][unit]
    return []


def _pick(cands, accn=None):
    """Choose one fact out of (priority, fact, tag) candidates.

    The newest fiscal period always wins first: a company that switched tags
    leaves the abandoned tag behind with real but stale values, and taking the
    first tag that happens to carry data would silently report a years-old
    figure. Only within the same period does tag priority decide, and then the
    newest `filed` date, which is how a restatement supersedes the original.
    """
    if not cands:
        return None, None
    if accn:
        same = [c for c in cands if c[1].get("accn") == accn]
        if same:
            cands = same
    newest = max(c[1]["end"] for c in cands)
    cands = [c for c in cands if c[1]["end"] == newest]
    prio, fact, tag = min(cands, key=lambda c: (c[0], _neg_filed(c[1])))
    return fact, tag


def _neg_filed(f):
    """Sort helper: newest `filed` first inside a min()."""
    return tuple(-ord(ch) for ch in f.get("filed", ""))


def annual_fact(facts, tag_list, end=None, accn=None):
    """Pick one annual (duration) fact.

    A fact qualifies when it comes from an annual form and its period really
    spans 350-380 days -- `fp: FY` is not trusted on its own, because filers do
    tag a single quarter as FY. When `end`/`accn` are given we are matching the
    fiscal year already chosen from revenue, so the result stays inside one
    filing.
    """
    cands = []
    for i, (ns, tag) in enumerate(tag_list):
        for f in _usd_facts(facts, ns, tag):
            if f.get("form") not in ANNUAL_FORMS or not f.get("start") or not f.get("end"):
                continue
            try:
                s = datetime.date.fromisoformat(f["start"])
                e = datetime.date.fromisoformat(f["end"])
            except ValueError:
                continue
            if not 350 <= (e - s).days <= 380:
                continue
            if end and f["end"] != end:
                continue
            cands.append((i, f, (ns, tag)))
    return _pick(cands, accn)


def instant_fact(facts, tag_list, end=None, accn=None):
    """Pick one point-in-time (balance sheet) fact, same preference order."""
    cands = []
    for i, (ns, tag) in enumerate(tag_list):
        for f in _usd_facts(facts, ns, tag):
            if f.get("form") not in ANNUAL_FORMS or f.get("start") or not f.get("end"):
                continue
            if end and f["end"] != end:
                continue
            cands.append((i, f, (ns, tag)))
    return _pick(cands, accn)


def extract(facts, cik):
    """Build the `sec` block for one company from its companyfacts payload."""
    rev, rev_tag = annual_fact(facts, DURATION_TAGS["revenue"])
    if not rev:
        return None
    end, accn = rev["end"], rev.get("accn")

    out = {
        "cik": cik,
        # labelled from the period that actually ends here, not from the fact's
        # `fy`, which is the fiscal year of the filing the fact appeared in and
        # so lags by one year whenever the number is a comparative
        "fy": "FY" + end[:4],
        "end": end,
        "form": rev.get("form"),
        "accn": accn,
        "revenue": to_b(rev["val"]),
        "tags": {"revenue": rev_tag[1]},
    }
    if accn:
        out["url"] = FILING_URL.format(cik=cik, plain=accn.replace("-", ""), accn=accn)

    ni, ni_tag = annual_fact(facts, DURATION_TAGS["netIncome"], end, accn)
    if ni:
        out["netIncome"] = to_b(ni["val"])
        out["tags"]["netIncome"] = ni_tag[1]

    ocf, ocf_tag = annual_fact(facts, DURATION_TAGS["ocf"], end, accn)
    capex, capex_tag = annual_fact(facts, DURATION_TAGS["capex"], end, accn)
    if ocf:
        out["ocf"] = to_b(ocf["val"])
        out["tags"]["ocf"] = ocf_tag[1]
        # capex is filed as a positive outflow; missing capex means FCF is not
        # computable, not that it equals operating cash flow
        if capex:
            out["capex"] = to_b(abs(capex["val"]))
            out["fcf"] = to_b(ocf["val"] - abs(capex["val"]))
            out["tags"]["capex"] = capex_tag[1]

    eq, eq_tag = instant_fact(facts, INSTANT_TAGS["equity"], end, accn)
    if eq:
        out["equity"] = to_b(eq["val"])
        out["tags"]["equity"] = eq_tag[1]

    # Borrowings only: a long-term component must be found, because the current
    # portion on its own is a fraction of the balance and would read as a real
    # number while being wrong. Filers who tag total debt only per instrument
    # (dimensional facts, which this API omits) therefore get no debt row at all
    # -- an empty cell is honest, a partial sum is not.
    parts, tags = [], []
    tot, tot_tag = instant_fact(facts, INSTANT_TAGS["debtTotalLong"], end, accn)
    if tot:
        parts.append(tot["val"]); tags.append(tot_tag[1])
    else:
        nc, nc_tag = instant_fact(facts, INSTANT_TAGS["debtNoncurrent"], end, accn)
        if nc:
            parts.append(nc["val"]); tags.append(nc_tag[1])
            cur, cur_tag = instant_fact(facts, INSTANT_TAGS["debtCurrent"], end, accn)
            if cur:
                parts.append(cur["val"]); tags.append(cur_tag[1])
    if parts:
        st, st_tag = instant_fact(facts, INSTANT_TAGS["debtShortTerm"], end, accn)
        if st:
            parts.append(st["val"]); tags.append(st_tag[1])
        out["debt"] = to_b(sum(parts))
        out["tags"]["debt"] = "+".join(tags)
    return out


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
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
    data = load_data()
    tickers = [c["ticker"] for c in data["companies"]]
    cik_map = load_cik_map()
    out = {}
    for n, tk in enumerate(tickers[a:b], 1):
        cik = (CIK_OVERRIDES.get(tk.upper())
               or cik_map.get(tk.upper().replace(".", "-"))
               or cik_map.get(tk.upper()))
        if cik is None:
            print(f"[{a+n}/{len(tickers)}] SKIP {tk}: no CIK in EDGAR map")
            continue
        try:
            facts = _get_json(FACTS_URL.format(cik=cik)).get("facts", {})
            blk = extract(facts, cik)
            if blk:
                out[tk] = blk
                print(f"[{a+n}/{len(tickers)}] OK  {tk:6} {blk['fy']} rev={blk['revenue']}B "
                      f"ni={blk.get('netIncome')}B fcf={blk.get('fcf')}B")
            else:
                # Usually a successor entity: the ticker now maps to a new CIK
                # (holding-company reorganisation) that has not filed an annual
                # report yet, so the history sits under the predecessor's CIK.
                print(f"[{a+n}/{len(tickers)}] SKIP {tk}: no annual revenue fact under CIK {cik}")
        except Exception as e:
            print(f"    sec ERR {tk}: {e}")
        time.sleep(SLEEP)
    path = os.path.join(PARTIAL_DIR, f"sec_{a}_{b}.json")
    with open(path, "w") as f:
        json.dump(out, f)
    print(f"wrote {len(out)} -> {path}")


def phase_merge():
    data = load_data()
    sec_map = {}
    for p in sorted(glob.glob(os.path.join(PARTIAL_DIR, "sec_*.json"))):
        with open(p) as f:
            sec_map.update(json.load(f))
    total = len(data["companies"])
    hit = sum(1 for c in data["companies"] if c["ticker"] in sec_map)
    ratio = hit / total if total else 0
    # A collapsed tag mapping (SEC renames a tag, the API changes shape) would
    # otherwise land silently as a table of blanks -- same guard as MIN_COUNT.
    if ratio < MIN_RATIO:
        print(f"ABORT: only {hit}/{total} companies matched ({ratio:.0%} < {MIN_RATIO:.0%}) "
              f"- data.json left untouched")
        sys.exit(1)
    for c in data["companies"]:
        if c["ticker"] in sec_map:
            c["sec"] = sec_map[c["ticker"]]
    data["secGenerated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%b %d, %Y")
    with open(os.path.join(ROOT, "data.json"), "w") as f:
        json.dump(data, f, separators=(",", ":"))
    with open(os.path.join(ROOT, "data.js"), "w") as f:
        f.write("window.DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print(f"MERGED sec into {hit}/{total} companies -> data.json / data.js")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "merge"
    if cmd == "fetch":
        phase_fetch(int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "merge":
        phase_merge()
    else:
        print("usage: build_sec.py fetch START END | merge")
