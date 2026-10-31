"""Download one ALFRED vintage per (series, origin date) with the free no-key CSV endpoint; cache on disk.
Usage: python fetch_vintages.py [workers]"""
import sys, time, io, concurrent.futures as cf, urllib.request, urllib.error
from common import *

ALFRED = 'https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={id}&vintage_date={d}&cosd={c}'
FRED = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={c}'


def get(url, tries=6):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None                      # no vintage that early (ICSA < 2009, NFCI < 2011)
            time.sleep(1.5 * (i + 1))
        except Exception as e:
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(url)


def job(sid, d):
    f = DATA / 'vint' / sid / f'{d:%Y%m%d}.csv'
    if f.exists():
        return 0
    c = '1990-01-01'
    b = get(ALFRED.format(id=sid, d=d, c=c))
    if b is None:
        return 0
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b)
    return 1


if __name__ == '__main__':
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    ods = sorted({o[3] for o in origins()})
    jobs = [(s, d) for d in ods for s in VINTAGE_IDS + ['GDPC1', 'POPTHM']]
    # unrevised daily spread: one download
    for sid in ('T10Y3M', 'ICSA', 'NFCI'):       # current (unrevised / fallback) series
        f = DATA / 'vint' / f'{sid}.csv'
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(get(FRED.format(id=sid, c='1990-01-01')))
    print(len(ods), 'origin dates;', len(jobs), 'downloads', flush=True)
    n = 0
    with cf.ThreadPoolExecutor(w) as ex:
        futs = [ex.submit(job, *j) for j in jobs]
        for i, fu in enumerate(cf.as_completed(futs)):
            n += fu.result()
            if i % 500 == 0:
                print(i, flush=True)
    print('done; new files', n)
