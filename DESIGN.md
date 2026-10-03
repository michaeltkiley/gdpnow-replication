# GDPNow Replication — Design Memo (for approval)

Status: **Decisions D1–D5 approved 2026-10-03** (see §4). No model code has been written yet.

## 0. Governing principle

**Every coefficient is re-estimated from data on every run.** The workbook's estimated objects (factor, AR and bridge coefficients, BVAR forecasts, blend weights, CIPI and IVA stacks) are never inputs to the production path. They are used only (i) as a test fixture in L1, to prove our code is correct, and (ii) as an optional benchmark against each new Atlanta Fed release. The pipeline must be able to run into the indefinite future with nothing hard-coded except the *documented specification constants* in one cited config file (§4 D4).

---

## 1. Target and source files

| Item | Value |
|---|---|
| Target nowcast | GDPNow for **2026:Q3**, published **Oct 1, 2026** (after construction spending and ISM Manufacturing) |
| Headline (full precision, `TrackingHistory`) | **3.677319405706525** (published 3.7) |
| Success criterion | Headline within ±0.1 pp; subcomponents reported, not pass/fail |
| Model workbook | `references/20261003_GDPTrackingModelDataAndForecasts.xlsx`, SHA-256 `113391cc…4e306`, identical to your copy and to the file live on atlantafed.org on 2026-10-03 |
| Methodology | Higgins (2014), FRBA WP 2014-7 (`references/20140701_Higgins_GDPNow_WP2014-07.pdf`, 86 pp) |
| Spec changes after 2014 | `references/20261003_ModificationsToGDPNowModel.pdf` (Oct 2017 → Dec 2025) |
| Release calendar | `references/20261003_GDPNowcastDataReleaseDates.xlsx` |
| Published summary | `references/20261003_RealGDPTrackingSlides.pdf` |

**Vintage note.** The workbook is the **Oct 1** vintage. The Employment Situation and full M3 report came out Oct 2 (an internal update, not posted), and the workbook's payroll series end in August. L3 must therefore reproduce data **as of 2026-10-01**, not as of today.

### Verification constants (`PUBLISHED_*`, from `TrackingHistory` and `ContribHistory`, Oct 1 column)

| Component | Growth % SAAR | Contribution, pp |
|---|---|---|
| GDP | 3.677319405706525 | — |
| PCE goods | 2.976039218610005 | 0.6284257233236813 |
| PCE services | 3.4106699052166567 | 1.5859191288468768 |
| Equipment | 18.49802092606929 | 1.0378131580282546 |
| Intellectual property products | 6.721885264362282 | 0.3823825376865659 |
| Nonresidential structures | 6.636770473952258 | 0.18921686390129455 |
| Residential | −1.1695403582793196 | −0.04324888595539576 |
| Federal government | 5.494064966467027 | 0.3297656114583558 |
| State and local government | 1.0741530073677508 | 0.12203516011165605 |
| Goods exports | −11.614261706775375 | −0.9402449589753905 |
| Services exports | 4.832122137236539 | 0.1939777338784257 |
| Goods imports | 16.474022928903096 | −1.805846653442799 |
| Services imports | 1.4519803464114744 | −0.04257157523700933 |
| CIPI level ($bn 2017) | 53.808069704056635 (prior quarter −63.438) | 2.0396955620818993 |

---

## 2. What the research established

