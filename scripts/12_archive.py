"""Vintage archive step: record the day's raw input values (new and revised) in the archive directory (the `data` branch checkout
in the daily workflow). See gdpnow/vintage.py. Usage: python scripts/12_archive.py [--dir vintage]"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gdpnow import store, vintage


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dir', default='vintage')
    a = ap.parse_args()
    con = store.connect()
    if not store.table_exists(con, 'raw_pulls'):
        raise SystemExit('no raw_pulls table: nothing to archive (the daily run stores its pulls there)')
    for as_of, new, rev, rem in vintage.archive(con, a.dir):
        print(f'archive {as_of}: {new} new, {rev} revised, {rem} removed observations')


if __name__ == '__main__':
    main()
