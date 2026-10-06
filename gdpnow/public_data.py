"""Public data access (FRED/ALFRED, BEA, Census) with an archive of every pull (decision D5).

Every fetch is stored in DuckDB table `raw_pulls` (source, series, asof, retrieved_at, date, value). A fetch
for (source, series, asof) already in the archive is served from it unless refresh=True.
"""
import datetime as dt
import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request

import pandas as pd

from . import store
from .config import load_env

load_env()
FRED = 'https://api.stlouisfed.org/fred/'
BEA = 'https://apps.bea.gov/api/data'


# ------------------------------------------------------------------------------------- fetch layer
# Every request that feeds the public inputs goes through _get / get_bytes / bea_trade_xlsx / bls_flat / census_bulk. In a
# production run (recording on) each is logged with a digest of its response in table `fetch_log`; the daily probe
# (gdpnow/probe.py) replays the logged requests and compares digests, so "did any raw input change?" is answered
# without running the build.
UA = {'User-Agent': 'Mozilla/5.0'}
SECRETS = ('FRED_API_KEY', 'BEA_API_KEY', 'CENSUS_API_KEY')
VOLATILE = {'realtime_start', 'realtime_end', 'responseTime', 'Request', 'UTCProductionTime'}   # request echoes and response timestamps, not data
_REC = {'asof': None, 'items': {}}


def _norm(o):
    if isinstance(o, dict):
        return {k: _norm(v) for k, v in o.items() if k not in VOLATILE}
    if isinstance(o, list):
        return [_norm(v) for v in o]
    return o


def digest_json(o):
    return hashlib.sha256(json.dumps(_norm(o), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def digest_bytes(b):
    return hashlib.sha256(b).hexdigest()


def tokenise(text, asof):
    """Make a request replayable on another day and free of secrets."""
    if text is None:
        return None
    for name in SECRETS:
        if os.environ.get(name):
            text = text.replace(os.environ[name], '{ENV:' + name + '}')
    return text.replace(str(asof), '{ASOF}')


def detokenise(text, asof):
    if text is None:
        return None
    for name in SECRETS:
        text = text.replace('{ENV:' + name + '}', os.environ.get(name, ''))
    return text.replace('{ASOF}', str(asof))


def begin_recording(asof):
    _REC['asof'], _REC['items'] = str(asof), {}


def _record(kind, url, body, digest):
    if _REC['asof'] is None:
        return
    url, body = tokenise(url, _REC['asof']), tokenise(body, _REC['asof'])
    key = hashlib.sha1(f'{kind}|{url}|{body}'.encode()).hexdigest()
    _REC['items'][key] = dict(key=key, kind=kind, url=url, body=body, digest=digest)


def save_recording(con):
    """Store this run's request log under its as-of date (replaces any earlier log for the date)."""
    if _REC['asof'] is None or not _REC['items']:
        return 0
    df = pd.DataFrame(list(_REC['items'].values()))
    df.insert(0, 'as_of', _REC['asof'])
    store.replace_rows(con, 'fetch_log', df, {'as_of': _REC['asof']})
    return len(df)


# Replay (the probe) sends a few hundred requests in minutes; the build spreads them over a long run. Per-host minimum
# gaps keep the probe under the providers' limits (BEA 100 requests a minute, FRED 120).
MIN_GAP = {'apps.bea.gov': 0.8, 'api.stlouisfed.org': 0.6}
_GATE = {}
_GATE_LOCK = threading.Lock()


def _throttle(url):
    host = urllib.parse.urlparse(url).netloc
    gap = MIN_GAP.get(host)
    if gap is None:
        return
    with _GATE_LOCK:
        gate = _GATE.setdefault(host, [threading.Lock(), 0.0])
    with gate[0]:
        wait = gate[1] - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        gate[1] = time.monotonic() + gap


def _get_core(url, tries=4, throttle=False):
    for k in range(tries):
        try:
            if throttle:
                _throttle(url)
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'gdpnow-replication'}),
                                        timeout=120) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if k == tries - 1:
                raise
            time.sleep(30 * (k + 1) if getattr(e, 'code', None) == 429 else 2 * (k + 1))


def _get(url, tries=4):
    d = _get_core(url, tries)
    _record('GET_JSON', url, None, digest_json(d))
    return d


