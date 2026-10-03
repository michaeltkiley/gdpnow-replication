"""Stage 07: write the replication report as a static HTML site (docs/index.html, GitHub Pages ready).

Reads stored results (DuckDB), diagnostics CSVs and the L3 estimates; no model is re-estimated here.
Usage: python scripts/07_site.py [--l3 L3_20261001] [--l2 L2_20261003] [--l1 L1_20261003]
"""
import argparse
import html
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import components as C, inputs, params, store
from gdpnow.config import DATA, ROOT

PUBLISHED_GDP = 3.677319405706525
SERIES = {'Published': '--s1', 'L2 (workbook data, our estimates)': '--s2', 'L3 (public data, our estimates)': '--s3'}


# ------------------------------------------------------------------------------------------ SVG helpers
def esc(x):
    return html.escape(str(x))


def svg_bars(rows, width=720, label_w=210, bar_h=18, gap=6, fmt='{:+.2f}', title='', series=None, xmin=None, xmax=None):
    """Horizontal grouped bars. rows: list of (label, [values per series]); series: list of (name, css var)."""
    series = series or [('', '--s1')]
    allv = [v for _, vs in rows for v in vs if v is not None and not np.isnan(v)]
    lo, hi = min(0, min(allv)) if xmin is None else xmin, max(0, max(allv)) if xmax is None else xmax
    pad = 0.08 * (hi - lo or 1)
    lo, hi = lo - pad, hi + pad
    plot_w = width - label_w - 60
    x = lambda v: label_w + (v - lo) / (hi - lo) * plot_w
    ns, group_h = len(series), len(series) * (bar_h + 2) + gap
    h = len(rows) * group_h + 36
    out = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="{esc(title)}" class="chart">']
    z = x(0)
    out.append(f'<line x1="{z:.1f}" x2="{z:.1f}" y1="6" y2="{h - 26}" class="axis"/>')
    for i, (lab, vs) in enumerate(rows):
        y0 = 8 + i * group_h
        out.append(f'<text x="{label_w - 8}" y="{y0 + group_h / 2 - 2}" text-anchor="end" class="lbl">{esc(lab)}</text>')
        for j, (v, (nm, var)) in enumerate(zip(vs, series)):
            if v is None or np.isnan(v):
                continue
            y = y0 + j * (bar_h + 2)
            x0, x1 = sorted([z, x(v)])
            out.append(f'<rect x="{x0:.1f}" y="{y}" width="{max(x1 - x0, 1):.1f}" height="{bar_h}" rx="3" '
                       f'fill="var({var})"><title>{esc(lab)} - {esc(nm)}: {fmt.format(v)}</title></rect>')
            tx = x1 + 5 if v >= 0 else x0 - 5
            out.append(f'<text x="{tx:.1f}" y="{y + bar_h - 5}" text-anchor="{"start" if v >= 0 else "end"}" '
                       f'class="val">{fmt.format(v)}</text>')
    out.append('</svg>')
    return ''.join(out)


def svg_lines(series, width=720, height=260, title='', ylabel='', labels=None):
    """series: list of (name, css var, pd.Series). Shared x (dates), one y axis."""
    allx = sorted(set().union(*[set(s.index) for _, _, s in series]))
    t0, t1 = allx[0], allx[-1]
    ys = [v for _, _, s in series for v in s.dropna()]
    lo, hi = min(ys), max(ys)
    pad = 0.1 * (hi - lo or 1)
    lo, hi = lo - pad, hi + pad
    L, R, T, B = 52, 90, 14, 28
    X = lambda d: L + (d - t0).days / max((t1 - t0).days, 1) * (width - L - R)
    Y = lambda v: T + (hi - v) / (hi - lo) * (height - T - B)
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}" class="chart">']
    for tick in np.linspace(lo + pad, hi - pad, 5):
        out.append(f'<line x1="{L}" x2="{width - R}" y1="{Y(tick):.1f}" y2="{Y(tick):.1f}" class="grid"/>'
                   f'<text x="{L - 6}" y="{Y(tick) + 4:.1f}" text-anchor="end" class="tick">{tick:.1f}</text>')
    if lo < 0 < hi:
        out.append(f'<line x1="{L}" x2="{width - R}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" class="axis"/>')
    for yr in sorted({d.year for d in allx}):
        d = pd.Timestamp(f'{yr}-01-01')
        if t0 <= d <= t1:
            out.append(f'<text x="{X(d):.1f}" y="{height - 8}" text-anchor="middle" class="tick">{yr}</text>')
    for name, var, s in series:
        s = s.dropna()
        pts = ' '.join(f'{X(d):.1f},{Y(v):.1f}' for d, v in s.items())
        out.append(f'<polyline points="{pts}" fill="none" stroke="var({var})" stroke-width="2" stroke-linejoin="round"/>')
        d, v = s.index[-1], s.iloc[-1]
        out.append(f'<circle cx="{X(d):.1f}" cy="{Y(v):.1f}" r="4" fill="var({var})" class="ring"/>'
                   f'<text x="{X(d) + 8:.1f}" y="{Y(v) + 4:.1f}" class="lbl">{esc(name)}</text>')
        for d, v in s.items():
            out.append(f'<circle cx="{X(d):.1f}" cy="{Y(v):.1f}" r="7" fill="transparent"><title>{esc(name)}, {d:%b %Y}: {v:.2f}</title></circle>')
    out.append(f'<text x="4" y="10" class="tick">{esc(ylabel)}</text></svg>')
    return ''.join(out)


