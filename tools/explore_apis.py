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
BLS_CONTACT = os.environ.get('BLS_CONTACT', '')       # secret: contact string BLS's download policy asks for in the User-Agent


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


# ---------------------------------------------------------------- round 4
def _diff(a, b, path='', acc=None, limit=12):
    acc = [] if acc is None else acc
    if len(acc) >= limit:
        return acc
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                acc.append(f'{path}/{k}: only in {"first" if k in a else "second"}')
            else:
                _diff(a[k], b[k], f'{path}/{k}', acc, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            acc.append(f'{path}: list length {len(a)} vs {len(b)}')
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f'{path}[{i}]', acc, limit)
    elif a != b:
        acc.append(f'{path}: {short(repr(a), 80)} vs {short(repr(b), 80)}')
    return acc


def bea4():
    import hashlib
    base = 'https://apps.bea.gov/api/data?UserID=' + BEA_KEY + '&method=GetData&ResultFormat=JSON'
    for tb, years in (('U50505', '1983,1984,1985,1986'), ('U50504', '1975,1976,1977,1978')):
        url = base + f'&DataSetName=NIUnderlyingDetail&TableName={tb}&Frequency=Q&Year={years}'
        got = {}
        for i in range(8):
            st, n, t, hdr, s = call(url)
            sha = hashlib.sha256(t.encode()).hexdigest()[:8]
            got.setdefault(sha, json.loads(t))
            time.sleep(0.8)
        out('BEA', tb, 'distinct raw responses in 8 calls:', len(got), list(got))
        if len(got) > 1:
            ks = list(got)
            for diff in _diff(got[ks[0]], got[ks[1]]):
                out('BEA diff', tb, diff)
    # bulk file: which first-letter groups exist (underlying detail codes?)
    st, n, t, hdr, s = call('https://apps.bea.gov/national/Release/TXT/NipaDataQ.txt', timeout=300)
    codes = {}
    for ln in t.splitlines()[1:]:
        c = ln.split(',')[0]
        codes[c[:1]] = codes.get(c[:1], 0) + 1
    out('BEA NipaDataQ first-letter groups (all):', dict(sorted(codes.items())))
    out('BEA NipaDataQ sample codes by letter:', {k: sorted({ln.split(',')[0] for ln in t.splitlines()[1:3000000:5000] if ln.startswith(k)})[:3] for k in sorted(codes)[:30]})
    st, n, t, hdr, s = call('https://apps.bea.gov/national/Release/TXT/', maxb=100000)
    out('BEA Release/TXT listing', st, n, short(t, 300))


def fred4():
    k = f'&api_key={FRED_KEY}&file_type=json'
    for label, extra in (('window yesterday 12:00 to now (CT)', '&start_time=202610051200&end_time=202610062359'),
                         ('window today 00:00 to 23:59', '&start_time=202610060000&end_time=202610062359'),
                         ('window last 6h only', '&start_time=202610060300&end_time=202610062359')):
        n_ours, pages, off, total = 0, 0, 0, None
        ours = set()
        try:
            import duckdb
            con = duckdb.connect('data/gdpnow.duckdb', read_only=True)
            for (u,) in con.execute("SELECT url FROM fetch_log WHERE as_of=(SELECT max(as_of) FROM fetch_log) AND url LIKE '%stlouisfed%'").fetchall():
                q = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
                if 'series_id' in q:
                    ours.add(q['series_id'][0])
        except Exception as e:
            out('fred4 inventory error', str(e)[:100])
        hit = {}
        while pages < 40:
            st, n, t, _, s = call(f'https://api.stlouisfed.org/fred/series/updates?filter_value=all&limit=1000&offset={off}' + extra + k)
            try:
                d = json.loads(t)
            except Exception:
                out('FRED window', label, 'error', st, short(t, 150))
                break
            total = d.get('count')
            for r in d.get('seriess', []):
                if r['id'] in ours:
                    hit[r['id']] = r.get('last_updated')
            pages += 1
            if len(d.get('seriess', [])) < 1000:
                break
            off += 1000
            time.sleep(0.6)
        out('FRED window', label, '| total series updated', total, 'pages', pages, 'our series flagged', len(hit), sorted(hit.items())[:8])


# ---------------------------------------------------------------- round 5: BLS flat files with an identifying User-Agent
def bls5():
    import re
    if not BLS_CONTACT:
        out('BLS flat files: no BLS_CONTACT secret set; skipped')
        return
    ua = {'User-Agent': f'gdpnow-replication/1.0 ({BLS_CONTACT})'}
    base = 'https://download.bls.gov/pub/time.series/'
    for sv in ('cu', 'ce', 'ln', 'wp', 'pc', 'ei', 'ci'):
        st, n, t, hdr, s = call(base + sv + '/', headers=ua, maxb=400000)
        t = t.replace(BLS_CONTACT, '***')
        rows = re.findall(r'(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}\s+[AP]M)\s+(\d+)\s+<A HREF="[^"]+">([^<]+)</A>', t)
        keep = [(d, round(int(sz) / 1e6, 1), nm) for d, sz, nm in rows if '.data.' in nm]
        out('BLS flat', sv, '| http', st, 'data files (date, MB, name):', keep[:16] if rows else short(t, 160))
        time.sleep(0.5)
    for u in ('cu/cu.data.1.AllItems', 'ce/ce.data.0.AllCESSeries', 'pc/pc.data.0.Current', 'ei/ei.data.0.Current', 'ln/ln.data.1.AllData'):
        try:
            req = urllib.request.Request(base + u, method='HEAD', headers=ua)
            with urllib.request.urlopen(req, timeout=60) as r:
                out('BLS HEAD', u, r.status, {k: v for k, v in dict(r.headers).items() if k.lower() in ('last-modified', 'etag', 'content-length')})
        except Exception as e:
            out('BLS HEAD', u, 'ERR', str(e)[:80])
        time.sleep(0.5)
    # compare a flat-file series with what the API returns, for a series we already use
    st, n, t, _, s = call(base + 'ce/ce.data.0.AllCESSeries', headers=ua, timeout=600, maxb=None)
    rows = [ln for ln in t.splitlines() if ln.startswith('CES9091911001') or ln.startswith('CES2023611806')]
    out('BLS flat CES file: MB', round(n / 1e6, 1), 'secs', s, 'lines', len(t.splitlines()), 'our two series rows', len(rows), 'sample', rows[:2], 'header', t.splitlines()[0][:80])


# ---------------------------------------------------------------- round 6: derive and validate the FRED -> BLS series map
BLS_FILES = ['cu/cu.data.0.Current', 'ce/ce.data.0.AllCESSeries', 'ln/ln.data.1.AllData', 'pc/pc.data.0.Current',
             'wp/wp.data.0.Current', 'ei/ei.data.0.Current', 'ci/ci.data.0.Current']
BLS_RELEASES = {50, 46, 188, 10, 11}          # FRED release ids whose source is BLS (Employment Situation, PPI, import/export prices, CPI, ECI)