1. **The workbook contains the model's full chain except the estimation code.** It has 55 sheets. The *value-only* sheets hold the inputs and estimated objects: monthly levels and transformed series (149), factor actuals and forecasts, factor-augmented AR coefficients, bridge coefficients, RLS weights, quarterly BVAR forecasts, quarterly price forecasts, inventory CIPI/IVA forecast stacks, and 488 quarterly NIPA series. The ten component sheets (`Consumption` … `StateLocal`) are **live Excel formulas** that turn those inputs into each component's nowcast.
2. **The component sheets reproduce the published numbers.** For example, `Equipment!FX6` = 18.498020926…, `Residential!FX6` = −1.16954…, and `Consumption!FX6` = 2.976039218610… all match `TrackingHistory` to about 1e-12. So the formulas are an executable specification for L1.
3. **The final aggregation is pinned down exactly.** Fisher-chaining the 13 published component growth rates with the workbook's `QtrlyPriceForecasts` reproduces **3.677319** to six decimals. This requires the paper's rule that the inventory price is the average of the current and previous end-of-quarter deflators. Using the end-of-quarter deflator as-is gives 3.695.
4. **Current model specification**: Higgins (2014) plus the modifications log.

   | Stage | Current spec | Source |
   |---|---|---|
   | Dynamic factor | 126 standardized monthly series, 1 factor with AR(3) dynamics, AR(1) idiosyncratic errors. Starting value from Stock–Watson missing-data principal components (PCA), then OLS, then Kalman smoother. No outlier replacement since Apr 2020. | WP §3 step 2b; mods 2018, 2020 |
   | Monthly fill-in | Factor-augmented AR: q∈[1,6] lags of the series (consumption q∈[3,6]), r∈[0,3] factor lags, chosen by AIC, plus 10 dummies for Mar 2020 through Dec 2020 | WP eq. 3; mods 2022 |
   | Bridge equations | OLS since 1985Q1 for each detailed subcomponent. Subcomponents without an indicator use AR(1) with 2020 quarterly dummies. Aggregated with previous-quarter nominal shares (Törnqvist) | WP eqs. 6–7; mods 2017, 2022 |
   | Quarterly BVARs | 13 real components, 5 lags, λ=0.15, sum-of-coefficients prior τ=10λ; the 13 deflators the same with λ=0.12. Bańbura–Giannone–Reichlin dummy-observation prior, sample from 1968 | WP step 1 and appendix |
   | Blend weights ("RLS") | Weighted least squares with weights 1/(1+t/80)², since 1985, restricted to sum to 1 and clipped to [0,1]. 2020Q1–Q4 dropped (an analogous adjustment for trade and inventories, which use contributions) | mods 2022, 2023 |
   | Monthly price BVAR | 34 prices, 12 lags, λ=0.05, Waggoner–Zha conditional forecasts | WP step 2a |
   | Consumption | Monthly real PCE detail (4 goods and 5 services buckets) with Törnqvist shares, retail-control and CPI mapping, unit auto sales, IP-utility and travel-service regressions | WP step 6; mods 2017 |
   | Net exports | Monthly real goods and services trade contribution via factor-augmented AR, consistency adjustment, blended with BVAR. Gold-adjusted BOP measures. A 5-variable gold BVAR and an 11-variable capital-goods-shares BVAR handle the windows after the advance (AEI) report | WP step 5; mods 2017, 2025 |
   | Inventories | Census book value + IVA identity; price BVAR → core-quantity BVAR (λ=0.07) → 6 block BVARs. IVAs from PPIs with turnover patterns, AR(1) discrepancy and joint likelihood maximization; Denton interpolation; Fisher-chained stocks. Blended with the BVAR (weight 0.857 monthly / 0.143 BVAR) | WP step 7; mods 2017 |
   | Aggregation | Fisher chain over the 13 components; contributions via BEA/Whelan with multinomial annualization | WP appendix A3–A5 |

5. **The workbook's data panel is not the whole factor panel.** The paper's 2014 factor list (124 series) includes Conference Board confidence (3 series), Michigan sentiment (3), ISM sub-indexes, ISM non-manufacturing, the Chicago Fed Midwest index and the S&P 500. None of these are in the workbook. The workbook has 113 transformed series plus the ISM composite, inventories and prices indexes (in `InventoryRaw`). The current panel has 126 series, and its exact membership isn't published.
6. **Proprietary or non-reproducible inputs in the workbook**: the ISM Manufacturing indexes (3; licensed), the KR-CRB spot commodity index (`PZALL`), the Macroeconomic Advisers / S&P Global monthly nominal GDP (`MGDPN…splice`, used as monthly weights for trade contributions), and Haver's X-13 seasonal adjustment of a few series (`saFTO`, `saFTOD`, `saRMFG`, the petroleum import price).

