# Vintage archive

Every raw input value the GDPNow replication reads, recorded when first seen and each time it changed (forward from the first file).

`deltas/<as_of>_<timestamp>.parquet`: source, series, date (observation), value, prior_value (NULL = new observation), seen_at
(when the pipeline retrieved it), as_of (the run date). A row with value NULL marks a date that disappeared from a series.
The state on a day is the newest row per (source, series, date) among the files up to that day. Written by `scripts/12_archive.py`;
read with `tools/vintage_asof.py` or DuckDB: `SELECT * FROM read_parquet('deltas/*.parquet')`.