def _bls_stream(path):
    """Yield (series_id, [(year, month_or_quarter_start, value)...]) per series from a BLS flat file (series are contiguous)."""
    cur, rows = None, []
    with open(path, encoding='utf8', errors='replace') as f:
        next(f)
        for ln in f:
            p = ln.rstrip('\n').split('\t')
            if len(p) < 4:
                continue
            sid = p[0].strip()
            per = p[2].strip()
            if per == 'M13' or per.startswith('S') or per == 'A01':
                continue
            try:
                m = int(per[1:]) if per[0] == 'M' else 3 * int(per[1:]) - 2 if per[0] == 'Q' else None
                v = float(p[3])
            except ValueError:
                continue
            if m is None:
                continue
            if sid != cur:
                if cur is not None:
                    yield cur, rows
                cur, rows = sid, []
            rows.append((int(p[1]), m, v))
    if cur is not None:
        yield cur, rows


def blsmap(inv):
    import os as _os
    ua = {'User-Agent': f'gdpnow-replication/1.0 ({BLS_CONTACT})'}
    k = f'&api_key={FRED_KEY}&file_type=json'
    ids = sorted(set(inv.get('fred') or []))
    # 1. which of our FRED series come from BLS releases, with their full histories
    fred_obs = {}
    for sid in ids:
        st, n, t, _, s = call('https://api.stlouisfed.org/fred/series/release?series_id=' + sid + k)
        try:
            rid = json.loads(t)['releases'][0]['id']
        except Exception:
            rid = None
        time.sleep(0.5)
        if rid in BLS_RELEASES:
            st, n, t, _, s = call('https://api.stlouisfed.org/fred/series/observations?series_id=' + sid + '&observation_start=1947-01-01' + k)
            d = json.loads(t)
            obs = {}
            for o in d.get('observations', []):
                if o['value'] not in ('.', ''):
                    y, m = int(o['date'][:4]), int(o['date'][5:7])
                    obs[(y, m)] = float(o['value'])
            fred_obs[sid] = obs
            time.sleep(0.5)
    out('BLS-release FRED series:', len(fred_obs), sorted(fred_obs))
    # 2. download the flat files (identifying User-Agent)
    _os.makedirs('data/bls', exist_ok=True)
    paths = []
    for f in BLS_FILES:
        t0 = time.time()
        req = urllib.request.Request('https://download.bls.gov/pub/time.series/' + f, headers=ua)
        dest = 'data/bls/' + f.split('/')[-1]
        with urllib.request.urlopen(req, timeout=900) as r, open(dest, 'wb') as fh:
            nb = 0
            while True:
                chunk = r.read(1 << 22)
                if not chunk:
                    break
                fh.write(chunk)
                nb += len(chunk)
        out('BLS download', f, 'MB', round(nb / 1e6, 1), 'secs', round(time.time() - t0, 1), 'last-modified', r.headers.get('Last-Modified'))
        paths.append(dest)
    # 3. index every BLS series by its last 12 observations
    index, full = {}, {}
    want = {sid.upper() for sid in fred_obs}
    for path in paths:
        t0 = time.time()
        n_series = 0
        for sid, rows in _bls_stream(path):
            n_series += 1
            if sid in want:
                full[sid] = {(y, m): v for y, m, v in rows}
            if len(rows) >= 12:
                key = tuple((y, m, round(v, 4)) for y, m, v in rows[-12:])
                index.setdefault(key, []).append((path.split('/')[-1][:2], sid))
        out('BLS parsed', path, 'series', n_series, 'secs', round(time.time() - t0, 1))
    # 4. map
    mapping, report = {}, []
    for fid, obs in sorted(fred_obs.items()):
        keys = sorted(obs)
        cands = []
        if len(keys) >= 12:
            key = tuple((y, m, round(obs[(y, m)], 4)) for y, m in keys[-12:])
            cands = index.get(key, [])
        direct = fid.upper() in full or any(fid.upper() == sid for _, sid in cands)
        mapping[fid] = cands
        report.append((fid, [c[1] for c in cands][:4], 'same-id' if any(fid.upper() == sid for _, sid in cands) else ''))
    unmatched = [fid for fid, c in mapping.items() if not c]
    ambiguous = {fid: [x[1] for x in c] for fid, c in mapping.items() if len(c) > 1}
    out('MAP matched uniquely:', sum(1 for c in mapping.values() if len(c) == 1), 'ambiguous:', len(ambiguous), 'unmatched:', len(unmatched))
    out('MAP unmatched FRED ids:', unmatched)
    out('MAP ambiguous (FRED -> candidates):', {k_: v[:6] for k_, v in ambiguous.items()})
    out('MAP table (unique):', {fid: c[0][1] for fid, c in mapping.items() if len(c) == 1})
    # 5. full-history comparison for the unique matches (needs the matched BLS series' full rows)
    need = {c[0][1] for c in mapping.values() if len(c) == 1}
    for path in paths:
        for sid, rows in _bls_stream(path):
            if sid in need and sid not in full:
                full[sid] = {(y, m): v for y, m, v in rows}
    for fid, c in sorted(mapping.items()):
        if len(c) != 1:
            continue
        b = full.get(c[0][1], {})
        f_ = fred_obs[fid]
        common = set(b) & set(f_)
        md = max((abs(b[x] - f_[x]) for x in common), default=None)
        only_f = sorted(set(f_) - set(b))
        only_b = sorted(set(b) - set(f_))
        out('MAPCHK', fid, '->', c[0][1], 'fred n', len(f_), 'bls n', len(b), 'common', len(common), 'max abs diff', md,
            '| only FRED', len(only_f), only_f[:2], '| only BLS', len(only_b), only_b[:2])


