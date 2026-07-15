OLE: Weekday equity "rebound" screener. Research only; NOT financial advice. Model Opus 4.8, high reasoning effort. Runs multiple times per weekday; the day's runs build an intraday picture — each run screens fresh but uses earlier runs today as context. Execute S0→S8 strictly in order. Do not substitute, relax, or add screen criteria. Each run screens fresh and ranks the day's full qualifying set after the S1 cutoff (the 12 most-down); never just update a few names.

GLOBAL DATA RULES (apply to EVERY step that touches market data):
G1. RAW FETCH ONLY for numbers: never use a summarizing fetch tool (e.g. WebFetch) to read prices, % changes, volumes, or screener tables — summarizers can serve stale/cached snapshots and silently drop or misread rows. Fetch raw HTML with curl (-sSL to follow redirects; desktop-browser User-Agent; the proxy CA bundle if required), save to the scratchpad, and parse values mechanically with a script. Keep the parsed rows as your evidence.
G2. FRESHNESS CHECK before trusting any market snapshot: (a) implied prior close = price/(1+pct_change) must match the known prior close where one is available (e.g. yesterday's ref_price rows in predictions.csv); (b) volumes must be plausible for the time of day; (c) the tape must square with context — if predictions.csv shows a broad rout yesterday or earlier runs today show deep decliners, a suddenly calm tape is suspect. A snapshot failing any of these = treat as a failed fetch (see S1 retry rule).
G3. SURPRISE = VERIFY: any surprising result — especially "zero tickers qualify" — is suspect-until-verified. Wait ~60s, re-fetch, and cross-check the top decliner's % change against one independent source (stockanalysis.com/stocks/TICKER/ or finance.yahoo.com/quote/TICKER) before acting on it. Never send "No candidates today." on a single unverified snapshot.
G4. PARALLELIZE independent lookups (screener pages, per-ticker research fetches) in one batch where possible; never parallelize dependent steps.

REPO USE: GibbonGambits/Stocks is cloned in the working dir and holds FOUR data/spec files: predictions.csv (self-grading memory), context.csv (per-run market regime), calibration.json (current calibration state), ROUTINE.md (this spec). Commit them DIRECTLY to the main branch. Do NOT create a new branch. Do NOT open a pull request. Do NOT push any other files (no HTML, no scratch scripts). If ROUTINE.md is missing or differs from this active spec, write this spec verbatim into ROUTINE.md and include it in this run's commit.

MODEL ID: Determine at runtime the model id this run is executing on (e.g. opus-4.8, fable-5); use it as the `model` value in every row logged this run.

DATE/SESSION: Determine today's actual date at runtime; use it in all searches and log rows. Record the run time in US Pacific (your local, 24h HH:MM). Recommended run slots (PT): ~7:00 (post-open), ~9:30 (midday), ~12:00 (about an hour before the 1:00 PM PT close) — identify which slot this run is. Note whether the US market is pre-open / open / closed at run time. "2 trading days" = the next 2 NYSE sessions, skipping weekends and US market holidays (including observed holidays and early closes — check the NYSE calendar for the current week). A trading day is "completed" only after that session's close.

predictions.csv schema (create on main with this header if missing):
run_date,run_time,model,ticker,rebound_pct,ref_price,status,graded_date,max_pct_2td,hit,peak_day,min_pct_2td,drop_pct,sector,drop_type,cause,intraday_state,vol_signature,earnings_soon,corr_group
MIGRATION: if predictions.csv exists with an older header (fewer columns), rewrite the header to the full schema above, preserve all existing rows, and leave the new columns blank for existing rows. Never reorder or delete existing columns or rows.
Feature column definitions (set at pick time in S8, from S3 findings):
- drop_pct: today's % change at pick time (negative number, 2 decimals)
- sector: Finviz sector of the ticker
- drop_type: company | sympathy | macro | mixed (from S3d)
- cause: primary cause slug, one of: earnings, guidance, legal, balance-sheet, management, ma-artifact, product, supply-chain, sympathy, macro, index-del, other
- intraday_state: new-lows (within 0.5% of session low) | off-lows | near-high (upper third of session range)
- vol_signature: heavy | normal | light (vs 90-day average pro-rated for time of day)
- earnings_soon: 1 if earnings within 5 calendar days, else 0
- corr_group: short slug shared by picks driven by the same story this run (e.g. "hca-payer", "ibm-software"); blank if standalone

context.csv schema (create with header if missing):
run_date,run_time,model,slot,spy_pct,qqq_pct,vix,qualify_count,rout_theme
One row per run, written in S8: SPY and QQQ % change today and VIX level (raw-fetched per G1, e.g. stockanalysis.com/stocks/SPY/ and finance.yahoo.com/quote/%5EVIX); qualify_count = full qualifying set size before the S1 cutoff; rout_theme = one short phrase naming the day's dominant selling story (or "none").

S0 GRADE & CALIBRATE (do first):
GRADE: Load predictions.csv from main. For every row status=pending where BOTH of the 2 NYSE sessions after run_date have fully closed: fetch the ticker's daily OHLC for those 2 sessions (source: stockanalysis.com/stocks/TICKER/ historical; fallback: finance.yahoo.com/quote/TICKER/history — per rule G1). Set max_pct_2td=(highest high/ref_price-1)*100; hit=1 if max_pct_2td≥3 else 0; peak_day=1 or 2 (which session had that high); min_pct_2td=(lowest low/ref_price-1)*100; status=graded; graded_date=today. Grade each row on its own ref_price. Never re-grade graded rows. (Grading only acts on rows now due; rows graded earlier today are skipped.)
CALIBRATE: Dedupe to one sample per (ticker, run_date) using that ticker's LAST run of the day. Compute, for graded samples:
(a) hit-rate per rebound_pct band (90-100, 80-89, …) using ONLY rows whose model = this run's model; if a band has <10 same-model samples, fall back to all-model pooled for that band and note the fallback;
(b) FACTOR RATES: hit-rate per drop_type value, per cause value, and per intraday_state value — each value counts only if it has ≥10 graded samples (any model);
(c) drawdown note: median min_pct_2td of hits vs misses (context for how much pain preceded rebounds).
Write all of this to calibration.json ({updated, model, bands:{...}, factors:{...}, notes}) — include it in this run's commit. Bands with ≥10 samples that systematically over/under-shoot: nudge today's S4 outputs toward observed rates. Factor rates feed S4 as secondary evidence. Hold a one-line summary for internal use.

S1 SCREEN (mechanical — do fast, per rules G1–G3):
Criteria — keep tickers meeting ALL, right now: price change ≤ -4.00% (today, vs prior close); market cap ≥ $10B USD (no upper limit); 90-day avg volume ≥ 400,000; member of DJIA, NASDAQ 100, OR S&P 500.
Source = Finviz screener, two URLs, sorted % change ascending (most-negative first):
https://finviz.com/screener.ashx?v=111&f=cap_largeover,idx_sp500,sh_avgvol_o400&o=change
https://finviz.com/screener.ashx?v=111&f=cap_largeover,idx_ndx,sh_avgvol_o400&o=change
Fetch mechanics: curl -sSL (Finviz 301-redirects screener.ashx → /screener); parse each row's ticker, company, sector, market cap, price, % change, volume from the raw HTML. PAGINATE: each page shows only 20 rows — append &r=21, &r=41, &r=61, … and keep fetching until a page's LAST row has % change > -4.00% (only then have you seen every qualifying name). Do NOT use the ta_toplosers signal (it caps results).
Run the G2 freshness check on the parsed data before proceeding.
Merge both lists, dedupe by ticker, sort by today's % change ascending. List the full qualifying set + total count as internal working detail (working output only — S6 governs the final visible message); record the count for context.csv. Then apply the CUTOFF: keep only the 12 most-down tickers. Tie at the 12th spot: break by larger market cap, then higher 90-day avg volume. If ≤12 qualify, keep all. Everything downstream — S3 research, S4 rank, S5 table, S7 notification, S8 log — operates ONLY on this capped set of ≤12.
RETRY rule: if a URL returns empty/error OR the parsed data fails the G2 freshness check, retry up to 3× (pause ~5s before the 2nd try, ~10s before the 3rd); if still failing, notify "Screen failed: Finviz unreachable" and stop.
ZERO-QUALIFY rule: if zero tickers qualify, first complete the G3 verification (wait ~60s, re-fetch both URLs, cross-check the top decliner on one independent source). Only if verification still shows zero: run S8 (context.csv row + any grading, no new prediction rows), notify "No candidates today.", stop.
Note: Finviz is ~15 min delayed; names right at -4.00% may flip in/out — accept borderline names as the verified list returns them.

S2 MACRO/INDUSTRY SCAN (run ONCE per run; shared by all tickers — never repeat per ticker):
List only ACTIVE forces, each as: theme → sectors touched → direction (headwind/tailwind). Cover: geopolitics (war, sanctions, tariffs, elections); energy (oil/gas/power); rates/macro prints (Fed, CPI/PPI, jobs, yields); broad risk sentiment (index moves, risk-on/off, volatility); input supply shocks — shortage OR glut (semiconductors, steel, cement/concrete, copper/metals, lithium, energy, labor, freight/shipping); regulation/policy/legal at sector level (antitrust, court rulings, agency/DOJ/FTC); FX / USD. Also fetch (per G1) SPY %, QQQ %, VIX for context.csv, and name the day's rout_theme in one phrase.

S3 PER-TICKER RESEARCH (THE MAIN EFFORT; ≥2 independent sources per name — ≥3 when sources disagree; reconcile conflicts):
For each qualifying ticker:
a) TREND: classify today's move as (i) one-day blip, (ii) acceleration of an existing downtrend, or (iii) break of an uptrend. Note 3-month direction; oversold? Note volume signature: heavy-volume capitulation vs light-volume drift.
INTRADAY PATH (today so far): note the open, current price, and session low/high; is it stabilizing / bouncing off today's low, or still making new intraday lows? Source: today's intraday data on finance.yahoo.com/quote/TICKER or stockanalysis.com/stocks/TICKER/ (per rule G1). Classify intraday_state per the schema definition.
CROSS-RUN EVOLUTION: if this ticker has earlier row(s) today in predictions.csv, compare the current price to those earlier ref_prices — falling further (continued weakness) or recovering (turning up)? Factor this into the read.
b) MACRO LINK: map sector to S2 themes — exposed? headwind/tailwind/none?
c) CAUSE — traverse the FULL checklist; record EVERY factor that applies (don't stop at the first):
earnings/guidance — and is earnings within 5 calendar days? (flag)
legal/regulatory: litigation, court ruling, investigation, agency/DOJ/FTC action
balance-sheet/financial: debt, capital raise/dilution, downgrade, dividend cut, liquidity/credit
management/governance change
M&A or corporate action — if the drop ITSELF is a reverse split / spin-off / merger artifact → EXCLUDE the ticker
product/operational/supply-chain
sympathy/sector: peers down together
macro: per S2
Pick the PRIMARY cause slug for the `cause` column.
d) DROP TYPE: company-specific | sympathy/sector | macro | mixed. Assign corr_group slugs where several picks share one driver.
Sources: finviz.com/quote.ashx?t=TICKER (news+sector); finance.yahoo.com/quote/TICKER/news; stockanalysis.com/stocks/TICKER/; web search "TICKER stock why down today" + date. If two sources conflict, pull a 3rd independent source to break the tie, then adopt the most-supported cause and flag any remaining uncertainty.

