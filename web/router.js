/* Walking route in the browser (Web Worker): START -> targets -> START, same rules as pipeline/route.py.
 *
 * Grid over the zone (0.5 m):
 *   cost 1    cells whose centre is >= 0.35 m inside inter-rows / authorised passages (on a finer raster, as route.py)
 *   cost 20   connectors: rest of the inter-row / passage area, ground in the study area or a block within 6 m of it
 *             (walkable, but counted as "outside" for the 2 % rule)
 *   blocked   canopies (centre < 0.36 m from one, as route.py), forbidden zones, everything else, and the row walls:
 *             every row axis (q.rows; the app adds the gap stretches too) drawn 2 x (0.30 + 0.35) m thick with round
 *             caps, collinear tile seams bridged, minus the authorised passages (q.passages). The trellis wires make a row
 *             impassable even where vines are missing, so the route walks ALONG the inter-rows and changes inter-row only
 *             past the row ends or on a road.
 * Targets are anchored to the nearest cheap cell within 2 m (visited = within 2 m); order = nearest neighbour + 2-opt
 * on shortest-path costs; legs are rebuilt cell by cell, then straightened by string pulling, first per leg (stops fixed),
 * then over the whole route (a stop may be passed within 2 m instead of through its cell): a shortcut is kept only if
 * it stays on walkable cells (on cheap cells if what it replaces was all cheap) and does not add walking outside.
 * Length, outside share and visited targets are measured on the final polyline.
 * Input polygons are arrays of rings, lines arrays of [x, y], in EPSG:32635 metres.
 */
'use strict';
const INF = Infinity, COST_CORE = 1, COST_LINK = 20, WALL = 0.30 + 0.35, SEAM = 2;
let INSIDE = null;                             // cell centre inside inter-rows / passages (what the 2 % rule measures)

self.onmessage = e => {
  try { self.postMessage({ok: true, result: plan(e.data)}); }
  catch (err) { self.postMessage({ok: false, error: err.message || String(err)}); }
};
const progress = (step, pct) => self.postMessage({progress: step, pct});

function raster(polys, G, touch = false, grow = 0) {   // touch: every cell the polygon touches (blocking layers); else cell centre
  const cv = new OffscreenCanvas(G.w, G.h), ctx = cv.getContext('2d');   // inside the polygon grown by `grow` m (round joins = buffer)
  ctx.fillStyle = '#fff'; ctx.strokeStyle = '#fff'; ctx.lineWidth = grow ? 2 * grow / G.res : 1; ctx.lineJoin = 'round';
  for (const rings of polys) {
    ctx.beginPath();
    for (const r of rings) { r.forEach(([x, y], i) => { const px = (x - G.x0) / G.res, py = (G.y1 - y) / G.res; i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }); ctx.closePath(); }
    ctx.fill('evenodd'); if (touch || grow) ctx.stroke();
  }
  const a = ctx.getImageData(0, 0, G.w, G.h).data, m = new Uint8Array(G.w * G.h), thr = touch ? 0 : 127;
  for (let i = 0; i < m.length; i++) m[i] = a[i * 4 + 3] > thr ? 1 : 0;
  return m;
}

