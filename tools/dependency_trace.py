"""Trace which formula cells and which input series the nowcast actually depends on.

Walks precedents backward from each component sheet's result cells. Cross-sheet lookups into
value-only sheets are recorded as leaves (sheet, lookup key); the key is resolved from the
cached value of the cell holding the ticker.

Usage: python tools/dependency_trace.py WORKBOOK.xlsx OUTDIR
Writes OUTDIR/live_cells.csv (formula cells on the dependency path) and
OUTDIR/leaf_inputs.csv (value-sheet series the nowcast reads).
"""
import csv, re, sys
from collections import deque
import openpyxl
from openpyxl.utils import column_index_from_string as ci, get_column_letter as cl

COMPONENT_SHEETS = ['Consumption', 'Equipment', 'IntellPropProd', 'NonresStructures', 'Residential',
                    'Inventories', 'ExportsImportsGoods', 'ExportsImportsServices', 'FederalGovt', 'StateLocal']
# Result cells: quarterly log change (SAAR) of each of the 13 GDP components, plus the inventory blend.
TARGETS = {'Consumption': ['FV6', 'FV39'], 'Equipment': ['FV6'], 'IntellPropProd': ['FV6'],
           'NonresStructures': ['FV6'], 'Residential': ['FV6'], 'ExportsImportsGoods': ['FV6', 'FV7'],
           'ExportsImportsServices': ['FV6', 'FV7'], 'FederalGovt': ['FV6'], 'StateLocal': ['FV6'],
           'Inventories': ['FV9', 'FV11', 'FX9', 'FX11']}
REF = re.compile(r"(?:'?([A-Za-z0-9_ ]+)'?!)?(\$?[A-Z]{1,3}\$?\d+)(?::(\$?[A-Z]{1,3}\$?\d+))?(?![\d(A-Za-z_])")
LOOKUP = re.compile(r"(VLOOKUP|HLOOKUP|MATCH)\(")


def split_args(f, start):
    """Split top-level comma-separated arguments of the call whose '(' is at f[start-1]."""
    depth, args, cur, i = 0, [], '', start
    while i < len(f):
        ch = f[i]
        if ch == '"':
            j = f.index('"', i + 1); cur += f[i:j + 1]; i = j + 1; continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            if depth == 0:
                args.append(cur); return args
            depth -= 1
        elif ch == ',' and depth == 0:
            args.append(cur); cur = ''; i += 1; continue
        cur += ch; i += 1
    return args


def resolve(expr, vals):
    """Resolve a lookup key: a literal string, a cell, or CONCATENATE of those."""
    e = expr.strip()
    if e.startswith('"'):
        return e.strip('"')
    if re.fullmatch(r'\$?[A-Z]{1,3}\$?\d+', e):
        v = vals.get(rc(e), f'?{e}')
        return v.date().isoformat() if hasattr(v, 'date') else str(v)
    m = re.fullmatch(r'CONCATENATE\((.*)\)', e)
    if m:
        return ''.join(resolve(a, vals) for a in split_args(e, e.index('(') + 1))
    return f'expr:{e[:40]}'


def rc(ref):
    m = re.match(r'\$?([A-Z]{1,3})\$?(\d+)', ref)
    return int(m.group(2)), ci(m.group(1))


