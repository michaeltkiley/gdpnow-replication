"""Durable vintage archive of every raw input value the model reads.

The daily run keeps its pulls in DuckDB (`raw_pulls`: source, series, as_of, retrieved_at, date, value) for seven days, and the
sources overwrite their history when they revise (no vintages, unlike ALFRED). This module records, forward in time and forever, each
observation when it was first seen and every time its value changed, so the data as it stood on any past day can be rebuilt.

Layout (a directory, in production the `data` branch of the repository):
    deltas/<as_of>_<write timestamp>.parquet   columns: source, series, date, value, prior_value, seen_at, as_of
    README.md
A delta file holds the observations that are new or revised since the archive's current state (prior_value NULL = new), plus a row
with value NULL for each date that disappeared from a series pulled that day. The first file is therefore the whole baseline.
The current state is the newest row per (source, series, date), read with DuckDB over all files; unchanged days write nothing.
"""
import datetime as dt
from pathlib import Path

import duckdb
import pandas as pd

TOL = 1e-9            # relative; a value counts as revised beyond this
README = """# Vintage archive

Every raw input value the GDPNow replication reads, recorded when first seen and each time it changed (forward from the first file).

`deltas/<as_of>_<timestamp>.parquet`: source, series, date (observation), value, prior_value (NULL = new observation), seen_at
(when the pipeline retrieved it), as_of (the run date). A row with value NULL marks a date that disappeared from a series.
The state on a day is the newest row per (source, series, date) among the files up to that day. Written by `scripts/12_archive.py`;
read with `tools/vintage_asof.py` or DuckDB: `SELECT * FROM read_parquet('deltas/*.parquet')`.
"""


def _files(d, upto=None):
    fs = sorted((Path(d) / 'deltas').glob('*.parquet'))
    if upto is not None:
        fs = [f for f in fs if f.stem.split('_')[0] <= str(upto)]
    return [str(f) for f in fs]


def state(d, upto=None, source=None, series=None):
    """DataFrame(source, series, date, value): the newest archived value per key among the files up to the run date `upto`
    (inclusive; None = all); keys whose newest row is a removal are left out."""
    fs = _files(d, upto)
    if not fs:
        return pd.DataFrame({'source': [], 'series': [], 'date': pd.to_datetime([]), 'value': []})
    where = ''
    args = [fs]
    if source is not None:
        where += ' AND source = ?'
        args.append(source)
    if series is not None:
        where += ' AND series = ?'
        args.append(series)
    q = f"""SELECT source, series, CAST(date AS DATE) AS date, value FROM (
              SELECT *, row_number() OVER (PARTITION BY source, series, date ORDER BY filename DESC) AS rn
              FROM read_parquet(?, filename = true) WHERE true {where}) WHERE rn = 1 AND value IS NOT NULL"""
    out = duckdb.connect().execute(q, args).fetchdf()
    out['date'] = pd.to_datetime(out['date'])
    return out


def compute_delta(today, d, as_of):
    """Delta rows (source, series, date, value, prior_value, seen_at, as_of) of `today` (DataFrame source, series, date, value, seen_at)
    against the archive in directory `d`."""
    db = duckdb.connect()
    t = today.copy()
    t['date'] = pd.to_datetime(t['date']).dt.normalize()
    arch = state(d)
    db.register('t', t)
    db.register('a', arch)
    new_or_rev = db.execute(f"""
        SELECT t.source, t.series, CAST(t.date AS DATE) AS date, t.value, a.value AS prior_value, t.seen_at, '{as_of}' AS as_of
        FROM t LEFT JOIN a ON t.source = a.source AND t.series = a.series AND CAST(t.date AS DATE) = CAST(a.date AS DATE)
        WHERE a.value IS NULL OR abs(a.value - t.value) > {TOL} * greatest(1, abs(a.value))""").fetchdf()
    removed = db.execute(f"""
        SELECT a.source, a.series, CAST(a.date AS DATE) AS date, CAST(NULL AS DOUBLE) AS value, a.value AS prior_value,
               current_timestamp::TIMESTAMP AS seen_at, '{as_of}' AS as_of
        FROM a JOIN (SELECT DISTINCT source, series FROM t) p ON a.source = p.source AND a.series = p.series
        LEFT JOIN t ON t.source = a.source AND t.series = a.series AND CAST(t.date AS DATE) = CAST(a.date AS DATE)
        WHERE t.value IS NULL""").fetchdf()
    return pd.concat([new_or_rev, removed], ignore_index=True)


def write_delta(delta, d, as_of):
    """Write a delta to `d`/deltas; returns the path."""
    p = Path(d) / 'deltas'
    p.mkdir(parents=True, exist_ok=True)
    path = p / f'{as_of}_{dt.datetime.now(dt.timezone.utc).strftime("%H%M%S%f")}.parquet'
    db = duckdb.connect()
    db.register('x', delta)
    db.execute(f"COPY (SELECT * FROM x ORDER BY source, series, date) TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    return str(path)


def prefix_rows(con, seen_at):
    """The borrowed history (growth rates of the spliced series taken from the GDPNow workbook, table hist_growth) as archive rows:
    source 'workbook_prefix', series = series name. Archived so a lost cache cannot erase the reference the monthly splice check compares against."""
    try:
        df = con.execute("SELECT name AS series, date, growth AS value FROM hist_growth WHERE source LIKE 'workbook%'").fetchdf()
    except Exception:                      # no splice store yet (first run)
        return pd.DataFrame(columns=['source', 'series', 'date', 'value', 'seen_at'])
    df.insert(0, 'source', 'workbook_prefix')
    df['seen_at'] = seen_at
    return df[['source', 'series', 'date', 'value', 'seen_at']]


def archive(con, d):
    """Archive every run date in raw_pulls from the newest already archived onward (oldest first). Returns [(as_of, new, revised, removed)]; idempotent: a date whose values
    are already archived writes nothing."""
    Path(d).mkdir(parents=True, exist_ok=True)
    readme = Path(d) / 'README.md'
    if not readme.exists():
        readme.write_text(README)
    res = []
    done = [Path(f).stem.split('_')[0] for f in _files(d)]
    newest = max(done) if done else ''          # older run dates still in raw_pulls are already in the archive: skip them
    dates = [r[0] for r in con.execute('SELECT DISTINCT as_of FROM raw_pulls WHERE as_of >= ? ORDER BY 1', [newest]).fetchall()]
    for as_of in dates:
        today = con.execute('SELECT source, series, date, value, retrieved_at AS seen_at FROM raw_pulls WHERE as_of = ?', [as_of]).fetchdf()
        if as_of == dates[-1]:             # the store holds only the current borrowed history: archive it with the newest run
            today = pd.concat([today, prefix_rows(con, dt.datetime.now())], ignore_index=True)
        delta = compute_delta(today, d, as_of)
        if len(delta):
            write_delta(delta, d, as_of)
        res.append((str(as_of), int((delta.prior_value.isna() & delta.value.notna()).sum()),
                    int((delta.prior_value.notna() & delta.value.notna()).sum()), int(delta.value.isna().sum())))
    return res