# ---------------------------------------------------------------- round 7: where does each mapped BLS series' full history live?
BLS_MAP = {'AWHMAN': 'CES3000000007', 'AWHNONAG': 'CES0500000007', 'CE16OV': 'LNS12000000', 'CES0800000001': 'CES0800000001', 'CES1021000001': 'CES1021000001', 'CES2023610001': 'CES2023610001', 'CES6054000001': 'CES6054000001', 'CES6054150001': 'CES6054150001', 'CES9091000001': 'CES9091000001', 'CES9092000001': 'CES9092000001', 'CES9093000001': 'CES9093000001', 'CLF16OV': 'LNS11000000', 'CNP16OV': 'LNU00000000', 'CPIAUCSL': 'CUSR0000SA0', 'CPIHOSSL': 'CUSR0000SAH', 'CPILFESL': 'CUSR0000SA0L1E', 'CUSR0000SAD': 'CUSR0000SAD', 'CUSR0000SAN': 'CUSR0000SAN', 'CUSR0000SASLE': 'CUSR0000SASLE', 'CUSR0000SETA01': 'CUSR0000SETA01', 'DMANEMP': 'CES3100000001', 'ECICONCOM': 'CIS2012300000000I', 'IQ': 'EIUIQ', 'IR': 'EIUIR', 'IR0': 'EIUIR0', 'IR10': 'EIUIR10', 'IR1DUR': 'EIUIR1DUR', 'IR1NONDUR': 'EIUIR1NONDUR', 'IR213COM': 'EIUIR213COM', 'IR2EXCOM': 'EIUIR2EXCOM', 'IR3': 'EIUIR3', 'IR4': 'EIUIR4', 'IREXPET': 'EIUIREXPET', 'LNS12035019': 'LNS12035019', 'LNS13023653': 'LNS13023653', 'LNS14000061': 'LNS14000061', 'MANEMP': 'CES3000000001', 'NDMANEMP': 'CES3200000001', 'PAYEMS': 'CES0000000001', 'PCU336411336411': 'PCU336411336411', 'PCU5312105312101': 'PCU5312105312101', 'PCUOMFGOMFG': 'PCUOMFG--OMFG--', 'PPIIDC': 'WPU03THRU15', 'UEMPMED': 'LNS13008276', 'UNEMPLOY': 'LNS13000000', 'USCONS': 'CES2000000001', 'USEHS': 'CES6500000001', 'USFIRE': 'CES5500000001', 'USGOOD': 'CES0600000001', 'USLAH': 'CES7000000001', 'USPBS': 'CES6000000001', 'USPRIV': 'CES0500000001', 'USSERV': 'CES8000000001', 'USTRADE': 'CES4200000001', 'USWTRADE': 'CES4142000001', 'WPSFD41312': 'WPSFD41312', 'WPSFD49207': 'WPSFD49207', 'WPSID61': 'WPSID61', 'WPSID61112': 'WPSID61112', 'WPSID61113': 'WPSID61113', 'WPSID6152': 'WPSID6152', 'WPSID69115': 'WPSID69115', 'WPU101706': 'WPU101706', 'WPUIP2321001': 'WPUIP2321001'}
BLS_AMBIGUOUS = {'AWOTMAN': ['CES3000000009', 'CES3100000009'], 'PCU236211236211': ['PCU236211236211', 'PCU236211236211P'], 'PCU236221236221': ['PCU236221236221', 'PCU236221236221P'], 'PCU236223236223': ['PCU236223236223', 'PCU236223236223P']}


def bls7():
    import re
    import os as _os
    ua = {'User-Agent': f'gdpnow-replication/1.0 ({BLS_CONTACT})'}
    k = f'&api_key={FRED_KEY}&file_type=json'
    base = 'https://download.bls.gov/pub/time.series/'
    targets = {}
    for fid, b in BLS_MAP.items():
        targets.setdefault(b, []).append(fid)
    for fid, cands in BLS_AMBIGUOUS.items():
        for b in cands:
            targets.setdefault(b, []).append(fid)
    # FRED history span per series
    span = {}
    for fid in sorted({f for fs in targets.values() for f in fs}):
        st, n, t, _, s = call('https://api.stlouisfed.org/fred/series?series_id=' + fid + k)
        try:
            d = json.loads(t)['seriess'][0]
            span[fid] = (d['observation_start'][:7], d['observation_end'][:7], d.get('frequency_short'))
        except Exception:
            span[fid] = None
        time.sleep(0.5)
    _os.makedirs('data/bls7', exist_ok=True)
    found = {}                                   # bls id -> list of (file, n, first, last)
    for sv in ('cu', 'wp', 'pc', 'ei', 'ci'):
        st, n, t, hdr, s = call(base + sv + '/', headers=ua, maxb=400000)
        files = [nm for nm in re.findall(r'<A HREF="[^"]+/([^/"]+)">', t) if '.data.' in nm]
        out('BLS7', sv, 'data files', len(files))
        for fn in files:
            dest = 'data/bls7/' + fn
            req = urllib.request.Request(base + sv + '/' + fn, headers=ua)
            with urllib.request.urlopen(req, timeout=900) as r, open(dest, 'wb') as fh:
                while True:
                    chunk = r.read(1 << 22)
                    if not chunk:
                        break
                    fh.write(chunk)
            for sid, rows in _bls_stream(dest):
                if sid in targets:
                    ym = sorted((y, m) for y, m, v in rows)
                    found.setdefault(sid, []).append((fn, len(ym), '%04d-%02d' % ym[0], '%04d-%02d' % ym[-1]))
            _os.remove(dest)
            time.sleep(0.3)
    # report
    for b, fids in sorted(targets.items()):
        fl = found.get(b, [])
        best = sorted(fl, key=lambda x: (x[2], -x[1]))[:3]
        out('FULLHIST', b, 'for FRED', fids, 'FRED span', span.get(fids[0]), '| files holding it:', [(f, n_, a, z) for f, n_, a, z in best])
    miss = [b for b in targets if b not in found and not b.startswith(('CES', 'LN', 'CIS')) or (b.startswith('CIS') and b not in found)]
    out('BLS ids not found in cu/wp/pc/ei/ci data files (expected for CES/LN which are in ce/ln):', sorted(miss))


# ---------------------------------------------------------------- round 8: the remaining unknowns for the BLS flat-file design
def bls8():
    import re
    import os as _os
    ua = {'User-Agent': f'gdpnow-replication/1.0 ({BLS_CONTACT})'}
    k = f'&api_key={FRED_KEY}&file_type=json'
    base = 'https://download.bls.gov/pub/time.series/'
    # (a) which cu file holds CUSR0000SEHK01 (we pull it from 1967 through the BLS API today)
    st, n, t, hdr, s = call(base + 'cu/', headers=ua, maxb=400000)
    files = [nm for nm in re.findall(r'<A HREF="[^"]+/([^/"]+)">', t) if '.data.' in nm]
    _os.makedirs('data/bls8', exist_ok=True)
    for fn in files:
        dest = 'data/bls8/' + fn
        req = urllib.request.Request(base + 'cu/' + fn, headers=ua)
        with urllib.request.urlopen(req, timeout=900) as r, open(dest, 'wb') as fh:
            while True:
                chunk = r.read(1 << 22)
                if not chunk:
                    break
                fh.write(chunk)
        for sid, rows in _bls_stream(dest):
            if sid == 'CUSR0000SEHK01':
                ym = sorted((y, m) for y, m, v in rows)
                out('SEHK01 in', fn, 'n', len(ym), '%04d-%02d' % ym[0], '%04d-%02d' % ym[-1])
        _os.remove(dest)
    # (b) AWOTMAN: compare both candidate CES series with FRED's full history
    st, n, t, _, s = call('https://api.stlouisfed.org/fred/series/observations?series_id=AWOTMAN&observation_start=1947-01-01' + k)
    fobs = {(int(o['date'][:4]), int(o['date'][5:7])): float(o['value']) for o in json.loads(t)['observations'] if o['value'] not in ('.', '')}
    req = urllib.request.Request(base + 'ce/ce.data.0.AllCESSeries', headers=ua)
    with urllib.request.urlopen(req, timeout=900) as r, open('data/bls8/ce.txt', 'wb') as fh:
        while True:
            chunk = r.read(1 << 22)
            if not chunk:
                break
            fh.write(chunk)
    want = {'CES3000000009', 'CES3100000009', 'CES9091911001', 'CES2023611806'}
    for sid, rows in _bls_stream('data/bls8/ce.txt'):
        if sid in want:
            b = {(y, m): v for y, m, v in rows}
            if sid.startswith('CES3'):
                common = set(b) & set(fobs)
                md = max((abs(b[x] - fobs[x]) for x in common), default=None)
                out('AWOTMAN candidate', sid, 'n', len(b), 'FRED n', len(fobs), 'common', len(common), 'max abs diff', md)
            else:
                ym = sorted(b)
                out('direct series in ce file', sid, 'n', len(b), '%04d-%02d' % ym[0], '%04d-%02d' % ym[-1])


