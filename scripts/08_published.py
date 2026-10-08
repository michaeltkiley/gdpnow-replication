"""Stage 08: load GDPNow's published nowcast path for the quarter in the dated workbook into DuckDB
(table gdpnow_published). Display/comparison only; never model input. History for earlier quarters accumulates
because each run adds its quarter and keeps the others.

Usage: python scripts/08_published.py [--date YYYYMMDD] [--force]
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import clock, published, store
from gdpnow.config import RAW, WORKBOOK_NAME


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--date', default=clock.today().strftime('%Y%m%d'), help='workbook vintage YYYYMMDD')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    path = RAW / f'{a.date}_{WORKBOOK_NAME}'
    if not path.exists():
        sys.exit(f'{path.name} not found: run 01_ingest_workbook.py --date {a.date} first')
    df = published.read_published(path)
    q = df.quarter.iloc[0]
    con = store.connect()
    if store.table_exists(con, 'gdpnow_published') and not a.force:
        last = con.execute('SELECT max(date) FROM gdpnow_published WHERE quarter = ?', [q]).fetchone()[0]
        if last is not None and str(last) >= df.date.max():
            print(f'{q}: published path through {last} already stored; use --force to reload')
            return
    df['vintage'] = a.date
    store.replace_rows(con, 'gdpnow_published', df[['quarter', 'date', 'kind', 'key', 'value', 'vintage']], {'quarter': q})
    g = df[(df.kind == 'growth') & (df.key == 'GDP')].sort_values('date')
    print(f'{q}: {g.date.nunique()} GDPNow updates {g.date.min()} .. {g.date.max()}; latest GDP nowcast {g.value.iloc[-1]:.3f}')


if __name__ == '__main__':
    main()
