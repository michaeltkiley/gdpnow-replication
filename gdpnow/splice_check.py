"""Monthly splice check: has the GDPNow history that the replication borrows changed?

Every series spliced from the GDPNow workbook (hist_growth rows with source 'workbook:*', the public sources being too short) keeps
the workbook's growth rates for the stretch before its public data begin. The workbook is revised over time, so the borrowed stretch
may no longer match what the workbook now says. This module compares the stored borrowed growth with the growth of the same series in
the newest workbook vintage and reports, per series, whether it changed, where and by how much. Check only: nothing is refreshed.
"""
import numpy as np
import pandas as pd

from . import history as H
from . import store

SHEETS = ['MonthlyLevels', 'MonthlyPriceLevels', 'QtrlyGDPData']      # sheets the spliced series are read from
TOL = 1e-6                                                          # absolute growth difference that counts as a change


def _fresh_growth(con, vintage, name, stored):
    """Growth of workbook series `name` in `vintage` (log or difference, whichever reproduces the stored rows better), or None."""
    for sheet in SHEETS:
        f = store.series_frame(con, vintage, sheet)
        if name in f and f[name].notna().any():
            lev = f[name].dropna()
            cands = {'log': H.growth(lev, 'log'), 'diff': H.growth(lev, 'diff')} if (lev > 0).all() else {'diff': H.growth(lev, 'diff')}
            err = {k: (g.reindex(stored.index) - stored).abs().max() for k, g in cands.items()}
            err = {k: (v if pd.notna(v) else np.inf) for k, v in err.items()}
            kind = min(err, key=err.get)
            return sheet, kind, cands[kind].dropna()
    return None


def run(con, vintage=None):
    """Returns (ok, rows DataFrame, notes list). ok is False when any borrowed series changed or vanished, or the check could not run."""
    notes = []
    vintage = vintage or store.latest_vintage(con)
    try:
        st = con.execute("SELECT name, date, growth FROM hist_growth WHERE source LIKE 'workbook%' ORDER BY name, date").fetchdf()
    except Exception:
        return False, pd.DataFrame(), ['no borrowed history stored (table hist_growth missing): the check cannot run']
    if st.empty:
        return False, pd.DataFrame(), ['no borrowed history stored: the check cannot run']
    st['date'] = pd.to_datetime(st['date'])
    rows = []
    for name, g in st.groupby('name'):
        stored = g.set_index('date')['growth']
        fresh = _fresh_growth(con, vintage, name, stored)
        if fresh is None:
            rows.append({'series': name, 'months': len(stored), 'status': 'MISSING', 'n_changed': len(stored), 'max_abs_diff': np.nan,
                         'first_changed': stored.index.min(), 'last_changed': stored.index.max(), 'detail': 'not in the newest workbook'})
            continue
        sheet, kind, fg = fresh
        diff = (fg.reindex(stored.index) - stored)
        gone = diff.isna()
        bad = diff.abs() > TOL
        chg = bad | gone
        status = 'ok' if not chg.any() else ('DISAPPEARED' if gone.any() else 'CHANGED')
        rows.append({'series': name, 'months': len(stored), 'status': status, 'n_changed': int(chg.sum()),
                     'max_abs_diff': float(diff.abs().max()) if diff.notna().any() else np.nan,
                     'first_changed': stored.index[chg].min() if chg.any() else pd.NaT,
                     'last_changed': stored.index[chg].max() if chg.any() else pd.NaT,
                     'detail': f'{sheet}, {kind}' + (f', {int(gone.sum())} dates no longer in the workbook' if gone.any() else '')})
    df = pd.DataFrame(rows)
    try:
        q = con.execute("""SELECT name, corr, n_overlap, as_of FROM hist_splice_checks WHERE layer = 'live_vs_reference' AND NOT accepted
                           AND as_of = (SELECT max(as_of) FROM hist_splice_checks WHERE layer = 'live_vs_reference')""").fetchdf()
        for r in q.itertuples():
            notes.append(f'live-vs-workbook agreement below the gate for {r.name}: corr {r.corr:.3f} over {r.n_overlap} (as of {r.as_of})')
    except Exception:
        notes.append('no splice-check history to read the live-vs-workbook agreement from')
    ok = bool((df.status == 'ok').all()) and not any('below the gate' in n for n in notes)
    return ok, df, notes


def report(ok, df, notes, vintage, asof):
    """Markdown body of the monthly issue."""
    n = len(df)
    bad = df[df.status != 'ok'] if n else df
    out = [f'**Monthly splice check {asof}: {"PASSED" if ok else "FAILED"}** (workbook vintage {vintage}).', '']
    if not n:
        out += notes
        return '\n'.join(out)
    if ok:
        out.append(f'All {n} spliced series match the newest GDPNow workbook over their borrowed stretch ({int(df.months.sum())} series-months, tolerance {TOL:g} in growth). Nothing to do.')
    else:
        out.append(f'{len(bad)} of {n} spliced series differ from the newest workbook over the borrowed stretch:')
        out += ['', '| series | status | months changed / borrowed | max abs growth diff | first changed | last changed | detail |', '|---|---|---|---|---|---|---|']
        for r in bad.itertuples():
            f = lambda d: '' if pd.isna(d) else d.strftime('%Y-%m')
            out.append(f'| {r.series} | {r.status} | {r.n_changed} / {r.months} | {r.max_abs_diff:.2g} | {f(r.first_changed)} | {f(r.last_changed)} | {r.detail} |')
        out += ['', 'A change means the workbook revised its own history (new data) or redefined the series. The daily nowcast keeps using the stored history until it is refreshed.']
    for x in notes:
        out.append(f'- {x}')
    return '\n'.join(out)