# ---------------------------------------------------------------- round 9: BEA bulk download paths
def bea9():
    import re
    ua = {'User-Agent': 'Mozilla/5.0'}
    # (a) discover download links on BEA's own pages
    pages = ['https://www.bea.gov/data/gdp/gross-domestic-product', 'https://www.bea.gov/itable/national-gdp-and-personal-income',
             'https://www.bea.gov/data/personal-consumption-expenditures-price-index', 'https://www.bea.gov/data/income-saving/personal-income',
             'https://www.bea.gov/data/gdp/gdp-industry', 'https://www.bea.gov/resources/learning-center/what-to-know-nipas',
             'https://apps.bea.gov/national/', 'https://apps.bea.gov/iTable/?reqid=19&step=2', 'https://www.bea.gov/data/special-topics/national-income-and-product-accounts-underlying-detail-tables']
    links = {}
    for u in pages:
        st, n, t, hdr, s = call(u, headers=ua, maxb=2000000)
        found = set(re.findall(r'href="([^"]+\.(?:txt|zip|xlsx?|csv)[^"]*)"', t, flags=re.I)) | set(re.findall(r'href="([^"]*(?:Release|national/)[^"]*)"', t))
        out('BEA page', u, st, 'bytes', n, 'candidate links', len(found))
        for l in found:
            links[l] = u
    interesting = sorted(l for l in links if re.search(r'nipa|underlying|Release|TXT|ZIP|Section|register|all_?xls', l, flags=re.I))
    out('BEA download-like links:', interesting[:80])
    # (b) registers next to the bulk NIPA files
    base = 'https://apps.bea.gov/national/Release/TXT/'
    for fn in ('SeriesRegister.txt', 'TablesRegister.txt', 'NipaDataA.txt', 'NipaDataQ.txt', 'NipaDataM.txt', 'NIPAFiles.zip', 'UnderlyingDetail.zip',
               'NipaUnderlyingDetail.txt', 'DataFilesDescription.txt', 'readme.txt'):
        try:
            req = urllib.request.Request(base + fn, method='HEAD', headers=ua)
            with urllib.request.urlopen(req, timeout=60) as r:
                out('BEA HEAD', fn, r.status, {k: v for k, v in dict(r.headers).items() if k.lower() in ('last-modified', 'content-length')})
        except urllib.error.HTTPError as e:
            out('BEA HEAD', fn, e.code)
        except Exception as e:
            out('BEA HEAD', fn, 'ERR', str(e)[:60])
    for fn in ('SeriesRegister.txt', 'TablesRegister.txt'):
        st, n, t, hdr, s = call(base + fn, headers=ua, timeout=300)
        if st != 200:
            continue
        lines = t.splitlines()
        out('BEA', fn, 'bytes', n, 'lines', len(lines), 'header', lines[0][:160], 'first rows', [l[:140] for l in lines[1:4]])
        ours = ['T10105', 'T20804', 'T31003', 'T40205B', 'T50305', 'T70203B', 'U001B', 'U001BC', 'U002BUI', 'U20404', 'U20405', 'U50404', 'U50405', 'U50504', 'U50505', 'U50705BM3', 'U50706BM', 'U70205S']
        hit = {o: sum(1 for l in lines if o in l) for o in ours}
        out('BEA', fn, 'rows mentioning our tables:', hit)
        ut = sorted({m for l in lines for m in re.findall(r'\bU\d[0-9A-Z]{3,9}\b', l)})[:40]
        out('BEA', fn, 'U-style table ids present:', ut)


# ---------------------------------------------------------------- round 10: BEA's open-data page and what it links to
def bea10():
    import re
    ua = {'User-Agent': 'Mozilla/5.0'}
    pat_dl = re.compile(r'\.(zip|txt|csv|xlsx?|json|gz)(\?|$)', re.I)

    def links(u, maxb=3000000):
        st, n, t, hdr, s = call(u, headers=ua, maxb=maxb)
        hrefs = re.findall(r'href="([^"#]+)"', t)
        title = re.search(r'<title>([^<]*)</title>', t)
        return st, n, (title.group(1).strip() if title else ''), t, hrefs

    def absu(h, base):
        if h.startswith('//'):
            return 'https:' + h
        if h.startswith('/'):
            return re.match(r'https?://[^/]+', base).group(0) + h
        return h if h.startswith('http') else base.rstrip('/') + '/' + h
    root = 'https://www.bea.gov/open-data'
    st, n, title, t, hrefs = links(root)
    hrefs = sorted({absu(h, root) for h in hrefs})
    out('BEA open-data page', st, 'bytes', n, 'title', title, 'links', len(hrefs))
    dl = [h for h in hrefs if pat_dl.search(h)]
    out('BEA open-data direct file links:', dl[:60])
    kids = [h for h in hrefs if re.search(r'open-data|/data/|dataset|download|apps\.bea\.gov', h, re.I)]
    out('BEA open-data child links (first 60):', kids[:60])
    seen = set()
    for k in kids:
        if len(seen) >= 14 or k in seen or k == root or not k.startswith('http'):
            continue
        seen.add(k)
        st2, n2, title2, t2, h2 = links(k, 1500000)
        h2 = sorted({absu(h, k) for h in h2})
        d2 = [h for h in h2 if pat_dl.search(h)]
        out('BEA child', k, st2, title2[:60], 'file links:', len(d2), d2[:12])
        time.sleep(0.5)
    # candidate register names (user-provided and ours)
    base = 'https://apps.bea.gov/national/Release/TXT/'
    for fn in ('NIPASeriesRegister.txt', 'NIPATablesRegister.txt', 'SeriesRegister.txt', 'TablesRegister.txt'):
        try:
            req = urllib.request.Request(base + fn, method='HEAD', headers=ua)
            with urllib.request.urlopen(req, timeout=60) as r:
                out('BEA HEAD', fn, r.status, {k: v for k, v in dict(r.headers).items() if k.lower() in ('last-modified', 'content-length')})
        except urllib.error.HTTPError as e:
            out('BEA HEAD', fn, e.code)
        except Exception as e:
            out('BEA HEAD', fn, 'ERR', str(e)[:60])


