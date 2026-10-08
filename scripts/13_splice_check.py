"""Monthly splice check (see gdpnow/splice_check.py): compare the borrowed GDPNow history stored in the state database with the newest
workbook. Writes the report (markdown) to --out and exits 0 if passed, 3 if failed. Run after 01_ingest_workbook.py.
Usage: python scripts/13_splice_check.py [--out splice_check.md]"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import clock, splice_check, store


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default='splice_check.md')
    a = ap.parse_args()
    con = store.connect()
    vint = store.latest_vintage(con)
    ok, df, notes = splice_check.run(con, vint)
    text = splice_check.report(ok, df, notes, vint, clock.today().isoformat())
    Path(a.out).write_text(text + '\n')
    print(text)
    print('RESULT', 'passed' if ok else 'failed')
    sys.exit(0 if ok else 3)


if __name__ == '__main__':
    main()
