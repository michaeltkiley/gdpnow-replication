"""Narrow input freshness check: flags series the model reads that went missing, got shorter, or stopped advancing.

Compares the day's pulls in `raw_pulls` with the previous run date's (the database keeps seven days). A series is flagged when it
  - disappeared: pulled on the previous run date, absent today under every source (a series that moved to another source is not);
  - shrank: fewer observations, or an earlier last observation, than on the previous run date;
  - is stale: its last observation is older than 2.5 normal release gaps plus 45 days (the gap is the series' own median spacing).
Series that legitimately behave so are listed in config/freshness.toml with the reason (keys are 'source|series' patterns, `*` allowed). Run only on days the inputs were rebuilt.
"""
import fnmatch
import tomllib
from pathlib import Path

import pandas as pd

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


def findings(con, asof):
    """[(source, series, kind, detail)] for every flagged series, before the ignore list."""
    today = summary(con, asof)
    prev_asof = con.execute('SELECT max(as_of) FROM raw_pulls WHERE as_of < ?', [str(asof)]).fetchone()[0]
    out = []
    if prev_asof is not None:
        prev = summary(con, prev_asof).set_index(['source', 'series'])
        t = today.set_index(['source', 'series'])
        now_series = set(today['series'])
        for key in prev.index.difference(t.index):
            if key[1] not in now_series:
                out.append((*key, 'disappeared', f'pulled {prev_asof}, not {asof}'))
        for key in prev.index.intersection(t.index):
            p, c = prev.loc[key], t.loc[key]
            if c['n'] < p['n']:
                out.append((*key, 'shrank', f'{int(p["n"])} -> {int(c["n"])} observations since {prev_asof}'))
            elif c['last'] < p['last']:
                out.append((*key, 'shrank', f'last observation {p["last"].date()} -> {c["last"].date()} since {prev_asof}'))
    now = pd.Timestamp(asof)
    for r in today.itertuples():
        if pd.notna(r.gap):
            age = (now - r.last).days
            if age > GAPS * r.gap + SLACK_DAYS:
                out.append((r.source, r.series, 'stale', f'last observation {r.last.date()} is {age} days old (normal gap {r.gap:.0f} days)'))
    return out


def check(con, asof):
    """Problem strings for the daily run (empty = fine)."""
    skip = ignored()
    return [f'input {k}: {s}|{x} {d}' for s, x, k, d in findings(con, asof) if not any(fnmatch.fnmatchcase(f'{s}|{x}', pat) for pat in skip)]
