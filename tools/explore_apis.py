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


# ---------------------------------------------------------------- round 2
def fred2(inv):
    k = f'&api_key={FRED_KEY}&file_type=json'
    bearer = {'User-Agent': 'Mozilla/5.0', 'Authorization': 'Bearer ' + FRED_KEY}
    base2 = 'https://api.stlouisfed.org/fred/v2/'
    st, n, t, _, s = call(base2 + 'release/observations?release_id=9&limit=2', headers=bearer)
    out('FRED v2 structure (release 9, limit 2):', st, n, short(t, 900))
    for label, q in (('series_id=RSAFS,RSXFS', 'release_id=9&series_id=RSAFS,RSXFS&limit=2'),
                     ('series_ids=RSAFS,RSXFS', 'release_id=9&series_ids=RSAFS,RSXFS&limit=2'),
                     ('series_id=RSAFS', 'release_id=9&series_id=RSAFS&limit=2'),
                     ('observation_date_start', 'release_id=9&observation_start=2026-01-01&limit=2'),
                     ('realtime_start vintage', 'release_id=9&realtime_start=2026-09-01&realtime_end=2026-09-01&limit=2'),
                     ('format=csv', 'release_id=9&format=csv&limit=2')):
        st, n, t, _, s = call(base2 + 'release/observations?' + q, headers=bearer)
        out('FRED v2 param test', label, st, n, short(t, 260))
    for ep in ('series/observations?series_id=RSAFS&limit=2', 'observations?series_id=RSAFS&limit=2'):
        st, n, t, _, s = call(base2 + ep, headers=bearer)
        out('FRED v2 endpoint', ep, st, n, short(t, 200))
    # change detection: series/updates
    base = 'https://api.stlouisfed.org/fred/'
    st, n, t, _, s = call(base + 'series/updates?filter_value=all&limit=2&start_time=202610061200' + k)
    out('FRED series/updates start_time:', st, n, short(t, 500))
    ours = set(inv.get('fred') or [])
    seen, pages, offset = {}, 0, 0
    while pages < 12:
        st, n, t, _, s = call(base + f'series/updates?filter_value=all&limit=1000&offset={offset}&start_time=202610051200' + k)
        try:
            d = json.loads(t)
        except Exception:
            out('FRED series/updates page error', st, short(t, 200))
            break
        rows = d.get('seriess', [])
        pages += 1
        for r in rows:
            if r['id'] in ours:
                seen[r['id']] = r.get('last_updated')
        out('FRED series/updates page', pages, 'rows', len(rows), 'count', d.get('count'), 'last row updated', rows[-1].get('last_updated') if rows else None)
        if len(rows) < 1000:
            break
        offset += 1000
        time.sleep(0.6)
    out('FRED our series updated since 2026-10-05 12:00 (per series/updates):', len(seen), sorted(seen.items())[:12])


def bea2(inv):
    base = 'https://apps.bea.gov/api/data?UserID=' + BEA_KEY + '&method=GetData&ResultFormat=JSON'
    st, n, t, hdr, s = call(base + '&DataSetName=NIPA&TableName=T10105&Frequency=Q&Year=ALL', maxb=2000)
    out('BEA API response headers:', {k: v for k, v in hdr.items() if k.lower() in ('last-modified', 'etag', 'cache-control', 'content-length', 'age', 'x-ratelimit-remaining', 'content-encoding')})
    tables = sorted({(a, b, c) for a, b, c, _ in inv.get('bea', [])})
    total_b, total_t, t_win, b_win = 0, 0.0, time.time(), 0
    for ds, tb, fr in tables:
        if b_win > 80e6 and time.time() - t_win < 60:          # stay under BEA's 100 MB a minute
            time.sleep(max(0, 61 - (time.time() - t_win)))
            t_win, b_win = time.time(), 0
        st, n, t, hdr, s = call(base + f'&DataSetName={ds}&TableName={tb}&Frequency={fr}&Year=ALL', timeout=300)
        total_b += n
        b_win += n
        total_t += s
        try:
            d = json.loads(t)['BEAAPI']
            rows = len(d['Results']['Data'])
            err = None
        except Exception:
            rows, err = None, short(t, 120)
        out('BEA Year=ALL', ds, tb, fr, '| http', st, 'MB', round(n / 1e6, 1), 'rows', rows, 'secs', s, err or '')
        time.sleep(0.8)
    out('BEA Year=ALL totals: tables', len(tables), 'MB', round(total_b / 1e6), 'secs', round(total_t))
    # equivalence with what the build archived
    try:
        import duckdb
        import pandas as pd
        con = duckdb.connect('data/gdpnow.duckdb', read_only=True)
        asof = con.execute("SELECT max(as_of) FROM raw_pulls WHERE source='bea'").fetchone()[0]
        for ds, tb, fr in (('NIUnderlyingDetail', 'U20405', 'M'), ('NIPA', 'T10105', 'Q')):
            fr_ = [f for d_, t_, f in tables if t_ == tb][0] if any(t_ == tb for _, t_, _ in tables) else fr
            arch = con.execute("SELECT series, date, value FROM raw_pulls WHERE source='bea' AND series LIKE ? AND as_of=?", [f'{ds}:{tb}:{fr_}|%', asof]).fetchdf()
            st, n, t, _, s = call(base + f'&DataSetName={ds}&TableName={tb}&Frequency={fr_}&Year=ALL', timeout=300)
            rows = json.loads(t)['BEAAPI']['Results']['Data']
            new = {}
            for r in rows:
                tp = r['TimePeriod']
                d_ = (pd.Period(tp, 'Q') if 'Q' in tp else pd.Period(tp.replace('M', '-'), 'M') if 'M' in tp else pd.Period(tp, 'Y')).end_time.normalize()
                try:
                    new[(r['LineNumber'] + '|' + r['LineDescription'], d_.date())] = float(r['DataValue'].replace(',', ''))
                except ValueError:
                    pass
            a = {(r.series.split('|', 1)[1], pd.Timestamp(r.date).date()): r.value for r in arch.itertuples()}
            common = set(a) & set(new)
            maxdiff = max((abs(a[c] - new[c]) for c in common), default=None)
            out('BEA equivalence detail', tb, 'only in archive', len(set(a) - set(new)), 'only in Year=ALL', len(set(new) - set(a)), 'common', len(common), 'max abs diff', maxdiff)
            out('BEA equivalence', ds, tb, fr_, 'archived obs', len(a), 'Year=ALL obs', len(new))
    except Exception as e:
        out('BEA equivalence skipped:', repr(e)[:200])