---

## 3. Replication levels, concretely

L1 is a test harness. **L2 and L3 are the product**, and L3 is the production configuration (public data, all coefficients re-estimated).

### L1 — mechanical recompute; code-correctness test only (expected: exact)
- **Inputs:** only the workbook's **value-only** sheets.
- **Work:** reimplement the logic of the ten component sheets in Python: monthly fill-in from the AR coefficients and the factor, quarterly aggregation, bridge equations, the BVAR blend, inventory stocks, then Fisher aggregation. No cached formula results are read as inputs.
- **Checks:** (a) each intermediate is compared with the workbook's cached cell values (tolerance 1e-8); (b) each component and the headline are compared with `PUBLISHED_*`.
- **Extra check:** recalculate the workbook headless in LibreOffice (installed) and confirm the cached values regenerate. This proves the formulas are the full transformation.

### L2 — re-estimation diagnostic (expected: close, not exact)
*Role (approved 2026-10-03):* L2 re-estimates every **group 1** registry item (P01–P19) from the workbook's constructed data, with every series cut at its actual-data boundary (§7 finding 1). That isolates estimation fidelity from data differences. Weights and transformations (groups 2–3) are not recomputed in L2. **L3 is the complete path**: every registry item is produced from public raw data.
Re-estimate each stage from the workbook's data, then run the chain two ways:
- **Swap one stage at a time:** replace one stage's workbook output with ours and keep everything else from L1. This attributes the headline gap to each stage.
- **Full chain:** everything re-estimated. **This is the ±0.1 pp test.**
- **Per-stage checks:** factor correlation and RMSE against `Factor`; each FA-AR lag choice and coefficient against `FactorAugARCoeffs`; bridge coefficients against `BridgeEqnCoeffs`; blend weights against `RLSweights`; BVAR forecasts against `QtrlyBVARForecasts` and `QtrlyPriceForecasts`; CIPI stacks against `CIPIbeastackFore` and `ivaBEAForeStack`.

### L3 — public data only (strict, as you chose)
- **Every input** comes from FRED/ALFRED, BEA, Census or BLS **as of 2026-10-01**.
- **Proprietary inputs** are dropped or replaced with public substitutes. The model is re-estimated (L2 code) and the gap is reported.
- **Stage by stage**, our public-data series are compared against the workbook's series to locate any differences.

---

## 4. Decisions (approved 2026-10-03)

**D1 — Inventory system: full re-estimation.** Follows from §0. The whole system is re-estimated on every run: the 34-variable conditional price BVAR, the 16-variable core-quantity BVAR and 6 block BVARs, the PPI-based IVA model (turnover patterns, AR(1) discrepancy, joint likelihood maximization), Denton interpolation, and the Fisher-chained stocks. It is built in layers, and the report shows the gap after each layer: (1) AR(4) for farm and other; (2) motor-vehicle and nonmerchant-wholesale blocks; (3) Census-industry blocks with IVAs. Highest-risk part of the project.

**D2 — Factor panel: public panel plus substitutes.** Rebuild the workbook's ~113 monthly series from public sources and add public substitutes for the licensed surveys:
- University of Michigan sentiment
- the Empire, Philadelphia, Richmond, Kansas City and Dallas Fed manufacturing surveys, in place of ISM and the Conference Board

The rule that month-h data aren't used until that month's ISM release is kept, using the public ISM release dates. Validation: correlation with the workbook's `Factor` ≥ 0.98, with the effect on the headline measured by the swap test.

**D3 — Licensed inputs elsewhere: public substitutes, Census X-13.**

