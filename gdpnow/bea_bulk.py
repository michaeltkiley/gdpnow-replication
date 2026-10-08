"""BEA NIPA and underlying-detail tables from BEA's bulk files, instead of the BEA API.

https://apps.bea.gov/national/Release/TXT/ holds three files this module reads:
  * SeriesRegister.txt   `SeriesCode,SeriesLabel,MetricName,CalculationType,DefaultScale,TableId:LineNo,SeriesCodeParents`
                         (TableId:LineNo lists every table line the series appears on, separated by '|'; the label is the
                         line description the API returns);
  * NipaDataQ.txt, NipaDataM.txt   `SeriesCode,Period,"Value"` for every series (quarterly: 1947Q1; monthly: 1967M01).
All 33 tables the model uses are in them (values equal the API's; exploration round 11). A table is assembled from the register
(its lines) and the bulk file of its frequency, returned as the API path did: columns 'line|description', within the date window the
API served for the table (config/bea_windows.toml: a bulk series carries its whole history on every table it appears on). The three files are
downloaded once per run. No fallback: a failed download fails the run.

Change signal: a header-only request per file (Last-Modified, ETag, length; BEA sets them at release time), logged for the daily
probe. BEA answers any made-up file name with HTTP 200 and an HTML page (no Last-Modified), so a response without Last-Modified is an error.
"""
import csv
import io
import os
import tomllib
from pathlib import Path
import urllib.request

import pandas as pd

from . import public_data as P
from .config import CONFIG

BASE = 'https://apps.bea.gov/national/Release/TXT/'
FILES = {'Q': 'NipaDataQ.txt', 'M': 'NipaDataM.txt'}
REGISTER = 'SeriesRegister.txt'
_TEXT = {}
_REG = {}
_SER = tomllib.load(open(CONFIG / 'bea_series.toml', 'rb'))['series']
MAP = {fid: (v[0], float(v[1])) for fid, v in _SER.items()}         # FRED id -> (BEA series code, divisor)
_INDEX = {}                                                         # frequency -> {series code: [(period, value string)]}
_MONTHLY = {}                                                       # BEA code -> Series, filled when NipaDataM is read in this process
# table -> {first, last, full_lines}: the date window kept for the table (see the file). GDPNOW_LEGACY_HISTORY=1 (before/after comparison only)
# restores the previous windows, which were the API's start dates for every table.
_ROOT = Path(__file__).resolve().parents[1]
_WIN_FILE = (_ROOT / os.environ['GDPNOW_WINDOWS_FILE'] if os.environ.get('GDPNOW_WINDOWS_FILE')
             else _ROOT / 'tools' / 'legacy_bea_windows.toml' if os.environ.get('GDPNOW_LEGACY_HISTORY') == '1' else CONFIG / 'bea_windows.toml')
WINDOWS = tomllib.load(open(_WIN_FILE, 'rb'))
_PRIORITY = {'Current Dollars': 0, 'Chained Dollars': 1}      # which metric's label names a concept


