"""GDPNow's own published nowcast path for the quarter in the workbook (TrackingHistory / ContribHistory sheets).

These sheets are Atlanta Fed outputs (the numbers shown on its website): one column per update date, one row per
component growth rate (TrackingHistory) or contribution to GDP growth (ContribHistory). Used only to display and
compare, never as model input.
"""
import re
from datetime import datetime

import pandas as pd

# Row label (leading number in the sheet) -> component key used throughout this project.
COMPONENTS = {2: 'CTG', 3: 'CS', 7: 'FNE', 8: 'FNP', 9: 'FNS', 10: 'FR', 12: 'GF', 13: 'GS',
              15: 'MM', 16: 'MS', 18: 'XM', 19: 'XS'}
# Other rows: matched by text.
EXTRA = {'GDP Nowcast': 'GDP', 'Final Sales': 'final_sales', 'Final Sales to Domestic Purchasers': 'final_sales_domestic',
         'Final Sales to Private Domestic Purchasers': 'private_domestic_final_purchases',
         'Change in inventory investment**': 'V'}


def _key(label):
    if not isinstance(label, str):
        return None
    t = label.strip()
    m = re.match(r'^(\d+)-', t)
    if m and int(m.group(1)) in COMPONENTS:
        # numbers 15/16 and 18/19 exist once in each sheet; 'Imports'/'Exports' headers are 14/17
        return COMPONENTS[int(m.group(1))]
    for text, key in EXTRA.items():
        if t.startswith(text):
            return key
    if t.startswith('Change in inventory investment'):
        return 'V'
    return None


def read_published(path):
    """Long DataFrame: date, kind ('growth' | 'contribution'), key, value, plus the quarter label."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out, quarter = [], None
    for sheet, kind in (('TrackingHistory', 'growth'), ('ContribHistory', 'contribution')):
        rows = list(wb[sheet].iter_rows(values_only=True))
        m = re.search(r'(\d{4})\s*[qQ]\s*([1-4])', str(rows[1][0]))
        if m:
            quarter = f'{m.group(1)}Q{m.group(2)}'
        dates = {j: d for j, d in enumerate(rows[0]) if isinstance(d, datetime)}
        for r in rows[2:]:
            key = None
            for cell in r[:2]:
                key = key or _key(cell)
            if key is None:
                continue
            if kind == 'contribution' and key == 'V':
                pass
            for j, d in dates.items():
                v = r[j]
                if isinstance(v, (int, float)):
                    out.append((d.date().isoformat(), kind, key, float(v)))
    df = pd.DataFrame(out, columns=['date', 'kind', 'key', 'value'])
    # TrackingHistory's 'Change in inventory investment' row is in $bn; contributions are only used from ContribHistory.
    df = df[~((df.kind == 'growth') & (df.key == 'V'))]
    df['quarter'] = quarter
    return df.drop_duplicates(['date', 'kind', 'key'], keep='last')
