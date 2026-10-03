"""Run the full assembly on an Inputs bundle: 13 components -> Fisher aggregate."""
import pandas as pd

from . import components as C
from .aggregate import aggregate

BRIDGE_IDS = ['FNEZ', 'FNPZ', 'FNSZ', 'FRZ', 'GFZ', 'GSZ']


def run(inp):
    """Returns (components DataFrame, aggregates dict, intermediates DataFrame)."""
    mon = C.Monthly(inp, inp.growth, inp.levels, inp.faar)
    growth, rows = {}, []

    def keep(block, out):
        for k, v in out.items():
            if isinstance(v, (int, float)) or hasattr(v, 'dtype') and getattr(v, 'ndim', 1) == 0:
                rows.append((block, k, float(v)))

    for cid in BRIDGE_IDS:
        growth[cid[:-1]], out = C.bridge_component(cid, inp, mon)
        keep(cid, out)
        rows.append((cid, 'total', growth[cid[:-1]]))
    (growth['CTG'], growth['CS']), out = C.consumption(inp, mon)
    for k, v in zip(['core_retail', 'new_mv', 'used_mv', 'gasoline'], out['goods_q']):
        rows.append(('CONS', f'q:{k}', float(v)))
    for k, v in zip(['food_services', 'electricity_gas', 'travel_out', 'travel_in', 'other_services'], out['services_q']):
        rows.append(('CONS', f'q:{k}', float(v)))
    rows += [('CONS', 'goods', growth['CTG']), ('CONS', 'services', growth['CS'])]
    for kind, (x, mm) in (('goods', ('XM', 'MM')), ('services', ('XS', 'MS'))):
        (growth[x], growth[mm]), out = C.trade(kind, inp, mon)
        keep(f'TRADE_{kind}', out)
        rows += [(f'TRADE_{kind}', 'exports', growth[x]), (f'TRADE_{kind}', 'imports', growth[mm])]
    cipi, out = C.inventories(inp)
    keep('INV', out)
    rows.append(('INV', 'cipi', cipi))
    comps, agg = aggregate(inp, growth, cipi)
    return comps, agg, pd.DataFrame(rows, columns=['block', 'key', 'value'])
