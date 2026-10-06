"""Census series from the Census Bureau's bulk program files, instead of the Census API and FRED.

https://www.census.gov/econ_datasets/ lists one zip per program; https://www.census.gov/econ_getzippedfile/?programCode=<CODE>
downloads it. A zip holds one CSV in sections (CATEGORIES, DATA TYPES, [ERROR TYPES], GEO LEVELS, TIME PERIODS, NOTES,
DATA UPDATED ON, DATA) and a README. The DATA rows carry integer indexes into the other sections plus `is_adj` (1 =
seasonally adjusted) and `val`. It is the same data the EITS API serves (all 14 series we used matched exactly).

config/census_series.toml maps the EITS dataset ids used in the code and the FRED series that Census publishes to
(program, category, data type, adjustment, geography). A program is downloaded once per run; each series used is
archived in `raw_pulls` (source 'census_bulk', series 'PROGRAM|category|datatype|is_adj|geo'). No fallback: a failed
download or a missing series fails the run.

Change signal: the program's size and last-updated stamp on Census's data-set download page (one request for all programs; the zip's
own Last-Modified is just the current time), logged as a CENSUS_ZIP request for the daily probe.
"""
import csv
import hashlib
import io
import re
import threading
import tomllib
import urllib.request
import zipfile

import pandas as pd

from . import public_data as P
from .config import CONFIG

URL = 'https://www.census.gov/econ_getzippedfile/?programCode={}'
_CFG = tomllib.load(open(CONFIG / 'census_series.toml', 'rb'))
EITS = dict(_CFG['eits'])                                   # EITS dataset id -> program code
FRED = {fid: tuple(v) for fid, v in _CFG['fred'].items()}   # FRED id -> (program, category, data type, is_adj, geo)
_FRAMES = {}        # program -> DataFrame(cat_code, dt_code, is_adj, geo_code, date, val), filled when a zip is loaded
_CACHE = {}


def covers(fred_id):
    return fred_id in FRED


def program_url(program):
    return URL.format(program)


def download(program):
    """The zip's bytes (3 tries)."""
    last = None
    for k in range(3):
        try:
            req = urllib.request.Request(program_url(program), headers=P.UA)
            return urllib.request.urlopen(req, timeout=300).read()
        except Exception as e:
            last = e
            if k < 2:
                import time
                time.sleep(5 * (k + 1))
    raise RuntimeError(f'Census bulk file {program} failed to download: {last}') from last


def csv_text(raw, program):
    z = zipfile.ZipFile(io.BytesIO(raw))
    return z.read(f'{program}-mf.csv').decode('utf8', 'replace')


PAGE = 'https://www.census.gov/econ_datasets/'
_PAGE = {}
_LOCK = threading.Lock()
_ROW = re.compile(r'programCode=([A-Z0-9]+)"[^>]*>[^<]*</a></td>\s*<td[^>]*>([^<]+)</td>\s*<td[^>]*>([^<]+)</td>')


def page_rows():
    """{program code: (size, last updated)} from Census's data-set download page (one request, shared by every program)."""
    with _LOCK:
        if not _PAGE:
            last = None
            for k in range(3):
                try:
                    html = urllib.request.urlopen(urllib.request.Request(PAGE, headers=P.UA), timeout=120).read().decode('utf8', 'replace')
                    rows = {m.group(1): (m.group(2).strip(), m.group(3).strip()) for m in _ROW.finditer(html)}
                    if not rows:
                        raise RuntimeError('no program rows found on the page')
                    _PAGE.update(rows)
                    break
                except Exception as e:
                    last = e
                    if k < 2:
                        import time
                        time.sleep(5 * (k + 1))
            else:
                raise RuntimeError(f'Census data-set page {PAGE} failed: {last}') from last
        return dict(_PAGE)


