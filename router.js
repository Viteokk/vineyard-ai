/* Walking route in the browser (Web Worker): START -> targets -> START, same rules as pipeline/route.py.
 *
 * Grid over the zone (0.5 m, 1 m for large zones):
 *   cost 1    cells at least ~0.35 m inside inter-rows / authorised passages
 *   cost 20   connectors: rest of the inter-row / passage area, ground in the study area or a block within 6 m of it
 *             (walkable, but counted as "outside" for the 2 % rule)
 *   blocked   canopies (+0.35 m), forbidden zones, everything else
 * Targets are anchored to the nearest cheap cell within 2 m (visited = within 2 m); order = nearest neighbour + 2-opt
 * on shortest-path costs; legs are rebuilt cell by cell. Input polygons are arrays of rings in EPSG:32635 metres.
 */
'use strict';
const INF = Infinity, COST_CORE = 1, COST_LINK = 20;
let INSIDE = null;                             // cell centre inside inter-rows / passages (what the 2 % rule measures)

self.onmessage = e => {
  try { self.postMessage({ok: true, result: plan(e.data)}); }
  catch (err) { self.postMessage({ok: false, error: err.message || String(err)}); }
};
const progress = (step, pct) => self.postMessage({progress: step, pct});

function raster(polys, G, touch = false) {   // touch: every cell the polygon touches (blocking layers); else cell centre inside
  const cv = new OffscreenCanvas(G.w, G.h), ctx = cv.getContext('2d');
  ctx.fillStyle = '#fff'; ctx.strokeStyle = '#fff'; ctx.lineWidth = 1;
  for (const rings of polys) {
    ctx.beginPath();
    for (const r of rings) r.forEach(([x, y], i) => { const px = (x - G.x0) / G.res, py = (G.y1 - y) / G.res; i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
    ctx.closePath(); ctx.fill('evenodd'); if (touch) ctx.stroke();
  }
  const a = ctx.getImageData(0, 0, G.w, G.h).data, m = new Uint8Array(G.w * G.h), thr = touch ? 0 : 127;
  for (let i = 0; i < m.length; i++) m[i] = a[i * 4 + 3] > thr ? 1 : 0;
  return m;
}

function distTo(mask, G, want = 1) {          // chamfer distance (in cells) to the nearest cell with mask == want
  const {w, h} = G, d = new Float32Array(w * h), D = Math.SQRT2;
  for (let i = 0; i < d.length; i++) d[i] = mask[i] === want ? 0 : INF;
  for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) {
    const i = r * w + c; let v = d[i]; if (!v) continue;
    if (c > 0) v = Math.min(v, d[i - 1] + 1);
    if (r > 0) { v = Math.min(v, d[i - w] + 1); if (c > 0) v = Math.min(v, d[i - w - 1] + D); if (c < w - 1) v = Math.min(v, d[i - w + 1] + D); }
    d[i] = v;
  }
  for (let r = h - 1; r >= 0; r--) for (let c = w - 1; c >= 0; c--) {
    const i = r * w + c; let v = d[i]; if (!v) continue;
    if (c < w - 1) v = Math.min(v, d[i + 1] + 1);
    if (r < h - 1) { v = Math.min(v, d[i + w] + 1); if (c < w - 1) v = Math.min(v, d[i + w + 1] + D); if (c > 0) v = Math.min(v, d[i + w - 1] + D); }
    d[i] = v;
  }
  return d;
}

class Heap {                                   // binary min-heap of (key, value) with lazy deletion
  constructor() { this.k = []; this.v = []; }
  get size() { return this.k.length; }
  push(key, val) { const k = this.k, v = this.v; let i = k.length; k.push(key); v.push(val);
    while (i > 0) { const p = (i - 1) >> 1; if (k[p] <= key) break; k[i] = k[p]; v[i] = v[p]; i = p; } k[i] = key; v[i] = val; }
  pop() { const k = this.k, v = this.v, topV = v[0], topK = k[0], lk = k.pop(), lv = v.pop(), n = k.length;
    if (n) { let i = 0; for (;;) { let c = 2 * i + 1; if (c >= n) break; if (c + 1 < n && k[c + 1] < k[c]) c++; if (k[c] >= lk) break; k[i] = k[c]; v[i] = v[c]; i = c; } k[i] = lk; v[i] = lv; }
    this.lastKey = topK; return topV; }
}

function dijkstra(G, cost, src, stopSet, pred, track) {
  const {w, h, res} = G, n = w * h, dist = new Float64Array(n).fill(INF), H = new Heap();
  const geo = track ? new Float32Array(n) : null, out = track ? new Float32Array(n) : null;
  const steps = [[-1, 0, 1], [1, 0, 1], [0, -1, 1], [0, 1, 1], [-1, -1, Math.SQRT2], [-1, 1, Math.SQRT2], [1, -1, Math.SQRT2], [1, 1, Math.SQRT2]];
  dist[src] = 0; H.push(0, src); if (pred) pred[src] = -1;
  let left = stopSet ? stopSet.size : -1;
  while (H.size) {
    const u = H.pop(), du = H.lastKey; if (du > dist[u]) continue;
    if (stopSet && stopSet.has(u) && --left <= 0) break;
    const r = (u / w) | 0, c = u - r * w, cu = cost[u];
    for (const [dr, dc, L] of steps) {
      const rr = r + dr, cc = c + dc; if (rr < 0 || rr >= h || cc < 0 || cc >= w) continue;
      const v = rr * w + cc, cv = cost[v]; if (cv === INF) continue;
      if (dr && dc && (cost[r * w + cc] === INF || cost[rr * w + c] === INF)) continue;   // no corner cutting past a blocked cell
      const nd = du + (cu + cv) * 0.5 * L * res;
      if (nd < dist[v]) { dist[v] = nd; if (pred) pred[v] = u; H.push(nd, v);
        if (track) { const sl = L * res; geo[v] = geo[u] + sl; out[v] = out[u] + (INSIDE[u] && INSIDE[v] ? 0 : sl); } }
    }
  }
  return track ? {dist, geo, out} : dist;
}

function plan(q) {
  const t0 = Date.now();
  const [bx0, by0, bx1, by1] = q.bbox, res = q.res;
  const G = {x0: bx0, y1: by1, res, w: Math.ceil((bx1 - bx0) / res), h: Math.ceil((by1 - by0) / res)};
  const n = G.w * G.h;
  const cx = i => G.x0 + ((i % G.w) + 0.5) * res, cy = i => G.y1 - (((i / G.w) | 0) + 0.5) * res;
  progress('Rasterizez inter-rândurile, pasajele și coroanele', 5);
  const A = raster(q.allowed, G); INSIDE = A;
  const C = raster(q.canopies, G, true), F = raster(q.forbidden, G, true), R = raster(q.region, G);
  progress('Construiesc grila de mers', 15);
  const dIn = distTo(A, G, 0), dA = distTo(A, G, 1), dC = distTo(C, G, 1);
  const cost = new Float32Array(n).fill(INF), coreD = 0.35 / res + 0.75, near = 6 / res, canD = (0.35 + 0.01) / res + 0.75;
  for (let i = 0; i < n; i++) {
    if (F[i] || dC[i] < canD) continue;
    if (A[i] && dIn[i] >= coreD) cost[i] = COST_CORE;
    else if ((A[i] || R[i]) && dA[i] <= near) cost[i] = COST_LINK;
  }
  const cellAt = (x, y) => { const c = Math.floor((x - G.x0) / res), r = Math.floor((G.y1 - y) / res); return r >= 0 && r < G.h && c >= 0 && c < G.w ? r * G.w + c : -1; };
  function anchor(x, y, radius) {              // nearest cheap cell within radius, else nearest walkable cell
    const k = Math.ceil(radius / res), c0 = Math.floor((x - G.x0) / res), r0 = Math.floor((G.y1 - y) / res);
    let best = -1, bd = INF, bestAny = -1, ba = INF;
    for (let r = r0 - k; r <= r0 + k; r++) { if (r < 0 || r >= G.h) continue;
      for (let c = c0 - k; c <= c0 + k; c++) { if (c < 0 || c >= G.w) continue;
        const i = r * G.w + c, co = cost[i]; if (co === INF) continue;
        const d = Math.hypot(cx(i) - x, cy(i) - y); if (d > radius) continue;
        if (co === COST_CORE && d < bd) { bd = d; best = i; }
        if (d < ba) { ba = d; bestAny = i; } } }
    return best >= 0 ? best : bestAny;
  }
  progress('Leg START-ul și țintele de grilă', 25);
  const s = anchor(q.start[0], q.start[1], q.startRadius || 80);
  if (s < 0) throw new Error(`START-ul nu e la mai puțin de ${q.startRadius || 80} m de un inter-rând sau pasaj din zonă`);
  const nodes = [s], nodeTargets = [[]], unreachable = [];
  const byCell = new Map([[s, 0]]);
  for (const t of q.targets) {
    const a = anchor(t.x, t.y, q.visit || 2);
    if (a < 0) { unreachable.push(t.id); continue; }
    if (byCell.has(a)) { nodeTargets[byCell.get(a)].push(t.id); continue; }
    byCell.set(a, nodes.length); nodes.push(a); nodeTargets.push([t.id]);
  }
  if (nodes.length > (q.maxNodes || 260)) throw new Error(`${nodes.length - 1} ținte în zonă: prea multe pentru calculul în browser. Alege o zonă mai mică sau un gol minim mai mare.`);
  const N = nodes.length, mk = () => Array.from({length: N}, () => new Float64Array(N)), D = mk(), GL = mk(), OL = mk();
  const all = new Set(nodes);
  for (let i = 0; i < N; i++) {
    if (i % 5 === 0) progress(`Distanțe pe teren: ${i} / ${N}`, 25 + Math.round(55 * i / N));
    const r = dijkstra(G, cost, nodes[i], all, null, true);
    for (let j = 0; j < N; j++) { const v = nodes[j]; D[i][j] = r.dist[v]; GL[i][j] = r.geo[v]; OL[i][j] = r.out[v]; }
  }
  const reach = [0];                            // only targets reachable from START (and back)
  for (let j = 1; j < N; j++) { if (isFinite(D[0][j]) && isFinite(D[j][0])) reach.push(j); else unreachable.push(...nodeTargets[j]); }
  progress('Ordinea țintelor (TSP)', 82);
  const d = (a, b) => (D[a][b] + D[b][a]) / 2;
  function solve(keep) {
    const left = new Set(keep); let tour = [0], cur = 0;
    while (left.size) { let b = -1, bd = INF; for (const j of left) if (D[cur][j] < bd) { bd = D[cur][j]; b = j; } tour.push(b); left.delete(b); cur = b; }
    tour.push(0);
    for (let improved = true, it = 0; improved && it < 60; it++) {     // 2-opt
      improved = false;
      for (let i = 1; i < tour.length - 2; i++) for (let k = i + 1; k < tour.length - 1; k++) {
        const delta = d(tour[i - 1], tour[k]) + d(tour[i], tour[k + 1]) - d(tour[i - 1], tour[i]) - d(tour[k], tour[k + 1]);
        if (delta < -1e-6) { tour = tour.slice(0, i).concat(tour.slice(i, k + 1).reverse(), tour.slice(k + 1)); improved = true; }
      }
    }
    return tour;
  }
  const share = t => { let g = 0, o = 0; for (let i = 1; i < t.length; i++) { g += GL[t[i - 1]][t[i]]; o += OL[t[i - 1]][t[i]]; } return g ? o / g : 0; };
  const maxOut = q.maxOutside ?? INF, dropped = [];
  let keep = reach.slice(1), tour = solve(keep);
  while (keep.length && share(tour) > maxOut) {       // like pipeline/route.py: drop the stops that cost the most outside walking
    let worst = -1, wv = -1;
    for (let i = 1; i < tour.length - 1; i++) { const a = tour[i - 1], b = tour[i], c = tour[i + 1], v = OL[a][b] + OL[b][c] - OL[a][c]; if (v > wv) { wv = v; worst = b; } }
    if (worst < 0) break;
    keep = keep.filter(j => j !== worst); dropped.push(...nodeTargets[worst]);
    tour = keep.length > 60 && dropped.length % 5 ? tour.filter(j => j !== worst) : solve(keep);
  }
  tour = solve(keep);
  progress('Desenez traseul pas cu pas', 90);
  const pred = new Int32Array(n), cells = [];
  for (let l = 0; l + 1 < tour.length; l++) {
    const a = nodes[tour[l]], b = nodes[tour[l + 1]]; if (a === b) continue;
    dijkstra(G, cost, a, new Set([b]), pred);
    const leg = []; for (let v = b; v !== -1 && v !== a; v = pred[v]) leg.push(v); leg.push(a); leg.reverse();
    if (cells.length) leg.shift(); cells.push(...leg);
  }
  if (!cells.length) cells.push(s, s);
  let len = 0, out = 0;
  for (let i = 1; i < cells.length; i++) { const L = Math.hypot(cx(cells[i]) - cx(cells[i - 1]), cy(cells[i]) - cy(cells[i - 1]));
    len += L; if (!A[cells[i]] || !A[cells[i - 1]]) out += L; }
  const coords = [];                              // drop collinear cells (lossless)
  for (let i = 0; i < cells.length; i++) {
    if (i > 0 && i < cells.length - 1) { const a = cells[i - 1], b = cells[i], c = cells[i + 1]; if (b - a === c - b) continue; }
    coords.push([+cx(cells[i]).toFixed(2), +cy(cells[i]).toFixed(2)]);
  }
  const onPath = new Set(cells), vis = q.visit || 2, k = Math.ceil(vis / res) + 1, visited = [];
  for (const t of q.targets) { const c0 = cellAt(t.x, t.y); if (c0 < 0) continue; const r0 = (c0 / G.w) | 0, cc0 = c0 % G.w; let hit = false;
    for (let r = r0 - k; r <= r0 + k && !hit; r++) for (let c = cc0 - k; c <= cc0 + k && !hit; c++) { const i = r * G.w + c;
      if (r >= 0 && r < G.h && c >= 0 && c < G.w && onPath.has(i) && Math.hypot(cx(i) - t.x, cy(i) - t.y) <= vis) hit = true; }
    if (hit) visited.push(t.id); }
  return {coords, length_m: len, outside_m: out, outside_share: len ? out / len : 0, snap_m: Math.hypot(cx(s) - q.start[0], cy(s) - q.start[1]),
    targets_total: q.targets.length, visited, unreachable, dropped, res, cells: n, ms: Date.now() - t0};
}
