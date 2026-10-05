"""Daily run: workbook (benchmark) -> published GDPNow path -> public data -> L3 estimate and nowcast ->
L1 integrity check -> record -> housekeeping -> sanity checks. Each step is an existing stage script, so a failed
day can be re-run stage by stage. Idempotent: finished steps are skipped unless --force.

Exit status: 0 = fine, 3 = results written but HELD by a sanity check (the page should not be updated), other =
failure. docs/data/status.json is always rewritten with the outcome.

Usage: python scripts/daily.py [--asof YYYY-MM-DD] [--force] [--max-jump 1.0]
"""
import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gdpnow import public_data, store

KEEP_RUNS = 5          # recent L3 runs whose estimates stay in the store (older ones are dropped)
KEEP_PULL_DAYS = 7     # archived raw pulls kept for this many days


def sh(*args):
    print('\n$', ' '.join(args), flush=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts' / args[0]), *args[1:]], check=True, cwd=ROOT)


def last_price_month(asof):
    con = store.connect()
    try:
        s = public_data.fred(con, 'CPIAUCSL', asof)
    finally:
        con.close()
    return s.index.max().strftime('%Y-%m')


def housekeeping(asof):
    con = store.connect()
    try:
        runs = [r[0] for r in con.execute("SELECT run_id FROM runs WHERE run_id LIKE 'L3_%' ORDER BY run_id").fetchall()]
        old = runs[:-KEEP_RUNS]
        for t in ('est_frames', 'est_params', 'est_series', 'est_provenance', 'provenance', 'nowcast_components',
                  'nowcast_aggregates', 'nowcast_intermediates', 'verification', 'runs'):
            if old and store.table_exists(con, t):
                try:
                    con.execute(f"DELETE FROM {t} WHERE run_id IN ({','.join('?' * len(old))})", old)
                except Exception:          # table without a run_id column
                    pass
        cutoff = (pd.Timestamp(asof) - pd.Timedelta(days=KEEP_PULL_DAYS)).date().isoformat()
        if store.table_exists(con, 'raw_pulls'):
            con.execute('DELETE FROM raw_pulls WHERE as_of < ?', [cutoff])
        print(f'housekeeping: dropped {len(old)} old runs, archived pulls older than {cutoff}')
    finally:
        con.close()


def sanity(asof, max_jump):
    """Checks on the record just written; returns a list of problems (empty = fine)."""
    hist = pd.read_csv(ROOT / 'docs' / 'data' / 'history.csv').sort_values('asof')
    problems, row = [], hist[hist['asof'] == asof].iloc[0]
    prev = hist[(hist['asof'] < asof) & (hist['quarter'] == row['quarter'])]
    if len(prev) and abs(row.nowcast - prev.nowcast.iloc[-1]) > max_jump:
        problems.append(f"nowcast moved {row.nowcast - prev.nowcast.iloc[-1]:+.2f} pp since {prev['asof'].iloc[-1]} (> {max_jump})")
    if pd.notna(row.l1_minus_gdpnow) and abs(row.l1_minus_gdpnow) > 1e-3:
        problems.append(f'integrity check: our code on GDPNow inputs differs from GDPNow by {row.l1_minus_gdpnow:+.4f} pp')
    if not (-15 < row.nowcast < 20):
        problems.append(f'nowcast {row.nowcast:.2f} outside plausible range')
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--asof', default=dt.date.today().isoformat())
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--max-jump', type=float, default=1.0, help='largest day-to-day nowcast change (pp) published without review')
    a = ap.parse_args()
    asof, vint = a.asof, a.asof.replace('-', '')
    fl = ['--force'] if a.force else []
    status = ROOT / 'docs' / 'data' / 'status.json'
    status.parent.mkdir(parents=True, exist_ok=True)

    def write(ok, message, **extra):
        status.write_text(json.dumps({'asof': asof, 'ok': ok, 'message': message,
                                      'finished': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), **extra}, indent=1))
    try:
        sh('01_ingest_workbook.py', '--date', vint, *fl)
        sh('08_published.py', '--date', vint, *fl)
        lpm = last_price_month(asof)
        print(f'last price month as of {asof}: {lpm}')
        sh('02_build_public.py', '--asof', asof, '--last-price-month', lpm, '--ism', 'public', *fl)
        sh('04_estimate.py', '--level', 'L3', '--asof', asof, *fl)
        sh('05_nowcast.py', '--level', 'L3', '--asof', asof, *fl)
        sh('05_nowcast.py', '--level', 'L1', '--vintage', vint, *fl)
        sh('09_record.py', '--asof', asof, '--vintage', vint, *fl)
        sh('10_decompose.py', '--asof', asof, *fl)
        sh('11_release_effects.py', '--asof', asof, '--last-price-month', lpm, *fl)
        housekeeping(asof)
        problems = sanity(asof, a.max_jump)
    except Exception as e:                       # includes failed stages (CalledProcessError)
        write(False, f'run failed: {e}')
        raise
    if problems:
        write(False, 'held for review: ' + '; '.join(problems), held=True)
        print('HELD:', problems)
        sys.exit(3)
    write(True, 'ok')
    print(f'\ndaily run {asof}: ok')


if __name__ == '__main__':
    main()
