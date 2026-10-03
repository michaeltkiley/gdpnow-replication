"""Stage 05: assemble the GDPNow nowcast from an Inputs bundle and store results in DuckDB.

L1 builds the bundle from the workbook (code-correctness test). L2/L3 bundles come from stage 04.

Usage: python scripts/05_nowcast.py --level L1 [--vintage YYYYMMDD] [--force]
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import inputs, nowcast, store


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--level', required=True, choices=['L1'])
    ap.add_argument('--vintage', help='workbook vintage (default: latest loaded)')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()

    con = store.connect()
    vintage = a.vintage or store.latest_vintage(con)
    run_id = f'{a.level}_{vintage}'
    if store.table_exists(con, 'runs') and not a.force:
        if con.execute('SELECT count(*) FROM runs WHERE run_id = ?', [run_id]).fetchone()[0]:
            print(f'run {run_id} already exists; use --force to recompute')
            return

    inp = inputs.from_workbook(con, vintage)
    comps, agg, inter = nowcast.run(inp)

    tag = lambda df: df.assign(run_id=run_id)[['run_id'] + list(df.columns)]
    store.replace_rows(con, 'nowcast_components', tag(comps), {'run_id': run_id})
    store.replace_rows(con, 'nowcast_aggregates',
                       tag(pd.DataFrame([(k, float(v)) for k, v in agg.items()], columns=['key', 'value'])),
                       {'run_id': run_id})
    store.replace_rows(con, 'nowcast_intermediates', tag(inter), {'run_id': run_id})
    prov = [(field, reg, inp.source) for field, regs in inputs.FIELD_REGISTRY.items() for reg in regs]
    store.replace_rows(con, 'provenance', tag(pd.DataFrame(prov, columns=['field', 'registry_id', 'source'])),
                       {'run_id': run_id})
    store.replace_rows(con, 'runs', pd.DataFrame([{
        'run_id': run_id, 'level': a.level, 'vintage': vintage, 'T': inp.T.date(), 'T1': inp.T1.date(),
        'gdp': float(agg['GDP']), 'created_at': dt.datetime.now()}]), {'run_id': run_id})
    print(comps.to_string(index=False, float_format=lambda x: f'{x:10.4f}'))
    print(f"\n{run_id}: GDP nowcast for {inp.T1.date()} = {agg['GDP']:.6f}% SAAR")


if __name__ == '__main__':
    main()
