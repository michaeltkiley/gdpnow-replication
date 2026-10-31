"""Daily MIDAS tracker: nowcast of the current (and, after quarter-end, the next) quarter as of a date, appended to
docs/data/midas_daily.json.  Spec = the final model (per-capita, trade balance dropped).
Usage: python live.py                       # today (downloads today's ALFRED vintages)
       python live.py 2026-04-01 2026-10-31 # backfill weekdays (downloads missing vintages)  [--force]"""
import os, sys, json, datetime as dt
os.environ.setdefault('MIDAS_SPEC', 'nominal_notrade_pc')
import numpy as np, pandas as pd
import nowcast as N
from common import *
import fetch_vintages as F

ROOT = HERE.parent
OUTJ = ROOT / 'docs' / 'data' / 'midas_daily.json'


def gdpnow_lookup():
    """Atlanta Fed's published nowcast by (date, quarter): workbook archive (to its last update) + this repo's daily history."""
    rows = []
    a = OUT / 'gdpnow_archive.csv'
    if a.exists():
        x = pd.read_csv(a, parse_dates=['date']); x['q'] = x['q'].astype(str)
        rows.append(x[['date', 'q', 'gdpnow']])
    h = ROOT / 'docs' / 'data' / 'history.csv'
    repl = pd.DataFrame()
    if h.exists():
        x = pd.read_csv(h, usecols=['asof', 'quarter', 'nowcast', 'gdpnow_date', 'gdpnow'], parse_dates=['asof', 'gdpnow_date'])
        rows.append(x.rename(columns={'gdpnow_date': 'date', 'quarter': 'q'})[['date', 'q', 'gdpnow']])
        repl = x.rename(columns={'quarter': 'q'})[['asof', 'q', 'nowcast']]
    g = pd.concat(rows).dropna().drop_duplicates(['date', 'q'], keep='last').sort_values('date')
    return g, repl


def targets(d):
    g = N.gdp_growth(d)
    q = g.index[-1] + 1
    qs = [q]
    if pd.Timestamp(d) >= pd.Period(q + 1, 'Q').start_time:       # quarter q is over: also nowcast the next one
        qs.append(q + 1)
    return qs


def record(d, q, gnow, repl):
    r = N.nowcast_origin((q.year, q.quarter, None, d))
    s = gnow[(gnow['q'] == str(q)) & (gnow['date'] <= pd.Timestamp(d))]
    p = repl[(repl['q'] == str(q)) & (repl['asof'] <= pd.Timestamp(d))] if len(repl) else repl
    rd = lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 4)
    return {'q': str(q), 'full': rd(r['full']), 'trio': rd(r['trio']), 'ar2': rd(r['ar2']),
            'pop': rd(r['pop_assump']), 'ym_full': rd(r['ym_full']), 'ym_trio': rd(r['ym_trio']),
            'c': {k[2:]: rd(v) for k, v in r.items() if k.startswith('c_')},
            'ct': {k[3:]: rd(v) for k, v in r.items() if k.startswith('ct_')},
            'lags': r.get('lags'), 'n': int(r['n_full']), 'gdpnow': rd(s['gdpnow'].iloc[-1]) if len(s) else None,
            'repl': rd(p['nowcast'].iloc[-1]) if len(p) else None}


def main(dates, force=False):
    db = json.loads(OUTJ.read_text()) if OUTJ.exists() else {}
    todo = [d for d in dates if force or d.isoformat() not in db]
    if not todo:
        print('nothing to do'); return
    new = F.fetch_dates(todo, 16, refresh_current=(dt.date.today() in todo))
    print(len(todo), 'dates;', new, 'new vintage files', flush=True)
    gnow, repl = gdpnow_lookup()
    for d in todo:
        try:
            db[d.isoformat()] = [record(d, q, gnow, repl) for q in targets(d)]
        except Exception as e:                                   # e.g. a holiday with no new vintage -> reuse neighbour
            print('skip', d, repr(e)[:100])
    OUTJ.parent.mkdir(parents=True, exist_ok=True)
    OUTJ.write_text(json.dumps(dict(sorted(db.items())), separators=(',', ':')))
    print('wrote', OUTJ, len(db), 'dates')


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) == 2:
        a, b = (dt.date.fromisoformat(x) for x in args)
        dates = [a + dt.timedelta(i) for i in range((b - a).days + 1) if (a + dt.timedelta(i)).weekday() < 5]
    else:
        dates = [dt.date.today()]
    main(dates, force='--force' in sys.argv)