| Licensed input | Used in | Public substitute |
|---|---|---|
| ISM composite and inventories indexes | inventory core BVAR | average of regional Fed survey composite and inventories components |
| ISM prices index | price BVAR | average of regional Fed survey prices-paid components |
| KR-CRB spot index | price BVAR | PPI crude materials plus WTI spot price |
| Macroeconomic Advisers monthly nominal GDP | trade contribution weights | BEA quarterly nominal GDP, Denton-interpolated with public monthly nominal indicators |
| Haver X-13 (`saFTO`, `saFTOD`, `saRMFG`, petroleum import price) | various | Census X-13ARIMA-SEATS, default settings, run each time |

Atlanta Fed constructed series are rebuilt from their documented definitions. Where a definition can't be recovered, the closest public proxy is used and listed in the gap table.

**D4 — Specification constants: fixed as documented; λ re-tuned by rule.**
- **Fixed constants**, in `config/spec.toml` with a citation for each:
  - estimation start dates (quarterly BVAR 1968; price BVAR 1983; bridges and blend 1985Q1); samples expand forward from these
  - lag counts and AIC ranges (q∈[1,6], consumption [3,6], r∈[0,3])
  - COVID dummies
  - recency weights 1/(1+t/80)²
  - 2020 exclusions
- **λ for every BVAR is fixed at the documented values** (0.15, 0.12, 0.05, 0.07, 0.25). *Revised 2026-10-03:* the workbook's quarterly BVAR is reproduced only at exactly λ = 0.15 (sharp minimum), so GDPNow evidently fixes λ. The Bańbura–Giannone–Reichlin rule gives ≈0.09 and is reported as a sensitivity (≈0.001 pp of headline on this date). The BVAR coefficients themselves are re-estimated every run.
- Nothing is ever calibrated to match the workbook.
- Where the documentation is silent, the factor and factor-augmented AR samples use the full data span. AIC lag choices are compared with `FactorAugARCoeffs` as a diagnostic only.

**D5 — Vintages: `--asof`, default today, plus an archive of every pull.**
- **Production:** current data (`--asof` = today).
- **Replication:** `--asof 2026-10-01`. FRED-hosted series come from ALFRED as of that date. BEA and Census API series are used when they weren't revised after that date; otherwise they are flagged as possible vintage drift.
- **Archive:** every run's raw pulls are stored in DuckDB with their retrieval date. Any run can be reproduced, and the archive builds a real-time dataset for L4.

---

## 5. Pipeline and repo layout (as agreed, refined)

```
gdpnow-replication/
  README.md  LICENSE (MIT)  DESIGN.md  .env (ignored)  .gitignore
  config/spec.toml             # documented specification constants, each with a citation (D4)
  references/                  # downloaded PDFs/xlsx (xlsx ignored), sha256 manifest
  gdpnow/                      # small library, one module per model stage
    io_workbook.py  factor.py  faar.py  bridge.py  bvar.py  blend.py
    consumption.py  trade.py  inventory.py  aggregate.py  public_data.py
  scripts/
    01_ingest_workbook.py      # download dated xlsx + sha256 → DuckDB (all value sheets, tidy)
    02_ingest_public.py        # FRED/ALFRED/BEA/Census as of --asof → DuckDB + YYYYMMDD_*.csv
    03_transform.py            # --level L2|L3: build transformed monthly/quarterly panels
    04_estimate.py             # --level L2|L3 [--stage factor|faar|bridge|bvar|blend|inventory]
    05_nowcast.py              # --level L1|L2|L3 [--swap STAGE]: run chain → components + GDP
    06_verify.py               # PUBLISHED_* constants; OK/MISMATCH per value and per stage
    07_site.py                 # writes docs/ static HTML site (matplotlib charts)
  data/                        # ignored: gdpnow.duckdb, raw/YYYYMMDD_*.csv
  docs/                        # generated site (committed later for GitHub Pages)
```
Every script skips work that's already done and accepts `--force`. None of these stages should run for hours; the largest, the inventory likelihood maximization, should take minutes. If one turns out to be long, I'll give you the command to run instead.