# ---------------------------------------------------------------- round 11: do the bulk NIPA files cover and match our 33 BEA tables?
def bea11():
    import csv
    import io
    import re
    ua = {'User-Agent': 'Mozilla/5.0'}
    base = 'https://apps.bea.gov/national/Release/TXT/'
    # (a) the open-data catalog
    st, n, t, hdr, s = call('https://apps.bea.gov/Data.json', headers=ua, maxb=5000000)
    try:
        d = json.loads(t)
        ds = d.get('dataset', d if isinstance(d, list) else [])
        out('Data.json top keys', list(d)[:10] if isinstance(d, dict) else 'list', 'datasets', len(ds))
        for x in ds[:60]:
            ttl = x.get('title', '')
            dist = [(y.get('title') or y.get('format') or '', y.get('downloadURL') or y.get('accessURL') or '') for y in x.get('distribution', [])]
            if re.search(r'NIPA|national income|underlying|GDP|personal income', ttl, re.I):
                out('Data.json', ttl[:80], dist[:4])
        out('Data.json titles (first 40):', [x.get('title', '')[:50] for x in ds[:40]])
    except Exception as e:
        out('Data.json parse error', repr(e)[:100], short(t, 200))
    # (b) register: (table, line) -> series code
    st, n, t, hdr, s = call(base + 'SeriesRegister.txt', headers=ua, timeout=300)
    reg = {}
    for row in csv.reader(io.StringIO(t)):
        if not row or row[0].startswith('%') or len(row) < 6:
            continue
        for tl in row[5].split('|'):
            if ':' in tl:
                tb, ln = tl.split(':', 1)
                reg.setdefault(tb, {})[ln] = row[0]
    out('register tables', len(reg))
    tables = [('NIPA', 'T10103', 'Q'), ('NIPA', 'T10105', 'Q'), ('NIPA', 'T10106', 'Q'), ('NIPA', 'T20804', 'M'), ('NIPA', 'T20805', 'M'),
              ('NIPA', 'T30903', 'Q'), ('NIPA', 'T30905', 'Q'), ('NIPA', 'T31003', 'Q'), ('NIPA', 'T31005', 'Q'), ('NIPA', 'T31006', 'Q'),
              ('NIPA', 'T31103', 'Q'), ('NIPA', 'T31105', 'Q'), ('NIPA', 'T40205B', 'Q'), ('NIPA', 'T50303', 'Q'), ('NIPA', 'T50305', 'Q'),
              ('NIPA', 'T50805B', 'Q'), ('NIPA', 'T50806B', 'Q'), ('NIPA', 'T50809A', 'Q'), ('NIPA', 'T50809B', 'Q'), ('NIPA', 'T70203B', 'Q'),
              ('NIPA', 'T70205B', 'Q'), ('NIUnderlyingDetail', 'U001B', 'M'), ('NIUnderlyingDetail', 'U001BC', 'M'), ('NIUnderlyingDetail', 'U002BUI', 'M'),
              ('NIUnderlyingDetail', 'U20404', 'M'), ('NIUnderlyingDetail', 'U20405', 'M'), ('NIUnderlyingDetail', 'U50404', 'Q'),
              ('NIUnderlyingDetail', 'U50405', 'Q'), ('NIUnderlyingDetail', 'U50504', 'Q'), ('NIUnderlyingDetail', 'U50505', 'Q'),
              ('NIUnderlyingDetail', 'U50705BM3', 'M'), ('NIUnderlyingDetail', 'U50706BM', 'M'), ('NIUnderlyingDetail', 'U70205S', 'M')]
    need = {fr: set() for fr in 'QMA'}
    for ds_, tb, fr in tables:
        need[fr] |= set(reg.get(tb, {}).values())
    # (c) bulk files: keep only the series codes we need
    bulk = {}
    for fr, fn in (('Q', 'NipaDataQ.txt'), ('M', 'NipaDataM.txt')):
        t0 = time.time()
        st, n, t, hdr, s = call(base + fn, headers=ua, timeout=600)
        got = {}
        for row in csv.reader(io.StringIO(t)):
            if len(row) == 3 and row[0] in need[fr]:
                try:
                    got.setdefault(row[0], {})[row[1]] = float(row[2].replace(',', ''))
                except ValueError:
                    pass
        bulk[fr] = got
        out('bulk', fn, 'MB', round(n / 1e6, 1), 'secs', round(time.time() - t0, 1), 'needed codes', len(need[fr]), 'found', len(got), 'last-modified', hdr.get('Last-Modified'))
    # (d) coverage per table
    for ds_, tb, fr in tables:
        codes = set(reg.get(tb, {}).values())
        have = sum(1 for c in codes if c in bulk[fr])
        out('COVER', tb, fr, 'register lines', len(reg.get(tb, {})), 'codes', len(codes), 'in bulk file', have)
    # (e) value equality with the API for a few tables (API rows carry SeriesCode?)
    apibase = 'https://apps.bea.gov/api/data?UserID=' + BEA_KEY + '&method=GetData&ResultFormat=JSON'
    for ds_, tb, fr in (('NIPA', 'T10105', 'Q'), ('NIPA', 'T31003', 'Q'), ('NIUnderlyingDetail', 'U50505', 'Q'), ('NIUnderlyingDetail', 'U001B', 'M'), ('NIUnderlyingDetail', 'U50706BM', 'M')):
        st, n, t, _, s = call(apibase + f'&DataSetName={ds_}&TableName={tb}&Frequency={fr}&Year=ALL', timeout=300)
        try:
            rows = json.loads(t)['BEAAPI']['Results']['Data']
        except Exception:
            out('EQUAL', tb, 'API error', short(t, 120))
            continue
        out('API row keys', tb, sorted(rows[0].keys()))
        n_cmp = n_diff = n_api_only = n_bulk_only = 0
        maxd = 0.0
        api_codes = set()
        seen = set()
        for r in rows:
            code = r.get('SeriesCode') or reg.get(tb, {}).get(r['LineNumber'])
            api_codes.add(code)
            try:
                v = float(r['DataValue'].replace(',', ''))
            except ValueError:
                continue
            seen.add((code, r['TimePeriod']))
            b = bulk[fr].get(code, {}).get(r['TimePeriod'])
            if b is None:
                n_api_only += 1
            else:
                n_cmp += 1
                d_ = abs(b - v)
                if d_ > 1e-6 * max(1.0, abs(v)):
                    n_diff += 1
                maxd = max(maxd, d_)
        for c in api_codes:
            n_bulk_only += sum(1 for p_ in bulk[fr].get(c, {}) if (c, p_) not in seen)
        out('EQUAL', tb, fr, 'API rows', len(rows), 'compared', n_cmp, 'differ', n_diff, 'max abs diff', maxd, 'only API', n_api_only, 'only bulk', n_bulk_only,
            'API SeriesCode == register code:', sum(1 for r in rows if r.get('SeriesCode') and r['SeriesCode'] == reg.get(tb, {}).get(r['LineNumber'])), 'of', sum(1 for r in rows if r.get('SeriesCode')))
        time.sleep(2)


