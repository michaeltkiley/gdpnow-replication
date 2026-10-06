"""Industrial production and capacity utilization series (FRED INDPRO, IPMAT, IPCONGD ... CUMFNS) from the Federal Reserve's G.17 release files
instead of FRED. https://www.federalreserve.gov/releases/g17/ipdisk/ip_sa.txt (index levels, SA) and utl_sa.txt (capacity utilization, SA) hold
every series: a header line `"CODE: label"` then one line per series and year, `"CODE"  YYYY  v1 .. v12` (a partial year has fewer values).
config/g17_series.toml maps each FRED id to its code; every mapped series equals FRED's on every month (exploration round 30).
Each file is downloaded once per run. Change signal: a header-only request per file (Last-Modified, ETag, length), logged for the daily probe.
No fallback: a failed download or a missing code fails the run.
"""
import re
import tomllib
import urllib.request

import pandas as pd

from . import public_data as P
from .config import CONFIG

BASE = 'https://www.federalreserve.gov/releases/g17/ipdisk/'
MAP = {fid: tuple(v) for fid, v in tomllib.load(open(CONFIG / 'g17_series.toml', 'rb'))['series'].items()}   # FRED id -> (file, code)
_TEXT = {}
_SERIES = {}                    # file -> {code: Series}
_ROW = re.compile(r'^"([^":]+)"\s+(\d{4})\s+(.*)$')


def covers(fred_id):
    return fred_id in MAP


def head(file):
    """(Last-Modified, ETag, Content-Length) of a G.17 file; raises without a Last-Modified."""
    req = urllib.request.Request(BASE + file, method='HEAD', headers=P.UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        h = r.headers
        if not h.get('Last-Modified'):
            raise RuntimeError(f'Fed G.17 {file}: no Last-Modified in the response')
        return h.get('Last-Modified'), h.get('ETag'), h.get('Content-Length')


def _text(file):
    if file not in _TEXT:
        P.record_head(BASE + file, head(file))
        last = None
        for k in range(3):
            try:
                _TEXT[file] = urllib.request.urlopen(urllib.request.Request(BASE + file, headers=P.UA), timeout=300).read().decode('latin-1')
                break
            except Exception as e:
                last = e
                if k < 2:
                    import time
                    time.sleep(5 * (k + 1))
        else:
            raise RuntimeError(f'Fed G.17 file {file} failed to download: {last}') from last
    return _TEXT[file]


def _parse(file):
    if file not in _SERIES:
        want = {code for f, code in MAP.values() if f == file}
        acc = {}
        for line in _text(file).splitlines():
            m = _ROW.match(line)
            if not m or m.group(1) not in want:
                continue
            for i, v in enumerate(m.group(3).split()[:12]):
                try:
                    acc.setdefault(m.group(1), {})[pd.Timestamp(int(m.group(2)), i + 1, 1)] = float(v)
                except ValueError:
                    pass                  # n.a. or a footnote marker: no observation
        _SERIES[file] = {c: pd.Series(d).sort_index() for c, d in acc.items()}
    return _SERIES[file]


def series(con, fred_id, asof):
    """The series FRED publishes as `fred_id` (index: first day of each month, like FRED)."""
    file, code = MAP[fred_id]
    if not P.REFRESH:
        s = P._archived(con, 'fed_g17', fred_id, asof)
        if s is not None:
            return s
    d = _parse(file)
    if code not in d:
        raise RuntimeError(f'Fed G.17 {file}: series {code} (FRED {fred_id}) not found')
    s = d[code]
    P._archive(con, 'fed_g17', fred_id, asof, s)
    return s