def census2():
    base = 'https://api.census.gov/data/timeseries/eits/'
    k = f'&key={CENSUS_KEY}'
    tot_b = 0
    for ds in ('advm3', 'ftdadv', 'm3', 'mrts', 'mrtsadv', 'mwtsadv'):
        st, n, t, hdr, s = call(base + ds + '?get=cell_value,time_slot_id,category_code,data_type_code,seasonally_adj&time=from+1992&for=us:*' + k, timeout=300)
        tot_b += n
        try:
            rows = len(json.loads(t)) - 1
        except Exception:
            rows = None
        out('CENSUS unfiltered from 1992', ds, '| http', st, 'MB', round(n / 1e6, 1), 'rows', rows, 'secs', s, 'hdrs', {kk: v for kk, v in hdr.items() if kk.lower() in ('last-modified', 'etag')})
    out('CENSUS six datasets total MB', round(tot_b / 1e6, 1))


def heads(inv):
    import duckdb
    con = duckdb.connect('data/gdpnow.duckdb', read_only=True)
    rows = con.execute("SELECT kind, url FROM fetch_log WHERE as_of = (SELECT max(as_of) FROM fetch_log) AND kind IN ('GET_BYTES', 'BEA_TRADE') OR url LIKE '%fiscaldata%'").fetchall()
    for kind, url in rows:
        if kind == 'BEA_TRADE':
            continue
        u = url
        try:
            req = urllib.request.Request(u, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=60) as r:
                h = dict(r.headers)
                out('HEAD', u.split('?')[0][-70:], r.status, {kk: v for kk, v in h.items() if kk.lower() in ('last-modified', 'etag', 'content-length')})
        except Exception as e:
            out('HEAD', u.split('?')[0][-70:], 'ERR', str(e)[:80])
    for u in ('https://apps.bea.gov/national/Release/TXT/NipaDataQ.txt',):
        for hdrs in ({'If-Modified-Since': 'Wed, 30 Sep 2026 12:30:02 GMT'}, {'If-Modified-Since': 'Tue, 29 Sep 2026 12:30:02 GMT'}):
            try:
                req = urllib.request.Request(u, method='HEAD', headers={'User-Agent': 'Mozilla/5.0', **hdrs})
                with urllib.request.urlopen(req, timeout=60) as r:
                    out('BEA conditional HEAD', hdrs, r.status)
            except urllib.error.HTTPError as e:
                out('BEA conditional HEAD', hdrs, e.code)


# ---------------------------------------------------------------- round 3
def bls3():
    base = 'https://download.bls.gov/pub/time.series/'
    st, n, t, hdr, s = call(base, maxb=200000)
    out('BLS flat-file root listing:', st, n, short(t, 300) if st != 200 else 'ok; surveys mentioned:', sorted(set(__import__('re').findall(r'time\.series/([a-z]{2})/', t)))[:60])
    import re
    for sv in ('ce', 'cu', 'ln', 'wp', 'pc', 'ei', 'ci', 'jt'):
        st, n, t, hdr, s = call(base + sv + '/', maxb=400000)
        rows = re.findall(r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s+[AP]M)\s+(\d+|&lt;dir&gt;)\s*<A HREF="([^"]+)">([^<]+)</A>', t)
        keep = [(d, sz, name) for d, sz, _, name in rows if '.data.' in name or name.endswith('.series') or 'AllData' in name]
        out('BLS', sv, '| http', st, 'files', len(rows), [(d, round(int(sz) / 1e6, 1) if sz.isdigit() else sz, nm) for d, sz, nm in keep][:14] if rows else short(t, 200))
    for u, hd in ((base + 'ce/ce.data.0.AllCESSeries', {'User-Agent': 'Mozilla/5.0'}),):
        try:
            req = urllib.request.Request(u, method='HEAD', headers=hd)
            with urllib.request.urlopen(req, timeout=60) as r:
                out('BLS HEAD', u[-60:], r.status, {k: v for k, v in dict(r.headers).items() if k.lower() in ('last-modified', 'etag', 'content-length')})
        except Exception as e:
            out('BLS HEAD', u[-60:], 'ERR', str(e)[:100])
    # why did the v1 API stop answering? (run D failed with a response lacking 'series')
    st, n, t, _, s = call('https://api.bls.gov/publicAPI/v1/timeseries/data/', data=json.dumps({'seriesid': ['CES9091911001'], 'startyear': '2017', 'endyear': '2026'}).encode(), headers={'Content-Type': 'application/json'})
    out('BLS v1 single series now:', st, short(t, 400))