function strokes(lines, G, width) {           // cells whose centre is within width/2 of a line (round caps and joins)
  const cv = new OffscreenCanvas(G.w, G.h), ctx = cv.getContext('2d');
  ctx.strokeStyle = '#fff'; ctx.lineWidth = width / G.res; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.beginPath();
  for (const l of lines) l.forEach(([x, y], i) => { const px = (x - G.x0) / G.res, py = (G.y1 - y) / G.res; i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
  ctx.stroke();
  const a = ctx.getImageData(0, 0, G.w, G.h).data, m = new Uint8Array(G.w * G.h);
  for (let i = 0; i < m.length; i++) m[i] = a[i * 4 + 3] > 127 ? 1 : 0;
  return m;
}

function seams(lines) {                        // rows are cut per tile: join collinear ends <= SEAM m apart (not neighbour rows at a headland)
  const ends = [];
  lines.forEach((l, k) => { if (l.length < 2) return;
    for (const [p, q] of [[l[0], l[1]], [l[l.length - 1], l[l.length - 2]]]) { const L = Math.hypot(p[0] - q[0], p[1] - q[1]); if (L > 0) ends.push({k, p, u: [(p[0] - q[0]) / L, (p[1] - q[1]) / L]}); } });
  ends.sort((a, b) => a.p[0] - b.p[0]);
  const out = [];
  for (let i = 0; i < ends.length; i++) for (let j = i + 1; j < ends.length && ends[j].p[0] - ends[i].p[0] <= SEAM; j++) {
    const a = ends[i], b = ends[j]; if (a.k === b.k) continue;
    const dx = b.p[0] - a.p[0], dy = b.p[1] - a.p[1], d = Math.hypot(dx, dy);
    if (d > SEAM || d < 0.01 || a.u[0] * b.u[0] + a.u[1] * b.u[1] > -0.96) continue;        // ends must face each other
    if (Math.abs(dx * a.u[1] - dy * a.u[0]) > 0.5 || dx * a.u[0] + dy * a.u[1] <= 0) continue;  // in line, b ahead of a
    out.push([a.p, b.p]);
  }
  return out;
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

function inset(polys, G, d) {                  // cells whose centre is >= d m inside the union of polys (like buffer(-d) in route.py):
  const k = Math.max(1, Math.min(4, Math.floor(Math.sqrt(16e6 / (G.w * G.h)))));  // chamfer on a k x finer raster (<= 16 M cells)
  const S = {x0: G.x0, y1: G.y1, res: G.res / k, w: G.w * k, h: G.h * k}, din = distTo(raster(polys, S), S, 0);
  const need = d / S.res + 0.75, a = (k - 1) >> 1, b = k >> 1, m = new Uint8Array(G.w * G.h);
  for (let r = 0; r < G.h; r++) for (let c = 0; c < G.w; c++) {         // fine cells at the coarse centre
    const r0 = (r * k + a) * S.w, r1 = (r * k + b) * S.w, c0 = c * k + a, c1 = c * k + b;
    m[r * G.w + c] = Math.min(din[r0 + c0], din[r0 + c1], din[r1 + c0], din[r1 + c1]) >= need ? 1 : 0;
  }
  return m;
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
        if (track) { const sl = L * res; geo[v] = geo[u] + sl; out[v] = out[u] + sl * ((INSIDE[u] ? 0 : 0.5) + (INSIDE[v] ? 0 : 0.5)); } }   // half a step per end outside
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
  const C = raster(q.canopies, G, false, 0.35 + 0.01), F = raster(q.forbidden, G, true), R = raster(q.region, G);   // C: as route.py, centre >= 0.36 m from every canopy
  progress('Construiesc grila de mers', 15);
  const core = inset(q.allowed, G, 0.35), dA = distTo(A, G, 1);
  const cost = new Float32Array(n).fill(INF), near = 6 / res;
  const rows = q.rows || [], W = rows.length ? strokes(rows.concat(seams(rows)), G, 2 * WALL) : null, P = W && raster(q.passages || [], G);
  for (let i = 0; i < n; i++) {
    if (F[i] || C[i] || (W && W[i] && !P[i])) continue;                // row wall, except on the authorised passages
    if (A[i] && core[i]) cost[i] = COST_CORE;
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
    const a = anchor(t.x, t.y, (q.visit || 2) - 0.01);     // -1 cm: the output is rounded to cm
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
  const step = res / 4;
  // Samples every <= res/4 (as pipeline/route.py visible()): outside length, or -1 if a sample leaves the walkable (cheap) cells.
  // A walkable centre is >= WALL from a row axis, so a sample on its cell is >= 0.29 m from it: the line cannot cross a row.
  function walk(ax, ay, bx, by, check, cheap) {
    const L = Math.hypot(bx - ax, by - ay), k = Math.max(1, Math.ceil(L / step)), sl = L / k;
    let out = 0;
    for (let s = 0; s < k; s++) {
      const t = (s + 0.5) / k, i = cellAt(ax + (bx - ax) * t, ay + (by - ay) * t);
      if (i < 0) { if (check) return -1; out += sl; continue; }
      if (check && (cost[i] === INF || (cheap && cost[i] !== COST_CORE))) return -1;
      if (!A[i]) out += sl;
    }
    return out;
  }
  function pull(leg) {                            // string pulling with the leg's ends fixed -> kept cells, [all cheap, outside m] per segment
    const m = leg.length - 1, nc = new Int32Array(m + 2), ol = new Float64Array(m + 1);
    for (let i = 0; i <= m; i++) nc[i + 1] = nc[i] + (cost[leg[i]] === COST_CORE ? 0 : 1);
    for (let i = 1; i <= m; i++) ol[i] = ol[i - 1] + walk(cx(leg[i - 1]), cy(leg[i - 1]), cx(leg[i]), cy(leg[i]), false);
    const keep = [leg[0]], segs = [];
    for (let i = 0; i < m;) {
      let best = i + 1, bo = ol[i + 1] - ol[i];
      for (let k = i + 2, miss = 0; k <= m && miss < 8; k++) {
        const o = walk(cx(leg[i]), cy(leg[i]), cx(leg[k]), cy(leg[k]), true, nc[k + 1] === nc[i]);
        if (o >= 0 && o <= ol[k] - ol[i] + 1e-9) { best = k; bo = o; miss = 0; } else miss++;
      }
      keep.push(leg[best]); segs.push([nc[best + 1] === nc[i], bo]); i = best;
    }
    return {keep, segs};
  }
  const vis = q.visit || 2, byId = new Map(q.targets.map(t => [t.id, t]));
  const segDist = (px, py, ax, ay, bx, by) => { const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy, u = L2 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / L2)) : 0;
    return [Math.hypot(ax + u * dx - px, ay + u * dy - py), u]; };
  const pred = new Int32Array(n), V = [s], SG = [], ST = [null];      // vertices, their segments, targets anchored at a stop vertex
  for (let l = 0; l + 1 < tour.length; l++) {
    const a = nodes[tour[l]], b = nodes[tour[l + 1]]; if (a === b) continue;
    dijkstra(G, cost, a, new Set([b]), pred);
    const leg = []; for (let v = b; v !== -1 && v !== a; v = pred[v]) leg.push(v); leg.push(a); leg.reverse();
    const {keep, segs} = pull(leg);
    for (let j = 1; j < keep.length; j++) { V.push(keep[j]); SG.push(segs[j - 1]); ST.push(null); }
    ST[ST.length - 1] = nodeTargets[tour[l + 1]].map(id => byId.get(id));
  }
  if (V.length < 2) { V.push(s); SG.push([true, 0]); ST.push(null); }
  // Second pull over the whole route: a stop may be passed at a distance instead of through its cell, as long as every target
  // anchored there stays within vis (-2 cm for the rounding) of the shortcut; same walkable / cheap / outside rules as above.
  const M = V.length - 1, NC = new Int32Array(M + 1), OS = new Float64Array(M + 1), path = [V[0]];
  for (let j = 1; j <= M; j++) { NC[j] = NC[j - 1] + (SG[j - 1][0] ? 0 : 1); OS[j] = OS[j - 1] + SG[j - 1][1]; }
  for (let i = 0; i < M;) {
    let best = i + 1;
    for (let k = i + 2, miss = 0; k <= M && miss < 8; k++) {
      const ax = cx(V[i]), ay = cy(V[i]), bx = cx(V[k]), by = cy(V[k]);
      let ok = true;
      for (let j = i + 1; j < k && ok; j++) if (ST[j]) ok = ST[j].every(t => segDist(t.x, t.y, ax, ay, bx, by)[0] <= vis - 0.02);
      const o = ok ? walk(ax, ay, bx, by, true, NC[k] === NC[i]) : -1;
      if (o >= 0 && o <= OS[k] - OS[i] + 1e-9) { best = k; miss = 0; } else miss++;
    }
    path.push(V[best]); i = best;
  }
  const coords = path.map(i => [+cx(i).toFixed(2), +cy(i).toFixed(2)]);
  let len = 0, out = 0;
  for (let i = 1; i < coords.length; i++) { const [ax, ay] = coords[i - 1], [bx, by] = coords[i];
    len += Math.hypot(bx - ax, by - ay); out += walk(ax, ay, bx, by, false); }
  const first = new Map();                          // visited = within vis of the final polyline, in the order the route passes them
  for (const t of q.targets) for (let i = 1; i < coords.length; i++) {
    const [ax, ay] = coords[i - 1], [bx, by] = coords[i];
    if (t.x < Math.min(ax, bx) - vis || t.x > Math.max(ax, bx) + vis || t.y < Math.min(ay, by) - vis || t.y > Math.max(ay, by) + vis) continue;
    const [d, u] = segDist(t.x, t.y, ax, ay, bx, by);
    if (d <= vis) { first.set(t.id, i + u); break; }
  }
  const visited = [...first.keys()].sort((a, b) => first.get(a) - first.get(b));
  return {coords, length_m: len, outside_m: out, outside_share: len ? out / len : 0, snap_m: Math.hypot(cx(s) - q.start[0], cy(s) - q.start[1]),
    targets_total: q.targets.length, visited, unreachable: unreachable.filter(id => !first.has(id)), dropped: dropped.filter(id => !first.has(id)),
    res, cells: n, walls: !!W, ms: Date.now() - t0};
}
