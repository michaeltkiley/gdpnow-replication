"""Which data release does an archived series belong to? Labels used to group data changes between two daily runs.

FRED series carry their release name in FRED itself (series/release; cached in the DuckDB table fred_release).
Everything else is labelled by its source (BEA table, Census dataset, BLS, Treasury, downloaded files).
"""
import json
import os
import urllib.parse
import urllib.request

import pandas as pd

from . import store
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


def public_inputs_diff(con, asof0, asof1, tol=1e-9):
    """Did the public inputs archived for `asof1` differ from those archived for `asof0`? Counts observations that are
    new, gone or revised across every archived pull, plus downloaded files (raw_files) whose content changed.
    Returns None when either day has no archive (cannot compare), else a dict of counts."""
    n0 = con.execute('SELECT count(*) FROM raw_pulls WHERE as_of = ?', [asof0]).fetchone()[0]
    n1 = con.execute('SELECT count(*) FROM raw_pulls WHERE as_of = ?', [asof1]).fetchone()[0]
    if not n0 or not n1:
        return None
    q = """
    SELECT sum(CASE WHEN a.value IS NULL THEN 1 ELSE 0 END) AS gone,
           sum(CASE WHEN b.value IS NULL THEN 1 ELSE 0 END) AS new,
           sum(CASE WHEN a.value IS NOT NULL AND b.value IS NOT NULL
                     AND NOT (a.value = b.value OR abs(a.value - b.value) <= ? * greatest(1, abs(b.value))) THEN 1 ELSE 0 END) AS revised,
           count(DISTINCT CASE WHEN a.value IS NULL OR b.value IS NULL OR NOT (a.value = b.value OR abs(a.value - b.value) <= ? * greatest(1, abs(b.value)))
                               THEN coalesce(a.source, b.source) || chr(1) || coalesce(a.series, b.series) END) AS series
    FROM (SELECT source, series, date, value FROM raw_pulls WHERE as_of = ?) a      -- a: asof1 (today)
    FULL OUTER JOIN (SELECT source, series, date, value FROM raw_pulls WHERE as_of = ?) b   -- b: asof0 (before)
      ON a.source = b.source AND a.series = b.series AND a.date = b.date"""
    gone, new, rev, ser = con.execute(q, [tol, tol, asof1, asof0]).fetchone()
    files = 0
    if store.table_exists(con, 'raw_files'):
        files = con.execute("""SELECT count(*) FROM (SELECT name, sha256 FROM raw_files WHERE as_of = ?) a
                               FULL OUTER JOIN (SELECT name, sha256 FROM raw_files WHERE as_of = ?) b ON a.name = b.name
                               WHERE a.sha256 IS DISTINCT FROM b.sha256""", [asof1, asof0]).fetchone()[0]
    return dict(new=int(new or 0), gone=int(gone or 0), revised=int(rev or 0), series=int(ser or 0), files=int(files))
