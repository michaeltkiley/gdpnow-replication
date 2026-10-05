"""Layer 1 of the daily decomposition: why did the nowcast change between two runs of the same quarter?

Everything is exact accounting at each level (midpoint rule for products), so the pieces add up:
  headline change = sum of component contribution changes (+ a small aggregation remainder)
  component growth change = bridge-model part + BVAR part + blend-weight part   (bridge components, trade, inventories)
  bridge part = subcomponent parts (+ composition), each = indicator data effect + coefficient effect (+ other)
Each growth effect is converted to percentage points of GDP growth with the component's contribution-to-growth ratio.
"""
import tomllib

import numpy as np

from .config import CONFIG

BRIDGE_IDS = {'FNEZ': 'FNE', 'FNPZ': 'FNP', 'FNSZ': 'FNS', 'FRZ': 'FR', 'GFZ': 'GF', 'GSZ': 'GS'}
CONS_GOODS = ['core_retail', 'new_mv', 'used_mv', 'gasoline']
CONS_SERVICES = ['food_services', 'electricity_gas', 'travel_out', 'travel_in', 'other_services']
BRIDGES = tomllib.load(open(CONFIG / 'bridges.toml', 'rb'))['components']
mid = lambda a, b: 0.5 * (a + b)


def load(con, run_id):
    comps = {r[0]: dict(label=r[1], g=r[2] if r[2] is not None else 0.0, c=r[3]) for r in con.execute(
        'SELECT id, label, growth_pct, contribution FROM nowcast_components WHERE run_id = ?', [run_id]).fetchall()}
    inter = {}
    for b, k, v in con.execute('SELECT block, key, value FROM nowcast_intermediates WHERE run_id = ?', [run_id]).fetchall():
        inter.setdefault(b, {})[k] = v
    coef = {(k1, k2): v for k1, k2, v in con.execute("SELECT k1, k2, value FROM est_params WHERE run_id = ? AND field = 'bridge'", [run_id]).fetchall()}
    agg = dict(con.execute('SELECT key, value FROM nowcast_aggregates WHERE run_id = ?', [run_id]).fetchall())
    return dict(comps=comps, inter=inter, coef=coef, agg=agg)


def model_growth(r, cid):
    """The annualized log growth the assembly works with (reported growth is its percent-change transform)."""
    i = r['inter']
    for blk, comp in BRIDGE_IDS.items():
        if comp == cid:
            return i.get(blk, {}).get('total')
    return {'CTG': i.get('CONS', {}).get('goods'), 'CS': i.get('CONS', {}).get('services'),
            'XM': i.get('TRADE_goods', {}).get('exports'), 'MM': i.get('TRADE_goods', {}).get('imports'),
            'XS': i.get('TRADE_services', {}).get('exports'), 'MS': i.get('TRADE_services', {}).get('imports'),
            'V': i.get('INV', {}).get('cipi')}.get(cid)


def blend_parts(a0, a1, b0, b1, w0, w1):
    """growth = w*A + (1-w)*B  ->  (A part, B part, weight part); exact."""
    wm = mid(w0, w1)
    return wm * (a1 - a0), (1 - wm) * (b1 - b0), (w1 - w0) * (mid(a0, a1) - mid(b0, b1))


def bridge_detail(cid, r0, r1):
    """Subcomponent -> indicator breakdown of the change in a bridge component's monthly-model growth."""
    i0, i1 = r0['inter'][cid], r1['inter'][cid]
    subs = BRIDGES[cid]['subcomponents']
    rows, comp_part = [], 0.0
    for s in subs:
        lhs = s['lhs']
        k = f'sub:{lhs}'
        if k not in i0 or k not in i1:
            continue
        sh0, sh1 = i0.get(f'share:{lhs}', 0.0), i1.get(f'share:{lhs}', 0.0)
        d_sub = i1[k] - i0[k]
        weight = mid(sh0, sh1)
        comp_part += (sh1 - sh0) * mid(i0[k], i1[k])
        items, explained = [], 0.0
        for ind in s.get('indicators', []):
            x0, x1 = i0.get(f'ind:{ind}'), i1.get(f'ind:{ind}')
            b0, b1 = r0['coef'].get((lhs, ind)), r1['coef'].get((lhs, ind))
            if None in (x0, x1, b0, b1):
                continue
            data, coefe = mid(b0, b1) * (x1 - x0), (b1 - b0) * mid(x0, x1)
            items.append(dict(name=ind, kind='indicator', d_ind=x1 - x0, ind_from=x0, ind_to=x1, coef=mid(b0, b1),
                              data=weight * data, coefficient=weight * coefe))
            explained += data + coefe
        c0, c1 = r0['coef'].get((lhs, 'Constant')), r1['coef'].get((lhs, 'Constant'))
        const_e = (c1 - c0) if None not in (c0, c1) else 0.0
        explained += const_e
        rows.append(dict(lhs=lhs, label=s['label'], kind=s['kind'], share=weight, sub_from=i0[k], sub_to=i1[k],
                         d_sub=d_sub, contribution_to_bridge=weight * d_sub, indicators=items,
                         constant=weight * const_e, other=weight * (d_sub - explained)))
    return rows, comp_part


