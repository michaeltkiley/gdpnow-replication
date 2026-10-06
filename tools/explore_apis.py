"""One-off exploration of what the data providers' APIs can batch (run by .github/workflows/explore-apis.yml, which
has the API keys). Prints compact, secret-free results prefixed 'X|'. Not part of the pipeline."""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
FRED_KEY, BEA_KEY, CENSUS_KEY = (os.environ.get(k, '') for k in ('FRED_API_KEY', 'BEA_API_KEY', 'CENSUS_API_KEY'))


def out(*a):
    print('X|' + ' '.join(str(x) for x in a), flush=True)


def call(url, data=None, headers=None, timeout=120, maxb=None):
    """(status, bytes_received, text head) with secrets removed."""
    t = time.time()
    try:
        req = urllib.request.Request(url, data=data, headers=headers or {'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read() if maxb is None else r.read(maxb)
            st, hdr = r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        body, st, hdr = e.read()[:600], e.code, dict(e.headers)
    except Exception as e:
        return 'ERR', 0, str(e)[:200], {}, round(time.time() - t, 1)
    text = body.decode('utf8', 'replace')
    for k in (FRED_KEY, BEA_KEY, CENSUS_KEY):
        if k:
            text = text.replace(k, '***')
    return st, len(body), text, hdr, round(time.time() - t, 1)


def short(s, n=260):
    return ' '.join(s.split())[:n]


# ---------------------------------------------------------------- inventory of what the build requests today
def inventory():
    try:
        import duckdb
        con = duckdb.connect('data/gdpnow.duckdb', read_only=True)
        rows = con.execute('SELECT as_of, kind, url FROM fetch_log WHERE as_of = (SELECT max(as_of) FROM fetch_log)').fetchall()
    except Exception as e:
        out('inventory unavailable:', str(e)[:150])
        return {}
    out('fetch_log as_of', rows[0][0] if rows else None, 'requests', len(rows))
    host = Counter(urllib.parse.urlparse(u).netloc or 'bea-trade' for _, _, u in rows)
    out('by host', dict(host))
    inv = {'fred': [], 'bea': [], 'census': [], 'bls': []}
    for _, k, u in rows:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
        if 'stlouisfed' in u:
            if 'series_id' in q:
                inv['fred'].append(q['series_id'][0])
        elif 'apps.bea.gov/api' in u:
            inv['bea'].append((q.get('DataSetName', [''])[0], q.get('TableName', [''])[0], q.get('Frequency', [''])[0], q.get('Year', [''])[0][:12]))
        elif 'api.census.gov' in u:
            inv['census'].append(u.split('?')[0].split('/')[-1] + ' ' + '|'.join(f'{a}={b[0]}' for a, b in q.items() if a not in ('key', 'get', 'for')))
        elif 'bls.gov' in u:
            inv['bls'].append(k)
    out('fred series', len(inv['fred']), 'distinct bea tables', len({t[:3] for t in inv['bea']}), 'bea requests', len(inv['bea']),
        'census requests', len(inv['census']), 'bls requests', len(inv['bls']))
    out('bea tables', sorted({(a, b, c) for a, b, c, _ in inv['bea']}))
    out('census requests', sorted(set(inv['census']))[:80])
    return inv


# ---------------------------------------------------------------- FRED
def fred(inv):
    base = 'https://api.stlouisfed.org/fred/'
    k = f'&api_key={FRED_KEY}&file_type=json'
    ids = inv.get('fred') or ['PAYEMS', 'INDPRO', 'CPIAUCSL']
    st, n, t, _, s = call(base + 'series/observations?series_id=' + ','.join(ids[:3]) + k + '&limit=2')
    out('FRED v1 observations with 3 comma-separated ids:', st, n, short(t))
    st, n, t, _, s = call(base + 'series/updates?filter_value=all&limit=3' + k)
    out('FRED v1 series/updates:', st, n, short(t, 400))
    for url in ('https://api.stlouisfed.org/fred/v2/release/observations?release_id=50&limit=3' + k,
                'https://api.stlouisfed.org/fred/v2/release/observations?release_id=50&format=json&limit=3'):
        for hdrs in ({'User-Agent': 'Mozilla/5.0'}, {'User-Agent': 'Mozilla/5.0', 'Authorization': 'Bearer ' + FRED_KEY}):
            st, n, t, _, s = call(url, headers=hdrs)
            out('FRED v2 release/observations', 'bearer' if 'Authorization' in hdrs else 'plain', url.split('?')[1][:40], st, n, short(t, 300))
    # which releases cover our series?
    rel = {}
    for sid in ids:
        st, n, t, _, s = call(base + 'series/release?series_id=' + sid + k)
        try:
            r = json.loads(t)['releases'][0]
            rel[sid] = (r['id'], r['name'])
        except Exception:
            rel[sid] = (None, short(t, 60))
        time.sleep(0.5)
    c = Counter(v for v in rel.values())
    out('FRED series per release (id, name, n_our_series):', [(i, nm, cnt) for (i, nm), cnt in c.most_common()])
    for (i, nm), cnt in c.most_common(6):
        if i:
            st, n, t, _, s = call(base + f'release/series?release_id={i}&limit=1' + k)
            try:
                out('FRED release', i, nm, 'total series in release:', json.loads(t).get('count'))
            except Exception:
                out('FRED release', i, nm, st, short(t, 80))


# ---------------------------------------------------------------- BEA
def bea(inv):
    base = 'https://apps.bea.gov/api/data?UserID=' + BEA_KEY + '&method=GetData&ResultFormat=JSON'

    def one(label, extra):
        st, n, t, hdr, s = call(base + extra)
        try:
            d = json.loads(t)['BEAAPI']
            res = d.get('Results', {})
            rows = len(res.get('Data', [])) if isinstance(res, dict) else None
            err = d.get('Error') or (res.get('Error') if isinstance(res, dict) else None)
            out('BEA', label, '| http', st, 'bytes', n, 'rows', rows, 'secs', s, '| err', short(json.dumps(err), 200) if err else None)
        except Exception:
            out('BEA', label, '| http', st, 'bytes', n, 'secs', s, '|', short(t, 200))
        time.sleep(1)
    one('NIPA two tables T10101,T10105 Q ALL', '&DataSetName=NIPA&TableName=T10101,T10105&Frequency=Q&Year=ALL')
    one('NIPA TableName=ALL Q ALL', '&DataSetName=NIPA&TableName=ALL&Frequency=Q&Year=ALL')
    one('NIPA T10101 Frequency=Q,M Year=ALL', '&DataSetName=NIPA&TableName=T10101&Frequency=Q,M&Year=ALL')
    one('NIPA T10101 Q ALL (baseline)', '&DataSetName=NIPA&TableName=T10101&Frequency=Q&Year=ALL')
    one('UnderlyingDetail U20405 Q Year=ALL', '&DataSetName=NIUnderlyingDetail&TableName=U20405&Frequency=Q&Year=ALL')
    one('UnderlyingDetail U20405 Q 1959-2026 as list', '&DataSetName=NIUnderlyingDetail&TableName=U20405&Frequency=Q&Year=' + ','.join(str(y) for y in range(1959, 2027)))
    one('UnderlyingDetail U20405 Q 4 years', '&DataSetName=NIUnderlyingDetail&TableName=U20405&Frequency=Q&Year=2020,2021,2022,2023')
    one('UnderlyingDetail two tables U20405,U20404 Q 4 years', '&DataSetName=NIUnderlyingDetail&TableName=U20405,U20404&Frequency=Q&Year=2020,2021,2022,2023')
    one('UnderlyingDetail U20405 Q 2023 only', '&DataSetName=NIUnderlyingDetail&TableName=U20405&Frequency=Q&Year=2023')
    # bulk downloads
    for u in ('https://apps.bea.gov/national/Release/TXT/NipaDataQ.txt', 'https://apps.bea.gov/national/Release/TXT/NipaDataM.txt',
              'https://apps.bea.gov/national/Release/ZIP/Nipa.zip', 'https://apps.bea.gov/national/Release/TXT/NipaDataA.txt',
              'https://apps.bea.gov/national/FA2004/Select.asp'):
        st, n, t, hdr, s = call(u, maxb=300)
        out('BEA bulk', u, st, 'content-length', hdr.get('Content-Length'), 'last-modified', hdr.get('Last-Modified'), short(t, 80) if st != 200 else '')


# ---------------------------------------------------------------- Census
def census():
    base = 'https://api.census.gov/data/timeseries/eits/'
    k = f'&key={CENSUS_KEY}'

    def one(label, url):
        st, n, t, _, s = call(url + k)
        try:
            d = json.loads(t)
            out('CENSUS', label, '| http', st, 'bytes', n, 'rows', len(d) - 1, 'secs', s, 'header', d[0])
        except Exception:
            out('CENSUS', label, '| http', st, 'bytes', n, '|', short(t, 200))
    one('mrts single category/data_type SA from 2024', base + 'mrts?get=cell_value,time_slot_id&category_code=44X72&data_type_code=SM&seasonally_adj=yes&time=from+2024&for=us:*')
    one('mrts NO category/data_type filter, SA from 2024', base + 'mrts?get=cell_value,time_slot_id,category_code,data_type_code,seasonally_adj&seasonally_adj=yes&time=from+2024&for=us:*')
    one('mrts category_code wildcard, data_type SM', base + 'mrts?get=cell_value,time_slot_id,category_code,data_type_code&category_code=*&data_type_code=SM&seasonally_adj=yes&time=from+2024&for=us:*')
    one('mrts two category codes comma', base + 'mrts?get=cell_value,time_slot_id&category_code=44X72,44000&data_type_code=SM&seasonally_adj=yes&time=from+2024&for=us:*')
    one('mrts NO filter all history from 1992', base + 'mrts?get=cell_value,time_slot_id,category_code,data_type_code,seasonally_adj&time=from+1992&for=us:*')
    one('advm3 NO filter from 2024', base + 'advm3?get=cell_value,time_slot_id,category_code,data_type_code,seasonally_adj&time=from+2024&for=us:*')


# ---------------------------------------------------------------- BLS
def bls():
    url = 'https://api.bls.gov/publicAPI/v1/timeseries/data/'
    sids = ['CUSR0000SEHK01', 'CES9091911001', 'CES2023611806']
    hdr = {'Content-Type': 'application/json'}
    for label, body in (('v1 three series, 2020-2026', {'seriesid': sids, 'startyear': '2020', 'endyear': '2026'}),
                        ('v1 three series, 1985-2026 (>10 years)', {'seriesid': sids, 'startyear': '1985', 'endyear': '2026'}),
                        ('v1 three series, 2017-2026 (10 years)', {'seriesid': sids, 'startyear': '2017', 'endyear': '2026'})):
        st, n, t, _, s = call(url, data=json.dumps(body).encode(), headers=hdr)
        try:
            d = json.loads(t)
            ns = [len(x['data']) for x in d.get('Results', {}).get('series', [])]
            out('BLS', label, '| http', st, 'status', d.get('status'), 'series returned', len(ns), 'obs each', ns, 'msg', short(json.dumps(d.get('message')), 220))
        except Exception:
            out('BLS', label, '| http', st, short(t, 200))


if __name__ == '__main__':
    which = sys.argv[1:] or ['inventory', 'fred', 'bea', 'census', 'bls']
    inv = inventory() if 'inventory' in which else {}
    for name, fn in (('fred', lambda: fred(inv)), ('bea', lambda: bea(inv)), ('census', census), ('bls', bls)):
        if name in which:
            try:
                fn()
            except Exception as e:
                out(name, 'EXPLORER ERROR', repr(e)[:200])