def head(url, timeout=60):
    """(Last-Modified, ETag, Content-Length) of any file from a header-only request."""
    req = urllib.request.Request(url, method='HEAD', headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        h = r.headers
        return h.get('Last-Modified'), h.get('ETag'), h.get('Content-Length')


def get_bytes(url, timeout=120):
    """A file's bytes. The change signal for the daily probe is the file's header (Last-Modified, ETag, length) when the server sends
    a Last-Modified, so the probe repeats a header-only request; a server without one is checked by a digest of the content."""
    try:
        h = head(url)
    except Exception:
        h = None
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()
    if h and h[0]:
        record_head(url, h)
    else:
        _record('GET_BYTES', url, None, digest_bytes(raw))
    return raw


def bea_trade_xlsx():
    """The BEA trade-release time-series workbook (its file name carries the release month, so the link is read off
    the release page each time)."""
    import re
    page = urllib.request.urlopen(urllib.request.Request(
        'https://www.bea.gov/data/intl-trade-investment/international-trade-goods-and-services', headers=UA), timeout=120).read().decode()
    link = re.search(r'href="([^"]*trad\d{4}-time-series\.xlsx)"', page).group(1)
    raw = urllib.request.urlopen(urllib.request.Request('https://www.bea.gov' + link, headers=UA), timeout=300).read()
    _record('BEA_TRADE', '', None, digest_bytes(raw))
    return raw


def head_digest(last_modified, etag, length):
    return hashlib.sha256(f'{last_modified}|{etag}|{length}'.encode()).hexdigest()


def record_head(url, head):
    """Log a header-only check of a bulk file (Last-Modified, ETag, length) so the probe can repeat it. `url` is the file's
    full URL (a BLS flat-file name given without a scheme is expanded)."""
    if not url.startswith('http'):
        url = 'https://download.bls.gov/pub/time.series/' + url
    _record('BULK_HEAD', url, None, head_digest(*head))


def replay(kind, url, body):
    """Fetch a logged request again (no recording); returns the digest of the response."""
    if kind == 'GET_JSON':
        return digest_json(_get_core(url, tries=6, throttle=True))
    if kind == 'GET_BYTES':
        return digest_bytes(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300).read())
    if kind in ('BLS_HEAD', 'BULK_HEAD'):          # BLS_HEAD: requests logged by the previous version
        if '/pub/time.series/' in url:
            from . import bls_flat
            return head_digest(*bls_flat.head(url.split('/pub/time.series/', 1)[1]))
        if 'federalreserve.gov/releases/g17/' in url:
            from . import fed_g17
            return head_digest(*fed_g17.head(url.rsplit('/', 1)[1]))
        if 'apps.bea.gov/national/Release/TXT/' in url:
            from . import bea_bulk
            return head_digest(*bea_bulk.head(url.rsplit('/', 1)[1]))
        return head_digest(*head(url))
    if kind == 'CENSUS_ZIP':
        from . import census_bulk
        return census_bulk.digest(url.split('programCode=', 1)[1])
    if kind == 'BEA_TRADE':
        saved, _REC['asof'] = _REC['asof'], None          # replay is not recorded
        try:
            return digest_bytes(bea_trade_xlsx())
        finally:
            _REC['asof'] = saved
    raise ValueError(kind)


# GDPNOW_REFRESH=1 (daily change check): every pull is fetched again once per process, replacing the archive for
# its as-of date, instead of being served from the archive (which is what makes a re-run of a day repeatable).
REFRESH = os.environ.get('GDPNOW_REFRESH') == '1'
_SEEN = set()


def refresh_once(key):
    """True once per process and key when GDPNOW_REFRESH is on (re-download a cached file once)."""
    if REFRESH and key not in _SEEN:
        _SEEN.add(key)
        return True
    return False

_OVR = {}


def _override():
    """Mixed-vintage runs (scripts/11_release_effects.py): env GDPNOW_OVERRIDE names a JSON file
    {new, prev, release: [[source, series], ...]}: the listed series are read as of `new`, every other series
    as of `prev` (if archived then, else `new`)."""
    if 'cfg' not in _OVR:
        f = os.environ.get('GDPNOW_OVERRIDE')
        _OVR['cfg'] = json.load(open(f)) if f else None
        if _OVR['cfg']:
            _OVR['rel'] = {tuple(x) for x in _OVR['cfg']['release']}
            _OVR['prev'] = None
    return _OVR['cfg']


def _prev_keys(con):
    if _OVR['prev'] is None:
        r = con.execute('SELECT DISTINCT source, series FROM raw_pulls WHERE as_of = ?', [_OVR['cfg']['prev']]).fetchall()
        _OVR['prev'] = set(r)
    return _OVR['prev']


def _as_of_for(con, source, series, asof):
    """The archive date to read (source, series) from."""
    c = _override()
    if not c:
        return str(asof)
    if (source, series) in _OVR['rel']:
        return c['new']
    return c['prev'] if (source, series) in _prev_keys(con) else c['new']


def _as_of_for_table(con, key, asof):
    c = _override()
    if not c:
        return str(asof)
    if any(sr == 'bea' and se.startswith(key + '|') for sr, se in _OVR['rel']):
        return c['new']
    return c['prev'] if any(sr == 'bea' and se.startswith(key + '|') for sr, se in _prev_keys(con)) else c['new']


