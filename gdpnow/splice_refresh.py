"""Refresh the borrowed GDPNow history after the monthly splice check found that the workbook revised it.

Replaces the stored borrowed growth (hist_growth, source 'workbook:*') by the growth in the newest workbook vintage, for the dates
the workbook still carries and only where it differs by more than the check's tolerance. Series or dates that vanished from the
workbook are left alone and reported. Every replaced value is logged in hist_growth_refresh_log (old and new value, vintage, time),
which is the audit trail and the way back; the vintage archive also records the change on the next daily run (source 'workbook_prefix').
Dry run by default: apply=False changes nothing.
"""
import datetime as dt

import pandas as pd

from . import splice_check as SC

LOG = 'hist_growth_refresh_log'


def plan(con, vintage):
    """DataFrame (name, date, old_growth, new_growth, old_source) of the stored borrowed values the newest workbook revised, and a list of notes."""
    st = con.execute("SELECT name, date, growth, source FROM hist_growth WHERE source LIKE 'workbook%' ORDER BY name, date").fetchdf()
    st['date'] = pd.to_datetime(st['date'])
    out, notes = [], []
    for name, g in st.groupby('name'):
        stored = g.set_index('date')['growth']
        fresh = SC._fresh_growth(con, vintage, name, stored)
        if fresh is None:
            notes.append(f'{name}: not in the newest workbook, left unchanged')
            continue
        fg = fresh[2].reindex(stored.index)
        gone = fg.isna()
        if gone.any():
            notes.append(f'{name}: {int(gone.sum())} dates no longer in the workbook, left unchanged')
        chg = ((fg - stored).abs() > SC.TOL) & ~gone
        if chg.any():
            src = g.set_index('date')['source']
            out.append(pd.DataFrame({'name': name, 'date': stored.index[chg], 'old_growth': stored[chg].to_numpy(),
                                     'new_growth': fg[chg].to_numpy(), 'old_source': src[chg].to_numpy()}))
    return (pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=['name', 'date', 'old_growth', 'new_growth', 'old_source'])), notes


def apply(con, p, vintage, asof):
    """Write the plan `p`: log the old values, then replace them. Returns the number of values replaced."""
    if p.empty:
        return 0
    now = dt.datetime.now()
    lg = p.copy()
    lg['vintage'], lg['refreshed_at'] = vintage, now
    con.register('_l', lg)
    con.execute(f'CREATE TABLE IF NOT EXISTS {LOG} AS SELECT * FROM _l LIMIT 0')
    con.execute(f'INSERT INTO {LOG} SELECT * FROM _l')
    con.unregister('_l')
    u = p[['name', 'date', 'new_growth']].copy()
    con.register('_u', u)
    con.execute(f"""UPDATE hist_growth SET growth = u.new_growth, source = 'workbook:{vintage}', as_of = '{asof}'
                    FROM _u u WHERE hist_growth.name = u.name AND hist_growth.date = u.date AND hist_growth.source LIKE 'workbook%'""")
    con.unregister('_u')
    return len(p)


def report(p, notes, vintage, applied):
    head = f'**Splice history refresh against workbook vintage {vintage}: {"APPLIED" if applied else "DRY RUN (nothing changed)"}**'
    if p.empty:
        return '\n'.join([head, '', 'No stored borrowed value differs from the newest workbook.'] + [f'- {n}' for n in notes])
    s = p.assign(d=(p.new_growth - p.old_growth).abs()).groupby('name').agg(
        n=('date', 'size'), first_d=('date', 'min'), last_d=('date', 'max'), max_abs_change=('d', 'max'))
    out = [head, '', f'{len(p)} values in {len(s)} series {"replaced" if applied else "would be replaced"}:', '',
           '| series | values | first | last | max abs change in growth |', '|---|---|---|---|---|']
    out += [f'| {n} | {r.n} | {r.first_d:%Y-%m} | {r.last_d:%Y-%m} | {r.max_abs_change:.3g} |' for n, r in s.iterrows()]
    out += [f'- {n}' for n in notes]
    if applied:
        out += ['', f'Old values are in table {LOG}. The next daily run archives the change (source workbook_prefix) and its nowcast shows the effect.']
    else:
        out += ['', 'To apply: dispatch `refresh-history` again with apply = true.']
    return '\n'.join(out)
