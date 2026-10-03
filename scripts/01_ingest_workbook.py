"""Stage 01: download (or copy) the GDPNow workbook as a dated file and load it into DuckDB.

The workbook is the L1 test fixture and the L2/L3 benchmark; nothing in it feeds the production path.

Usage: python scripts/01_ingest_workbook.py [--date YYYYMMDD] [--file PATH] [--force]
"""
import argparse
import datetime as dt
import hashlib
import shutil
import sys
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import store, workbook
from gdpnow.config import RAW, WORKBOOK_NAME, WORKBOOK_URL


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--date', default=dt.date.today().strftime('%Y%m%d'), help='vintage date YYYYMMDD')
    ap.add_argument('--file', help='use this local copy instead of downloading')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    dest = RAW / f'{a.date}_{WORKBOOK_NAME}'
    if not dest.exists() or a.force:
        if a.file:
            shutil.copyfile(a.file, dest)
            source = f'file:{Path(a.file).resolve()}'
        else:
            req = urllib.request.Request(WORKBOOK_URL, headers={'User-Agent': 'Mozilla/5.0 (gdpnow-replication)'})
            with urllib.request.urlopen(req) as r, open(dest, 'wb') as f:
                shutil.copyfileobj(r, f)
            source = WORKBOOK_URL
    else:
        source = 'existing'
    digest = sha256(dest)

    con = store.connect()
    if store.table_exists(con, 'wb_vintage') and not a.force:
        row = con.execute('SELECT sha256 FROM wb_vintage WHERE vintage = ?', [a.date]).fetchone()
        if row and row[0] == digest:
            print(f'vintage {a.date} already loaded (sha256 {digest[:12]}); use --force to reload')
            return

    tables = workbook.read_workbook(dest)
    for name, df in tables.items():
        df.insert(0, 'vintage', a.date)
        store.replace_rows(con, name, df, {'vintage': a.date})
        print(f'{name:16s} {len(df):8d} rows')
    info = pd.DataFrame([{'vintage': a.date, 'file': str(dest), 'sha256': digest, 'source': source,
                          'loaded_at': dt.datetime.now()}])
    store.replace_rows(con, 'wb_vintage', info, {'vintage': a.date})
    print(f'vintage {a.date} loaded: {dest.name} sha256 {digest}')


if __name__ == '__main__':
    main()
