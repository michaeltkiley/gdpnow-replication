"""BEA NIPA and underlying-detail tables from BEA's bulk files, instead of the BEA API.

https://apps.bea.gov/national/Release/TXT/ holds three files this module reads:
  * SeriesRegister.txt   `SeriesCode,SeriesLabel,MetricName,CalculationType,DefaultScale,TableId:LineNo,SeriesCodeParents`
                         (TableId:LineNo lists every table line the series appears on, separated by '|'; the label is the
                         line description the API returns);
  * NipaDataQ.txt, NipaDataM.txt   `SeriesCode,Period,"Value"` for every series (quarterly: 1947Q1; monthly: 1967M01).
All 33 tables the model uses are in them (values equal the API's; exploration round 11). A table is assembled from the register
(its lines) and the bulk file of its frequency, returned as the API path did: columns 'line|description'. The three files are
downloaded once per run. No fallback: a failed download fails the run.

Change signal: a header-only request per file (Last-Modified, ETag, length; BEA sets them at release time), logged for the daily
probe. BEA answers any made-up file name with HTTP 200 and an HTML page (no Last-Modified), so a response without Last-Modified is an error.
"""
import csv
import io
import urllib.request

import pandas as pd

from . import public_data as P

BASE = 'https://apps.bea.gov/national/Release/TXT/'
FILES = {'Q': 'NipaDataQ.txt', 'M': 'NipaDataM.txt'}
REGISTER = 'SeriesRegister.txt'
_TEXT = {}
_REG = {}


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


def register():
    """{table id: {line number: (series code, label)}} from SeriesRegister.txt."""
    if not _REG:
        for row in csv.reader(io.StringIO(_text(REGISTER))):
            if len(row) < 6 or row[0].startswith('%'):
                continue
            for tl in row[5].split('|'):
                if ':' in tl:
                    tb, ln = tl.split(':', 1)
                    _REG.setdefault(tb, {})[ln] = (row[0], row[1])
    return _REG


def table(table_id, frequency):
    """DataFrame(series 'line|label', date, value) for a table, like the API path built it. Dates are period ends."""
    if frequency not in FILES:
        raise ValueError(f'BEA bulk files cover Q and M, not {frequency}')
    lines = register().get(table_id)
    if not lines:
        raise RuntimeError(f'BEA table {table_id} is not in {REGISTER}')
    code_line = {code: (ln, label) for ln, (code, label) in lines.items()}
    recs = []
    for row in csv.reader(io.StringIO(_text(FILES[frequency]))):
        if len(row) != 3 or row[0] not in code_line:
            continue
        try:
            v = float(row[2].replace(',', ''))
        except ValueError:
            continue
        ln, label = code_line[row[0]]
        per = row[1]
        date = (pd.Period(per.replace('M', '-'), 'M') if 'M' in per else pd.Period(per, 'Q')).end_time.normalize()
        recs.append((f'{ln}|{label}', date, v))
    if not recs:
        raise RuntimeError(f'BEA table {table_id} ({frequency}): no data in {FILES[frequency]}')
    return pd.DataFrame(recs, columns=['series', 'date', 'value'])