def _archived(con, source, series, asof):
    if not store.table_exists(con, 'raw_pulls'):
        return None
    asof = _as_of_for(con, source, series, asof)
    if REFRESH and (source, series, str(asof)) not in _SEEN:
        _SEEN.add((source, series, str(asof)))
        return None
    df = store.query(con, 'SELECT date, value FROM raw_pulls WHERE source = ? AND series = ? AND as_of = ?',
                     (source, series, str(asof)))
    if df.empty:
        return None
    s = pd.Series(df.value.to_numpy(), index=pd.to_datetime(df.date)).sort_index()
    return s


def _archive(con, source, series, asof, s):
    df = pd.DataFrame({'source': source, 'series': series, 'as_of': str(asof), 'retrieved_at': dt.datetime.now(),
                       'date': s.index, 'value': s.to_numpy()})
    store.replace_rows(con, 'raw_pulls', df, {'source': source, 'series': series, 'as_of': str(asof)})


def fred(con, series_id, asof, refresh=False):
    """Observations of a FRED series as known on `asof` (ALFRED real-time period). Series listed in
    config/bls_series.toml come from BLS's flat files (gdpnow/bls_flat.py), those in config/census_series.toml from Census's bulk files (gdpnow/census_bulk.py); BEA tables come from BEA's bulk files (gdpnow/bea_bulk.py)."""
    from . import bea_bulk, bea_trade, bea_vehicles, bls_flat, census_bulk, fed_g17
    if bls_flat.covers(series_id):
        return bls_flat.series(con, series_id, asof)
    if census_bulk.covers(series_id):
        return census_bulk.series(con, series_id, asof)
    if bea_bulk.covers(series_id):
        return bea_bulk.series(con, series_id, asof)
    if bea_trade.covers(series_id):
        return bea_trade.series(con, series_id, asof)
    if bea_vehicles.covers(series_id):
        return bea_vehicles.series(con, series_id, asof)
    if fed_g17.covers(series_id):
        return fed_g17.series(con, series_id, asof)
    if not refresh:
        s = _archived(con, 'fred', series_id, asof)
        if s is not None:
            return s
    q = dict(series_id=series_id, api_key=os.environ['FRED_API_KEY'], file_type='json',
             realtime_start=str(asof), realtime_end=str(asof), observation_start='1947-01-01')
    try:
        d = _get(FRED + 'series/observations?' + urllib.parse.urlencode(q))
    except Exception:           # series without usable real-time history (e.g. licensed NAR data): current vintage
        q.pop('realtime_start'), q.pop('realtime_end')
        d = _get(FRED + 'series/observations?' + urllib.parse.urlencode(q))
    obs = [(o['date'], float(o['value'])) for o in d.get('observations', []) if o['value'] not in ('.', '')]
    s = pd.Series({pd.Timestamp(a): b for a, b in obs}, dtype=float).sort_index()
    _archive(con, 'fred', series_id, asof, s)
    return s


def fred_info(series_id):
    q = dict(series_id=series_id, api_key=os.environ['FRED_API_KEY'], file_type='json')
    try:
        d = _get(FRED + 'series?' + urllib.parse.urlencode(q), tries=1)
    except Exception:
        return {}
    return d.get('seriess', [{}])[0]


def fred_search(text, limit=10):
    q = dict(search_text=text, api_key=os.environ['FRED_API_KEY'], file_type='json', limit=limit,
             order_by='popularity')
    d = _get(FRED + 'series/search?' + urllib.parse.urlencode(q))
    return [(s['id'], s['title'], s['frequency_short'], s['observation_end'], s.get('seasonal_adjustment_short'))
            for s in d.get('seriess', [])]


def bea_table(con, dataset, table, frequency, asof, refresh=False):
    """All lines of a BEA NIPA / underlying-detail table (current vintage, from BEA's bulk files; archived under `asof`).
    Returns DataFrame indexed by period end with columns 'line|description'."""
    key = f'{dataset}:{table}:{frequency}'
    if REFRESH and (key, str(asof)) not in _SEEN:
        _SEEN.add((key, str(asof)))
        refresh = True
    if not refresh and store.table_exists(con, 'raw_pulls'):
        df = store.query(con, "SELECT series, date, value FROM raw_pulls WHERE source = 'bea' AND series LIKE ? AND as_of = ?",
                         (key + '|%', _as_of_for_table(con, key, asof)))
        if not df.empty:
            w = df.pivot(index='date', columns='series', values='value')
            w.index = pd.to_datetime(w.index)
            w.columns = [c[len(key) + 1:] for c in w.columns]
            return w.sort_index()
    from . import bea_bulk
    df = bea_bulk.table(table, frequency)
    if dataset != 'NIPA':          # the API path asked underlying-detail tables from 1959 on
        df = df[df.date >= '1959-01-01']
    df = df.drop_duplicates(['series', 'date'])
    for ser, g in df.groupby('series'):
        _archive(con, 'bea', f'{key}|{ser}', asof, pd.Series(g.value.to_numpy(), index=g.date))
    w = df.pivot(index='date', columns='series', values='value')
    return w.sort_index()