def component_effects(r0, r1):
    """Decompose the change from run0 to run1 (dicts from load()). Returns a JSON-ready dict."""
    out, items = [], []
    for cid, c in r1['comps'].items():
        c0 = r0['comps'].get(cid)
        if c0 is None:
            continue
        dc = c['c'] - c0['c']
        m0, m1 = model_growth(r0, cid), model_growth(r1, cid)
        dg = (m1 - m0) if None not in (m0, m1) else 0.0
        den = (m1 + m0) if None not in (m0, m1) else 0.0
        k = (c['c'] + c0['c']) / den if abs(den) > 1e-6 else (dc / dg if abs(dg) > 1e-9 else 0.0)
        if cid == 'V':                       # contribution = (CIPI - previous CIPI) / GDP: linear in the level
            k = dc / dg if abs(dg) > 1e-9 else 0.0
        node = dict(id=cid, label=c['label'], g_from=c0['g'], g_to=c['g'], d_reported_growth=c['g'] - c0['g'],
                    c_from=c0['c'], c_to=c['c'], d_contribution=dc, d_growth=dg, k=k, parts=[], detail=None)
        blk = next((b for b, i in BRIDGE_IDS.items() if i == cid), None)
        if blk and blk in r0['inter'] and blk in r1['inter']:
            a, b_ = r0['inter'][blk], r1['inter'][blk]
            dm, dv, dw = blend_parts(a['bridge'], b_['bridge'], a['bvar'], b_['bvar'], a['w_monthly'], b_['w_monthly'])
            node['parts'] = [('Monthly-indicator model', dm), ('Quarterly BVAR', dv), ('Blend weight', dw)]
            subs, comp_part = bridge_detail(blk, r0, r1)
            node['detail'] = dict(subcomponents=subs, composition=mid(a['w_monthly'], b_['w_monthly']) * comp_part)
        elif cid in ('XM', 'MM', 'XS', 'MS'):
            blk = 'TRADE_goods' if cid in ('XM', 'MM') else 'TRADE_services'
            kind = 'exports' if cid in ('XM', 'XS') else 'imports'
            a, b_ = r0['inter'].get(blk, {}), r1['inter'].get(blk, {})
            if a and b_:
                dm, dv, dw = blend_parts(a[f'monthly_{kind}'], b_[f'monthly_{kind}'], a[f'bvar_{kind}'], b_[f'bvar_{kind}'],
                                         a['w_monthly'], b_['w_monthly'])
                node['parts'] = [('Monthly model', dm), ('Quarterly BVAR', dv), ('Blend weight', dw)]
        elif cid == 'V':
            a, b_ = r0['inter'].get('INV', {}), r1['inter'].get('INV', {})
            if a and b_:
                dm, dv, dw = blend_parts(a['cipi_monthly_model'], b_['cipi_monthly_model'], a['cipi_bvar'], b_['cipi_bvar'],
                                         a['w_monthly'], b_['w_monthly'])
                node['parts'] = [('Monthly inventory model', dm), ('Quarterly BVAR', dv), ('Blend weight', dw)]
        elif cid in ('CTG', 'CS'):
            blk, names = ('CONS', CONS_GOODS if cid == 'CTG' else CONS_SERVICES)
            a, b_ = r0['inter'].get(blk, {}), r1['inter'].get(blk, {})
            parts = []
            for n in names:
                q0, q1 = a.get(f'q:{n}'), b_.get(f'q:{n}')
                if None in (q0, q1):
                    continue
                w0, w1 = a.get(f'wt:{n}'), b_.get(f'wt:{n}')
                w0, w1 = (w1 if w0 is None else w0), (w0 if w1 is None else w1)       # older runs lack stored weights
                parts.append((n, (mid(w0, w1) * (q1 - q0)) if None not in (w0, w1) else (q1 - q0)))
            if parts and None not in (m0, m1):
                parts.append(('bucket weights', dg - sum(v for _, v in parts)))        # shifts in the nominal bucket shares
            node['parts'] = parts
        node['parts'] = [dict(name=n, d_growth=v, pp=v * node['k']) for n, v in node['parts']]
        node['other_pp'] = dc - node['k'] * node['d_growth']          # weights / prices in the Fisher aggregation
        out.append(node)
        items.append((cid, c['label'], dc))
    total = r1['agg']['GDP'] - r0['agg']['GDP']
    drivers = []
    for n in out:
        for p in n['parts']:
            drivers.append(dict(component=n['id'], what=p['name'], pp=p['pp']))
        for sub in (n['detail'] or {}).get('subcomponents', []):
            for it in sub['indicators']:
                drivers.append(dict(component=n['id'], what=f"{it['name']} (indicator data)", pp=it['data'] * n['k'], via=sub['lhs']))
                drivers.append(dict(component=n['id'], what=f"{it['name']} (coefficient)", pp=it['coefficient'] * n['k'], via=sub['lhs']))
            drivers.append(dict(component=n['id'], what=f"{sub['label']} (constant/other)", pp=(sub['constant'] + sub['other']) * n['k'], via=sub['lhs']))
        drivers.append(dict(component=n['id'], what='aggregation weights and prices', pp=n['other_pp']))
    return dict(drivers=sorted(drivers, key=lambda d: -abs(d['pp'])), headline=dict(from_=r0['agg']['GDP'], to=r1['agg']['GDP'], delta=total),
                components=out, aggregation_remainder=total - sum(x[2] for x in items))
