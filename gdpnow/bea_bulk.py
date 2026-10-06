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
import tomllib
import urllib.request

import pandas as pd

from . import public_data as P
from .config import CONFIG

BASE = 'https://apps.bea.gov/national/Release/TXT/'
FILES = {'Q': 'NipaDataQ.txt', 'M': 'NipaDataM.txt'}
REGISTER = 'SeriesRegister.txt'
_TEXT = {}
_REG = {}
WINDOWS = tomllib.load(open(CONFIG / 'bea_windows.toml', 'rb'))     # table -> {first, last}: the API's date window (see the file)
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
    for row in csv.reader(io.StringIO(_text(FILES[frequency]))):
        if len(row) != 3 or row[0] not in code_lines:
            continue
        try:
            v = float(row[2].replace(',', ''))
        except ValueError:
            continue
        per = row[1]
        date = (pd.Period(per.replace('M', '-'), 'M') if 'M' in per else pd.Period(per, 'Q')).end_time.normalize()
        for ln, label in code_lines[row[0]]:
            recs.append((f'{ln}|{label}', date, v))
    if not recs:
        raise RuntimeError(f'BEA table {table_id} ({frequency}): no data in {FILES[frequency]}')
    df = pd.DataFrame(recs, columns=['series', 'date', 'value'])
    w = WINDOWS.get(table_id, {})
    if 'first' in w:
        df = df[df.date >= pd.Period(w['first'], frequency).end_time.normalize()]
    if 'last' in w:
        df = df[df.date <= pd.Period(w['last'], frequency).end_time.normalize()]
    return df