def _head(url, ua=None):
    try:
        req = urllib.request.Request(url, method='HEAD', headers={'User-Agent': ua or 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as r:
            h = {k.lower(): v for k, v in dict(r.headers).items()}
            return r.status, {k: h[k] for k in ('last-modified', 'content-length', 'content-type', 'etag') if k in h}
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as ex:
        return 'ERR', {'err': str(ex)[:80]}


def census12():
    """Census: unfiltered EITS per dataset (and equality with today's 14 filtered calls), real download links on census.gov pages,
    candidate bulk-file URLs (with a made-up control name to spot soft-404s), calendar endpoints."""
    import re
    base = 'https://api.census.gov/data/timeseries/eits/'
    k = f'&key={CENSUS_KEY}'
    specs = [('advm3', 'MDM', 'TI'), ('advm3', 'MDM', 'VS'), ('advm3', 'MDM', 'NO'), ('advm3', 'NXA', 'VS'), ('advm3', 'DEF', 'VS'),
             ('advm3', 'NAP', 'VS'), ('m3', 'MNM', 'TI'), ('m3', 'MNM', 'VS'), ('m3', 'MTM', 'VS'), ('m3', 'MTM', 'TI'),
             ('mwtsadv', '42', 'IM'), ('mrts', '4400A', 'IM'), ('mrtsadv', '4400A', 'IM'), ('ftdadv', 'CBG', 'EXP')]
    # (a) unfiltered per dataset
    for ds in dict.fromkeys(d for d, _, _ in specs):
        url = base + ds + '?get=cell_value,time_slot_id,category_code,data_type_code,seasonally_adj&time=from+1992&for=us:*' + k
        st, n, t, _, secs = call(url, timeout=300)
        try:
            d = json.loads(t)
        except Exception:
            out('C12 unfiltered', ds, 'http', st, 'bytes', n, 'secs', secs, '|', short(t, 200))
            continue
        hdr, rows = d[0], d[1:]
        ix = {c: hdr.index(c) for c in hdr}
        out('C12 unfiltered', ds, 'http', st, 'bytes', n, 'rows', len(rows), 'secs', secs, 'header', hdr,
            'categories', len({r[ix['category_code']] for r in rows}), 'data_types', sorted({r[ix['data_type_code']] for r in rows})[:30],
            'sa values', sorted({r[ix['seasonally_adj']] for r in rows}))
        for dsx, cat, dt in specs:
            if dsx != ds:
                continue
            sub = {r[ix['time']]: r[ix['cell_value']] for r in rows if r[ix['category_code']] == cat and r[ix['data_type_code']] == dt
                   and r[ix['seasonally_adj']].lower() in ('yes', 'y', 'true')}
            st2, n2, t2, _, _ = call(base + f'{ds}?get=cell_value,time_slot_id&category_code={cat}&data_type_code={dt}&seasonally_adj=yes&time=from+1992&for=us:*' + k, timeout=300)
            try:
                d2 = json.loads(t2)
                h2 = d2[0]
                old = {r[h2.index('time')]: r[h2.index('cell_value')] for r in d2[1:]}
            except Exception:
                out('C12 equal', ds, cat, dt, 'filtered call failed', st2, short(t2, 120))
                continue
            diff = sum(1 for kk, v in old.items() if sub.get(kk) != v)
            out('C12 equal', ds, cat, dt, 'filtered rows', len(old), 'unfiltered subset', len(sub), 'differ/missing', diff, 'only unfiltered', len(set(sub) - set(old)))
            time.sleep(0.5)
    # (b) links on census.gov pages
    pages = ['https://www.census.gov/econ/currentdata/', 'https://www.census.gov/econ/currentdata/datasets/', 'https://www.census.gov/econ/currentdata/dbsearch',
             'https://www.census.gov/construction/c30/historical_data/', 'https://www.census.gov/construction/c30/c30index.html',
             'https://www.census.gov/construction/nrc/historical_data/', 'https://www.census.gov/construction/nrs/historical_data/',
             'https://www.census.gov/economic-indicators/', 'https://www.census.gov/data/developers/data-sets/economic-indicators.html']
    for u in pages:
        st, n, t, hdr, secs = call(u, maxb=3000000)
        links = sorted(set(re.findall(r'href="([^"]+\.(?:csv|zip|xlsx?|txt|json|ics)[^"]*)"', t, flags=re.I)))
        out('C12 page', u, st, 'bytes', n, 'file links', len(links), links[:40])
    # (c) candidate URLs vs a made-up control
    cands = ['https://www.census.gov/econ/currentdata/datasets/m3.zip', 'https://www.census.gov/econ/currentdata/datasets/m3.csv',
             'https://www.census.gov/econ/currentdata/datasets/mrts.zip', 'https://www.census.gov/econ/currentdata/datasets/mwts.zip',
             'https://www.census.gov/econ/currentdata/datasets/ftd.zip', 'https://www.census.gov/econ/currentdata/datasets/zzz_control_nonexistent.zip',
             'https://www.census.gov/construction/c30/csv/total.csv', 'https://www.census.gov/construction/c30/xlsx/total.xlsx',
             'https://www.census.gov/construction/nrc/csv/starts_cust.csv', 'https://www.census.gov/construction/nrc/xls/starts_cust.xls',
             'https://www.census.gov/construction/nrc/csv/permits_cust.csv', 'https://www.census.gov/construction/nrc/xls/permits_cust.xls',
             'https://www.census.gov/construction/nrs/xls/price_uc_cust.xlsx', 'https://www.census.gov/construction/zzz_control_nonexistent.csv']
    for u in cands:
        out('C12 HEAD', u, *_head(u))
    # (d) calendar endpoints
    for u in ('https://api.census.gov/data/economic/indicators/calendar', 'https://www.census.gov/economic-indicators/calendar.json',
              'https://www.census.gov/economic-indicators/calendar-listview.html'):
        st, n, t, hdr, secs = call(u, maxb=500000)
        out('C12 calendar', u, st, 'bytes', n, 'ctype', hdr.get('Content-Type'), '|', short(t, 220))


def census13():
    """Census bulk program zips from https://www.census.gov/econ_datasets/ (programCode links): the page's size/Last Updated table,
    HEAD on each zip, zip contents and layout, and equality of the M3ADV/M3/MWTSADV/MRTS/MRTSADV/FTDADV contents with the EITS API."""
    import io
    import re
    import zipfile
    page = 'https://www.census.gov/econ_datasets/'
    st, n, t, hdr, secs = call(page, maxb=3000000)
    rows = re.findall(r'<tr>\s*<td>(.*?)</td>\s*<td><a href="([^"]+programCode=(\w+))[^"]*"[^>]*>([^<]+)</a></td>\s*<td[^>]*>([^<]+)</td>\s*<td>([^<]+)</td>', t, flags=re.S)
    out('C13 page', page, st, 'bytes', n, 'rows parsed', len(rows), 'last-modified header', hdr.get('Last-Modified'), 'ctype', hdr.get('Content-Type'))
    for name, url, code, fn, size, upd in rows:
        out('C13 listing', code, '|', ' '.join(name.split()), '|', fn, size, upd)
    want = ['M3ADV', 'M3', 'MWTSADV', 'MRTS', 'MRTSADV', 'FTDADV', 'VIP', 'RESCONST', 'RESSALES', 'MARTS', 'MWTS']
    blobs = {}
    for code in want:
        url = f'https://www.census.gov/econ_getzippedfile/?programCode={code}'
        out('C13 HEAD', code, *_head(url))
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read()
                h = {k.lower(): v for k, v in dict(r.headers).items()}
            out('C13 GET', code, 'status', r.status, 'bytes', len(body), 'secs', round(time.time() - t0, 1),
                {k: h[k] for k in ('last-modified', 'content-type', 'content-disposition', 'etag') if k in h}, 'magic', body[:4])
        except Exception as e:
            out('C13 GET', code, 'ERR', str(e)[:120])
            continue
        try:
            z = zipfile.ZipFile(io.BytesIO(body))
        except Exception as e:
            out('C13 zip', code, 'not a zip', str(e)[:80], short(body[:200].decode('utf8', 'replace'), 150))
            continue
        blobs[code] = z
        for zi in z.infolist():
            with z.open(zi) as f:
                head = f.read(700).decode('utf8', 'replace')
            out('C13 member', code, zi.filename, 'size', zi.file_size, 'date', zi.date_time, '|', short(head, 420))
        time.sleep(1)
    # layout-agnostic equality: find the member that carries values, compare to the EITS API
    import pandas as pd
    base = 'https://api.census.gov/data/timeseries/eits/'
    k = f'&key={CENSUS_KEY}'
    specs = [('M3ADV', 'advm3', 'MDM', 'TI'), ('M3ADV', 'advm3', 'NXA', 'VS'), ('M3', 'm3', 'MNM', 'TI'), ('M3', 'm3', 'MTM', 'VS'),
             ('MWTSADV', 'mwtsadv', '42', 'IM'), ('MRTS', 'mrts', '4400A', 'IM'), ('MRTSADV', 'mrtsadv', '4400A', 'IM'), ('FTDADV', 'ftdadv', 'CBG', 'EXP')]
    for code, ds, cat, dt in specs:
        z = blobs.get(code)
        if z is None:
            continue
        for zi in z.infolist():
            if not zi.filename.lower().endswith(('.csv', '.txt')):
                continue
            try:
                df = pd.read_csv(z.open(zi), dtype=str)
            except Exception as e:
                out('C13 parse', code, zi.filename, 'ERR', str(e)[:80])
                continue
            out('C13 columns', code, zi.filename, 'rows', len(df), list(df.columns)[:14])
            cols = {c.lower(): c for c in df.columns}
            cc = next((cols[c] for c in cols if 'cat' in c and 'code' in c), None)
            dc = next((cols[c] for c in cols if 'data' in c and 'type' in c and 'code' in c), None)
            sc = next((cols[c] for c in cols if 'seas' in c), None)
            vc = next((cols[c] for c in cols if c in ('cell_value', 'val', 'value')), None)
            tc = next((cols[c] for c in cols if c in ('per_name', 'time', 'period', 'per_idx', 'date')), None)
            out('C13 guess', code, 'cat', cc, 'dt', dc, 'sa', sc, 'val', vc, 'time', tc)
            break


def census14():
    """Census bulk zip layout: full README, section titles and first rows of each section in M3ADV and RESCONST."""
    import io
    import zipfile
    for code in ('M3ADV', 'RESCONST'):
        req = urllib.request.Request(f'https://www.census.gov/econ_getzippedfile/?programCode={code}', headers={'User-Agent': 'Mozilla/5.0'})
        z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(req, timeout=300).read()))
        if code == 'M3ADV':
            for i, line in enumerate(z.read('/README').decode('utf8', 'replace').splitlines()):
                out('C14 README', i, line[:230])
        txt = z.read(f'{code}-mf.csv').decode('utf8', 'replace').splitlines()
        out('C14 lines', code, len(txt))
        titles = [i for i, l in enumerate(txt) if l.strip() and l == l.upper() and ',' not in l and not l[0].isdigit() and len(l) < 40]
        out('C14 titles', code, [(i, txt[i]) for i in titles])
        for i in titles:
            out('C14 section', code, txt[i], 'line', i, [txt[j][:170] for j in range(i + 1, min(i + 4, len(txt)))])
        data_at = [i for i in titles if txt[i].startswith('DATA') and 'TYPES' not in txt[i]]
        for i in data_at[:1]:
            out('C14 data block head', code, [txt[j][:200] for j in range(i, min(i + 6, len(txt)))])


