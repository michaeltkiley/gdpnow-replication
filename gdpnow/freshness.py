"""Input freshness check: flags series the model reads that went missing, got shorter, or stopped advancing.

Compares the day's pulls in `raw_pulls` with baselines: the summary (observation count, last date) of each series as of the last run
that passed, kept per *regime* in table `freshness_baseline`. The regime is the day's gate states (gdpnow/gates.py: inputs read only in
part of the release cycle) and each baseline also carries the code hash of its run. What a finding is depends on what can explain it:

  hold (the day's number rests on a bad input)
    - disappeared: in a baseline from the same regime and the same code, absent today under every source. Nothing in the calendar or the
      code explains the absence, so an input the model should read went missing.
    - shrank: its last observation is earlier than in the last baseline, or it lost more than REVISION_OBS observations.
    - stale: its last observation is older than 2.5 normal release gaps plus 45 days (the gap is the series' own median spacing).
  note (published with a warning in status.json; nothing is held)
    - a baseline from another regime or other code has series not read today: the gate closed or the code changed, so the baseline is
      relearned (the one thing this cannot catch is a code change that drops a read by mistake; tests and review cover that);
    - a series lost REVISION_OBS observations or fewer, its last observation unchanged (a source revision).

Why: a check that infers the expected inputs from yesterday's pulls fails on every legitimate change in what is pulled (a release
calendar, a relabel, a revision, a rerun of another date) and is repaired by listing exceptions after the fact. Here the expectation
comes from the code that decides what to read, so those cases cannot fail the run, and only a loss nothing explains does.

A day with holds leaves the baselines as they were, so a problem is not forgotten after one email. Series that legitimately end are
listed in config/freshness.toml with the reason (keys are 'source|series' patterns, `*` allowed).
"""
import fnmatch
import tomllib
from pathlib import Path

import pandas as pd

TABLE = 'freshness_baseline'
CONFIG = Path(__file__).resolve().parents[1] / 'config' / 'freshness.toml'
SLACK_DAYS, GAPS = 45, 2.5
REVISION_OBS = 2


def line_key(source, series):
    """Identity of a series across runs. A BEA bulk series is 'dataset:table:freq|line|label' and the label is borrowed from a concept stem
    (bea_bulk.register), so an unrelated series added to the register can rename lines without changing their data: BEA is matched on
    'dataset:table:freq|line' alone."""
    return series.rsplit('|', 1)[0] if source == 'bea' and series.count('|') >= 2 else series


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
    """DataFrame(regime, code, saved, source, series, n, last) saved by passing runs, or None. A table from before regimes existed
    (source, series, n, last) is read with regime and code unknown: it serves the shrink test but matches no regime."""
    for cols in ('regime, code, saved, source, series, n, "last"', "NULL AS regime, NULL AS code, '' AS saved, source, series, n, \"last\""):
        try:
            return con.execute(f'SELECT {cols} FROM {TABLE}').fetchdf().assign(last=lambda d: pd.to_datetime(d['last']))
        except Exception:
            continue
    return None


def save_baseline(con, today, regime, code, asof):
    """Replace the baseline of this regime with today's summary; baselines of other regimes stay (legacy rows are dropped)."""
    old = baseline(con)
    keep = old[old['regime'].notna() & (old['regime'] != regime)] if old is not None else None
    new = today[['source', 'series', 'n', 'last']].assign(regime=regime, code=code, saved=str(asof))
    cols = ['regime', 'code', 'saved', 'source', 'series', 'n', 'last']
    out = new[cols] if keep is None or keep.empty else pd.concat([keep[cols], new[cols]], ignore_index=True)
    con.register('_b', out)
    con.execute(f'CREATE OR REPLACE TABLE {TABLE} AS SELECT * FROM _b')
    con.unregister('_b')


def findings(con, asof, today=None, regime='', code=None):
    """[(source, series, kind, detail, severity)] for every flagged series, before the ignore list; severity is 'hold' or 'note'."""
    today = summary(con, asof) if today is None else today
    base = baseline(con)
    out = []
    if base is not None and len(base):
        # compare on line_key; names are reported as they were in the baseline (disappeared) or today (shrank)
        base = base.assign(k=[line_key(a, b) for a, b in zip(base['source'], base['series'])])
        same = base[(base['regime'] == regime) & (base['code'] == code)] if code is not None else base.iloc[0:0]
        latest = base.sort_values('saved').drop_duplicates(['source', 'k'], keep='last').set_index(['source', 'k'])
        prev = same.drop_duplicates(['source', 'k']).set_index(['source', 'k'])
        t = today.assign(k=[line_key(a, b) for a, b in zip(today['source'], today['series'])]).drop_duplicates(['source', 'k']).set_index(['source', 'k'])
        now_series = set(t.index.get_level_values('k'))
        for key in prev.index.difference(t.index):
            if key[1] not in now_series:
                out.append((key[0], prev.loc[key, 'series'], 'disappeared', 'in the last passing run with the same gates and code, not today', 'hold'))
        unread = [key for key in latest.index.difference(t.index).difference(prev.index) if key[1] not in now_series]
        if unread:
            out.append(('*', '*', 'relearned', f'{len(unread)} series read in an earlier run under other gates or code are not read today '
                        f'(gates today: {regime or "none"}); baseline relearned', 'note'))
        for key in latest.index.intersection(t.index):
            p, c = latest.loc[key], t.loc[key]
            if c['last'] < p['last']:
                out.append((key[0], c['series'], 'shrank', f'last observation {p["last"].date()} -> {c["last"].date()} since the last passing check', 'hold'))
            elif c['n'] < p['n']:
                lost = int(p['n'] - c['n'])
                out.append((key[0], c['series'], 'shrank' if lost > REVISION_OBS else 'revised',
                            f'{int(p["n"])} -> {int(c["n"])} observations since the last passing check', 'hold' if lost > REVISION_OBS else 'note'))
    now = pd.Timestamp(asof)
    for r in today.itertuples():
        if pd.notna(r.gap):
            age = (now - r.last).days
            if age > GAPS * r.gap + SLACK_DAYS:
                out.append((r.source, r.series, 'stale', f'last observation {r.last.date()} is {age} days old (normal gap {r.gap:.0f} days)', 'hold'))
    return out


def check(con, asof, code=None, regime=None):
    """(holds, notes): problem strings for the daily run (no holds = fine) and warnings to publish with the day. A day without holds
    becomes the baseline of its regime. `code` is the run's code hash; `regime` defaults to the day's recorded gates."""
    from . import gates
    regime = gates.regime(con, asof) if regime is None else regime
    skip = ignored()
    today = summary(con, asof)
    holds, notes = [], []
    for s, x, k, d, sev in findings(con, asof, today, regime, code):
        if any(fnmatch.fnmatchcase(f'{s}|{x}', pat) for pat in skip):
            continue
        (holds if sev == 'hold' else notes).append(f'input {k}: {s}|{x} {d}' if s != '*' else f'input {k}: {d}')
    if not holds:
        save_baseline(con, today, regime, code, asof)
    return holds, notes
