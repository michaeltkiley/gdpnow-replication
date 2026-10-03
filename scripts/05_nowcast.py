"""Stage 05: assemble the GDPNow nowcast from an Inputs bundle and store results in DuckDB.

L1 builds the bundle from the workbook (code-correctness test); L2/L3 overlay our stage-04 estimates.

Usage: python scripts/05_nowcast.py --level L1|L2 [--vintage YYYYMMDD] [--force]
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import inputs, nowcast, params, store


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--level', required=True, choices=['L1', 'L2', 'L3'])
    ap.add_argument('--vintage', help='workbook vintage (default: latest loaded)')
    ap.add_argument('--asof', help='L3 only: as-of date of the public data bundle')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()

    con = store.connect()
    vintage = a.vintage or store.latest_vintage(con)
    run_id = f'{a.level}_{a.asof.replace("-", "")}' if a.level == 'L3' else f'{a.level}_{vintage}'
    if store.table_exists(con, 'runs') and not a.force:
        if con.execute('SELECT count(*) FROM runs WHERE run_id = ?', [run_id]).fetchone()[0]:
            print(f'run {run_id} already exists; use --force to recompute')
            return

    if a.level == 'L3':     # public bundle + our stage-04 estimates; the workbook is never read
        import pickle
        from gdpnow.config import DATA
        base = pickle.load(open(DATA / f'{a.asof.replace("-", "")}_public_inputs.pkl', 'rb'))['bundle'][0]
        inp = params.overlay(con, run_id, base)
    else:
        inp = inputs.from_workbook(con, vintage)
        if a.level != 'L1':     # our estimates from stage 04 replace the workbook's
            inp = params.overlay(con, run_id, inp)
    comps, agg, inter = nowcast.run(inp)

    tag = lambda df: df.assign(run_id=run_id)[['run_id'] + list(df.columns)]
    store.replace_rows(con, 'nowcast_components', tag(comps), {'run_id': run_id})
    store.replace_rows(con, 'nowcast_aggregates',
                       tag(pd.DataFrame([(k, float(v)) for k, v in agg.items()], columns=['key', 'value'])),
                       {'run_id': run_id})
    store.replace_rows(con, 'nowcast_intermediates', tag(inter), {'run_id': run_id})
    prov = [(field, reg, inp.provenance[field]) for field, regs in inputs.FIELD_REGISTRY.items() for reg in regs]
    store.replace_rows(con, 'provenance', tag(pd.DataFrame(prov, columns=['field', 'registry_id', 'source'])),
                       {'run_id': run_id})
    store.replace_rows(con, 'runs', pd.DataFrame([{
        'run_id': run_id, 'level': a.level, 'vintage': vintage, 'T': inp.T.date(), 'T1': inp.T1.date(),
        'gdp': float(agg['GDP']), 'created_at': dt.datetime.now()}]), {'run_id': run_id})
    print(comps.to_string(index=False, float_format=lambda x: f'{x:10.4f}'))
    print(f"\n{run_id}: GDP nowcast for {inp.T1.date()} = {agg['GDP']:.6f}% SAAR")


if __name__ == '__main__':
    main()