def _census_zip(code):
    import io
    import zipfile
    req = urllib.request.Request(f'https://www.census.gov/econ_getzippedfile/?programCode={code}', headers={'User-Agent': 'Mozilla/5.0'})
    return zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(req, timeout=300).read()))


def _census_parse(code):
    """Section-aware parse of a programCode zip -> (frame with codes, updated-on string)."""
    import csv
    import io
    import pandas as pd
    lines = _census_zip(code).read(f'{code}-mf.csv').decode('utf8', 'replace').splitlines()
    secs, cur = {}, None
    for l in lines:
        if l.strip() and l == l.upper() and ',' not in l and not l[0].isdigit() and len(l) < 40:
            cur = l.strip()
            secs[cur] = []
        elif cur is not None:
            secs[cur].append(l)

    def tab(name):
        rows = [r for r in csv.reader(secs.get(name, [])) if r]
        return pd.DataFrame(rows[1:], columns=rows[0]) if rows else None
    cats, dts, pers, geos = tab('CATEGORIES'), tab('DATA TYPES'), tab('TIME PERIODS'), tab('GEO LEVELS')
    data = pd.read_csv(io.StringIO('\n'.join(secs['DATA'])))
    d = data.merge(cats[['cat_idx', 'cat_code']].astype({'cat_idx': int}), on='cat_idx')
    if 'et_idx' in d:
        d = d[d.et_idx == 0]
    d = d.merge(dts[['dt_idx', 'dt_code']].astype({'dt_idx': int}), on='dt_idx')
    d = d.merge(pers.astype({'per_idx': int}), on='per_idx').merge(geos[['geo_idx', 'geo_code']].astype({'geo_idx': int}), on='geo_idx')
    d['date'] = pd.to_datetime(d.per_name, format='%b-%Y', errors='coerce') + pd.offsets.MonthEnd(0)
    d['val'] = pd.to_numeric(d.val, errors='coerce')
    return d, ' '.join(secs.get('DATA UPDATED ON', [''])[:1]), cats, dts


