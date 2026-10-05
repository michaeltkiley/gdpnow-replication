"""Public data access (FRED/ALFRED, BEA, Census) with an archive of every pull (decision D5).

Every fetch is stored in DuckDB table `raw_pulls` (source, series, asof, retrieved_at, date, value). A fetch
for (source, series, asof) already in the archive is served from it unless refresh=True.
"""
import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request

import pandas as pd

from . import store
from .config import load_env

load_env()
FRED = 'https://api.stlouisfed.org/fred/'
BEA = 'https://apps.bea.gov/api/data'


def _get(url, tries=4):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'gdpnow-replication'}),
                                        timeout=120) as r:
                return json.loads(r.read().decode())
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2 * (k + 1))


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
    """Observations of a FRED series as known on `asof` (ALFRED real-time period)."""
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
    """All lines of a BEA NIPA / underlying-detail table (current vintage; archived under `asof`).
    Returns DataFrame indexed by period end with columns 'line|description'."""
    key = f'{dataset}:{table}:{frequency}'
    if not refresh and store.table_exists(con, 'raw_pulls'):
        df = store.query(con, "SELECT series, date, value FROM raw_pulls WHERE source = 'bea' AND series LIKE ? AND as_of = ?",
                         (key + '|%', _as_of_for_table(con, key, asof)))
        if not df.empty:
            w = df.pivot(index='date', columns='series', values='value')
            w.index = pd.to_datetime(w.index)
            w.columns = [c[len(key) + 1:] for c in w.columns]
            return w.sort_index()
    if dataset == 'NIPA':
        chunks = ['ALL']
    else:   # underlying-detail requests are size-capped: fetch in 4-year chunks
        ys = list(range(1959, dt.date.today().year + 1))
        chunks = [','.join(str(y) for y in ys[i:i + 4]) for i in range(0, len(ys), 4)]
    rows = []
    for years in chunks:
        q = dict(UserID=os.environ['BEA_API_KEY'], method='GetData', DataSetName=dataset, TableName=table,
                 Frequency=frequency, Year=years, ResultFormat='JSON')
        d = _get(BEA + '?' + urllib.parse.urlencode(q))
        res = d['BEAAPI'].get('Results')
        if res is None or 'Data' not in res:
            if dataset == 'NIPA':
                raise RuntimeError(f'BEA {key}: {json.dumps(d)[:300]}')
            continue          # years before a table starts return no data
        rows += res['Data']
    recs = []
    for r in rows:
        tp = r['TimePeriod']
        if 'Q' in tp:
            date = pd.Period(tp.replace('Q', 'Q'), 'Q').end_time.normalize()
        elif 'M' in tp:
            date = pd.Period(tp.replace('M', '-'), 'M').end_time.normalize()
        else:
            date = pd.Period(tp, 'Y').end_time.normalize()
        try:
            v = float(r['DataValue'].replace(',', ''))
        except ValueError:
            continue
        recs.append((f"{r['LineNumber']}|{r['LineDescription']}", date, v))
    df = pd.DataFrame(recs, columns=['series', 'date', 'value']).drop_duplicates(['series', 'date'])
    for ser, g in df.groupby('series'):
        _archive(con, 'bea', f'{key}|{ser}', asof, pd.Series(g.value.to_numpy(), index=g.date))
    w = df.pivot(index='date', columns='series', values='value')
    return w.sort_index()
