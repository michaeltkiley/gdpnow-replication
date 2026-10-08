# Implementation guide: public-data GDPNow

This repository re-implements the Federal Reserve Bank of Atlanta's GDPNow nowcast of real GDP growth and
runs it from public data. This guide describes what the implementation does today and **every way it differs
from the GDPNow approach**. It is written for someone who wants to run it, interpret its output, or extend it.

Sources for the GDPNow method: Higgins (2014), FRBA Working Paper 2014-7 ("WP"), and the Atlanta Fed's
"Modifications to GDPNow Model" ("Mods", Oct 2017 – Dec 2025), both in `references/`.

## 1. What it does

For a given as-of date the implementation builds the 13-component bottom-up nowcast of real GDP growth for the
current quarter:

1. Assemble monthly and quarterly data (public sources only; section 6).
2. Estimate **every parameter from that data on each run** (section 4): a one-factor dynamic factor model,
   factor-augmented autoregressions for monthly indicators, bridge equations, quarterly and monthly BVARs,
   blend weights, the inventory system and the trade models. No coefficient is read from the GDPNow workbook.
3. Forecast each GDP component, combine bridge-equation and BVAR forecasts with the estimated blend weights,
   aggregate with a Fisher chain, and compute contributions to growth.

Three levels are produced so the pieces can be checked separately:

| Level | Inputs | Parameters | Purpose |
|---|---|---|---|
| L1 | GDPNow workbook (data and coefficients) | from the workbook | tests the assembly and aggregation code |
| L2 | GDPNow workbook data | estimated here | tests the estimation code |
| L3 | **public data only** | estimated here | the public-data implementation (this guide) |

The workbook is used for L1/L2 and as a benchmark; the L3 path never reads workbook coefficients (checked by a
provenance test in `scripts/06_verify.py`).

## 2. Current results (2026:Q3 nowcast as of October 1, 2026; published 3.677319)

| Level | GDP (% SAAR) | Difference |
|---|---|---|
| L1 | 3.6773 | −1.5e-7 (135 of 138 intermediate cells within 1e-8; 3 documented exceptions) |
| L2 | 3.969 | +0.29 |
| **L3** | **3.762** | **+0.085** |

L3 components (growth, % SAAR, and contribution for inventories) against the published values:

| Component | L3 | GDPNow |
|---|---|---|
| PCE goods | 3.06 | 2.98 |
| PCE services | 3.42 | 3.41 |
| Equipment | 17.33 | 18.50 |
| Intellectual property products | 6.76 | 6.72 |
| Nonresidential structures | 6.64 | 6.64 |
| Residential | −1.86 | −1.17 |
| Federal government | 5.63 | 5.49 |
| State and local government | 1.11 | 1.07 |
| Goods exports | −13.24 | −11.61 |
| Services exports | 4.76 | 4.83 |
| Goods imports | 14.12 | 16.47 |
| Services imports | 1.83 | 1.45 |
| Change in private inventories (contribution, pp) | 2.08 | 2.04 |

The headline difference is the net of component differences that partly offset each other (for example goods
exports and goods imports). Section 5 lists the causes.

The implementation was validated for this one nowcast date. Behaviour on other dates (different data-release
patterns, other quarters) has not been tested.

## 3. Running it

```
python scripts/01_ingest_workbook.py --date YYYYMMDD    # L1/L2 only: download the workbook (+sha256) -> DuckDB
python scripts/02_build_public.py --asof YYYY-MM-DD --last-price-month YYYY-MM   # build the L3 data bundle
python scripts/04_estimate.py --level L3 --asof YYYY-MM-DD                        # estimate all parameters
python scripts/05_nowcast.py --level L3 --asof YYYY-MM-DD                         # nowcast + contributions
python scripts/06_verify.py --run L3_YYYYMMDD                                    # compare with published values
python scripts/07_site.py                                                         # HTML report -> docs/report.html
```

* Each stage skips work that is already done; `--force` redoes it. A full L3 run takes about ten minutes.
* **First run on a fresh clone:** run stage 01 once (it downloads the GDPNow workbook) before stage 02, so the
  history prefix (DD4) is stored in the DuckDB growth store; later runs and as-of dates reuse the stored history.
