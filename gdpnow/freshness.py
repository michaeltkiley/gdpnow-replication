"""Narrow input freshness check: flags series the model reads that went missing, got shorter, or stopped advancing.

Compares the day's pulls in `raw_pulls` with a baseline: the summary (observation count, last date) of each series as of the last run that
passed this check, kept in table `freshness_baseline` (not the previous calendar day: a same-day rerun or an older code state must not
serve as the baseline). The first run has no baseline and only gets the stale check. A series is flagged when it
  - disappeared: in the baseline, absent today under every source (a series that moved to another source is not);
  - shrank: fewer observations, or an earlier last observation, than in the baseline;
  - is stale: its last observation is older than 2.5 normal release gaps plus 45 days (the gap is the series' own median spacing).
Series that legitimately behave so are listed in config/freshness.toml with the reason (keys are 'source|series' patterns, `*` allowed). Run only on days the inputs were rebuilt. A day with problems leaves the baseline as it was, so a problem is not forgotten after one email.
"""
import fnmatch
import tomllib
from pathlib import Path

import pandas as pd

TABLE = 'freshness_baseline'
CONFIG = Path(__file__).resolve().parents[1] / 'config' / 'freshness.toml'
SLACK_DAYS, GAPS = 45, 2.5


def ignored():
    return list(tomllib.loads(CONFIG.read_text()).get('ignore', {})) if CONFIG.exists() else []


def summary(con, asof):
    """DataFrame(source, series, n, first, last, gap): one row per series pulled on `asof`; gap = median spacing in days."""
    df = con.execute('SELECT source, series, date FROM raw_pulls WHERE as_of = ? ORDER BY source, series, date', [str(asof)]).fetchdf()
    df['date'] = pd.to_datetime(df['date'])
    rows = []
    for (src, ser), g in df.groupby(['source', 'series']):
        d = g['date'].sort_values()
        rows.append((src, ser, len(d), d.iloc[0], d.iloc[-1], float(d.diff().dt.days.median()) if len(d) > 1 else float('nan')))
    return pd.DataFrame(rows, columns=['source', 'series', 'n', 'first', 'last', 'gap'])


def baseline(con):
    """DataFrame(source, series, n, last) saved by the last passing check, or None."""
    try:
        return con.execute(f'SELECT source, series, n, "last" FROM {TABLE}').fetchdf().assign(last=lambda d: pd.to_datetime(d['last']))
    except Exception:
        return None


def save_baseline(con, today):
    con.register('_b', today[['source', 'series', 'n', 'last']])
    con.execute(f'CREATE OR REPLACE TABLE {TABLE} AS SELECT * FROM _b')
    con.unregister('_b')


def findings(con, asof, today=None):
    """[(source, series, kind, detail)] for every flagged series, before the ignore list."""
    today = summary(con, asof) if today is None else today
    base = baseline(con)
    out = []
    if base is not None:
        prev = base.set_index(['source', 'series'])
        t = today.set_index(['source', 'series'])
        now_series = set(today['series'])
        for key in prev.index.difference(t.index):
            if key[1] not in now_series:
                out.append((*key, 'disappeared', 'in the last passing check, not today'))
        for key in prev.index.intersection(t.index):
            p, c = prev.loc[key], t.loc[key]
            if c['n'] < p['n']:
                out.append((*key, 'shrank', f'{int(p["n"])} -> {int(c["n"])} observations since the last passing check'))
            elif c['last'] < p['last']:
                out.append((*key, 'shrank', f'last observation {p["last"].date()} -> {c["last"].date()} since the last passing check'))
    now = pd.Timestamp(asof)
    for r in today.itertuples():
        if pd.notna(r.gap):
            age = (now - r.last).days
            if age > GAPS * r.gap + SLACK_DAYS:
                out.append((r.source, r.series, 'stale', f'last observation {r.last.date()} is {age} days old (normal gap {r.gap:.0f} days)'))
    return out


def check(con, asof):
    """Problem strings for the daily run (empty = fine); a passing check becomes the new baseline."""
    skip = ignored()
    today = summary(con, asof)
    problems = [f'input {k}: {s}|{x} {d}' for s, x, k, d in findings(con, asof, today) if not any(fnmatch.fnmatchcase(f'{s}|{x}', pat) for pat in skip)]
    if not problems:
        save_baseline(con, today)
    return problems
