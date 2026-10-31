const $ = id => document.getElementById(id);
const fm = (v, d = 2) => v == null || isNaN(v) ? '–' : (+v).toFixed(d);
const sg = (v, d = 2) => v == null ? '–' : (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(d);
const qlab = q => 'Q' + q.slice(5) + ' ' + q.slice(0, 4);
const MODEL = { full: { name: 'MIDAS, 14 indicators', c: 'c', ym: 'ym_full' }, trio: { name: 'MIDAS, 3 indicators', c: 'ct', ym: 'ym_trio' } };
const GL = { labor: 'Labor market', production: 'Production & orders', spending: 'Spending & sentiment', housing: 'Housing', trade: 'Trade', inventories: 'Inventories', prices: 'Prices', financial: 'Financial conditions', 'GDP lag': 'Last quarter’s growth', trend: 'Trend growth (per person)', pop: 'Population' };
const HLAB = ['End of month 1', 'End of month 2', 'End of month 3', '15th of month after'];
const COL = { model: 'var(--series-rep)', gn: 'var(--series-gdpnow)', repl: 'var(--g4)', ar: 'var(--ink-muted)', act: 'var(--ink)' };
let S, D, dates, st = { m: 'full', sub: 'data', q: null, since: null };
const tip = $('tip');
function showTip(e, h) { tip.innerHTML = h; tip.style.display = 'block'; const w = tip.offsetWidth; tip.style.left = Math.min(e.clientX + 14, innerWidth - w - 8) + 'px'; tip.style.top = (e.clientY + 14) + 'px'; }
function hideTip() { tip.style.display = 'none'; }
const gid = Object.fromEntries((window.__P = []).concat([]));

function recFor(date, q) { return (D[date] || []).find(r => r.q === q); }
function datesOf(q) { return dates.filter(d => recFor(d, q)); }
function stage(date, q) { const m1 = (+q.slice(5) - 1) * 3, m = new Date(date).getUTCMonth(); return Math.max(0, Math.min(3, m - m1 - (new Date(date).getUTCFullYear() - +q.slice(0, 4)) * 12)); }
function groupOf(id) { if (id === 'gdp_lag') return 'GDP lag'; const p = S.predictors.find(x => x.id === id); return p ? p.group : id; }
function decomp(rec, m) {
  const M = MODEL[m], o = {};
  for (const [id, v] of Object.entries(rec[M.c] || {})) { const g = groupOf(id); o[g] = (o[g] || 0) + v; }
  o.trend = rec[M.ym]; o.pop = rec.pop; return o;
}
function actualOf(q) { const r = S.track.find(t => t.qp === q.replace('Q', 'Q') && t.actual != null); return r ? r.actual : null; }
const toX = d => Date.parse(d);

/* ---------------- generic line chart ---------------- */
function lineChart(el, series, o) {
  const W = 760, H = 300, L = 44, R = 16, T = 12, B = 30, w = W - L - R, h = H - T - B;
  const all = series.flatMap(s => s.pts.map(p => p[1])).concat((o.hl || []).map(x => x.y));
  let y0 = o.ymin ?? Math.floor(Math.min(...all, 0) - 0.3), y1 = o.ymax ?? Math.ceil(Math.max(...all) + 0.3);
  const X = x => L + (x - o.x0) / (o.x1 - o.x0 || 1) * w, Y = y => T + (y1 - y) / (y1 - y0) * h;
  let s = '';
  const step = (y1 - y0) > 12 ? 4 : (y1 - y0) > 6 ? 2 : 1;
  for (let v = Math.ceil(y0 / step) * step; v <= y1; v += step) s += `<line class="grid" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${v}</text>`;
  if (y0 < 0 && y1 > 0) s += `<line class="axis" x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}"/>`;
  for (const [x, lab] of o.ticks) s += `<line class="grid" x1="${X(x)}" x2="${X(x)}" y1="${T}" y2="${T + h}"/><text x="${X(x)}" y="${H - 8}" text-anchor="middle">${lab}</text>`;
  for (const hl of (o.hl || [])) s += `<line x1="${L}" x2="${W - R}" y1="${Y(hl.y)}" y2="${Y(hl.y)}" stroke="${hl.color}" stroke-width="1.5" stroke-dasharray="6 4"/><text x="${W - R}" y="${Y(hl.y) - 5}" text-anchor="end" style="fill:${hl.color}">${hl.label}</text>`;
  for (const se of series) {
    const pts = se.pts.filter(p => p[1] != null);
    if (!pts.length) continue;
    if (se.line !== false) s += `<polyline fill="none" stroke="${se.color}" stroke-width="2" ${se.dash ? 'stroke-dasharray="5 4"' : ''} stroke-linejoin="round" points="${pts.map(p => X(p[0]).toFixed(1) + ',' + Y(Math.max(y0, Math.min(y1, p[1]))).toFixed(1)).join(' ')}"/>`;
    if (se.dots !== false) for (const p of pts) s += `<circle cx="${X(p[0]).toFixed(1)}" cy="${Y(Math.max(y0, Math.min(y1, p[1]))).toFixed(1)}" r="${se.r || 3}" fill="${se.color}" data-t="${se.name}: ${p[2] ?? ''} <b>${fm(p[1])}</b>"/>`;
  }
  el.innerHTML = s;
  el.onpointermove = e => { const t = e.target.getAttribute && e.target.getAttribute('data-t'); if (t) showTip(e, t); else hideTip(); };
  el.onpointerleave = hideTip;
}
function monthTicks(a, b) { const t = []; const d = new Date(a); d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() + 1); while (+d <= b) { t.push([+d, d.toLocaleString('en', { month: 'short', timeZone: 'UTC' })]); d.setUTCMonth(d.getUTCMonth() + 1); } return t; }
function legend(el, items) { el.innerHTML = items.map(i => `<span class="item"><i class="ln" style="background:${i[1]};${i[2] ? 'height:0;border-top:2px dashed ' + i[1] : ''}"></i>${i[0]}</span>`).join(''); }