def main(path, outdir):
    fw = openpyxl.load_workbook(path, read_only=True)
    vw = openpyxl.load_workbook(path, read_only=True, data_only=True)
    F, V = {}, {}
    for s in COMPONENT_SHEETS:
        F[s] = {(r, c): x for r, row in enumerate(fw[s].iter_rows(values_only=True), 1)
                for c, x in enumerate(row, 1) if x is not None}
        V[s] = {(r, c): x for r, row in enumerate(vw[s].iter_rows(values_only=True), 1)
                for c, x in enumerate(row, 1) if x is not None}

    live, leaves = set(), {}
    queue = deque((s, rc(t)) for s, ts in TARGETS.items() for t in ts)
    while queue:
        s, cell = queue.popleft()
        if (s, cell) in live:
            continue
        live.add((s, cell))
        f = F[s].get(cell)
        if not (isinstance(f, str) and f.startswith('=')):
            continue
        # Leaves: lookups into other sheets, keyed by a ticker held in a cell or a literal string.
        here = f'{s}!{cl(cell[1])}{cell[0]}'
        for m in LOOKUP.finditer(f):
            args = split_args(f, m.end())
            if len(args) < 2:
                continue
            sm = re.match(r"\s*'?([A-Za-z0-9_ ]+)'?!", args[1])
            if not sm or sm.group(1) in F:
                continue
            kind = 'row' if m.group(1) == 'VLOOKUP' else 'col' if m.group(1) == 'HLOOKUP' else 'match'
            leaves.setdefault((sm.group(1), kind, resolve(args[0], V[s])), set()).add(here)
        for sheet, a, b in REF.findall(f):
            if sheet and sheet not in F and not re.search(r'(LOOKUP|MATCH)\(', f):
                leaves.setdefault((sheet, 'cell', a + (':' + b if b else '')), set()).add(here)
        # In-workbook precedents among component sheets.
        for sheet, a, b in REF.findall(f):
            t = sheet or s
            if t not in F:
                continue
            (r1, c1), (r2, c2) = rc(a), rc(b) if b else rc(a)
            for (r, c) in F[t]:
                if r1 <= r <= r2 and c1 <= c <= c2:
                    queue.append((t, (r, c)))

    with open(f'{outdir}/live_cells.csv', 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['sheet', 'cell', 'row', 'formula_or_value'])
        for s, (r, c) in sorted(live):
            w.writerow([s, f'{cl(c)}{r}', r, str(F[s].get((r, c)))[:300]])
    with open(f'{outdir}/leaf_inputs.csv', 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['sheet', 'kind', 'key', 'n_uses', 'example_user'])
        for (sheet, kind, k), users in sorted(leaves.items()):
            w.writerow([sheet, kind, k, len(users), sorted(users)[0]])
    write_inputs_used(leaves, f'{outdir}/inputs_used.csv')
    print(f'live cells: {len(live)}  leaf series: {len(leaves)}')


# Sheets whose contents are estimated parameters or model forecasts, never data.
ESTIMATE_SHEETS = {'BridgeEqnCoeffs', 'FactorAugARCoeffs', 'ConsFactorAugARCoeffs', 'RLSweights',
                   'UtilTravelCoeffs', 'FarmOtherInvCoeffs'}
OUTPUT_SHEETS = {'Factor', 'QtrlyBVARForecasts', 'QtrlyPriceForecasts', 'CIPIbeastackFore', 'ivaBEAForeStack'}
RAW_SUFFIX = re.compile(r'[@_](USNA|USECON|IP|LABOR|PPIR?|CPIDATA|USINT|SURVEYS)$')


def classify(sheet, key):
    """Classify one leaf input as coefficient, model output, raw data or constructed data."""
    if sheet in ESTIMATE_SHEETS:
        return 'coefficient'
    if sheet in OUTPUT_SHEETS:
        return 'model_output'
    if sheet == 'InvDefDatafr':
        return 'data+model_output'          # history is data; quarter being nowcast is Denton forecast
    if re.search(r'(fr|Fore|Rev)$', key):
        return 'data+model_output'          # actual history with model-forecast tail
    if RAW_SUFFIX.search(key):
        return 'raw' if sheet not in ('TransformedMonthlySeries', 'ConsTransformedMonthlySeries',
                                       'dLogQtrlyGrowth') else 'constructed(transform of raw)'
    return 'constructed'


def write_inputs_used(leaves, path):
    rows = {}
    for (sheet, kind, k) in leaves:
        if kind == 'cell' or re.fullmatch(r'\d{4}-\d{2}-\d{2}', k) or k in ('Ticker', 'None'):
            continue
        rows[(sheet, k)] = classify(sheet, k)
    with open(path, 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['sheet', 'key', 'class'])
        for (sheet, k), c in sorted(rows.items()):
            w.writerow([sheet, k, c])


if __name__ == '__main__':
    main(*sys.argv[1:3])
