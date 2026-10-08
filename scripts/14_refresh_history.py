"""Refresh the borrowed GDPNow history from the newest workbook (see gdpnow/splice_refresh.py). Dry run unless --apply.
Run after 01_ingest_workbook.py. Usage: python scripts/14_refresh_history.py [--apply] [--out refresh.md]"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import clock, splice_check, splice_refresh, store


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--out', default='refresh.md')
    a = ap.parse_args()
    con = store.connect()
    vint = store.latest_vintage(con)
    p, notes = splice_refresh.plan(con, vint)
    n = splice_refresh.apply(con, p, vint, clock.today().isoformat()) if a.apply else 0
    text = splice_refresh.report(p, notes, vint, a.apply)
    if a.apply:                                   # verify: the check must now pass for every series the workbook still carries
        ok, df, _ = splice_check.run(con, vint)
        left = df[df.status.isin(['CHANGED'])] if len(df) else df
        text += f'\n\nCheck after refresh: {len(left)} series still differ.'
        if len(left):
            sys.exit(Path(a.out).write_text(text + '\n') and 3)
    Path(a.out).write_text(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
