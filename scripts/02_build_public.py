"""Stage 02: pull public data (FRED/ALFRED as of --asof, BEA, Census, BLS, Treasury) and build the L3 data
bundle: monthly/quarterly panels and constructed series (splices, deflations, X-13 adjustments, aggregates).

Raw FRED/BEA/BLS/Treasury pulls are archived in DuckDB (table raw_pulls) with their retrieval date; the built
bundle is saved to data/<asof>_public_inputs.pkl. Skips if the bundle exists; --force rebuilds.

Usage: python scripts/02_build_public.py [--asof YYYY-MM-DD] [--last-price-month YYYY-MM] [--ism public|seeded] [--force]
"""
import argparse
import datetime as dt
import pickle
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import inputs_public, store
from gdpnow.config import DATA


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--asof', default=dt.date.today().isoformat())
    ap.add_argument('--last-price-month', default='2026-08',
                    help='last month with published CPI/PPI/trade prices as of --asof')
    ap.add_argument('--ism', default='public', choices=['public', 'seeded'],
                    help='ISM indexes: regional-survey stand-ins (core, strict public) or workbook-seeded history (sensitivity)')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    out = DATA / f'{a.asof.replace("-", "")}_public_inputs.pkl'
    if out.exists() and not a.force:
        print(f'{out.name} exists; use --force to rebuild')
        return
    con = store.connect()
    last = pd.Timestamp(a.last_price_month) + pd.offsets.MonthEnd(0)
    bundle = inputs_public.build(con, a.asof, last, ism=a.ism)
    with open(out, 'wb') as f:
        pickle.dump({'bundle': bundle, 'asof': a.asof, 'last_price_month': str(last.date())}, f)
    inp = bundle[0]
    print(f'built {out.name}: T={inp.T.date()} T1={inp.T1.date()} monthly series={inp.growth.shape[1]} '
          f'factor panel={bundle[1].shape}')


if __name__ == '__main__':
    main()