---

## 6. Order of work, with a checkpoint after each milestone

1. **M1 — L1 complete.** Ingest, the component logic, aggregation, the verify script and the LibreOffice recalc check. *Checkpoint: L1 report.*
2. **M2 — L2 core.** Factor, FA-AR, bridges, quarterly BVARs and the blend, so investment and government are re-estimated. *Checkpoint.*
3. **M3 — L2 consumption and trade.** Includes the price BVAR, gold and capital-goods BVARs.
4. **M4 — L2 inventories** (per D1). *Checkpoint: full L2 headline vs ±0.1.*
5. **M5 — L3.** Public data mapping (about 920 tickers, reduced to the set actually used) and rerun. *Checkpoint.*
6. **M6 — site.** Static HTML site: headline, component table, stage-by-stage diagnostics, gap table, charts.

**Main risks:** the inventory system (D1); undocumented details in Atlanta Fed constructed series (L3); and the treatment of trade and inventories in the January 2023 weighting change, which the docs describe only as an "adjustment similar to" dropping 2020.

---

## 7. Parameter registry and workbook audit (2026-10-03)

### What "coefficient" covers
Every number in the model that isn't raw source data. The registry, [`registry/parameters.csv`](registry/parameters.csv), lists 53 families in six groups:

| Group | Families | How produced on each run |
|---|---|---|
| 1 Estimated parameters | 19 (P01–P19) | Estimated: factor model, factor-augmented AR equations including AIC lag orders, 44 bridge equations, blend weights, quarterly quantity and price BVARs, monthly price BVAR, inventory BVARs and IVA likelihood model, gold and capital-goods BVARs, travel and utility regressions, services trade deflator regressions, λ by the BGR rule |
| 2 Aggregation weights | 10 (W01–W10) | Computed from data: subcomponent nominal shares, PCE bucket shares, Fisher GDP and inventory aggregation, trade contribution weights, import price weights, structures deflator weights, retail-control Fisher prices, Fisher subtraction, Törnqvist trade aggregates |
| 3 Transformation constants | 5 (T01–T05) | Computed from data: standardization, splice ratios, X-13 seasonal factors, Denton and Atkeson–Ohanian interpolation, the shutdown fill rule |
| 4 Published external constants | 9 (E01–E09) | Cited agency tables: Census construction-spending weights α, the CPI→PCE, PPI→structures, IVA price and end-use mappings, indicator mappings, gold definitions, release calendar. Each gets a citation and a check for agency revisions |
| 5 Rules | 8 live (R01–R08), plus display-only (X01) | Documented rules implemented in code; display-only formulas are excluded |
| 6 Specification constants | S01 | `config/spec.toml` (D4) |

### How the audit was done (reproducible tools)
- `tools/formula_patterns.py` collapses roughly 47,000 component-sheet formulas into 1,099 relative (R1C1-style) patterns. Their numeric literals are only unit conversions and lookup indexes.
- `tools/dependency_trace.py` walks precedents backward from each component's result cell. **25,196 formula cells are live.** Everything else is display-only (X01): trailing averages, the Feb/Aug/Nov rule, pasted snapshots, which a reference trace confirmed nothing reads. The tool also writes [`registry/inputs_used.csv`](registry/inputs_used.csv), the 427 inputs the assembly reads: 214 coefficients, 21 model outputs, 16 data series with model-forecast tails, 139 constructed series, and 37 raw series.
- Both tools can be rerun on any future workbook to detect specification changes.