def legend(series):
    return '<div class="legend">' + ''.join(
        f'<span><i style="background:var({v})"></i>{esc(n)}</span>' for n, v in series) + '</div>'


def table(df, fmt=None, cls=''):
    fmt = fmt or {}
    head = ''.join(f'<th scope="col">{esc(c)}</th>' for c in df.columns)
    body = ''
    for _, r in df.iterrows():
        cells = ''
        for c in df.columns:
            v = r[c]
            if isinstance(v, (float, np.floating)):
                cells += '<td class="num">' + ('' if np.isnan(v) else fmt.get(c, '{:.3f}').format(v)) + '</td>'
            else:
                cells += f'<td>{esc(v)}</td>'
        body += f'<tr>{cells}</tr>'
    return f'<div class="tablewrap"><table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


# --------------------------------------------------------------------------------------------- data
def load(con, run):
    comps = store.query(con, 'SELECT id, label, growth_pct, contribution FROM nowcast_components WHERE run_id = ?', (run,)).set_index('id')
    agg = dict(store.query(con, 'SELECT key, value FROM nowcast_aggregates WHERE run_id = ?', (run,)).values)
    return comps, agg


def published_path(con, vintage):
    cells = store.query(con, "SELECT sheet, row, col, num, text FROM wb_cells WHERE vintage = ? AND sheet = 'TrackingHistory'", (vintage,))
    dates = {r.col: pd.Timestamp(r.text) for r in cells[cells.row == 1].itertuples() if r.text}
    lab = {r.row: r.text for r in cells[cells.col == 1].itertuples() if r.text}
    gdp_row = next(r for r, t in lab.items() if str(t).startswith('GDP'))
    vals = {r.col: r.num for r in cells[cells.row == gdp_row].itertuples() if r.num is not None}
    return pd.Series({dates[c]: v for c, v in vals.items() if c in dates}).sort_index()


def indicator_table(con, l2_run, l3_est, vintage):
    l2 = params.overlay(con, l2_run, inputs.from_workbook(con, vintage))
    l3 = l3_est
    m2, m3 = C.Monthly(l2, l2.growth, l2.levels, l2.faar), C.Monthly(l3, l3.growth, l3.levels, l3.faar)
    rows = []
    for cid, comp in C.BRIDGES['components'].items():
        for s in comp['subcomponents']:
            for ind in s.get('indicators', []):
                try:
                    rows.append((comp['label'], ind, C.indicator_growth(ind, m2, l2), C.indicator_growth(ind, m3, l3)))
                except Exception:
                    pass
    d = pd.DataFrame(rows, columns=['Component', 'Indicator', 'Workbook data (L2)', 'Public data (L3)']).drop_duplicates('Indicator')
    d['Difference'] = d['Public data (L3)'] - d['Workbook data (L2)']
    return d