/* ---------------- waterfall ---------------- */
function waterfall(el, a, b, da, db) {
  const keys = Object.keys(GL).filter(k => (a[k] != null || b[k] != null));
  const items = keys.map(k => ({ k, v: (b[k] || 0) - (a[k] || 0) })).filter(x => Math.abs(x.v) >= 0.005);
  const startV = Object.values(a).reduce((s, v) => s + (v || 0), 0), endV = Object.values(b).reduce((s, v) => s + (v || 0), 0);
  const rows = [{ lab: `Nowcast ${da}`, from: 0, to: startV, tot: true }];
  let run = startV;
  for (const it of items) { rows.push({ lab: GL[it.k], from: run, to: run + it.v, v: it.v }); run += it.v; }
  rows.push({ lab: `Nowcast ${db}`, from: 0, to: endV, tot: true });
  const W = 760, LW = 190, RH = 28, H = rows.length * RH + 24, vals = rows.flatMap(r => [r.from, r.to]).concat([0]);
  const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo || 1) * 0.12, x0 = lo - pad, x1 = hi + pad;
  const X = v => LW + (v - x0) / (x1 - x0) * (W - LW - 56);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Waterfall of changes in the nowcast">`;
  s += `<line class="axis" x1="${X(0)}" x2="${X(0)}" y1="4" y2="${H - 18}"/>`;
  rows.forEach((r, i) => {
    const y = 6 + i * RH, xa = Math.min(X(r.from), X(r.to)), xb = Math.max(X(r.from), X(r.to));
    const fill = r.tot ? 'var(--ink-secondary)' : (r.v >= 0 ? 'var(--series-rep)' : 'var(--series-gdpnow)');
    s += `<text class="lbl" x="${LW - 8}" y="${y + 15}" text-anchor="end" ${r.tot ? 'style="font-weight:600"' : ''}>${r.lab}</text>`;
    s += `<rect x="${xa.toFixed(1)}" y="${y}" width="${Math.max(xb - xa, 1.5).toFixed(1)}" height="${RH - 8}" rx="3" fill="${fill}"/>`;
    const lab = r.tot ? fm(r.to) : sg(r.v);
    s += `<text x="${(xb + 6).toFixed(1)}" y="${y + 14}">${lab}</text>`;
    if (i < rows.length - 1 && !rows[i + 1].tot) s += `<line stroke="var(--rule)" stroke-dasharray="2 3" x1="${X(r.to)}" x2="${X(r.to)}" y1="${y + RH - 8}" y2="${y + RH}"/>`;
  });
  el.innerHTML = s + '</svg>' + (items.length ? '' : '<p class="sub">No change in any component over this period.</p>');
}

