"""Which data release does an archived series belong to? Labels used to group data changes between two daily runs.

FRED series carry their release name in FRED itself (series/release; cached in the DuckDB table fred_release).
Everything else is labelled by its source (BEA table, Census dataset, BLS, Treasury, downloaded files).
"""
import json
import os
import urllib.parse
import urllib.request

import pandas as pd

from .config import load_env

load_env()

CENSUS_DATASETS = {
    'advm3': 'Advance Durable Goods (Census)', 'm3': "Manufacturers' Shipments, Inventories and Orders (Census)",
    'mrts': 'Monthly Retail Trade (Census)', 'marts': 'Advance Retail Sales (Census)',
    'mrtsadv': 'Advance Retail Inventories (Census)', 'mwtsadv': 'Advance Wholesale Inventories (Census)',
    'mwts': 'Monthly Wholesale Trade (Census)', 'ftdadv': 'Advance International Trade in Goods (Census)',
    'ftd': 'International Trade in Goods (Census)', 'vip': 'Construction Spending (Census)',
}
# BEA table prefixes -> release (monthly consumption and inventory detail; everything quarterly is the GDP release)
BEA_MONTHLY = {'U20404': 'Personal Income and Outlays (BEA)', 'U20405': 'Personal Income and Outlays (BEA)',
               'U20406': 'Personal Income and Outlays (BEA)', 'T20804': 'Personal Income and Outlays (BEA)',
               'T20805': 'Personal Income and Outlays (BEA)', 'T20806': 'Personal Income and Outlays (BEA)',
               'U001A': 'Inventories and Sales (BEA monthly detail)', 'U001B': 'Inventories and Sales (BEA monthly detail)',
               'U001BC': 'Inventories and Sales (BEA monthly detail)', 'U002BU': 'Inventories and Sales (BEA monthly detail)',
               'U002BUI': 'Inventories and Sales (BEA monthly detail)', 'U50705BM1': 'Inventories and Sales (BEA monthly detail)',
               'U50705BM2': 'Inventories and Sales (BEA monthly detail)', 'U50705BM3': 'Inventories and Sales (BEA monthly detail)',
               'U50706BM': 'Inventories and Sales (BEA monthly detail)', 'U70205S': 'Light-Vehicle Sales (BEA)'}
FIXED = {'bls': 'Employment (BLS)', 'treasury': 'Monthly Treasury Statement', 'bea_ita': 'International Transactions (BEA)',
         'census_hist': 'Construction / housing price files (Census)'}


def _fred_release(con, sid):
    con.execute('CREATE TABLE IF NOT EXISTS fred_release (series_id VARCHAR, release VARCHAR)')
    r = con.execute('SELECT release FROM fred_release WHERE series_id = ?', [sid]).fetchone()
    if r:
        return r[0]
    name = 'FRED (release unknown)'
    try:
        q = dict(series_id=sid, api_key=os.environ['FRED_API_KEY'], file_type='json')
        d = json.load(urllib.request.urlopen('https://api.stlouisfed.org/fred/series/release?' + urllib.parse.urlencode(q), timeout=30))
        name = d['releases'][0]['name']
    except Exception:
        pass
    con.execute('INSERT INTO fred_release VALUES (?, ?)', [sid, name])
    return name


def label(con, source, series):
    """Release label for an archived pull (`source`, `series` as stored in raw_pulls)."""
    if source == 'fred':
        return _fred_release(con, series)
    if source.startswith('census_eits:'):
        return CENSUS_DATASETS.get(source.split(':')[1], 'Census economic indicators')
    if source == 'bea':
        t = series.split('|')[0].split(':')[1] if ':' in series.split('|')[0] else ''
        freq = series.split('|')[0].split(':')[-1]
        return BEA_MONTHLY.get(t) or ('GDP and underlying detail (BEA)' if freq == 'Q' else 'BEA monthly detail')
    return FIXED.get(source, source)


def changes(con, asof0, asof1, tol=1e-9):
    """Raw pulls that differ between two as-of dates: per series, count of new observations and of revised values,
    with the release label. Returns a DataFrame (source, series, release, n_new, n_rev, last_new)."""
    q = """
    WITH a AS (SELECT source, series, date, value FROM raw_pulls WHERE as_of = ?),
         b AS (SELECT source, series, date, value FROM raw_pulls WHERE as_of = ?)
    SELECT b.source, b.series,
           sum(CASE WHEN a.value IS NULL THEN 1 ELSE 0 END) AS n_new,
           sum(CASE WHEN a.value IS NOT NULL AND abs(a.value - b.value) > ? * greatest(1, abs(a.value)) THEN 1 ELSE 0 END) AS n_rev,
           max(CASE WHEN a.value IS NULL THEN b.date END) AS last_new
    FROM b LEFT JOIN a USING (source, series, date)
    GROUP BY 1, 2 HAVING n_new > 0 OR n_rev > 0"""
    d = con.execute(q, [asof0, asof1, tol]).fetchdf()
    # series that exist only on one day (new pulls) carry no comparison
    have0 = set(r[0] for r in con.execute('SELECT DISTINCT source || chr(1) || series FROM raw_pulls WHERE as_of = ?', [asof0]).fetchall())
    d = d[pd.Series([f'{s}\x01{k}' in have0 for s, k in zip(d.source, d.series)], index=d.index, dtype=bool)].copy()
    d['release'] = [label(con, s, k) for s, k in zip(d.source, d.series)]
    return d.reset_index(drop=True)