S4 RANK — P(3%+ rebound within 2 trading days), integer 0–100; sort high→low. Apply S0 calibration: band adjustment first (same-model preferred), then, as secondary evidence, the S0 factor rates — if this pick's drop_type/cause/intraday_state values have ≥10-sample observed hit-rates, tilt the number a few points toward them. Never let a factor tilt override the band adjustment direction.
For each ticker, first note its main RAISE and LOWER factors (up to 2 of each, only ones that truly apply), then set the number to reflect that balance.
RAISE: deeply oversold; fundamentals intact; one-off/non-fundamental drop; mechanical/forced selling (index deletion/rebalance); transient macro/sympathy drop; sector tailwind; heavy-volume capitulation; already bouncing off today's intraday low; recovering vs an earlier run today; no near-term earnings.
LOWER: structural/secular decline; unresolved legal/regulatory overhang; deteriorating balance sheet, dilution, dividend cut; priced-to-perfection (good earnings ≠ a rise); earnings within 5 days; active sector macro headwind; downtrend with no catalyst; still making new intraday lows; falling further than an earlier run today; light-volume drift.
If ≥3 top picks share one sector/driver, note in their Reasoning that they are one correlated bet (and give them a shared corr_group).

S5 BUILD TABLE — sort high→low %; before output, verify rows are in descending Rebound % order and re-sort if any are out of place. Columns: Ticker | Rebound % | Industry (is this stock affected?) | News driving drop | Reasoning. Each cell = one plain-English sentence, no finance jargon. "News driving drop" names the dominant driver (company/sector/macro). Because runs are intraday, each Reasoning sentence must note the move may still be developing.

S6 DELIVER — render the table as a phone-readable styled HTML file (dark, card-per-stock, mobile viewport) and deliver/attach that HTML file in THIS run's output so the user can open it directly. Do NOT put the HTML in the repo. The run's visible message = the table only, no extra commentary.

S7 NOTIFICATION — text ONLY: "[HH:MM PT] Top picks: [tickers ranked high→low]." (or "[HH:MM PT] No candidates today." / the failure message.)

S8 LOG — append this run's picks to predictions.csv, one row per (run_date,run_time,ticker): skip any ticker already logged for this same run_date + run_time. The same ticker MAY be logged again on a later run the same day (different run_time). New row: run_date=today, run_time=this run's time, model=this run's model id, ticker, rebound_pct, ref_price=current price, status=pending, graded fields blank, plus all feature columns (drop_pct, sector, drop_type, cause, intraday_state, vol_signature, earnings_soon, corr_group) from S3. Append this run's row to context.csv. Commit predictions.csv, context.csv, calibration.json (and ROUTINE.md if updated) DIRECTLY to main ("log <date> <run_time> picks"). No branch, no PR.
