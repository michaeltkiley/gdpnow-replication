# gdpnow-replication

An independent implementation of the Federal Reserve Bank of Atlanta's **GDPNow** nowcast of real GDP growth that
runs from **public data** and re-estimates every model parameter on each run. First application: the
**2026:Q3 nowcast as of October 1, 2026** (published 3.677%).

| Level | Inputs → parameters | GDP (% SAAR) | vs published 3.677319 |
|---|---|---|---|
| L1 | GDPNow workbook data and coefficients (code check) | 3.6773 | −1.5e-7 |
| L2 | workbook data → parameters estimated here | 3.969 | +0.29 |
| **L3** | **public data only → parameters estimated here** | **3.762** | **+0.085** |

**Read [IMPLEMENTATION.md](IMPLEMENTATION.md)** for how the implementation works, how to run it, and a complete
list of its differences from the GDPNow approach (data that is not public, series built differently, model
components that differ). Component-level results are there and in the generated report `docs/report.html`.

## Quick start

```
python scripts/01_ingest_workbook.py --date 20261003                 # once: downloads the workbook (history store, L1/L2)
python scripts/02_build_public.py --asof 2026-10-01 --last-price-month 2026-08
python scripts/04_estimate.py --level L3 --asof 2026-10-01
python scripts/05_nowcast.py --level L3 --asof 2026-10-01
python scripts/06_verify.py --run L3_20261001 && python scripts/07_site.py
```

Needs Python (pandas, numpy, scipy, statsmodels, duckdb, openpyxl), `pdftotext`, the Census X-13ARIMA-SEATS
binary in `tools/x13/`, and free FRED, BEA and Census API keys in `.env` (not committed). Stages skip finished
work; `--force` redoes them.

## Contents

`gdpnow/` library · `scripts/` pipeline stages · `config/` documented specification constants and bridge structure ·
`registry/` parameter registry and input audits · `tools/` audit and search utilities · `references/` GDPNow
documentation · `IMPLEMENTATION.md` user guide · `DESIGN.md` development notes.

## Sources

- Higgins, Patrick (2014), "GDPNow: A Model for GDP 'Nowcasting'," FRBA Working Paper 2014-7.
- Federal Reserve Bank of Atlanta, "Modifications to GDPNow Model" (2017–2025), and the GDPNow model workbook
  (downloaded by stage 01; it contains Haver Analytics data and is not redistributed).

This project is not affiliated with the Federal Reserve Bank of Atlanta. License: MIT.