def digest(program):
    """What the daily probe compares for a program: its size and last-updated stamp on Census's download page (the zip itself
    carries only the current time as Last-Modified). One page request serves all programs."""
    rows = page_rows()
    if program not in rows:
        raise RuntimeError(f'Census data-set page lists no program {program}')
    return hashlib.sha256('|'.join(rows[program]).encode()).hexdigest()


def _sections(text):
    secs, cur = {}, None
    for line in text.splitlines():
        if line.strip() and line == line.upper() and ',' not in line and not line[0].isdigit() and len(line) < 40:
            cur = line.strip()
            secs[cur] = []
        elif cur is not None:
            secs[cur].append(line)
    return secs


def parse(text):
    """DataFrame(cat_code, dt_code, is_adj, geo_code, date, val) for the data rows (error rows dropped); date = month end."""
    secs = _sections(text)

    def table(name):
        rows = [r for r in csv.reader(secs[name]) if r]
        return pd.DataFrame(rows[1:], columns=rows[0])
    cats, dts, geos, pers = table('CATEGORIES'), table('DATA TYPES'), table('GEO LEVELS'), table('TIME PERIODS')
    d = pd.read_csv(io.StringIO('\n'.join(secs['DATA'])))
    if 'et_idx' in d:
        d = d[d.et_idx == 0]
    cat = dict(zip(cats.cat_idx.astype(int), cats.cat_code))
    dtc = dict(zip(dts.dt_idx.astype(int), dts.dt_code))
    geo = dict(zip(geos.geo_idx.astype(int), geos.geo_code))
    per = dict(zip(pers.per_idx.astype(int), pd.to_datetime(pers.per_name, format='%b-%Y') + pd.offsets.MonthEnd(0)))
    out = pd.DataFrame({'cat_code': d.cat_idx.map(cat), 'dt_code': d.dt_idx.map(dtc), 'is_adj': d.is_adj.astype(int),
                        'geo_code': d.geo_idx.map(geo), 'date': d.per_idx.map(per), 'val': pd.to_numeric(d.val, errors='coerce')})
    return out.dropna(subset=['val', 'date'])


def _frame(program):
    if program not in _FRAMES:
        raw = download(program)
        text = csv_text(raw, program)
        P._record('CENSUS_ZIP', program_url(program), None, digest(program))
        _FRAMES[program] = parse(text)
    return _FRAMES[program]


def get(con, asof, program, cat, dt, adj=1, geo='US', first_of_month=False):
    """One series of a program (index: month end, or the first day of the month like FRED when `first_of_month`)."""
    key = f'{program}|{cat}|{dt}|{adj}|{geo}'
    if (key, first_of_month) in _CACHE:
        return _CACHE[(key, first_of_month)]
    s = None
    if not P.REFRESH:
        s = P._archived(con, 'census_bulk', key, asof)
    if s is None:
        d = _frame(program)
        sel = d[(d.cat_code == cat) & (d.dt_code == dt) & (d.is_adj == adj) & (d.geo_code == geo)]
        if sel.empty:
            raise RuntimeError(f'Census {program}: series not found: {key}')
        s = sel.drop_duplicates('date', keep='last').set_index('date').val.sort_index().astype(float)
        P._archive(con, 'census_bulk', key, asof, s)
    if first_of_month:
        s = s.set_axis(s.index.to_period('M').to_timestamp())
    _CACHE[(key, first_of_month)] = s
    return s


def eits(con, asof, dataset, category, data_type, seasonal='yes'):
    """What the EITS API's `dataset` returned for (category, data type), seasonally adjusted by default."""
    return get(con, asof, EITS[dataset], category, data_type, 1 if seasonal == 'yes' else 0)


def series(con, fred_id, asof):
    """The series FRED publishes as `fred_id`, from Census's bulk file (index: first day of each month, like FRED)."""
    program, cat, dt, adj, geo = FRED[fred_id]
    return get(con, asof, program, cat, dt, adj, geo, first_of_month=True)
