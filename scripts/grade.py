#!/usr/bin/env python3
"""S0 grader: migrate predictions.csv to the v2 schema, grade due pending rows,
write calibration.json.

Usage: python3 scripts/grade.py --model <model-id> [--csv predictions.csv]
                                [--calib calibration.json] [--dry-run]
A pending row is DUE when the ticker's price history shows 2 trading sessions
after run_date, both dated before today (US/Eastern) — i.e. both fully closed.
Grades: max_pct_2td, hit (>=3%), peak_day, min_pct_2td.
Prints a summary to stdout; exits 0 on success (even if nothing was due).
"""
import argparse, csv, json, re, sys, time, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HEADER = ("run_date,run_time,model,ticker,rebound_pct,ref_price,status,graded_date,"
          "max_pct_2td,hit,peak_day,min_pct_2td,drop_pct,sector,drop_type,cause,"
          "intraday_state,vol_signature,earnings_soon,corr_group").split(",")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

def today_eastern():
    # US/Eastern = UTC-4 (EDT) or UTC-5 (EST); precise DST handling matters only
    # within 1h of midnight ET, when the routine never runs. Use -5 as the
    # conservative choice (later date rollover = never grades a day too early).
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()

def fetch_history(ticker, retries=3):
    url = f"https://stockanalysis.com/stocks/{ticker}/history/"
    for i in range(retries):
        if i:
            time.sleep(5 * i)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
        except Exception:
            continue
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            cells = [re.sub(r"<[^>]+>", "", c).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            # Date, Open, High, Low, Close, AdjClose, Change, Volume
            if len(cells) >= 8 and re.match(r"^[A-Z][a-z]{2} \d{1,2}, \d{4}$", cells[0]):
                try:
                    rows.append({
                        "date": datetime.strptime(cells[0], "%b %d, %Y").date(),
                        "high": float(cells[2].replace(",", "")),
                        "low": float(cells[3].replace(",", "")),
                    })
                except ValueError:
                    pass
        if rows:
            return sorted(rows, key=lambda r: r["date"])
    return None

def migrate(rows):
    """Return rows as dicts keyed by v2 HEADER, padding missing columns."""
    out = []
    for r in rows:
        d = dict.fromkeys(HEADER, "")
        if "model" in r:                      # already v2-ish
            for k in HEADER:
                d[k] = r.get(k, "")
        else:                                  # v1: no model / feature columns
            for k in ("run_date", "run_time", "ticker", "rebound_pct", "ref_price",
                      "status", "graded_date", "max_pct_2td", "hit"):
                d[k] = r.get(k, "")
        out.append(d)
    return out

def grade(rows, dry):
    today = today_eastern()
    graded_n, skipped = 0, []
    by_ticker = {}
    for r in rows:
        if r["status"] != "pending":
            continue
        run_date = datetime.strptime(r["run_date"], "%Y-%m-%d").date()
        t = r["ticker"]
        if t not in by_ticker:
            by_ticker[t] = fetch_history(t)
        hist = by_ticker[t]
        if hist is None:
            skipped.append(f"{t} ({r['run_date']}): history fetch failed")
            continue
        window = [h for h in hist if run_date < h["date"] < today][:2]
        if len(window) < 2:
            continue  # not yet due
        ref = float(r["ref_price"])
        highs = [h["high"] for h in window]
        max_pct = (max(highs) / ref - 1) * 100
        r_upd = {
            "max_pct_2td": f"{max_pct:.2f}",
            "hit": "1" if max_pct >= 3 else "0",
            "peak_day": str(highs.index(max(highs)) + 1),
            "min_pct_2td": f"{(min(h['low'] for h in window) / ref - 1) * 100:.2f}",
            "status": "graded",
            "graded_date": today.isoformat(),
        }
        if not dry:
            r.update(r_upd)
        graded_n += 1
    return graded_n, skipped

def calibrate(rows, model):
    last = {}   # (ticker, run_date) -> last-run row of that day
    for r in rows:
        if r["status"] == "graded" and r["max_pct_2td"] != "":
            k = (r["ticker"], r["run_date"])
            if k not in last or (r["run_time"] or "00:00") >= (last[k]["run_time"] or "00:00"):
                last[k] = r
    samples = list(last.values())

    def rates(keyfn, min_n=10):
        agg = defaultdict(lambda: [0, 0])
        for s in samples:
            k = keyfn(s)
            if k is None or k == "":
                continue
            agg[k][0] += 1
            agg[k][1] += int(s["hit"])
        return {k: {"n": n, "hits": h, "rate": round(h / n, 2)}
                for k, (n, h) in sorted(agg.items()) if n >= 1}

    def band(s):
        return f"{int(s['rebound_pct']) // 10 * 10}-{int(s['rebound_pct']) // 10 * 10 + 9}"

    same_model = [s for s in samples if s["model"] == model]
    bands_model = rates_from(same_model, band) if same_model else {}
    return {
        "updated": today_eastern().isoformat(),
        "model": model,
        "n_samples_all": len(samples),
        "n_samples_this_model": len(same_model),
        "bands_all_models": rates(band),
        "bands_this_model": bands_model,
        "factors": {
            "drop_type": rates(lambda s: s["drop_type"]),
            "cause": rates(lambda s: s["cause"]),
            "intraday_state": rates(lambda s: s["intraday_state"]),
        },
        "notes": "bands/factors include n & hits; apply nudges only where n>=10 "
                 "(prefer bands_this_model; fall back to bands_all_models)",
    }

def rates_from(samples, keyfn):
    agg = defaultdict(lambda: [0, 0])
    for s in samples:
        agg[keyfn(s)][0] += 1
        agg[keyfn(s)][1] += int(s["hit"])
    return {k: {"n": n, "hits": h, "rate": round(h / n, 2)}
            for k, (n, h) in sorted(agg.items())}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--csv", default="predictions.csv")
    ap.add_argument("--calib", default="calibration.json")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with open(a.csv, newline="") as f:
        rows = migrate(list(csv.DictReader(f)))
    graded_n, skipped = grade(rows, a.dry_run)
    calib = calibrate(rows, a.model)

    if not a.dry_run:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=HEADER)
            w.writeheader()
            w.writerows(rows)
        with open(a.calib, "w") as f:
            json.dump(calib, f, indent=1)

    print(json.dumps({"graded_now": graded_n, "skipped": skipped,
                      "pending_left": sum(1 for r in rows if r["status"] == "pending"),
                      "calibration": calib}, indent=1))
    if skipped:
        sys.exit(4)   # partial: some due rows could not be graded — model must retry those per G1

if __name__ == "__main__":
    main()
