"""Fisher chain aggregation of the 13 components and contributions to growth (Higgins 2014 appendix A3-A5;
BEA NIPA Handbook ch. 4; Whelan 2000)."""
import numpy as np
import pandas as pd

# id, real quantity ticker, label, sign (imports enter negatively)
COMPONENTS = [
    ('CTG', 'CTGZ_USNA', 'PCE goods', 1), ('CS', 'CSZ_USNA', 'PCE services', 1),
    ('FNE', 'FNEZ_USNA', 'Equipment', 1), ('FNP', 'FNPZ_USNA', 'Intellectual property products', 1),
    ('FNS', 'FNSZ_USNA', 'Nonresidential structures', 1), ('FR', 'FRZ_USNA', 'Residential', 1),
    ('GF', 'GFZ_USNA', 'Federal government', 1), ('GS', 'GSZ_USNA', 'State and local government', 1),
    ('XM', 'XMZ_USNA', 'Goods exports', 1), ('XS', 'XSZ_USNA', 'Services exports', 1),
    ('MM', 'MMZ_USNA', 'Goods imports', -1), ('MS', 'MSZ_USNA', 'Services imports', -1),
    ('V', 'VZ_USNA', 'Change in private inventories', 1),
]


def fisher_growth(p0, p1, q0, q1):
    return np.sqrt((p0 @ q1 / (p0 @ q0)) * (p1 @ q1 / (p1 @ q0)))


def aggregate(inp, growth, cipi):
    """growth: id -> T1 log growth (SAAR) for the 12 non-inventory components; cipi: T1 real CIPI ($bn).

    Returns a DataFrame of component growth (% SAAR) and contributions (pp), plus aggregates.
    """
    T, T1, nipa = inp.T, inp.T1, inp.nipa
    Tm = T - pd.offsets.QuarterEnd(1)
    ids = [c[0] for c in COMPONENTS]
    sign = np.array([c[3] for c in COMPONENTS], dtype=float)
    q0 = np.array([nipa[z][T] for _, z, _, _ in COMPONENTS])
    q1 = np.array([nipa[z][T] * np.exp(growth[i] / 400) if i != 'V' else cipi * 1000 for i, z, _, _ in COMPONENTS])
    p0 = np.array([inp.prices_T0[i + 'Z'] for i in ids]) / 100
    p1 = np.array([inp.prices_T1[i + 'Z'] for i in ids]) / 100
    # Inventory prices are end-of-quarter: use the average of current and previous end-of-quarter values.
    v = ids.index('V')
    p0[v] = (inp.prices_T0['VZ'] + inp.prices_Tm['VZ']) / 200
    p1[v] = (inp.prices_T1['VZ'] + inp.prices_T0['VZ']) / 200
    q0s, q1s = sign * q0, sign * q1
    gross = fisher_growth(p0, p1, q0s, q1s)
    pf = fisher_growth(q0s, q1s, p0, p1)                         # Fisher price index (gross)
    w = p1 / pf + p0
    c = w * (q1s - q0s) / (w @ q0s)                              # quarterly contributions, sum = gross - 1
    s = c.sum()
    contrib = 100 * c * ((1 + s) ** 4 - 1) / s                   # multinomial annualization, split evenly
    pct = [100 * ((q1[k] / q0[k]) ** 4 - 1) if i != 'V' else np.nan for k, i in enumerate(ids)]
    df = pd.DataFrame({'id': ids, 'label': [c_[2] for c_ in COMPONENTS], 'growth_pct': pct, 'contribution': contrib})

    def sub_growth(keep):
        k = np.array([i in keep for i in ids])
        return 100 * (fisher_growth(p0[k], p1[k], q0s[k], q1s[k]) ** 4 - 1)

    agg = {
        'GDP': 100 * (gross ** 4 - 1),
        'final_sales': sub_growth(set(ids) - {'V'}),
        'final_sales_domestic': sub_growth({'CTG', 'CS', 'FNE', 'FNP', 'FNS', 'FR', 'GF', 'GS'}),
        'private_domestic_final_purchases': sub_growth({'CTG', 'CS', 'FNE', 'FNP', 'FNS', 'FR'}),
        'PCE': sub_growth({'CTG', 'CS'}),
        'cipi_level': cipi, 'cipi_prev': q0[v] / 1000, 'cipi_change': cipi - q0[v] / 1000,
        'net_exports_level': float(sum(sign[k] * q1[k] for k, i in enumerate(ids) if i in ('XM', 'XS', 'MM', 'MS')) / 1000),
    }
    return df, agg