/* ---------------- render: data tab ---------------- */
function pickSince() {
  const ds = datesOf(st.q), last = ds[ds.length - 1];
  if (!st.since || !ds.includes(st.since)) { const t = new Date(last); t.setUTCDate(t.getUTCDate() - 7); st.since = [...ds].reverse().find(d => d <= t.toISOString().slice(0, 10)) || ds[0]; }
}
function renderData() {
  const m = st.m, M = MODEL[m], ds = datesOf(st.q), last = ds[ds.length - 1];
  pickSince();
  const now = recFor(last, st.q), then = recFor(st.since, st.q), act = actualOf(st.q), a = stage(last, st.q);
  const rm = S.stats.pre.rmsfe[m][a], other = MODEL[m === 'full' ? 'trio' : 'full'];
  const chg = now[m] - then[m];
  $('headline').innerHTML = `<p class="lead">${qlab(st.q)} real GDP growth, ${M.name}</p>
    <div class="big">${fm(now[m])}<small>% SAAR</small></div>
    <div class="when">As of ${last}${act != null ? ` · BEA advance estimate: <b>${fm(act)}%</b>` : ''} · ${sg(chg)} pp since ${st.since}</div>
    <div class="tiles">
      <div class="tile"><b>${fm(now.gdpnow)}</b><span>Published GDPNow (Atlanta Fed)</span></div>
      <div class="tile"><b>${fm(now.repl)}</b><span>GDPNow replication (this site)</span></div>
      <div class="tile"><b>${fm(now[m === 'full' ? 'trio' : 'full'])}</b><span>${other.name}</span></div>
      <div class="tile"><b>${fm(now.ar2)}</b><span>No-indicator benchmark (AR(2) + population)</span></div>
      <div class="tile"><b>±${fm(rm, 1)}</b><span>Typical miss at this stage (${HLAB[a].toLowerCase()}; 2005–19 RMSFE)</span></div>
      <div class="tile"><b>${now.n}</b><span>Quarters used to fit the model</span></div>
    </div>`;
  $('qsel').innerHTML = [...new Set(dates.flatMap(d => D[d].map(r => r.q)))].sort().reverse().map(q => `<option value="${q}" ${q === st.q ? 'selected' : ''}>${qlab(q)}</option>`).join('');
  const si = $('since'); si.min = ds[0]; si.max = last; si.value = st.since;
  $('stamp').textContent = `${st.since} → ${last}`;
  $('wfTitle').textContent = `What changed in the ${qlab(st.q)} nowcast`;
  waterfall($('wf'), decomp(then, m), decomp(now, m), st.since, last);
  // evolution
  const pts = k => ds.map(d => [toX(d), recFor(d, st.q)[k], d]);
  const x0 = toX(ds[0]), x1 = toX(last);
  $('evoTitle').textContent = `Evolution of the ${qlab(st.q)} estimate`;
  const ser = [{ name: M.name, color: COL.model, pts: pts(m), r: 2.2 }, { name: 'GDPNow', color: COL.gn, pts: pts('gdpnow'), r: 2.2 }, { name: 'GDPNow replication', color: COL.repl, pts: pts('repl'), r: 2.2 }, { name: 'No-indicator benchmark', color: COL.ar, dash: 1, dots: false, pts: pts('ar2') }];
  lineChart($('evo'), ser, { x0, x1: x1 + (x1 - x0) * 0.02 + 1, ticks: monthTicks(x0, x1), hl: act != null ? [{ y: act, color: 'var(--ink)', label: `BEA advance ${fm(act)}` }] : [] });
  legend($('evoLegend'), [[M.name, COL.model], ['Published GDPNow', COL.gn], ['GDPNow replication (this site)', COL.repl], ['No-indicator benchmark', COL.ar, 1]]);
  // indicator table
  const cs = now[M.c], rows = Object.entries(cs).map(([id, v]) => { const p = S.predictors.find(x => x.id === id); return { id, lab: id === 'gdp_lag' ? 'Previous quarter’s GDP growth' : p.label, g: GL[groupOf(id)], v, lag: (now.lags || {})[id] }; }).sort((x, y) => Math.abs(y.v) - Math.abs(x.v));
  $('tabCap').textContent = `${qlab(st.q)}, as of ${last}`;
  $('indtab').innerHTML = '<tr><th>Indicator</th><th>Group</th><th class="num">Behind (months)</th><th class="num">Contribution, pp</th></tr>' +
    rows.map(r => `<tr><td>${r.lab}</td><td>${r.g}</td><td class="num">${m === 'full' && r.lag != null ? r.lag : '–'}</td><td class="num ${r.v >= 0 ? 'pos' : 'neg'}">${sg(r.v)}</td></tr>`).join('') +
    `<tr><td>Trend growth per person</td><td>Intercept</td><td class="num">–</td><td class="num">${fm(now[M.ym])}</td></tr><tr><td>Population growth assumed</td><td>Added back</td><td class="num">–</td><td class="num">${fm(now.pop)}</td></tr><tr><th colspan="3">Nowcast</th><th class="num">${fm(now[m])}</th></tr>`;
}

