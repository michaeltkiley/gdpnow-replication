"""Recalculate the workbook headless in LibreOffice and compare every numeric cell of the component and
published-history sheets with the cached values Excel saved. Confirms the cached values we test against are
what the formulas produce.

Usage: python tools/recalc_check.py WORKBOOK.xlsx OUTDIR
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import openpyxl

SHEETS = ['Consumption', 'Equipment', 'IntellPropProd', 'NonresStructures', 'Residential', 'Inventories',
          'ExportsImportsGoods', 'ExportsImportsServices', 'FederalGovt', 'StateLocal', 'Table', 'TableCont']
# Force LibreOffice to recalculate OOXML files on load (OOXMLRecalcMode 0 = always).
PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>"""


def values(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    return {s: {(r, c): x for r, row in enumerate(wb[s].iter_rows(values_only=True), 1)
                for c, x in enumerate(row, 1) if isinstance(x, (int, float)) and not isinstance(x, bool)}
            for s in SHEETS}


def main(path, outdir):
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as prof:
        user = Path(prof) / 'user'; user.mkdir()
        (user / 'registrymodifications.xcu').write_text(PROFILE)
        subprocess.run(['soffice', f'-env:UserInstallation=file://{prof}', '--headless', '--convert-to', 'xlsx',
                        '--outdir', str(outdir), str(path)], check=True, capture_output=True, timeout=1800)
    recalc = outdir / Path(path).name
    a, b = values(path), values(recalc)
    worst = []
    for s in SHEETS:
        n = bad = 0
        for k, x in a[s].items():
            y = b[s].get(k)
            n += 1
            if y is None or abs(x - y) > 1e-9 * max(1.0, abs(x)):
                bad += 1
                worst.append((abs(x - (y if y is not None else 0)), s, k, x, y))
        print(f'{s:24s} cells={n:6d} differ={bad}')
    for w in sorted(worst, reverse=True)[:15]:
        print('  ', w)
    print('RESULT:', 'OK' if not worst else f'{len(worst)} cells differ')


if __name__ == '__main__':
    main(*sys.argv[1:3])