# --------------------------------------------------------------------------------------------- page
CSS = """
:root{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#807f7a;--line:#e5e4de;--card:#ffffff;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--surface:#fcfcfb}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121211;--ink:#fff;--ink2:#c3c2b7;--muted:#9a998f;--line:#2f2f2c;--card:#1a1a19;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--surface:#1a1a19}}
:root[data-theme="dark"]{--bg:#121211;--ink:#fff;--ink2:#c3c2b7;--muted:#9a998f;--line:#2f2f2c;--card:#1a1a19;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--surface:#1a1a19}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 16px 80px}
h1{font-size:2rem;line-height:1.2;margin:0 0 6px}h2{font-size:1.35rem;margin:44px 0 8px}h3{font-size:1.05rem;margin:26px 0 6px}
p,li{color:var(--ink2)}a{color:var(--s1)}code{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-size:.88em}
.sub{color:var(--muted);margin:0 0 22px}.hero{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}
.tile{flex:1 1 180px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.tile b{display:block;font-size:1.9rem;color:var(--ink);line-height:1.1}.tile span{color:var(--muted);font-size:.85rem}
.chart{width:100%;height:auto;background:var(--surface);border:1px solid var(--line);border-radius:10px;margin:8px 0}
.chart .lbl{fill:var(--ink);font-size:12px}.chart .val{fill:var(--ink2);font-size:11px}.chart .tick{fill:var(--muted);font-size:11px}
.chart .axis{stroke:var(--muted);stroke-width:1}.chart .grid{stroke:var(--line);stroke-width:1}.chart .ring{stroke:var(--surface);stroke-width:2}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:.88rem;color:var(--ink2);margin:6px 0}.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.tablewrap{overflow-x:auto;margin:8px 0}table{border-collapse:collapse;width:100%;font-size:.88rem;background:var(--card)}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left}th{color:var(--ink);font-weight:600;white-space:nowrap}td.num{text-align:right;font-variant-numeric:tabular-nums;color:var(--ink)}
.note{border-left:3px solid var(--s2);padding:4px 14px;background:var(--card);border-radius:0 8px 8px 0;margin:14px 0}
nav a{margin-right:14px;font-size:.9rem}details{margin:10px 0}summary{cursor:pointer;color:var(--ink)}
"""


