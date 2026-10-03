"""Parse the Atlanta Fed GDPNow workbook into tidy tables (cached values only, never formulas)."""
import datetime as dt

import openpyxl
import pandas as pd

# Sheets laid out as rows of series: col A tcode, col B description, col C ticker, then dated columns.
SERIES_SHEETS = ['MonthlyLevels', 'TransformedMonthlySeries', 'ConsMonthlyLevels', 'ConsTransformedMonthlySeries',
                 'MonthlyPriceLevels', 'InventoryRaw', 'QtrlyGDPData', 'NomQtrlyComps', 'QtrlyActDLog',
                 'dLogQtrlyGrowth', 'QtrlyPriceForecasts', 'QtrlyBVARForecasts', 'InvDefDatafr',
                 'CIPIbeastackFore', 'ivaBEAForeStack']
COMPONENT_SHEETS = ['Consumption', 'Equipment', 'IntellPropProd', 'NonresStructures', 'Residential',
                    'Inventories', 'ExportsImportsGoods', 'ExportsImportsServices', 'FederalGovt', 'StateLocal']
PUBLISHED_SHEETS = ['TrackingHistory', 'ContribHistory']
FAAR_SHEETS = ['FactorAugARCoeffs', 'ConsFactorAugARCoeffs']


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _date(x):
    return pd.Timestamp(x).normalize() if isinstance(x, (dt.datetime, dt.date)) else None


def parse_series(rows, sheet):
    """Return (values, meta). Header rows are any row whose 4th cell is a date; they may repeat."""
    vals, meta, seen, header = [], [], set(), None
    for i, r in enumerate(rows, 1):
        if len(r) > 3 and _date(r[3]) is not None:
            header = [_date(x) for x in r]
            continue
        if header is None or len(r) < 3 or not isinstance(r[2], str) or r[2] == 'Ticker':
            continue
        ticker = r[2].strip()
        if ticker in seen:          # duplicate row: keep the first occurrence
            continue
        seen.add(ticker)
        meta.append((sheet, ticker, r[0] if _num(r[0]) else None, r[1] if isinstance(r[1], str) else None, i))
        for j in range(3, min(len(r), len(header))):
            if header[j] is not None and _num(r[j]):
                vals.append((sheet, ticker, header[j], float(r[j])))
    return vals, meta


def parse_factor(rows):
    dates = [_date(x) for x in rows[0]]
    out = []
    for r in rows[1:4]:
        for j in range(1, len(r)):
            if dates[j] is not None and _num(r[j]):
                out.append(('Factor', r[0], dates[j], float(r[j])))
    return out


def parse_faar(rows, sheet):
    """Long table: ticker, term (const, ar1..ar12, f0..f3), value."""
    out = []
    for r in rows[2:]:
        if len(r) < 3 or not isinstance(r[2], str):
            continue
        terms = [('const', 3)] + [(f'ar{k}', 4 + k) for k in range(1, 13)] + [(f'f{k}', 18 + k) for k in range(4)]
        for name, j in terms:
            if j < len(r) and _num(r[j]):
                out.append((sheet, r[2].strip(), name, float(r[j])))
    return out


def parse_pairs(rows, sheet):
    """LHS / RHS / value sheets: BridgeEqnCoeffs (value in col D), UtilTravelCoeffs (col C)."""
    vcol = 3 if sheet == 'BridgeEqnCoeffs' else 2
    return [(sheet, r[0], r[1], float(r[vcol])) for r in rows[1:]
            if len(r) > vcol and isinstance(r[0], str) and isinstance(r[1], str) and _num(r[vcol])]


def parse_rls(rows):
    return [('RLSweights', r[2], term, float(r[j])) for r in rows[1:] if len(r) > 4 and isinstance(r[2], str)
            for term, j in (('monthly', 3), ('bvar', 4)) if _num(r[j])]


def parse_farm(rows):
    out = []
    for r in rows[2:]:
        if len(r) > 8 and isinstance(r[2], str):
            for term, j in [('const', 3), ('ar1', 5), ('ar2', 6), ('ar3', 7), ('ar4', 8)]:
                if _num(r[j]):
                    out.append(('FarmOtherInvCoeffs', r[2], term, float(r[j])))
    return out


def parse_cells(rows, sheet):
    out = []
    for i, r in enumerate(rows, 1):
        for j, x in enumerate(r, 1):
            if x is None or x == '':
                continue
            if _num(x):
                out.append((sheet, i, j, float(x), None))
            else:
                out.append((sheet, i, j, None, x.isoformat() if isinstance(x, dt.datetime) else str(x)))
    return out


def read_workbook(path):
    """Parse every sheet the replication needs. Returns dict of DataFrames."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = {s: [list(r) for r in wb[s].iter_rows(values_only=True)]
            for s in SERIES_SHEETS + COMPONENT_SHEETS + PUBLISHED_SHEETS + FAAR_SHEETS +
            ['Factor', 'BridgeEqnCoeffs', 'UtilTravelCoeffs', 'RLSweights', 'FarmOtherInvCoeffs']}
    vals, meta = [], []
    for s in SERIES_SHEETS:
        v, m = parse_series(rows[s], s)
        vals += v; meta += m
    vals += parse_factor(rows['Factor'])
    faar = sum((parse_faar(rows[s], s) for s in FAAR_SHEETS), [])
    coef = (parse_pairs(rows['BridgeEqnCoeffs'], 'BridgeEqnCoeffs') + parse_pairs(rows['UtilTravelCoeffs'], 'UtilTravelCoeffs')
            + parse_rls(rows['RLSweights']) + parse_farm(rows['FarmOtherInvCoeffs']))
    cells = sum((parse_cells(rows[s], s) for s in COMPONENT_SHEETS + PUBLISHED_SHEETS), [])
    return {
        'wb_series': pd.DataFrame(vals, columns=['sheet', 'ticker', 'date', 'value']),
        'wb_series_meta': pd.DataFrame(meta, columns=['sheet', 'ticker', 'tcode', 'description', 'row']),
        'wb_faar': pd.DataFrame(faar, columns=['sheet', 'ticker', 'term', 'value']),
        'wb_coef': pd.DataFrame(coef, columns=['sheet', 'lhs', 'rhs', 'value']),
        'wb_cells': pd.DataFrame(cells, columns=['sheet', 'row', 'col', 'num', 'text']),
    }