def head(file):
    """(Last-Modified, ETag, Content-Length) of a bulk file; raises when BEA answers with its HTML page instead of the file."""
    req = urllib.request.Request(BASE + file, method='HEAD', headers=P.UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        h = r.headers
        if not h.get('Last-Modified'):
            raise RuntimeError(f'BEA {file}: no Last-Modified in the response (file missing?)')
        return h.get('Last-Modified'), h.get('ETag'), h.get('Content-Length')


def _text(file):
    if file not in _TEXT:
        P.record_head(BASE + file, head(file))
        last = None
        for k in range(3):
            try:
                req = urllib.request.Request(BASE + file, headers=P.UA)
                raw = urllib.request.urlopen(req, timeout=600).read()
                break
            except Exception as e:
                last = e
                if k < 2:
                    import time
                    time.sleep(5 * (k + 1))
        else:
            raise RuntimeError(f'BEA bulk file {file} failed to download: {last}') from last
        _TEXT[file] = raw.decode('utf8', 'replace')
    return _TEXT[file]


def stem(code):
    """A BEA series code is a concept stem plus a final metric letter (A191RC current dollars, A191RX chained dollars, A191RJ
    quantity index ...)."""
    return code[:-1] if len(code) > 1 else code


def register():
    """{table id: {line number: (series code, description)}} from SeriesRegister.txt. A series carries one label for all its
    tables, and the labels of one concept differ by metric ('Equals: Gross national product' for its price index), while
    code that pairs a nominal line with its quantity or price line (public_nipa._match) compares descriptions. So every series
    of a concept stem gets the same description: the label of its current-dollar series, else its chained-dollar series,
    else the first one in the file."""
    if not _REG:
        rows = [r for r in csv.reader(io.StringIO(_text(REGISTER))) if len(r) >= 6 and not r[0].startswith('%')]
        best = {}
        for r in rows:
            rank = _PRIORITY.get(r[2], 2)
            if stem(r[0]) not in best or rank < best[stem(r[0])][0]:
                best[stem(r[0])] = (rank, r[1])
        for r in rows:
            for tl in r[5].split('|'):
                if ':' in tl:
                    tb, ln = tl.split(':', 1)
                    _REG.setdefault(tb, {})[ln] = (r[0], best[stem(r[0])][1])
    return _REG


def _rows(frequency):
    """The bulk file of a frequency parsed once per process: {series code: [(period, value text)]}."""
    if frequency not in _INDEX:
        idx = {}
        for row in csv.reader(io.StringIO(_text(FILES[frequency]))):
            if len(row) == 3 and not row[0].startswith('%'):
                idx.setdefault(row[0], []).append((row[1], row[2]))
        _INDEX[frequency] = idx
    return _INDEX[frequency]


def table(table_id, frequency):
    """DataFrame(series 'line|label', date, value) for a table, like the API path built it. Dates are period ends."""
    if frequency not in FILES:
        raise ValueError(f'BEA bulk files cover Q and M, not {frequency}')
    lines = register().get(table_id)
    if not lines:
        raise RuntimeError(f'BEA table {table_id} is not in {REGISTER}')
    code_lines = {}               # a series can sit on several lines of one table (some tables repeat a line)
    for ln, (code, label) in lines.items():
        code_lines.setdefault(code, []).append((ln, label))
    recs = []
    idx = _rows(frequency)
    for code, lns in code_lines.items():
        for per, txt in idx.get(code, ()):
            try:
                v = float(txt.replace(',', ''))
            except ValueError:
                continue
            date = (pd.Period(per.replace('M', '-'), 'M') if 'M' in per else pd.Period(per, 'Q')).end_time.normalize()
            for ln, label in lns:
                recs.append((f'{ln}|{label}', date, v))
    if not recs:
        raise RuntimeError(f'BEA table {table_id} ({frequency}): no data in {FILES[frequency]}')
    df = pd.DataFrame(recs, columns=['series', 'date', 'value'])
    w = WINDOWS.get(table_id, {})
    full = df.series.str.split('|').str[0].isin([str(x) for x in w.get('full_lines', [])])      # lines kept with their whole history
    if 'first' in w:
        df = df[full | (df.date >= pd.Period(w['first'], frequency).end_time.normalize())]
    if 'last' in w:
        df = df[df.date <= pd.Period(w['last'], frequency).end_time.normalize()]
    return df


def covers(fred_id):
    return fred_id in MAP


def series(con, fred_id, asof):
    """The series FRED publishes as `fred_id`, from NipaDataM.txt (index: first day of each month, like FRED)."""
    code, div = MAP[fred_id]
    if code not in _MONTHLY and not P.REFRESH:
        s = P._archived(con, 'bea_bulk', code, asof)
        if s is not None:
            _MONTHLY[code] = s
    if code not in _MONTHLY:
        wanted = {c for c, _ in MAP.values()}
        got = {}
        idx = _rows('M')
        for c in wanted:
            for per, txt in idx.get(c, ()):
                try:
                    v = float(txt.replace(',', ''))
                except ValueError:
                    continue
                got.setdefault(c, {})[pd.Period(per.replace('M', '-'), 'M').to_timestamp()] = v
        missing = wanted - set(got)
        if missing:
            raise RuntimeError(f'{FILES["M"]}: series not found: {sorted(missing)}')
        for c, vals in got.items():
            s = pd.Series(vals, dtype=float).sort_index()
            _MONTHLY[c] = s
            P._archive(con, 'bea_bulk', c, asof, s)
    return _MONTHLY[code] / div
