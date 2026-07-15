#!/usr/bin/env python3
"""S1 screener: fetch Finviz S&P500 + NDX decliners, output qualifying set as JSON.

Usage: python3 scripts/screen.py [--threshold -4.0] [--cap 12]
Output (stdout): JSON {fetched_at_utc, threshold, qualify_count, qualifying, capped}
Each row: {ticker, company, sector, mcap_b, price, pct, volume}
Exit codes: 0 ok, 2 fetch failure after retries, 3 parse produced no rows (layout change?).
"""
import argparse, json, re, sys, time, urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
URLS = [
    "https://finviz.com/screener.ashx?v=111&f=cap_largeover,idx_sp500,sh_avgvol_o400&o=change",
    "https://finviz.com/screener.ashx?v=111&f=cap_largeover,idx_ndx,sh_avgvol_o400&o=change",
]

def fetch(url, retries=3):
    delays = [0, 5, 10]
    last_err = None
    for i in range(retries):
        if delays[i]:
            time.sleep(delays[i])
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
            if len(html) > 10000:
                return html
            last_err = f"short response ({len(html)} bytes)"
        except Exception as e:
            last_err = str(e)
    print(f"FETCH FAILED: {url}: {last_err}", file=sys.stderr)
    sys.exit(2)

def parse_page(html):
    rows = re.findall(r'<tr[^>]*class="[^"]*styled-row[^"]*"[^>]*>(.*?)</tr>', html, re.S)
    out = []
    for r in rows:
        raw_cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in raw_cells]
        # v=111 columns: No., Ticker, Company, Sector, Industry, Country, MarketCap, P/E, Price, Change, Volume
        if len(cells) >= 11 and re.match(r"^-?[\d.]+%$", cells[9]):
            # Ticker cell now embeds a logo whose alt/fallback text pollutes
            # stripped text ("PPNR"); take the ticker from the stock?t= href.
            m = re.search(r'href="stock\?t=([A-Za-z0-9.\-]+)', raw_cells[1])
            if m:
                cells[1] = m.group(1)
            mc = cells[6]
            mult = {"B": 1.0, "T": 1000.0, "M": 0.001}.get(mc[-1:], None)
            mcap_b = round(float(mc[:-1]) * mult, 2) if mult and re.match(r"^[\d.]+[TBM]$", mc) else None
            out.append({
                "ticker": cells[1], "company": cells[2], "sector": cells[3],
                "mcap_b": mcap_b, "price": float(cells[8]),
                "pct": float(cells[9].rstrip("%")),
                "volume": int(cells[10].replace(",", "")),
            })
    return out

def scan(url, threshold):
    """Paginate until a page's last row is above threshold (or page short/empty)."""
    all_rows, offset = [], 1
    while True:
        page = parse_page(fetch(f"{url}&r={offset}" if offset > 1 else url))
        if not page:
            break
        all_rows.extend(page)
        if page[-1]["pct"] > threshold or len(page) < 20 or offset > 500:
            break
        offset += 20
    return all_rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=-4.0)
    ap.add_argument("--cap", type=int, default=12)
    a = ap.parse_args()

    merged = {}
    for url in URLS:
        for row in scan(url, a.threshold):
            merged.setdefault(row["ticker"], row)
    if not merged:
        print("PARSE FAILURE: zero rows parsed from either URL — layout change?", file=sys.stderr)
        sys.exit(3)

    qual = sorted([r for r in merged.values() if r["pct"] <= a.threshold],
                  key=lambda r: (r["pct"], -(r["mcap_b"] or 0), -r["volume"]))
    print(json.dumps({
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "threshold": a.threshold,
        "qualify_count": len(qual),
        "qualifying": qual,
        "capped": qual[: a.cap],
    }, indent=1))

if __name__ == "__main__":
    main()
