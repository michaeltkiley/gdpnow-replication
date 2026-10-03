# gdpnow-replication

An independent replication of the Federal Reserve Bank of Atlanta's **GDPNow** nowcast of real GDP growth,
built so that every coefficient, weight and transformation is re-estimated from public data on every run.

Target of the first replication: the GDPNow nowcast for **2026:Q3 published October 1, 2026 (3.7%; 3.677319 at
full precision)**.

## Status

| Level | What it shows | Status |
|---|---|---|
| L1 | Our code, given the Atlanta Fed's own inputs and estimates, reproduces their published nowcast | **Done**: GDP 3.6773193 vs 3.6773194; all 13 components and contributions within 1e-5; 135/138 intermediate workbook cells within 1e-8 (3 explained) |
| L2 | Re-estimating every model parameter from the workbook's data | Next (M2-M4) |
| L3 | Rebuilding everything from public raw data (FRED/ALFRED, BEA, Census, BLS) | Later (M5) |

See [DESIGN.md](DESIGN.md) for the full design, decisions and findings, and
[registry/parameters.csv](registry/parameters.csv) for every non-data quantity in the model and how it is produced.

## Pipeline

```
python scripts/01_ingest_workbook.py --date 20261003      # download dated workbook (+sha256) -> DuckDB
python scripts/05_nowcast.py --level L1                   # assemble the nowcast
python scripts/06_verify.py --run L1_20261003             # OK / MISMATCH vs published values and workbook cells
```

Each stage skips work already done; pass `--force` to redo it. Data live in `data/gdpnow.duckdb` and dated
CSV files in `data/` (not committed). API keys for later stages go in `.env` (`FRED_API_KEY`, `BEA_API_KEY`,
`CENSUS_API_KEY`; not committed).

## Layout

```
gdpnow/      library: workbook parsing, monthly machinery, component assembly, Fisher aggregation
scripts/     pipeline stages 01-07
config/      spec.toml (documented specification constants, cited), bridges.toml (bridge structure, cited)
registry/    parameters.csv (every non-data quantity), inputs_used.csv (every input the nowcast reads)
tools/       workbook audit tools (formula patterns, dependency trace, LibreOffice recalculation check)
references/  methodology paper, modification notes, release calendar (workbook itself not committed)
```

## Sources

- Higgins, Patrick (2014), "GDPNow: A Model for GDP 'Nowcasting'," FRBA Working Paper 2014-7.
- Federal Reserve Bank of Atlanta, "Modifications to GDPNow Model" (2017-2025) and the GDPNow model workbook
  `GDPTrackingModelDataAndForecasts.xlsx` (downloaded by `01_ingest_workbook.py`; contains Haver Analytics data
  and is therefore not redistributed here).

This project is not affiliated with the Federal Reserve Bank of Atlanta.

License: MIT.