### Findings
1. **Several "data" sheets contain model output:**
   - `MonthlyPriceLevels`: all 33 series run to Dec 2026 (price BVAR tails).
   - `ConsMonthlyLevels` and `ConsTransformedMonthlySeries`: `*fr`, `*Fore` and `*Rev` series (regression and BVAR outputs).
   - `InvDefDatafr`: the 2026Q3 values are Denton forecasts.
   - `TransformedMonthlySeries`: deflated series embed price BVAR forecasts for months where the nominal value is released before its price index.

   Nothing from these sheets past the actual-data boundary may be used as data.
2. **The KR-CRB index (`PZALL`) stops at Mar 2026.** The Atlanta Fed's own price BVAR is running on a stale input, so the D3 substitute is needed anyway.
3. **Oct 1 falls inside the "AEI window"**: the Advance Economic Indicators report came out Sep 30 and the full trade report on Oct 6. So the gold BVAR (P15) and the capital-goods shares BVAR (P16) are both active for this replication. Neither is represented in the workbook.
4. **The workbook confirms the documented AR(1)+2020-dummy specification** (P04) for the 21 subcomponents without an indicator, rather than the paper's AR(4).
5. **Specification gap:** the 2023 note's "adjustment similar to" dropping 2020, for the trade and inventory blend regressions, isn't defined precisely. This will need a documented choice when M2 reaches the blend regressions.

### Enforcement
Every run writes a provenance manifest. Each group 1–3 registry entry gets one record: produced by (module, run id, input-data hash). `06_verify.py` fails the run if any group 1–3 entry has no record, or if any production-path value was read from the workbook.

---

## 8. M1 result: L1 code-correctness test (2026-10-03)

**Result: PASS.** Run `L1_20261003`; full output in `data/20261003_verify_L1_20261003.csv`.

| Check | Result |
|---|---|
| GDP headline | 3.6773193 vs published 3.67731941, diff −1.5e-7 pp (tolerance 1e-6) |
| 12 component growth rates, 13 contributions, 6 aggregates (PCE, final sales, final sales to domestic purchasers, private domestic final purchases, CIPI level and change) | all within 1e-5 of published |
| 138 intermediate workbook cells: 44 subcomponent forecasts and shares, 22 indicator growth rates, component totals, consumption buckets, trade, inventories | 135 within 1e-8; 3 explained (below) |

**Findings from building L1**
1. **Federal government residual.** The workbook's own `FederalGovt!FX6` (5.494062476) differs from its `TrackingHistory` (5.494064966) by 2.5e-6 pp. Our code reproduces the sheet exactly, and the gap is the entire 1.5e-7 pp headline residual: substituting the published federal value makes GDP exact to 1e-13.
2. **Travel PCE revisions (registry R09).** The sheet's revision rule compares two state cells, `Consumption!FV64:FV65`, that are blank in the posted workbook, so the sheet applies travel revisions and shows services at 3.4549. The published figure (3.4107) is reproduced exactly (to 9e-14) when revisions apply only if PCE and travel-trade data end in the same month. On Oct 1, PCE ran through August and trade through July, so no revisions applied. We implement the rule from the data's release state. The sheet's travel rows and services total are the 3 known cell exceptions.
3. **Final aggregation is exact.** Fisher chain over 13 components, with the inventory price taken as the mean of the current and previous end-of-quarter deflators. Contributions use BEA/Whelan with multinomial annualization, split evenly.
4. **Inventory CIPI.** The monthly model rolls the Census-industry real stocks forward from Q1 actuals through a model Q2 to Q3, then blends with the BVAR (0.857/0.143). The result is reproduced to 8e-13.
5. **LibreOffice recalculation check** (`tools/recalc_check.py`). 10 of the 12 sheets recalculate identically. Consumption and Inventories differ only in cells whose formulas branch on `TYPE()` or use approximate `MATCH`, where LibreOffice's function semantics differ from Excel's. This is not stale cached values: our independent Python reproduces Excel's cached values for every checked cell.
6. **Lookup case-insensitivity.** Excel lookups ignore case, so `DSf_USNA` in the sheet matches `DSF_USNA` in the data. Our code uses the stored spelling.

