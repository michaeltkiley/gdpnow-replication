"""Store and reload estimated parameters (stage 04 -> stage 05) in DuckDB."""
import dataclasses

import pandas as pd

from . import store


def save(con, run_id, est, replaced):
    """Persist the Inputs fields our code produced."""
    rows, series = [], []
    for k, v in est.faar.items():
        rows += [('faar', k, t, x) for t, x in v.items()]
    for k, v in est.bridge.items():
        rows += [('bridge', k, t, x) for t, x in v.items()]
    for k, (a, b) in est.blend.items():
        rows += [('blend', k, 'monthly', a), ('blend', k, 'bvar', b)]
    rows += [('bvar', k, '', x) for k, x in est.bvar.items()]
    rows += [('prices_T1', k, '', x) for k, x in est.prices_T1.items()]
    series += [('factor', 'factor', d, x) for d, x in est.factor.items()]
    df = pd.DataFrame(rows, columns=['field', 'k1', 'k2', 'value']).assign(run_id=run_id)
    ds = pd.DataFrame(series, columns=['field', 'k1', 'date', 'value']).assign(run_id=run_id)
    prov = pd.DataFrame([(f, s) for f, s in est.provenance.items()], columns=['field', 'source']).assign(run_id=run_id)
    store.replace_rows(con, 'est_params', df, {'run_id': run_id})
    store.replace_rows(con, 'est_series', ds, {'run_id': run_id})
    store.replace_rows(con, 'est_provenance', prov, {'run_id': run_id})


def overlay(con, run_id, inp):
    """Return inp with every stored estimate from run_id substituted, and provenance updated."""
    p = store.query(con, 'SELECT field, k1, k2, value FROM est_params WHERE run_id = ?', (run_id,))
    s = store.query(con, 'SELECT field, k1, date, value FROM est_series WHERE run_id = ?', (run_id,))
    prov = dict(store.query(con, 'SELECT field, source FROM est_provenance WHERE run_id = ?', (run_id,)).values)
    nested = lambda f: {k: dict(zip(g.k2, g.value)) for k, g in p[p.field == f].groupby('k1')}
    flat = lambda f: dict(zip(p[p.field == f].k1, p[p.field == f].value))
    blend = {k: (d['monthly'], d['bvar']) for k, d in nested('blend').items()}
    fac = s[s.field == 'factor']
    return dataclasses.replace(
        inp, faar=nested('faar'), bridge=nested('bridge'), blend=blend, bvar=flat('bvar'),
        prices_T1=flat('prices_T1'), factor=pd.Series(fac.value.to_numpy(), index=pd.to_datetime(fac.date)),
        provenance=prov)