def census15():
    """(1) values of the 14 EITS series in the bulk zips vs the API; (2) code tables of VIP/RESCONST/RESSALES and comparison of
    candidate cells with FRED (HOUST, HOUST1F, HSN1F, ASPNHSUS, TLPBLCONS, PNRESCONS); (3) non-PDF sources of the AEI end-use table;
    (4) change signals (HEAD) for BEA's IDS-0182 zips and trade workbook."""
    import re
    import pandas as pd
    base = 'https://api.census.gov/data/timeseries/eits/'
    k = f'&key={CENSUS_KEY}'
    specs = [('M3ADV', 'advm3', 'MDM', 'TI'), ('M3ADV', 'advm3', 'MDM', 'VS'), ('M3ADV', 'advm3', 'MDM', 'NO'), ('M3ADV', 'advm3', 'NXA', 'VS'),
             ('M3ADV', 'advm3', 'DEF', 'VS'), ('M3ADV', 'advm3', 'NAP', 'VS'), ('M3', 'm3', 'MNM', 'TI'), ('M3', 'm3', 'MNM', 'VS'),
             ('M3', 'm3', 'MTM', 'VS'), ('M3', 'm3', 'MTM', 'TI'), ('MWTSADV', 'mwtsadv', '42', 'IM'), ('MRTS', 'mrts', '4400A', 'IM'),
             ('MRTSADV', 'mrtsadv', '4400A', 'IM'), ('FTDADV', 'ftdadv', 'CBG', 'EXP')]
    cache = {}
    for code, ds, cat, dt in specs:
        if code not in cache:
            cache[code] = _census_parse(code)
            out('C15 parsed', code, 'rows', len(cache[code][0]), 'updated on', cache[code][1])
        d = cache[code][0]
        bulk = d[(d.cat_code == cat) & (d.dt_code == dt) & (d.is_adj == 1) & (d.geo_code == 'US')].set_index('date').val
        st, n, t, _, _ = call(base + f'{ds}?get=cell_value,time_slot_id&category_code={cat}&data_type_code={dt}&seasonally_adj=yes&time=from+1992&for=us:*' + k, timeout=300)
        try:
            j = json.loads(t)
            h = j[0]
            api = {pd.Period(r[h.index('time')], 'M').end_time.normalize(): float(r[h.index('cell_value')]) for r in j[1:] if r[h.index('cell_value')] not in ('', '(S)')}
        except Exception:
            out('C15 equal', code, cat, dt, 'API failed', st, short(t, 100))
            continue
        b = {ts.normalize(): v for ts, v in bulk.items() if pd.notna(v)}
        diff = [kk for kk, v in api.items() if kk not in b or abs(b[kk] - v) > 1e-9 * max(1, abs(v))]
        out('C15 equal', code, cat, dt, 'API', len(api), 'bulk', len(b), 'differ/missing in bulk', len(diff), 'only bulk', len(set(b) - set(api)), 'last', max(api), api[max(api)], b.get(max(api)))
        time.sleep(0.5)
    # (2) housing/construction mapping
    fred_ids = {'HOUST': ('RESCONST', 'ASTARTS', 'TOTAL'), 'HOUST1F': ('RESCONST', 'ASTARTS', 'SINGLE'), 'HSN1F': ('RESSALES', 'ASOLD', 'TOTAL'),
                'TLPBLCONS': ('VIP', None, None), 'PNRESCONS': ('VIP', None, None), 'ASPNHSUS': ('RESSALES', None, None)}
    for code in ('RESCONST', 'RESSALES', 'VIP'):
        if code not in cache:
            cache[code] = _census_parse(code)
        d, upd, cats, dts = cache[code]
        out('C15 codes', code, 'updated', upd, 'rows', len(d), 'categories', list(cats.cat_code)[:70], 'data types', list(dts.dt_code)[:40], 'geos', sorted(d.geo_code.unique())[:12], 'is_adj', sorted(d.is_adj.unique()))
    def fred_series(fid):
        st, n, t, _, _ = call(f'https://api.stlouisfed.org/fred/series/observations?series_id={fid}&api_key={FRED_KEY}&file_type=json&observation_start=2024-01-01', timeout=120)
        j = json.loads(t)
        return {pd.Timestamp(o['date']) + pd.offsets.MonthEnd(0): float(o['value']) for o in j['observations'] if o['value'] != '.'}
    for fid, (code, cat, dt) in fred_ids.items():
        f = fred_series(fid)
        d = cache[code][0]
        best = []
        recent = d[d.date >= '2024-01-31']
        for (c_, t_, a_, g_), g in recent.groupby(['cat_code', 'dt_code', 'is_adj', 'geo_code']):
            s = g.set_index('date').val
            common = [x for x in f if x in s.index]
            if len(common) >= 8:
                err = max(abs(s[x] - f[x]) / max(1, abs(f[x])) for x in common)
                best.append((round(err, 6), c_, t_, a_, g_, len(common)))
        best.sort()
        out('C15 FRED match', fid, 'FRED last', max(f), f[max(f)], 'best bulk cells (maxrel err, cat, dt, is_adj, geo, n)', best[:4])
    # (3) non-PDF AEI sources
    pages = ['https://www.census.gov/foreign-trade/Press-Release/current_press_release/index.html', 'https://www.census.gov/economic-indicators/',
             'https://www.census.gov/foreign-trade/statistics/historical/index.html', 'https://www.census.gov/foreign-trade/data/index.html',
             'https://www.census.gov/foreign-trade/Press-Release/current_press_release/exh1.pdf']
    for u in pages:
        st, n, t, hdr, _ = call(u, maxb=3000000)
        links = sorted(set(re.findall(r'href="([^"]*(?:advance|exh|ftd|enduse|\.csv|\.xlsx?|\.zip|\.txt|\.json)[^"]*)"', t, flags=re.I)))
        out('C15 AEI page', u, st, 'bytes', n, 'ctype', hdr.get('Content-Type'), 'links', len(links), links[:25])
    st, n, t, _, _ = call(base + 'ftd?get=cell_value&category_code=*&data_type_code=*&time=2026-07&for=us:*' + k)
    out('C15 eits ftd', st, short(t, 300))
    st, n, t, _, _ = call('https://api.census.gov/data/timeseries/eits.json')
    out('C15 eits catalog', st, n, short(t, 200))
    # (4) BEA change signals
    for u in ('https://apps.bea.gov/international/zip/IDS0182.zip', 'https://apps.bea.gov/international/zip/IDS0182-Hist.zip',
              'https://apps.bea.gov/international/zip/zzz_control_nonexistent.zip'):
        out('C15 BEA HEAD', u, *_head(u))
    st, n, t, _, _ = call('https://www.bea.gov/data/intl-trade-investment/international-trade-goods-and-services', headers={'User-Agent': 'Mozilla/5.0'}, maxb=3000000)
    links = sorted(set(re.findall(r'href="([^"]*time-series[^"]*\.xlsx)"', t)))
    out('C15 BEA trade page', st, 'links', links)
    for l in links[:3]:
        out('C15 BEA HEAD', l, *_head('https://www.bea.gov' + l))


if __name__ == '__main__':
    which = sys.argv[1:] or ['inventory', 'fred', 'bea', 'census', 'bls']
    if 'inventory' not in which and any(w.endswith('2') or w in ('heads', 'fred4', 'blsmap') for w in which):
        which = ['inventory'] + which
    inv = inventory() if 'inventory' in which else {}
    for name, fn in (('fred', lambda: fred(inv)), ('bea', lambda: bea(inv)), ('census', census), ('bls', bls),
                     ('fred2', lambda: fred2(inv)), ('bea2', lambda: bea2(inv)), ('census2', census2), ('heads', lambda: heads(inv)),
                     ('bls3', bls3), ('bea3', bea3), ('fred3', fred3), ('bea4', bea4), ('fred4', fred4), ('bls5', bls5), ('blsmap', lambda: blsmap(inv)), ('bls7', bls7), ('bls8', bls8), ('bea9', bea9), ('bea10', bea10), ('bea11', bea11), ('census12', census12), ('census13', census13), ('census14', census14), ('census15', census15)):
        if name in which:
            try:
                fn()
            except Exception as e:
                out(name, 'EXPLORER ERROR', repr(e)[:200])
