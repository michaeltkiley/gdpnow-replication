"""Daily run: workbook (benchmark) -> published GDPNow path -> public data -> L3 estimate and nowcast ->
L1 integrity check -> record -> housekeeping -> sanity checks. Each step is an existing stage script, so a failed
day can be re-run stage by stage.

Change check: the public data is pulled afresh on every run and compared with the pulls behind the last recorded
run. If no public input changed (and the code, config and library versions did not, and the last run was clean),
estimation is skipped: the day's record repeats the last nowcast, flagged `unchanged`, and only the benchmark
(GDPNow's published numbers) is refreshed. Otherwise the full pipeline runs, also when the same date was already
recorded (a second run on a day with new releases replaces that day's record). --force skips the check and replays
the archived pulls of the date, as before.

Exit status: 0 = fine, 3 = results written but HELD by a sanity check (the page should not be updated), other =
failure. docs/data/status.json is always rewritten with the outcome.

Usage: python scripts/daily.py [--asof YYYY-MM-DD] [--force] [--max-jump 1.0]
"""
import argparse
import datetime as dt
import hashlib
import importlib.metadata as md
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gdpnow import public_data, releases, store

KEEP_RUNS = 5          # recent L3 runs whose estimates stay in the store (older ones are dropped)
KEEP_PULL_DAYS = 7     # archived raw pulls kept for this many days


def sh(*args, env=None):
    print('\n$', ' '.join(args), flush=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts' / args[0]), *args[1:]], check=True, cwd=ROOT,
                   env={**os.environ, **(env or {})})


def code_hash():
    """Fingerprint of everything besides data that determines the estimate: library code, config, the estimation
    stage scripts and the versions of the numerical libraries."""
    h = hashlib.sha256()
    files = sorted(ROOT.glob('gdpnow/*.py')) + sorted((ROOT / 'config').glob('*')) + sorted(ROOT.glob('scripts/0[2-5]_*.py'))
    for f in files:
        h.update(f.name.encode() + f.read_bytes())
    for pkg in ('numpy', 'pandas', 'scipy', 'statsmodels', 'duckdb', 'openpyxl'):
        h.update(f'{pkg}=={md.version(pkg)}'.encode())
    return h.hexdigest()[:16]


def previous_run(asof):
    """(asof, code_hash) of the last recorded run on or before `asof`, or None."""
    hist = ROOT / 'docs' / 'data' / 'history.csv'
    if not hist.exists():
        return None
    d = pd.read_csv(hist, usecols=['asof'])
    d = d[d['asof'] <= asof]
    if d.empty:
        return None
    prev = d['asof'].max()
    f = ROOT / 'docs' / 'data' / 'runs' / f'{prev}.json'
    return prev, (json.loads(f.read_text()).get('code_hash') if f.exists() else None)


def snapshot_pulls(asof):
    """Keep the archive of `asof` under the key `<asof>~prev`, because the refresh is about to replace it."""
    con = store.connect()
    try:
        if not store.table_exists(con, 'raw_pulls'):
            return None
        key = f'{asof}~prev'
        for t in ('raw_pulls', 'raw_files'):
            if store.table_exists(con, t):
                con.execute(f'DELETE FROM {t} WHERE as_of = ?', [key])
                con.execute(f"INSERT INTO {t} SELECT * REPLACE (? AS as_of) FROM {t} WHERE as_of = ?", [key, asof])
        return key
    finally:
        con.close()


def drop_snapshots():
    con = store.connect()
    try:
        for t in ('raw_pulls', 'raw_files'):
            if store.table_exists(con, t):
                con.execute(f"DELETE FROM {t} WHERE as_of LIKE '%~prev'")
    finally:
        con.close()


def stamp(asof, **fields):
    f = ROOT / 'docs' / 'data' / 'runs' / f'{asof}.json'
    rec = json.loads(f.read_text())
    rec.update(fields)
    f.write_text(json.dumps(rec, indent=1))


def last_price_month(asof, refresh=False):
    con = store.connect()
    try:
        s = public_data.fred(con, 'CPIAUCSL', asof, refresh=refresh)
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
        check = not a.force
        prev = previous_run(asof) if check else None
        prev_ok = bool(prev) and status.exists() and bool(json.loads(status.read_text()).get('ok'))
        base = (snapshot_pulls(asof) if prev and prev[0] == asof else prev[0] if prev else None) if check else None
        lpm = last_price_month(asof, refresh=check)
        print(f'last price month as of {asof}: {lpm}')
        args02 = ['--asof', asof, '--last-price-month', lpm, '--ism', 'public'] + (['--force'] if check else fl)
        sh('02_build_public.py', *args02, env={'GDPNOW_REFRESH': '1'} if check else None)
        chash = code_hash()
        unchanged = False
        if check:
            diff = None
            con = store.connect()
            try:
                diff = releases.public_inputs_diff(con, base, asof) if base else None
            finally:
                con.close()
            same_month = bool(prev) and prev[0][:7] == asof[:7]
            unchanged = (diff is not None and not any(diff.values()) and prev_ok and prev[1] == chash and same_month)
            print(f'change check vs {prev[0] if prev else None}: public inputs {diff}; code unchanged {bool(prev) and prev[1] == chash}; '
                  f'last run ok {prev_ok}; same month {same_month} -> {"NO NEW DATA, skipping estimation" if unchanged else "estimating"}')
            fl = ['--force']                 # a date already recorded today is replaced, not skipped
        if unchanged:
            sh('05_nowcast.py', '--level', 'L1', '--vintage', vint)
            sh('09_record.py', '--asof', asof, '--carry-from', prev[0], '--vintage', vint)
        else:
            sh('04_estimate.py', '--level', 'L3', '--asof', asof, *fl)
            sh('05_nowcast.py', '--level', 'L3', '--asof', asof, *fl)
            sh('05_nowcast.py', '--level', 'L1', '--vintage', vint, *fl)
            sh('09_record.py', '--asof', asof, '--vintage', vint, *fl)
            sh('10_decompose.py', '--asof', asof, *fl)
            sh('11_release_effects.py', '--asof', asof, '--last-price-month', lpm, *fl)
            stamp(asof, code_hash=chash)
        drop_snapshots()
        housekeeping(asof)
        problems = sanity(asof, a.max_jump)
    except Exception as e:                       # includes failed stages (CalledProcessError)
        write(False, f'run failed: {e}')
        raise
    if problems:
        write(False, 'held for review: ' + '; '.join(problems), held=True)
        print('HELD:', problems)
        sys.exit(3)
    write(True, 'no new data: nowcast unchanged' if unchanged else 'ok', **({'unchanged': True} if unchanged else {}))
    print(f'\ndaily run {asof}: ok')


if __name__ == '__main__':
    main()
