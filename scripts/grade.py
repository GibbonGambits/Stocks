#!/usr/bin/env python3
"""S0 grader: migrate predictions.csv to the v3 schema, grade due pending rows,
backfill 5-trading-day fields, write calibration.json.

Usage: python3 scripts/grade.py --model <model-id> [--csv predictions.csv]
                                [--calib calibration.json] [--dry-run]
A pending row is DUE when the ticker's price history shows 2 trading sessions
after run_date, both dated before today (US/Eastern) — i.e. both fully closed.
Grades: max_pct_2td, hit (>=3%), peak_day, min_pct_2td.
Second stage: any row (pending or graded) with blank hit_5td whose first 5
post-run sessions have all closed gets max_pct_5td / hit_5td filled the same
way over the 5-session window. 5td fields are analysis-only — the prediction
target and status transition stay on the 2td horizon.
Prints a summary to stdout; exits 0 on success (even if nothing was due).
"""
import argparse, csv, json, re, sys, time, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HEADER = ("run_date,run_time,model,ticker,rebound_pct,ref_price,status,graded_date,"
          "max_pct_2td,hit,peak_day,min_pct_2td,drop_pct,sector,drop_type,cause,"
          "intraday_state,vol_signature,earnings_soon,corr_group,"
          "max_pct_5td,hit_5td").split(",")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

def today_eastern():
    # US/Eastern = UTC-4 (EDT) or UTC-5 (EST); precise DST handling matters only
    # within 1h of midnight ET, when the routine never runs. Use -5 as the
    # conservative choice (later date rollover = never grades a day too early).
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()

def weekdays_between(d1, d2):
    """Weekdays strictly between d1 and d2. Sessions are a subset of weekdays,
    so a count < N proves an N-session window cannot be complete yet."""
    n, d = 0, d1 + timedelta(days=1)
    while d < d2:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n

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

def migrate(rows, cols=None):
    """Return rows as dicts keyed by cols (HEADER + any unknown extra columns,
    preserved verbatim per the spec's never-delete rule), padding missing ones."""
    cols = cols or HEADER
    extras = [c for c in cols if c not in HEADER]
    out = []
    for r in rows:
        d = dict.fromkeys(cols, "")
        if "model" in r:                      # already v2-ish
            for k in cols:
                d[k] = r.get(k, "") or ""
        else:                                  # v1: no model / feature columns
            for k in ("run_date", "run_time", "ticker", "rebound_pct", "ref_price",
                      "status", "graded_date", "max_pct_2td", "hit", *extras):
                d[k] = r.get(k, "") or ""
        out.append(d)
    return out

def grade(rows, dry):
    today = today_eastern()
    graded_n, filled5_n, skipped = 0, 0, []
    by_ticker = {}
    for r in rows:
        needs_2td = r["status"] == "pending"
        needs_5td = r["status"] in ("pending", "graded") and r.get("hit_5td", "") == ""
        if not (needs_2td or needs_5td):
            continue
        run_date = datetime.strptime(r["run_date"], "%Y-%m-%d").date()
        # pre-fetch guards: an N-session window needs >= N closed weekdays, so
        # skip the fetch (and never report a fetch failure) for rows that
        # cannot possibly be due yet. 5td additionally needs >= 7 calendar
        # days (a 5-session window always spans a weekend).
        could_2td = needs_2td and weekdays_between(run_date, today) >= 2
        could_5td = needs_5td and (today - run_date).days >= 7
        if not (could_2td or could_5td):
            continue
        t = r["ticker"]
        if t not in by_ticker:
            by_ticker[t] = fetch_history(t)
        hist = by_ticker[t]
        if hist is None:
            if could_2td:
                skipped.append(f"{t} ({r['run_date']}): history fetch failed")
            continue
        window = [h for h in hist if run_date < h["date"] < today]
        ref = float(r["ref_price"])
        graded_now = needs_2td and len(window) >= 2
        if graded_now:
            w2 = window[:2]
            highs = [h["high"] for h in w2]
            max_pct = (max(highs) / ref - 1) * 100
            r_upd = {
                "max_pct_2td": f"{max_pct:.2f}",
                "hit": "1" if max_pct >= 3 else "0",
                "peak_day": str(highs.index(max(highs)) + 1),
                "min_pct_2td": f"{(min(h['low'] for h in w2) / ref - 1) * 100:.2f}",
                "status": "graded",
                "graded_date": today.isoformat(),
            }
            if not dry:
                r.update(r_upd)
            graded_n += 1
        if r.get("hit_5td", "") == "" and (r["status"] == "graded" or graded_now) \
                and len(window) >= 5:
            w5 = window[:5]
            max5 = (max(h["high"] for h in w5) / ref - 1) * 100
            if not dry:
                r["max_pct_5td"] = f"{max5:.2f}"
                r["hit_5td"] = "1" if max5 >= 3 else "0"
            filled5_n += 1
    return graded_n, filled5_n, skipped

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
    samples5 = [s for s in samples if s.get("hit_5td", "") != ""]
    same_model5 = [s for s in samples5 if s["model"] == model]
    return {
        "updated": today_eastern().isoformat(),
        "model": model,
        "n_samples_all": len(samples),
        "n_samples_this_model": len(same_model),
        "n_samples_5td": len(samples5),
        "bands_all_models": rates_from(samples, band),
        "bands_this_model": rates_from(same_model, band),
        "bands_all_models_5td": rates_from(samples5, band, "hit_5td"),
        "bands_this_model_5td": rates_from(same_model5, band, "hit_5td"),
        "factors": {
            "drop_type": rates(lambda s: s["drop_type"]),
            "cause": rates(lambda s: s["cause"]),
            "intraday_state": rates(lambda s: s["intraday_state"]),
        },
        "notes": "bands/factors include n & hits; apply nudges only where n>=10 "
                 "(prefer bands_this_model; fall back to bands_all_models). "
                 "*_5td bands are analysis-only context (patience effect); the "
                 "S4 prediction target stays the 2td rebound",
    }

def rates_from(samples, keyfn, hitkey="hit"):
    agg = defaultdict(lambda: [0, 0])
    for s in samples:
        agg[keyfn(s)][0] += 1
        agg[keyfn(s)][1] += int(s[hitkey])
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
        rd = csv.DictReader(f)
        raw = list(rd)
        cols = HEADER + [c for c in (rd.fieldnames or []) if c not in HEADER]
    rows = migrate(raw, cols)
    graded_n, filled5_n, skipped = grade(rows, a.dry_run)
    calib = calibrate(rows, a.model)

    if not a.dry_run:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        with open(a.calib, "w") as f:
            json.dump(calib, f, indent=1)

    print(json.dumps({"graded_now": graded_n, "filled_5td": filled5_n,
                      "skipped": skipped,
                      "pending_left": sum(1 for r in rows if r["status"] == "pending"),
                      "calibration": calib}, indent=1))
    if skipped:
        sys.exit(4)   # partial: some due rows could not be graded — model must retry those per G1

if __name__ == "__main__":
    main()