def bea3():
    base = 'https://apps.bea.gov/api/data?UserID=' + BEA_KEY + '&method=GetData&ResultFormat=JSON'
    import hashlib
    url = base + '&DataSetName=NIUnderlyingDetail&TableName=U50505&Frequency=Q&Year=1983,1984,1985,1986'
    res = []
    for i in range(3):
        st, n, t, hdr, s = call(url)
        d = json.loads(t)['BEAAPI']
        rows = d['Results']['Data']
        res.append((hashlib.sha256(t.encode()).hexdigest()[:10], rows, d))
        time.sleep(1)
    out('BEA same request x3, raw sha', [r[0] for r in res], 'rows', [len(r[1]) for r in res])
    keys = [[(x['LineNumber'], x['TimePeriod']) for x in r[1]] for r in res]
    vals = [{(x['LineNumber'], x['TimePeriod']): x['DataValue'] for x in r[1]} for r in res]
    out('BEA row ORDER identical:', keys[0] == keys[1] == keys[2], '| same key set:', set(keys[0]) == set(keys[1]) == set(keys[2]), '| same values:', vals[0] == vals[1] == vals[2])
    top = [{k: v for k, v in r[2].items() if k != 'Results'} for r in res]
    out('BEA non-data parts differ:', json.dumps(top[0], sort_keys=True)[:200] != json.dumps(top[1], sort_keys=True)[:200], 'notes', short(json.dumps(res[0][2]['Results'].get('Notes', ''))[:300], 300))
    # does the bulk NIPA file contain the underlying-detail (U) tables?
    st, n, t, hdr, s = call('https://apps.bea.gov/national/Release/TXT/NipaDataQ.txt', timeout=300)
    lines = t.splitlines()
    codes = {}
    for ln in lines[1:]:
        c = ln.split(',')[0].strip('"')
        codes[c[:1]] = codes.get(c[:1], 0) + 1
    out('BEA NipaDataQ.txt bytes', n, 'lines', len(lines), 'header', lines[0][:100], 'first row', lines[1][:100], 'first-letter counts', dict(sorted(codes.items())[:12]))
    # FRED-style change signal for BEA UD: does U20405 Year=latest carry the same history digest?
    st, n, t, hdr, s = call(base + '&DataSetName=NIUnderlyingDetail&TableName=U20405&Frequency=M&Year=2026')
    out('BEA UD monthly one year', 'MB', round(n / 1e6, 2), 'secs', s)


def fred3():
    k = f'&api_key={FRED_KEY}&file_type=json'
    base = 'https://api.stlouisfed.org/fred/series/updates?filter_value=all&limit=1'
    for label, extra in (('start_time only', '&start_time=202610061200'), ('start_time+end_time', '&start_time=202610061200&end_time=202610061800'),
                         ('start_time with colon', '&start_time=2026-10-06 12:00'), ('no time', '')):
        st, n, t, _, s = call(base + extra.replace(' ', '%20') + k)
        try:
            d = json.loads(t)
            out('FRED series/updates', label, st, 'count', d.get('count'), 'first updated', d['seriess'][0].get('last_updated') if d.get('seriess') else None)
        except Exception:
            out('FRED series/updates', label, st, short(t, 160))


if __name__ == '__main__':
    which = sys.argv[1:] or ['inventory', 'fred', 'bea', 'census', 'bls']
    if 'inventory' not in which and any(w.endswith('2') or w == 'heads' for w in which):
        which = ['inventory'] + which
    inv = inventory() if 'inventory' in which else {}
    for name, fn in (('fred', lambda: fred(inv)), ('bea', lambda: bea(inv)), ('census', census), ('bls', bls),
                     ('fred2', lambda: fred2(inv)), ('bea2', lambda: bea2(inv)), ('census2', census2), ('heads', lambda: heads(inv)),
                     ('bls3', bls3), ('bea3', bea3), ('fred3', fred3)):
        if name in which:
            try:
                fn()
            except Exception as e:
                out(name, 'EXPLORER ERROR', repr(e)[:200])