/* ---------------- render: replication tab ---------------- */
function renderRep() {
  const m = st.m, M = MODEL[m];
  $('predtab').innerHTML = '<tr><th>Indicator</th><th>Group</th><th>Frequency</th><th>Transform</th><th>In this model</th></tr>' + S.predictors.map(p => {
    const inm = m === 'trio' ? p.trio : p.used; return `<tr style="${inm ? '' : 'opacity:.45'}"><td>${p.label} <span class="caption">(${p.id})</span></td><td>${GL[p.group]}</td><td>${p.freq}</td><td>${p.transform}</td><td>${inm ? 'yes' : (p.used ? 'no' : 'tested, dropped')}</td></tr>`; }).join('');
  let acc = '';
  for (const k of ['pre', 'post', 'gn', 'covid']) {
    const s = S.stats[k], gn = s.rmsfe.gdpnow, mm = s.rmsfe[m], ar = s.rmsfe.ar2;
    acc += `<h3>${s.label} <span class="caption">(${s.quarters} quarters)</span></h3><div class="tablewrap"><table><tr><th>Forecast date</th><th class="num">${M.name}</th><th class="num">No-indicator benchmark</th>${gn ? '<th class="num">Published GDPNow</th>' : ''}</tr>` +
      [0, 1, 2, 3].map(i => `<tr><td>${HLAB[i]}</td><td class="num"><b>${fm(mm[i])}</b></td><td class="num">${fm(ar[i])}</td>${gn ? `<td class="num">${fm(gn[i])}</td>` : ''}</tr>`).join('') + '</table></div>';
  }
  if (m === 'full') acc += `<p class="sub">Average error at the end of the quarter (nowcast minus outcome), ${M.name}: ${sg(S.bias.pre[m] ?? S.bias.pre.full)} pp in 2005–19 and ${sg(S.bias.post[m] ?? S.bias.post.full)} pp in 2022–26 (14-indicator model; the benchmark: ${sg(S.bias.pre.ar2)} and ${sg(S.bias.post.ar2)}).</p>`;
  $('acc').innerHTML = acc;
  // track
  const tr = S.track.filter(r => r.h === 3 && r.actual != null);
  const qs = tr.map(r => r.qp), xi = i => i, N = qs.length;
  const mk = k => tr.map((r, i) => [i, r[k], qlab(r.qp.replace('Q', '-Q').replace('-Q', '').length ? r.qp.slice(0, 4) + '-' + r.qp.slice(4) : r.qp)]);
  const yr = []; qs.forEach((q, i) => { if (q.endsWith('Q1') && +q.slice(0, 4) % 3 === 0 || (q === qs[0])) yr.push([i, q.slice(0, 4)]); });
  const labs = (k) => tr.map((r, i) => [i, r[k], r.qp]);
  lineChart($('trk'), [{ name: 'BEA advance estimate', color: COL.act, pts: labs('actual'), line: true, r: 2.5 }, { name: M.name, color: COL.model, pts: labs(m), line: false, r: 3.2 }, { name: 'GDPNow', color: COL.gn, pts: labs('gdpnow'), line: false, r: 3.2 }, ], { x0: -1, x1: N, ticks: yr, ymin: -8, ymax: 10 });
  legend($('trkLegend'), [['BEA advance estimate', COL.act], [M.name + ' (end of quarter)', COL.model], ['Published GDPNow (end of quarter)', COL.gn]]);
  const last8 = tr.slice(-8).reverse();
  $('lastq').innerHTML = '<tr><th>Quarter</th><th class="num">Advance estimate</th><th class="num">' + M.name + '</th><th class="num">GDPNow</th><th class="num">AR(2) benchmark</th></tr>' + last8.map(r => `<tr><td>${qlab(r.qp.slice(0, 4) + '-' + r.qp.slice(4))}</td><td class="num">${fm(r.actual)}</td><td class="num">${fm(r[m])}</td><td class="num">${fm(r.gdpnow)}</td><td class="num">${fm(r.ar2)}</td></tr>`).join('');
  // blend
  if (m === 'full') {
    $('blend').innerHTML = '<p class="sub" style="margin-top:0">If you already follow GDPNow, does a share of this model improve on it? The weight is the MIDAS share that minimises error over the sample (chosen in sample, so the gain is an upper bound).</p>' +
      Object.entries(S.blend).map(([lab, rows]) => `<h3>${lab}</h3><div class="tablewrap"><table><tr><th>Forecast date</th><th class="num">GDPNow</th><th class="num">MIDAS</th><th class="num">Best MIDAS weight</th><th class="num">Blend</th><th class="num">Error correlation</th></tr>` + rows.map((r, i) => `<tr><td>${HLAB[i]}</td><td class="num">${fm(r.gdpnow)}</td><td class="num">${fm(r.midas)}</td><td class="num">${Math.round(r.w * 100)}%</td><td class="num"><b>${fm(r.blend)}</b></td><td class="num">${fm(r.corr)}</td></tr>`).join('') + '</table></div>').join('') +
      '<p class="sub">Before COVID, a 35–50% weight on MIDAS lowers GDPNow’s error by roughly 8–13%. Since 2022 the gain is small (0–25% weight, 0–8% lower error), so treat MIDAS as a cross-check rather than an input.</p>';
  } else $('blend').innerHTML = '<p class="sub">The blend analysis uses the 14-indicator model.</p>';
  $('path').innerHTML = '<tr><th>Step</th><th class="num">2005–19</th><th class="num">2022–26</th><th class="num">AR(2) 2005–19</th><th class="num">AR(2) 2022–26</th></tr>' + S.path.map(p => `<tr><td>${p.label}</td><td class="num">${fm(p.pre)}</td><td class="num">${fm(p.post)}</td><td class="num">${fm(p.ar_pre)}</td><td class="num">${fm(p.ar_post)}</td></tr>`).join('') + '<tr><td colspan="5" class="caption">RMSFE at the end of the quarter, 14- or 15-indicator model.</td></tr>';
}

