#!/usr/bin/env python3
"""Reconciliation + bounds validation for data.json.

Recomputes every derivable field from its stored inputs (upside, distHigh,
impliedUpside, EV/FCF; net & FCF margin against the latest annual revenue),
bounds-checks the rest, writes docs/validation-report.md, and exits 1 on hard
anomalies so CI never commits a broken dataset. Before this, the only guard
was a row count.

Hard FAIL  -> derived field disagrees with its own inputs, duplicate ticker,
              non-positive price, shrunken universe (pipeline bug territory).
WARN       -> source-data oddities worth eyeballing (extreme multiples or
              implied upside, margin vs FY-revenue divergence, missing sector).
"""
import datetime, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "docs", "validation-report.md")

TOL_PP = 0.25   # tolerance (percentage points) for fields the pipeline rounds to 0.1
fails, warns = [], []

payload = json.load(open(os.path.join(ROOT, "data.json")))
cos = payload["companies"]

if payload["count"] != len(cos):
    fails.append(f"count field says {payload['count']} but companies has {len(cos)}")
if len(cos) < 100:
    fails.append(f"universe shrank to {len(cos)} companies (< 100)")
seen = set()
for c in cos:
    if c["ticker"] in seen:
        fails.append(f"duplicate ticker {c['ticker']}")
    seen.add(c["ticker"])

for c in cos:
    t, price = c["ticker"], c.get("price")
    if not price or price <= 0:
        fails.append(f"{t}: non-positive price {price}")
        continue
    # --- reconciliation: derived fields must match their stored inputs
    if c.get("ptAvg") and c.get("upside") is not None:
        exp = (c["ptAvg"] - price) / price * 100
        if abs(c["upside"] - exp) > TOL_PP:
            fails.append(f"{t}: upside {c['upside']} != {exp:.1f} recomputed from ptAvg/price")
    if c.get("high52") and c.get("distHigh") is not None:
        exp = (price - c["high52"]) / c["high52"] * 100
        if abs(c["distHigh"] - exp) > TOL_PP:
            fails.append(f"{t}: distHigh {c['distHigh']} != {exp:.1f} recomputed from high52/price")
    if c.get("impliedPrice") and c.get("impliedUpside") is not None:
        exp = (c["impliedPrice"] - price) / price * 100
        if abs(c["impliedUpside"] - exp) > TOL_PP:
            fails.append(f"{t}: impliedUpside {c['impliedUpside']} != {exp:.1f} recomputed from impliedPrice")
    if c.get("evFcf") and c.get("ev") and c.get("fcf"):
        exp = c["ev"] / c["fcf"]
        if abs(c["evFcf"] - exp) > max(0.06, abs(exp) * 0.02):
            fails.append(f"{t}: evFcf {c['evFcf']} != {exp:.1f} recomputed from ev/fcf")
    # --- margins vs latest annual revenue (TTM-vs-FY mismatch tolerated: warn)
    rh = c.get("revHist") or []
    rev = rh[-1].get("r") if rh else None
    if rev:
        if c.get("netIncome") is not None and c.get("netMargin") is not None:
            exp = c["netIncome"] / rev * 100
            if abs(exp - c["netMargin"]) > 8:
                warns.append(f"{t}: netMargin {c['netMargin']}% vs {exp:.1f}% from netIncome/FY-revenue")
        if c.get("fcf") is not None and c.get("fcfMargin") is not None:
            exp = c["fcf"] / rev * 100
            if abs(exp - c["fcfMargin"]) > 8:
                warns.append(f"{t}: fcfMargin {c['fcfMargin']}% vs {exp:.1f}% from fcf/FY-revenue")
    # --- bounds
    if c.get("peg") is not None and (c["peg"] < 0 or c["peg"] > 10):
        warns.append(f"{t}: PEG {c['peg']} out of [0, 10]")
    if c.get("debtEquity") is not None and c["debtEquity"] > 50:
        warns.append(f"{t}: debt/equity {c['debtEquity']} > 50")
    if c.get("netMargin") is not None and not -100 <= c["netMargin"] <= 100:
        warns.append(f"{t}: netMargin {c['netMargin']}% out of [-100, 100]")
    if c.get("roe") is not None and not -200 <= c["roe"] <= 400:
        warns.append(f"{t}: ROE {c['roe']}% out of [-200, 400]")
    if c.get("upside") is not None and abs(c["upside"]) > 300:
        warns.append(f"{t}: analyst upside {c['upside']}% beyond ±300%")
    if c.get("impliedUpside") is not None and not -80 <= c["impliedUpside"] <= 300:
        warns.append(f"{t}: implied upside {c['impliedUpside']}% outlier — check source multiples")
    if c.get("sector") in (None, "", "—"):
        warns.append(f"{t}: missing sector")

status = "FAIL" if fails else "PASS"
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
lines = [
    "# Data validation report", "",
    f"Generated {now} · snapshot **{payload['generated']}** · {len(cos)} companies",
    f"Result: **{status}** — {len(fails)} hard failure(s), {len(warns)} warning(s)", "",
]
if fails:
    lines += ["## Hard failures (block the refresh)", ""] + [f"- {f}" for f in fails] + [""]
if warns:
    lines += ["## Warnings (source-data oddities, non-blocking)", ""] + [f"- {w}" for w in warns] + [""]
if not fails and not warns:
    lines += ["All reconciliation and bounds checks passed with no warnings.", ""]
with open(REPORT, "w") as f:
    f.write("\n".join(lines))

print(f"{status}: {len(fails)} failures, {len(warns)} warnings -> docs/validation-report.md")
for f_ in fails: print("FAIL  " + f_)
for w in warns: print("warn  " + w)
sys.exit(1 if fails else 0)