**Code layout produced in M1:** `gdpnow/{config,store,workbook,inputs,monthly,components,aggregate,nowcast}.py`, `scripts/{01_ingest_workbook,05_nowcast,06_verify}.py`, `config/{spec,bridges}.toml`. The assembly code consumes an `Inputs` bundle, so L2/L3 only have to produce that bundle from our own estimates; the assembly is the same code L1 has verified.

---

## 9. M2 result: L2 core re-estimation (2026-10-03)

Our code now estimates the factor, all 46 factor-augmented AR equations (with AIC lag selection), all 44 bridge equations, both quarterly BVARs, and the six investment/government blend weights, from the workbook's data. Run `L2_20261003`: **GDP 3.8261 vs published 3.6773 (+0.149 pp)**. Diagnostics are in `data/20261003_diagnostics_L2_20261003.csv`, attribution in `data/20261003_attribution_L2_20261003.csv`.

| Stage (our estimate replaces the workbook's) | Headline effect alone | Cumulative | Fidelity vs workbook |
|---|---|---|---|
| Dynamic factor | **+0.255** | +0.255 | correlation 0.9994 over 1967–2026, but Sep-2026 value +0.016 vs −0.341 |
| Factor-augmented AR equations | −0.024 | +0.243 | 9/46 exact (1e-14); 28/46 same AIC lags; rest differ moderately |
| Bridge equations | −0.079 | +0.166 | 21/21 AR(1)+dummy exact; 22 indicator bridges close (median coef. diff 0.03) |
| Quarterly quantity and price BVARs | −0.001 | +0.164 | forecasts within 0.05 pp (quantities) and 0.16 pp (prices) at documented λ |
| Blend weights (investment, government) | −0.017 | +0.149 | within 0.003 of workbook except federal (0.630 vs 0.655) |

**Findings**
1. **The factor's latest month dominates.** On Oct 1 only five panel series in the workbook report September (claims, two Philly Fed indexes, ISM composite and inventories). On these our factor, like the Atlanta Fed's own `AltFactor` sheet (+0.003), shows a neutral September; the official factor shows −0.341, which must reflect September data the workbook does not contain (ISM subindexes, Conference Board, Michigan, other surveys). *Decision (2026-10-03):* add public September surveys in L3 (per D2) and report the latest-month gap as its own line in the gap decomposition.
2. **λ is fixed in GDPNow today.** The workbook's quarterly BVAR is reproduced only at exactly λ = 0.15. The BGR rule gives ≈0.09, with a headline effect of ≈0.001 pp. *D4 revised:* λ is fixed at the documented values; the BGR rule is reported as a sensitivity.
3. **Bridge regressors use the nowcast quarter's availability pattern** (paper eq. 5). Past quarters' indicators are rebuilt with the currently missing months replaced by factor-augmented AR forecasts, so bridge coefficients depend on the nowcast date. Excluding 2020 from indicator bridges does *not* improve the fit to the workbook (median difference 0.06 vs 0.03), so it isn't applied (not documented).
4. **Blend weights confirm the 2022/2023 specification:** restricted WLS with weights 1/(1+t/80)², 2020 excluded. Including 2020 gives very different weights (e.g. equipment 0.98 vs 0.86).
5. **Factor-augmented AR residual differences** don't come from the sample window, the factor version or dummies. They are most likely an older estimation vintage for some equations (construction, home sales and trade were revised in late September).

**Still from the workbook (provenance check flags them):** monthly price BVAR (P08/P09), travel/utility regressions (P10), farm and other inventory AR (P11), inventory CIPI/IVA system and deflators (P12–P14), trade and inventory blend weights (part of P05). These are M3 (consumption and trade) and M4 (inventories).

**Pipeline:** `04_estimate.py --level L2` → `05_nowcast.py --level L2` → `06_verify.py --run L2_<vintage>`. Per-field provenance is now carried in `Inputs.provenance` and checked in stage 06.

---

## 10. M3–M4 result: L2 complete (2026-10-03)

Every group-1 registry item is now estimated by our code from the workbook's data (provenance check: 29/29 OK). Run `L2_20261003`: **GDP 3.9635 vs published 3.6773 (+0.286 pp)**.

| Stage (alone, vs L1) | Effect | Fidelity vs workbook |
|---|---|---|
| Dynamic factor | +0.255 | Sep-2026 value (see §9) |
| Factor-augmented AR equations | −0.024 | §9 |
| Bridge equations | −0.079 | §9 |
| Quarterly BVARs | −0.001 | §9 |
| Monthly price BVAR (34-var, 12 lags, λ=0.05, Waggoner–Zha conditioning on Sep oil and ISM prices) | ≈0 | Sep import/export/CPI/retail-deflator forecasts within 0.1–0.8 pp annualized |
| Travel/utility regressions | ≈0 | electricity exact; travel within 0.15 (constant) |
| Inventory IVA model | +0.041 | July exact (underlying-detail rule); August within 1–6 $bn |
| Inventory CIPI block BVARs | −0.043 | Sep Census CIPI and MV/nonmerchant paths within a few $bn |
| Inventory deflators | ≈0 | within 0.6 index points |
| Farm/other inventory AR(4) | **+0.120** | not reproducible: no sample window, end date or dummy choice matches; the workbook coefficients evidently come from an older (pre-2023-revision) data vintage |
| Blend weights (all 9) | +0.003 | all within 0.01, except federal (0.025) |

**Choices and deviations (documented, not tuned to the workbook)**
1. **Monthly price BVAR:** the last actual month on Oct 1 is August for every price series. Monthly nominal GDP (Macroeconomic Advisers/S&P) is actual through July and extended at **4.5% SAAR**, as in WP fn 21 (registry R10); the workbook shows exactly this. The BVAR conditions on September WTI and ISM prices, which were released by Oct 1. Without this conditioning, September trade prices are off by 8–13 pp; with it, they match.
2. **Inventory IVA (P13):** the first month of the quarter uses BEA underlying-detail stocks (Mods Oct-2017; exact). Later months use a holding-gain model: IVA = −12·book₋₁·Σ w·Δln PPI, with non-negative weights over the available PPIs and turnover lags ≤4, plus an AR(1) discrepancy (WP step 7e). The workbook lacks the paper's industry net-output PPI composites (Table A8c), so the model chooses non-negative combinations of the broader PPIs that are available.
3. **Inventory CIPI (P12):** core BVAR on the paper's 16 variables (Table A8a; 6 lags; from 1983; λ=0.07) with conditional forecasts given released data. The six block equations (Table A8b scalings) are estimated by OLS from 1997 (NAICS data), not as Bayesian blocks, and the ML step combining IVA and BVAR likelihoods (WP 7f) is not implemented. The IVA model and BVAR are used sequentially instead.
4. **Inventory deflators (P14):** proportional extrapolation of each end-of-quarter deflator using the end-of-quarter-month change in the matching spliced trade sales deflator from the price BVAR (CPI new vehicles, carried forward, for motor-vehicle dealers). This stands in for the Denton step (WP fn 32). Of the variants tested it was closest to the workbook (mean 1.1 pp annualized).
5. **Inventory blend history:** the paper uses fixed parameters; we re-estimate the inventory model for each past quarter. History starts 1997Q3 because NAICS monthly inventory data begin in 1997.
6. **Consumption inputs** `*Fore` and `*Rev` (model-derived) are replaced by the published series they extend; their regressions are re-estimated (P10).
7. **AEI-window goods trade** (gold BVAR P15, capital-goods shares BVAR P16): in L2 the workbook's August goods trade values already embed these constructions, so they are taken as data in L2 and rebuilt from public data in L3.
