"""BLS series from BLS's flat files (https://download.bls.gov/pub/time.series/), instead of FRED.

config/bls_series.toml maps a FRED series id to a BLS series id and the flat file holding its full history. A file is
downloaded once per run (streamed; only the series we use are kept) and each series archived in `raw_pulls`
(source 'bls_flat', series = the BLS id). BLS asks automated clients to identify themselves: the request header
carries the contact string in the BLS_CONTACT environment variable (a repo secret; never committed). There is no
fallback: if the contact string is missing or a download fails, the run fails (and GitHub emails the failure).

Change signal: a header-only request per file returns Last-Modified, ETag and length, which BLS sets at release time
(public_data.head_digest; recorded for the daily probe like every other request).
"""
import os
import tomllib
import urllib.request

import pandas as pd

from . import public_data as P
from .config import CONFIG

BASE = 'https://download.bls.gov/pub/time.series/'
_CFG = tomllib.load(open(CONFIG / 'bls_series.toml', 'rb'))
MAP = {fid: (v[0], v[1]) for fid, v in _CFG['series'].items()}
DIRECT = {bid: (v[0], v[1]) for bid, v in _CFG.get('direct', {}).items()}      # bls id -> (file, first year kept)
_CACHE = {}          # BLS id -> Series, filled when a file is loaded in this process
_LOADED = set()


def covers(fred_id):
    return fred_id in MAP


def headers():
    contact = os.environ.get('BLS_CONTACT', '').strip()
    if not contact:
        raise RuntimeError('BLS_CONTACT is not set (BLS requires a contact string in the User-Agent)')
    return {'User-Agent': f'gdpnow-replication/1.0 ({contact})'}


def url(file):
    return BASE + file


def head(file):
    """(Last-Modified, ETag, Content-Length) of a flat file, without downloading it."""
    req = urllib.request.Request(url(file), method='HEAD', headers=headers())
    with urllib.request.urlopen(req, timeout=60) as r:
        h = r.headers
        return h.get('Last-Modified'), h.get('ETag'), h.get('Content-Length')


def parse(lines, wanted):
    """{bls id: {first-of-period Timestamp: value}} for the wanted series from the lines of a flat file. Monthly (M01-M12)
    and quarterly (Q01-Q04, dated by the quarter's first month, as FRED does) observations; annual, semiannual and
    M13 averages and non-numeric values are skipped."""
    out = {}
    header = True
    for ln in lines:
        if header:
            header = False
            continue
        p = ln.rstrip('\n').split('\t')
        if len(p) < 4:
            continue
        sid = p[0].strip()
        if sid not in wanted:
            continue
        per = p[2].strip()
        if per[:1] == 'M' and per != 'M13':
            month = int(per[1:])
        elif per[:1] == 'Q':
            month = 3 * int(per[1:]) - 2
        else:
            continue
        try:
            v = float(p[3])
        except ValueError:
            continue
        out.setdefault(sid, {})[pd.Timestamp(int(p[1]), month, 1)] = v
    return out


def load(con, asof, file):
    """Download one flat file and archive every configured series it holds."""
    if file in _LOADED:
        return
    wanted = {b for b, f in MAP.values() if f == file} | {b for b, (f, _) in DIRECT.items() if f == file}
    P.record_head(file, head(file))
    req = urllib.request.Request(url(file), headers=headers())
    with urllib.request.urlopen(req, timeout=900) as r:
        got = parse((ln.decode('utf8', 'replace') for ln in r), wanted)
    missing = wanted - set(got)
    if missing:
        raise RuntimeError(f'{file}: series not found: {sorted(missing)}')
    for bid, vals in got.items():
        s = pd.Series(vals, dtype=float).sort_index()
        _CACHE[bid] = s
        P._archive(con, 'bls_flat', bid, asof, s)
    _LOADED.add(file)


def series(con, fred_id, asof):
    """The series FRED publishes as `fred_id`, from BLS's flat file (index: first day of each period, like FRED)."""
    bid, file = MAP[fred_id]
    return _get(con, asof, bid, file)


def _get(con, asof, bid, file):
    if bid in _CACHE:
        return _CACHE[bid]
    if not P.REFRESH:
        s = P._archived(con, 'bls_flat', bid, asof)
        if s is not None:
            _CACHE[bid] = s
            return s
    load(con, asof, file)
    return _CACHE[bid]


def direct(con, asof, bls_id):
    """A BLS series FRED does not carry, from its first configured year on (index: first day of each period)."""
    file, first = DIRECT[bls_id]
    s = _get(con, asof, bls_id, file)
    return s[s.index.year >= first]