* `--last-price-month` is the last month with published CPI/PPI/trade prices at the as-of date.
* `02_build_public.py --ism seeded` is an optional variant that supplies ISM history from the workbook (DD1).
* Requirements: Python 3 with pandas, numpy, scipy, statsmodels, duckdb, openpyxl; the Census X-13ARIMA-SEATS binary at `tools/x13/x13as/x13as_ascii`
  (statsmodels calls it); free API keys for FRED, BEA and Census in `.env` (`FRED_API_KEY`, `BEA_API_KEY`,
  `CENSUS_API_KEY`).
* Everything lands in `data/` (DuckDB file and dated flat files); nothing in `data/` or `.env` is committed.

## 4. What is estimated each run

All parameter families are listed in [`registry/parameters.csv`](registry/parameters.csv) (53 families in six
groups) with the module that produces each one. In summary:

| Estimated every run (module) | Method |
|---|---|
| Dynamic factor: loadings, AR(3) factor, AR(1) idiosyncratic errors (`factor.py`) | PCA start, EM, Kalman smoother |
| Factor-augmented AR equations incl. AIC lag orders and 2020 dummies (`faar.py`) | OLS, AIC over own lags 1–6 (consumption 3–6), factor lags 0–3 |
| Bridge equations; AR(1) + 2020 dummies where no indicator (`bridge.py`) | OLS from 1985Q1 |
| Blend weights (`blend.py`) | restricted weighted least squares, recency weights 1/(1+t/80)², 2020 excluded |
| Quarterly quantity and price BVARs (`bvar.py`) | dummy-observation (Banbura–Giannone–Reichlin) prior |
| Monthly price BVAR with conditional forecasts (`estimate.py`) | 12 lags, Waggoner–Zha |
| Services-trade deflator regressions, electricity and travel PCE regressions (`estimate.py`) | OLS |
| Inventory system: IVA model, core BVAR, block equations, farm/other AR(4) (`inventory.py`) | see MD1–MD4 |
| Gold BVAR and capital-goods-shares BVAR (`trade_bvar.py`) | see MD6 |
| Aggregation weights, share weights, price indices | computed from the data each run |

Fixed specification constants taken from the GDPNow documentation (lags, BVAR tightness 0.15 / 0.12 / 0.05 / 0.07
/ 0.25, sample starts, dummy structure) are in [`config/spec.toml`](config/spec.toml) with a citation for each.
Bridge structure (which indicators feed which sub-component) is in `config/bridges.toml`.

## 5. Differences from GDPNow

Each item states what GDPNow does, what this implementation does, and the consequence.

### Data that is not public or not available in the same form

