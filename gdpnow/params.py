"""Store and reload estimated Inputs fields (stage 04 -> stage 05) in DuckDB, in long form."""
import dataclasses

import numpy as np
import pandas as pd

from . import store

NESTED = ['faar', 'bridge', 'util_travel', 'farm_other']          # {key: {term: value}}
FLAT = ['bvar', 'prices_T1']                                       # {key: value}
FRAMES = ['monthly_prices', 'cons_growth', 'cipi_paths', 'iva_paths', 'inv_deflators']   # DataFrame (date x col)


def save(con, run_id, est, fields):
    rows, frames = [], []
    for f in fields:
        v = getattr(est, f)
        if f in NESTED:
            rows += [(f, k, t, float(x)) for k, d in v.items() for t, x in d.items()]
        elif f in FLAT:
            rows += [(f, k, '', float(x)) for k, x in v.items()]
        elif f == 'blend':
            rows += [(f, k, part, float(x)) for k, w in v.items() for part, x in zip(('monthly', 'bvar'), w)]
        elif f == 'factor':
            frames.append(pd.DataFrame({'field': f, 'col': 'factor', 'date': v.index, 'value': v.to_numpy()}))
        elif f in FRAMES:
            long = v.stack().reset_index()
            long.columns = ['date', 'col', 'value']
            frames.append(long.assign(field=f)[['field', 'col', 'date', 'value']])
    store.replace_rows(con, 'est_params', pd.DataFrame(rows, columns=['field', 'k1', 'k2', 'value']).assign(run_id=run_id),
                       {'run_id': run_id})
    store.replace_rows(con, 'est_frames', pd.concat(frames).assign(run_id=run_id), {'run_id': run_id})
    prov = pd.DataFrame(list(est.provenance.items()), columns=['field', 'source']).assign(run_id=run_id)
    store.replace_rows(con, 'est_provenance', prov, {'run_id': run_id})


def overlay(con, run_id, inp):
    """Return inp with every stored estimate from run_id substituted and provenance updated."""
    p = store.query(con, 'SELECT field, k1, k2, value FROM est_params WHERE run_id = ?', (run_id,))
    fr = store.query(con, 'SELECT field, col, date, value FROM est_frames WHERE run_id = ?', (run_id,))
    prov = dict(store.query(con, 'SELECT field, source FROM est_provenance WHERE run_id = ?', (run_id,)).values)
    upd = {}
    for f, g in p.groupby('field'):
        if f in NESTED:
            upd[f] = {k: dict(zip(h.k2, h.value)) for k, h in g.groupby('k1')}
        elif f in FLAT:
            upd[f] = dict(zip(g.k1, g.value))
        elif f == 'blend':
            upd[f] = {k: (h.set_index('k2').value['monthly'], h.set_index('k2').value['bvar']) for k, h in g.groupby('k1')}
    for f, g in fr.groupby('field'):
        wide = g.pivot(index='date', columns='col', values='value')
        wide.index = pd.to_datetime(wide.index)
        upd[f] = wide['factor'] if f == 'factor' else wide.sort_index()
    return dataclasses.replace(inp, **upd, provenance={**inp.provenance, **prov})