def build(args):
    con = store.connect()
    c1, a1 = load(con, args.l1)
    c2, a2 = load(con, args.l2)
    c3, a3 = load(con, args.l3)
    runs = store.query(con, 'SELECT run_id, vintage FROM runs').set_index('run_id').vintage
    vintage = runs[args.l1]
    path = published_path(con, vintage)
    att = pd.read_csv(DATA / f'{vintage}_attribution_{args.l2}.csv')
    est3 = pickle.load(open(next(DATA.glob('*_estimated_' + args.l3 + '.pkl')), 'rb'))['est']
    ind = indicator_table(con, args.l2, est3, vintage)
    diag3 = pd.read_csv(next(DATA.glob('*_diagnostics_' + args.l3 + '.csv')))
    wbf = store.series_frame(con, vintage, 'Factor')['Actual+Forecast']
    ours = params.overlay(con, args.l3, inputs.from_workbook(con, vintage)).factor
    reg = pd.read_csv(ROOT / 'registry' / 'parameters.csv')
    ver = {r: pd.read_csv(next(DATA.glob(f'*_verify_{r}.csv'))) for r in (args.l1, args.l2, args.l3)}

    gdp = {'Published': PUBLISHED_GDP, 'L1': a1['GDP'], 'L2': a2['GDP'], 'L3': a3['GDP']}
    comp_rows = [(c3.label[k], [comps_c.contribution.get(k) for comps_c in (c1, c2, c3)]) for k in c3.index]
    ser = [('Published (= L1)', '--s1'), ('L2: workbook data, our estimates', '--s2'), ('L3: public data, our estimates', '--s3')]
    sec = []

    sec.append(f"""
<h1>Replicating the Atlanta Fed GDPNow nowcast</h1>
<p class="sub">Target: the GDPNow estimate for 2026:Q3 published October 1, 2026. Independent Python implementation; every
coefficient, weight and transformation is re-estimated from data on each run. Local replication package, not affiliated
with the Federal Reserve Bank of Atlanta.</p>
<nav><a href="#headline">Headline</a><a href="#components">Components</a><a href="#stages">Stages</a><a href="#data">Public data gaps</a><a href="#factor">Factor</a><a href="#registry">Registry</a><a href="#reproduce">Reproduce</a></nav>
<div class="hero">
<div class="tile"><b>{PUBLISHED_GDP:.2f}%</b><span>Published GDPNow, 2026:Q3 (SAAR)</span></div>
<div class="tile"><b>{gdp['L1']:.4f}%</b><span>L1: our code on Atlanta Fed inputs ({gdp['L1'] - PUBLISHED_GDP:+.1e} pp)</span></div>
<div class="tile"><b>{gdp['L2']:.2f}%</b><span>L2: re-estimated on their data ({gdp['L2'] - PUBLISHED_GDP:+.2f} pp)</span></div>
<div class="tile"><b>{gdp['L3']:.2f}%</b><span>L3: public data only ({gdp['L3'] - PUBLISHED_GDP:+.2f} pp)</span></div></div>
""")

    sec.append(f"""<h2 id="headline">Headline result</h2>
<p>Three levels of replication, each stricter than the last. <b>L1</b> feeds the Atlanta Fed's own inputs and estimated
parameters to our code: it reproduces the published nowcast to {abs(gdp['L1'] - PUBLISHED_GDP):.1e} pp, which validates the model
assembly (all 13 components, contributions, 138 intermediate cells). <b>L2</b> re-estimates every parameter from their data.
<b>L3</b> rebuilds the data from public sources (FRED/ALFRED as of Oct 1, BEA, Census, BLS, Treasury) and re-estimates everything.</p>
<p>Success was defined as the headline within ±0.1 pp. <b>L1 passes; L2 ({gdp['L2'] - PUBLISHED_GDP:+.2f} pp) and L3 ({gdp['L3'] - PUBLISHED_GDP:+.2f} pp) do not.</b>
L3 is within 0.25 pp, but it is not a ±0.1 pp replication; the report below says where each gap comes from.</p>""")
    path_s = path.loc[:]
    sec.append(legend([('Published nowcast path', '--s1')]) + svg_lines(
        [('Published', '--s1', path_s)], title='Evolution of the published GDPNow estimate for 2026Q3', ylabel='% SAAR'))
    sec.append('<p class="sub">Daily evolution of the published 2026:Q3 nowcast (workbook TrackingHistory), ending at the October 1 value being replicated.</p>')

    sec.append(f'<h2 id="components">Contributions to growth, by level</h2>{legend(ser)}')
    sec.append(svg_bars(comp_rows, title='Contributions to 2026Q3 growth, percentage points', series=ser, fmt='{:+.2f}'))
    tbl = pd.DataFrame({'Component': c3.label, 'Published growth %': c1.growth_pct, 'L2 growth %': c2.growth_pct, 'L3 growth %': c3.growth_pct,
                        'Published contrib.': c1.contribution, 'L2 contrib.': c2.contribution, 'L3 contrib.': c3.contribution}).reset_index(drop=True)
    sec.append(table(tbl, {k: '{:+.2f}' for k in tbl.columns[1:]}))
    sec.append('<p class="sub">L1 equals the published numbers to ≈1e-6 and is not plotted separately. Inventories enter only as a contribution.</p>')

    sec.append('<h2 id="stages">Where the L2 gap comes from</h2><p>Replace one Atlanta Fed estimate at a time with ours (holding everything else at their values), and measure the headline change:</p>')
    a = att.copy()
    rows = [(r.stage, [r.alone]) for r in a.itertuples()]
    sec.append(svg_bars(rows, series=[('Effect alone on the headline (pp)', '--s2')], title='Headline effect of each re-estimated stage', fmt='{:+.3f}', label_w=270))
    sec.append(table(a.rename(columns={'stage': 'Stage', 'alone': 'Effect alone (pp)', 'cumulative': 'Cumulative (pp)'}), {'Effect alone (pp)': '{:+.4f}', 'Cumulative (pp)': '{:+.4f}'}))
    sec.append("""<div class="note"><b>Two items explain nearly all of L2's gap.</b> The dynamic factor's latest month (September 2026) depends on survey data the
workbook does not contain, so on the workbook's own data our factor reads neutral for September, as does the Atlanta Fed's own “AltFactor” sheet
(+0.25 pp). Second, the workbook's farm and “other” inventory AR(4) coefficients cannot be reproduced from any sample window or specification (+0.12 pp).</div>""")

    sec.append(f"""<h2 id="data">L3: what changes with public data only</h2>
<p>L3 differs from L2 only in data. Public regional Fed surveys (Philadelphia, New York, Dallas) replace the licensed ISM series, and with them the factor's September
value moves to {float(diag3[(diag3.block == 'factor') & (diag3.item == 'Sep value')].ours.iloc[0]):+.2f} (workbook: {float(diag3[(diag3.block == 'factor') & (diag3.item == 'Sep value')].workbook.iloc[0]):+.2f}), so most of the L2 factor gap closes.
The remaining differences come from series the Atlanta Fed constructs with licensed or proprietary inputs. The table compares each bridge indicator's quarterly growth (SAAR, %) built from
their data and from public data:</p>""")
    sec.append(table(ind, {'Workbook data (L2)': '{:+.1f}', 'Public data (L3)': '{:+.1f}', 'Difference': '{:+.1f}'}))
    sec.append("""<div class="note"><b>Known public-data gaps (all documented in DESIGN.md §11):</b> Haver seasonal adjustment replaced by Census X-13 on Treasury outlays, trade end-use
categories and prices; no public monthly series for federal vs state-and-local construction spending (total public construction is used for both); no long public history of
existing-home sales (brokers' commissions indicator uses new-home sales only); licensed ISM and Macroeconomic Advisers/S&P monthly GDP replaced by regional-survey averages and a
BEA-based interpolation; nondurable manufacturers' inventories for the latest month appear only in the full M3 report (Oct 2) and are forecast.</div>""")

    sec.append('<h2 id="factor">The dynamic factor</h2>')
    fr = pd.concat([ours, wbf], axis=1, keys=['ours', 'wb']).dropna().loc['2022-01-01':]
    sec.append(legend([('Atlanta Fed factor', '--s1'), ('Our factor (L3, public data)', '--s3')]) + svg_lines(
        [('Atlanta Fed', '--s1', fr.wb), ('Ours (L3)', '--s3', fr.ours)], title='Dynamic factor, 2022 to 2026', ylabel='factor (standardized)'))
    j = pd.concat([ours, wbf], axis=1, keys=['ours', 'wb']).dropna()
    sec.append(f'<p class="sub">Correlation over 1967–2026: {j.ours.corr(j.wb):.4f}. The last point is the ragged-edge month (September 2026), estimated from only a handful of series.</p>')

    sec.append('<h2 id="registry">What counts as a coefficient: the parameter registry</h2>')
    sec.append("<p>Every number in the model that is not raw data (53 families) is listed in <code>registry/parameters.csv</code> and classified by how it is produced on each run. "
               "The verification step fails a run if any estimated parameter, weight or transformation constant was read from the workbook.</p>")
    g = reg.groupby('group').agg(families=('id', 'count'), how=('how_produced', lambda s: ', '.join(sorted(set(s))))).reset_index()
    sec.append(table(g.rename(columns={'group': 'Group', 'families': 'Families', 'how': 'How produced'}), {'Families': '{:.0f}'}))
    v3 = ver[args.l3]
    prov = v3[v3.kind == 'provenance']
    sec.append(f'<p>Provenance check, L3 run: <b>{(prov.status == "OK").sum()} of {len(prov)}</b> registry links produced from public data or our estimates; none from the workbook.</p>')

    sec.append(f"""<h2 id="reproduce">Reproduce</h2>
<details open><summary>Pipeline</summary><pre><code>python scripts/01_ingest_workbook.py --date 20261003      # dated workbook + sha256 (benchmark and L1/L2 data)
python scripts/05_nowcast.py --level L1 &amp;&amp; python scripts/06_verify.py --run L1_20261003
python scripts/04_estimate.py --level L2 &amp;&amp; python scripts/05_nowcast.py --level L2 &amp;&amp; python scripts/06_verify.py --run L2_20261003
python scripts/02_build_public.py --asof 2026-10-01 --last-price-month 2026-08
python scripts/04_estimate.py --level L3 --asof 2026-10-01
python scripts/05_nowcast.py --level L3 --asof 2026-10-01 &amp;&amp; python scripts/06_verify.py --run L3_20261001
python scripts/07_site.py</code></pre></details>
<p>For production use, run stages 02–05 with today's date: <code>--asof</code> defaults to today and every pull is archived with its retrieval date. API keys (FRED, BEA, Census) go in <code>.env</code>.
Design decisions, the audit of the workbook, and every deviation are in <a href="../DESIGN.md">DESIGN.md</a> in the repository.</p>
<p class="sub">Sources: Higgins (2014), FRBA Working Paper 2014-7; “Modifications to GDPNow Model” (FRBA, 2017–2025); GDPNow model workbook (not redistributed: contains Haver Analytics data).</p>""")
    return '\n'.join(sec)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--l1', default='L1_20261003')
    ap.add_argument('--l2', default='L2_20261003')
    ap.add_argument('--l3', default='L3_20261001')
    a = ap.parse_args()
    body = build(a)
    out = ROOT / 'docs'
    out.mkdir(exist_ok=True)
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>GDPNow Replication</title><style>{CSS}</style></head><body><main>{body}</main></body></html>')
    (out / 'index.html').write_text(page)
    print(f'wrote {out / "index.html"} ({len(page) // 1024} KB)')


if __name__ == '__main__':
    main()