**DD1. ISM manufacturing indexes.** GDPNow uses the ISM PMI composite, inventories and prices indexes (licensed;
ISM publishes them behind a login).
*Here:* stand-ins built from the regional Federal Reserve manufacturing surveys on FRED, averaged and rescaled to
the ISM convention (50 + x/2): composite = Philadelphia current activity, New York general business conditions,
Dallas general business activity; prices = the three districts' prices-paid indexes; inventories = New York,
Philadelphia and Dallas (finished goods) inventory indexes (Dallas's materials index ended in December 2017 and is no longer used). Uses: the composite is a factor-panel
input; the prices stand-in conditions the monthly price BVAR. **The ISM inventories stand-in is not used anywhere,
and neither stand-in is used in the inventory models** (the core inventory BVAR runs on 14 instead of 16 variables
and the block equations omit the two ISM terms; `ism_in_inventory_models` in `spec.toml`). The stand-ins track ISM's
monthly changes only loosely (the inventories one very loosely). `--ism seeded` instead uses the workbook's ISM
history through the last released month and a regression nowcast for the latest month.

**DD2. Monthly nominal GDP.** GDPNow uses Macroeconomic Advisers' (S&P) monthly GDP, actual through the latest
release and extended at 4.5% SAAR.
*Here:* BEA quarterly nominal GDP interpolated to months with monthly nominal PCE (the GDP/PCE ratio held linear
within the quarter), extended at 4.5% SAAR after the last actual month as GDPNow does. Month-to-month movements
differ from the Macro Advisers series. It is used only for the GDP shares in the net-exports contribution formula.

**DD3. Existing-home sales (NAR).** NAR sells the historical data file (its site: "Historical data can be
purchased"); its free monthly release PDFs and the FRED mirror carry only the latest 13–14 months, and no complete
free source was found (FRED/ALFRED hold no longer history). Earlier history therefore comes from the
workbook-derived prefix (DD4), with the 13 public months as the live layer. The level of the series (existing
versus new sales) uses unit sales × the trailing 12-month average NAR median price ÷ the real-estate-brokerage
PPI. The series enters only the brokers'-commissions bridge (about 15% of residential investment).

**DD4. Workbook-derived history prefix (data only).** For 65 series the public source starts later than the model
needs (for example Census end-use trade from 1999, Treasury defense outlays from 2015, NAR existing-home sales, and
many Census manufacturing, trade and construction series that begin in 1992–1993 on the public APIs). The months before the first public observation
are filled with the GDPNow workbook's own growth rates for that series. This is **data, never coefficients**; it
is stored once in the DuckDB growth store (`hist_growth`) and reused, and each public series is checked against
the workbook where they overlap (`hist_splice_checks`). The full list with the first public month and the length
of the filled prefix is [`registry/workbook_history_prefix.csv`](registry/workbook_history_prefix.csv) (rewritten by stage 02). Without
the workbook (production use after the first run) the stored history is used. If a public series is later
redefined, the store detects that its stored growth no longer agrees with the live source, drops the stored live
rows and logs a `store_reset`.

**DD5. Data vintages.** The ~20 series still read from FRED are retrieved as known on the as-of date (ALFRED). Everything
read from primary-source files (BLS flat files, Census program zips, BEA bulk files, trade and vehicle workbooks, the Fed's G.17
files, BEA IDS-0182, Treasury) is **current vintage on the download date**, archived with the retrieval date (the archive keeps
seven days in the run's database; the durable vintage archive below keeps every value as first seen or revised). GDPNow uses the vintages its workbook held on its update date, so revisions
published after that date can differ (for example Census book values for the prior month).

**DD6. Prior-vintage travel data.** GDPNow's workbook keeps the previous vintage of the monthly travel-services
trade series to estimate revisions to the latest month of travel PCE. The previous vintage of that BEA file is not
available, so those columns are empty and the travel revision adjustment is inactive. (It is also inactive under
GDPNow's own rule on the replicated date.) The previous-vintage retail and food-services series used for the
analogous retail revision terms are built from FRED/ALFRED as known one month earlier.

### Seasonal adjustment

**DD7. X-13ARIMA-SEATS here, Haver default there.** GDPNow seasonally adjusts some series with Haver's default
settings. This implementation uses Census X-13ARIMA-SEATS (default automatic settings, from a stated start year)
for: manufacturing PPI (1985), intermediate-materials PPI (1985), new-home average price (1975),
manufactured-home price (2014), Treasury total outlays (1990), defense outlays (2000), civilian-aircraft PPI
(1990), petroleum import price (1990). The Treasury outlay series match the workbook's adjusted series only
approximately (growth correlation about 0.95 and 0.98).

### Series built differently from GDPNow's

**SD1. Structures price (monthly private nonresidential construction).** GDPNow: previous-quarter-share-weighted
log change of ~25 structure-type prices (WP eq. A1, Table A9; Mods Dec-2025 for data centers). *Here:* the same
construction with public series: PPIs for new office, warehouse and industrial construction, the steel-pipe PPI,
the Census house-price deflator, and the Atkeson–Ohanian extrapolation of each type's own NIPA deflator. Growth
correlation with the workbook's series is 0.97 (not exact).

**SD2. Improvements deflator.** GDPNow: geometric mean of the Census house-price deflator, a PPI for net inputs to
residential maintenance and repair, and a construction-ECI extrapolation. *Here:* identical from 2015 on (that
PPI series was discontinued in 2014, after which the mean is of the other two); before 2015 the PPI term uses
BLS `WPUIP2321001` as the closest public series.

**SD3. Goods import price.** Eight BLS end-use import price indexes (foods, petroleum seasonally adjusted,
industrial supplies durable and nondurable ex petroleum, computers, capital goods ex computers, autos, consumer
goods) weighted by previous-quarter NIPA import shares. Growth correlation with the workbook's series is 0.99,
not exact. The goods export price is the BLS all-exports index (matches exactly).

**SD4. Services-trade deflators.** The quarterly regression (NIPA services deflator change on a goods price and
the lagged four-quarter change) is applied to every month. Exports use the BLS all-exports index and match the
workbook's series exactly; imports use the BLS all-imports-excluding-petroleum index.

**SD5. Manufactured-homes value.** Census shipments × the seasonally adjusted Census average sales price; the
price (published with a longer lag) is extended at its trailing 12-month growth rate; no PPI deflator is applied.
Growth correlation with the workbook's series is 0.99.

**SD6. Construction spending detail.** Federal and state-and-local construction come from Census historical
tables (deflated by the structures price); new single-family plus multifamily construction (permanent-site
housing) from the Census private-construction table; the Census house-price deflator from `price_uc_cust.xlsx`.

**SD7. Trade detail.** Goods trade by end use, BOP-basis nonmonetary gold, and BEA's own seasonal adjustment
come from BEA IDS-0182 (current vintage); the advance-report month comes from the Census Advance Economic
Indicators report's Excel Table 1 (`tab1adv.xlsx`); monthly travel services trade from BEA's
trade-release time-series file. Core capital-goods exports follow the Mods composition and have growth
correlation 0.99 with the workbook's series (core imports match exactly); the remaining difference is not
identified.

**SD8. Other series.** CPI major appliances from the BLS API (not on FRED); retail ex-autos and other-industries
inventories by Törnqvist subtraction; truck/bus shares from BEA underlying-detail table 7.2.5S with a
constant-share proxy where the public history is short.

### Model components that differ

**MD1. Inventory valuation adjustment (IVA) model.** GDPNow estimates the IVA turnover weights and the CIPI block
models jointly by maximum likelihood with industry PPI composites. *Here:* the first month of the quarter is
rebuilt exactly from BEA underlying-detail stocks (as in the Mods); later months use a holding-gain model
IVA = −12 · book₋₁ · Σ w·Δln PPI with non-negative weights over a fixed set of public PPIs per industry (turnover
lags ≤ 4) plus an AR(1) discrepancy, estimated by non-negative least squares **sequentially**, not jointly.

**MD2. Inventory deflators for the nowcast quarter.** GDPNow uses a Denton interpolation of the monthly sales
prices (WP fn. 32). *Here:* proportional extrapolation of each end-of-quarter deflator with the end-of-quarter-month
change in the matching sales deflator (CPI new vehicles, carried forward, for motor-vehicle dealers).

**MD3. Inventory block equations.** GDPNow's industry block models are Bayesian blocks combined with the IVA
likelihood (WP step 7f). *Here:* the six industry block equations are estimated by OLS from 1997 (NAICS data),
scaled as in WP Table A8b, and used sequentially with the IVA model (MD1); the core BVAR uses the paper's
variables, 6 lags, from 1983, tightness 0.07.

**MD4. Farm and other-industry inventories.** An AR(4) on quarterly growth is re-estimated each run from 1985Q1.
The workbook's own AR(4) coefficients cannot be reproduced by any sample window tried, so the forecasts differ
(in L2 this accounts for about +0.12 pp of the headline).

**MD5. Housing: not implemented.** The WP appendix algorithm (eq. A2) that forecasts construction spending for
months where housing starts and prices are out but the Census spending release is not, and the multifamily
starts/spending BVAR, are not implemented. Permanent-site residential investment uses published Census spending.
This has no effect on the replicated date (August spending was already released) but would matter on dates when
spending lags starts.

**MD6. Gold BVAR and capital-goods-shares BVAR (the advance-report month).** Both follow the Mods (gold: five
variables, six lags, tightness 0.25; shares: eleven variables, six lags) but the Mods leave several choices
open. Choices made here: both use the BGR sum-of-coefficients prior (variables in log levels, shares in levels); the
gold BVAR sample starts in 2013; the shares BVAR uses the full IDS-0182 sample (1999 onward) and chooses its tightness each run
from {0.01, 0.02, 0.05, 0.1, 0.25, 0.5} by recursive pseudo-out-of-sample error over the last 36 months. These
choices were checked against the one published vintage, which is a calibration risk for other dates. The
August goods exports/imports they produce differ from GDPNow's (goods exports −13.2 vs −11.6, goods imports 14.1
vs 16.5, growth); in the headline the two nearly offset. Census-basis aggregates in the gold BVAR come from the
advance report; BOP gold from IDS-0182.

**MD7. BVAR tightness.** Fixed at the documented values (no data-driven rule) except the shares BVAR (MD6). The
Banbura–Giannone–Reichlin data-driven tightness rule is not implemented.

**MD8. Dynamic factor at the ragged edge.** Same specification as GDPNow, estimated on the public panel (the
ISM inventories stand-in is excluded, DD1). Its value for the latest month differs from GDPNow's factor (in L2,
where the data are identical, this accounts for about +0.26 pp of the headline); the cause is not identified.

**MD9. Monthly price BVAR with missing last months.** Series whose last one to three months are not yet published
(for example the BEA sales deflators) are kept in the panel: the BVAR is fitted on the complete sample and the
missing months are filled by the conditional forecast, with published values imposed where they exist.

## 6. Public sources

| Source | Used for | Access |
|---|---|---|
| BLS flat files (`download.bls.gov/pub/time.series/`, 17 files) | employment, hours, earnings, CPI, PPI, import/export prices, ECI (68 series that FRED mirrors, plus BLS-only series) | public download; `BLS_CONTACT` secret supplies the User-Agent contact string (`gdpnow/bls_flat.py`, `config/bls_series.toml`) |
| Census program zips (`census.gov/econ_getzippedfile/?programCode=`) | M3 and advance durable goods, retail and wholesale trade and inventories, advance goods trade, new residential construction and sales, construction spending, manufactured housing, ~40 series that FRED mirrors | public download (`gdpnow/census_bulk.py`, `config/census_series.toml`) |
| Census Advance Economic Indicators table | advance-month goods trade by end-use category | public xlsx `census.gov/econ/indicators/tab1adv.xlsx` (`gdpnow/trade_bvar.py`) |
| Census construction tables, price file | owner-split and private construction spending; house-price deflator | public xlsx |
| BEA bulk files (`apps.bea.gov/national/Release/TXT/`) | all 33 NIPA and underlying-detail tables (quarterly and monthly) and the PCE, income and wage series FRED mirrors | public download (`gdpnow/bea_bulk.py`, `config/bea_series.toml`, `config/bea_windows.toml`) |
| BEA trade-release time series | monthly goods and services trade balances, travel services | public xlsx (`gdpnow/bea_trade.py`) |
| BEA Motor vehicles workbook (`apps.bea.gov/national/xls/gap_hist.xlsx`) | light-vehicle sales (domestic, foreign, heavy trucks) | public xlsx (`gdpnow/bea_vehicles.py`) |
| BEA IDS-0182 | goods trade by end use, Census and BOP basis, SA, nonmonetary gold | public zip download |
| Federal Reserve G.17 files (`federalreserve.gov/releases/g17/Current/ipdisk/`) | industrial production, capacity utilization, motor vehicle assemblies (`ip_sa.txt`, `utl_sa.txt`, `auto_sa.txt`) | public text files (`gdpnow/fed_g17.py`, `config/g17_series.toml`) |
| Treasury FiscalData | Monthly Treasury Statement (defense outlays) | API (no key) |
| FRED / ALFRED | what has no clean public file: regional Fed surveys (Dallas, Philadelphia, New York), Cleveland Fed median and trimmed-mean CPI, CFNAI, jobless claims, WTI oil, UMich sentiment, the Treasury statement balance, NAR existing-home sales and prices (licensed: FRED serves 13 months only) | API, `FRED_API_KEY` |
| Census X-13ARIMA-SEATS | seasonal adjustment | binary in `tools/x13/` |

Every file source has the same contract: one download per file per run, each series archived in `raw_pulls`, values checked
against FRED's over their full history before they replaced it (`tools/explore_apis.py`), and **no fallback**: a failed
download or a missing series fails the run (the workflow emails the failure).

**As-of date.** The run's date is the US Central date (`gdpnow/clock.py`), not the runner's UTC date: FRED rejects a real-time date after its own
current date (HTTP 400), and GitHub can start the scheduled run hours late, past midnight UTC.

**Daily change check (`gdpnow/probe.py`).** Before building, the daily run repeats the cheap signal of every request logged by the
last production build and skips the build when none changed (and the code, config, month and last run are as before). The signal
is chosen per source: BLS, BEA, Fed and file downloads whose server sends Last-Modified and an ETag are checked by a header-only
request (Content-Length is ignored: BLS sends it intermittently); Census program zips by their size and last-updated stamp on
`census.gov/econ_datasets/` (one request; the zips' own Last-Modified is just the current time); Census spreadsheets, whose CDN
sends no ETag and a Last-Modified that differs between its servers, by a digest of the content; FRED by a digest of the
response. A probe replays about 60 small requests in a few seconds.

**Vintage archive (`gdpnow/vintage.py`, branch `data`).** After each successful daily run the workflow records every input
value the model read (FRED, the bulk files, IDS-0182, the AEI advance trade table) as it was first seen and each time it changed,
so the inputs as they stood on any past day can be rebuilt. The archive is a directory of Parquet files,
`deltas/<as_of>_<timestamp>.parquet` (source, series, date, value, prior_value, seen_at, as_of), on the orphan `data` branch; the
first file is the whole baseline, later files hold only new or revised observations (relative tolerance 1e-9) plus a row with a NULL
value for each date that disappeared from a series pulled that day; unchanged days write nothing. The state on a day is the newest
row per (source, series, date) among the files up to that day (`tools/vintage_asof.py`, or DuckDB over `deltas/*.parquet`). The step
runs after the results commit, so a failure emails but cannot lose a result; it is idempotent. First production run (2026-10-07):
3.5 MB baseline, 0.75 MB the next day, 37 KB for a day with few changes.

**Input freshness check (`gdpnow/freshness.py`).** On every day the inputs are rebuilt, after the build, the run compares each series
in `raw_pulls` with a baseline (observation count and last date per series, table `freshness_baseline`, saved by the last run that
passed the check) and flags a series that disappeared (absent today under every source), shrank (fewer observations or an earlier last
observation), or is stale (last observation older than 2.5 times its own median release gap plus 45 days). A flag holds the run like
the nowcast sanity checks: results are written, the page is not updated, the workflow fails and GitHub emails, and the baseline is left
as it was so the problem is not forgotten after one email. Series that legitimately behave so are listed with a reason in
`config/freshness.toml` (patterns `source|series`). Skip days (nothing new) are not checked; the first run has no baseline and gets
only the stale check.

## 7. Repository map

| Path | Contents |
|---|---|
| `gdpnow/` | library: data builders (`public_*.py`, `ids0182.py`, `history.py`), source readers (`bls_flat.py`, `census_bulk.py`, `bea_bulk.py`, `bea_trade.py`, `bea_vehicles.py`, `fed_g17.py`), change check (`probe.py`), vintage archive (`vintage.py`), freshness check (`freshness.py`), estimation (`factor.py`, `faar.py`, `bridge.py`, `blend.py`, `bvar.py`, `estimate.py`, `inventory.py`, `trade_bvar.py`), assembly (`components.py`, `nowcast.py`, `aggregate.py`) |
| `scripts/` | pipeline stages 01, 02, 04, 05, 06, 07, 12 (vintage archive; `archive_to_branch.sh` pushes it to `data`) |
| `config/` | `spec.toml` (documented constants), `bridges.toml`, `transforms.toml`, `public_series.toml`, and the per-source series maps `bls_series.toml`, `census_series.toml`, `bea_series.toml`, `bea_windows.toml`, `g17_series.toml`, `freshness.toml` (ignore list of the freshness check) |
| `registry/` | `parameters.csv` (every non-data quantity), `inputs_used.csv`, `input_audit.csv` and `input_audit_all.csv` (public series vs workbook, per series), `workbook_history_prefix.csv` (DD4), `match_search.csv` |
| `tools/` | `audit_inputs.py` (compare every public series with the workbook), `search_matches.py` (search all Census/BEA series for a match to a workbook series), workbook-audit utilities |
| `docs/report.html` | generated replication report |
| `docs/index.html` | live dashboard (static; reads `docs/data/`) |
| `DESIGN.md` | chronological development notes (background; superseded by this guide where they differ) |

The input audit (`python tools/audit_inputs.py --all`) classifies each workbook series as exact, good, fair,
poor or not built, and flags those that end earlier than the workbook's; for the series the model reads, none is
poor.

## 8. Limits

* Validated for a single nowcast (2026:Q3 as of 2026-10-01). The real-time track record over many dates is not
  part of this repository.
* Items DD1, DD2, MD1, MD2, MD5, MD6 and MD8 are the main sources of difference between the public run and GDPNow.
* The replication needs the ISM-type licensed data and Macro Advisers' monthly GDP to eliminate DD1 and DD2; the
  other differences are specification or vintage choices that can be changed in code.

This project is not affiliated with the Federal Reserve Bank of Atlanta. License: MIT.