/* ---------------- wiring ---------------- */
const SUBT = { full: ['Where is GDP growth heading, according to 14 monthly, weekly and daily indicators?', 'Industrial production, payrolls, retail sales, consumption, orders, housing, inventories, prices, sentiment and financial conditions, each entering with a flexible lag profile and shrunk toward zero.'], trio: ['What do just three indicators say?', 'Industrial production, the Chicago Fed financial conditions index and the yield-curve slope: the small information set used in Chan’s MIDAS tutorial and in Chan, Poon and Zhu’s paper, here with the same real-time estimator as the larger model.'] };
function setModel(m) {
  st.m = m; document.querySelectorAll('#top button').forEach(b => b.setAttribute('aria-selected', b.dataset.top === m));
  $('msub').textContent = SUBT[m][0]; $('mcite').textContent = SUBT[m][1]; renderData(); renderRep();
}
function setSub(s) {
  st.sub = s; document.querySelectorAll('.tabs.sub button').forEach(b => b.setAttribute('aria-selected', b.dataset.sub === s));
  $('p-data').hidden = s !== 'data'; $('p-rep').hidden = s !== 'replication'; history.replaceState(null, '', '#' + st.m + (s === 'replication' ? '-rep' : ''));
}
Promise.all([fetch('data/midas_static.json').then(r => r.json()), fetch('data/midas_daily.json').then(r => r.json())]).then(([s, d]) => {
  S = s; D = d; dates = Object.keys(D).sort();
  const last = dates[dates.length - 1]; st.q = D[last][0].q;
  $('asof').textContent = `As of ${last}. Updated each weekday after the day's releases.`;
  document.querySelectorAll('#top button').forEach(b => b.onclick = () => { setModel(b.dataset.top); });
  document.querySelectorAll('.tabs.sub button').forEach(b => b.onclick = () => setSub(b.dataset.sub));
  $('qsel').onchange = e => { st.q = e.target.value; st.since = null; renderData(); };
  $('since').onchange = e => { const v = e.target.value, ds = datesOf(st.q); st.since = [...ds].reverse().find(x => x <= v) || ds[0]; renderData(); };
  document.querySelectorAll('.quick button').forEach(b => b.onclick = () => { const ds = datesOf(st.q), L = ds[ds.length - 1], t = new Date(L); if (b.dataset.q === 'first') st.since = ds[0]; else { t.setUTCDate(t.getUTCDate() - (b.dataset.q === 'week' ? 7 : 30)); st.since = [...ds].reverse().find(x => x <= t.toISOString().slice(0, 10)) || ds[0]; } renderData(); });
  const h = location.hash.slice(1); if (h.startsWith('trio')) st.m = 'trio';
  setModel(st.m); if (h.endsWith('-rep')) setSub('replication');
}).catch(e => { const b = $('banner'); b.style.display = 'block'; b.className = 'banner fail'; b.textContent = 'Could not load data: ' + e; });
