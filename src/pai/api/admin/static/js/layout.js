// Pure DAG layout (no DOM): layered, left -> right, ordered to reduce edge crossings.
export const NODE_W = 196;
export const NODE_H = 76;
const GAP_X = 64;
const GAP_Y = 24;
const PAD = 48;

/** Layer = longest path from any source. Cycles (never expected) are cut defensively. */
export function assignLayers(nodes, edges) {
  const ids = new Set(nodes.map((n) => n.spec_id));
  const deps = new Map(nodes.map((n) => [n.spec_id, []]));
  for (const e of edges) if (ids.has(e.source) && ids.has(e.target) && e.source !== e.target) deps.get(e.target).push(e.source);
  const layer = new Map();
  const visiting = new Set();
  const depth = (id) => {
    if (layer.has(id)) return layer.get(id);
    if (visiting.has(id)) return 0;                      // cycle guard
    visiting.add(id);
    const d = deps.get(id).reduce((m, p) => Math.max(m, depth(p) + 1), 0);
    visiting.delete(id);
    layer.set(id, d);
    return d;
  };
  nodes.forEach((n) => depth(n.spec_id));
  return layer;
}

export function layoutDag(nodes, edges) {
  if (!nodes.length) return { positions: {}, edges: [], width: 0, height: 0 };
  const layerOf = assignLayers(nodes, edges);
  const layers = [];
  nodes.forEach((n) => { (layers[layerOf.get(n.spec_id)] ||= []).push(n.spec_id); });
  // A (never expected) cycle can leave a layer empty; compact so layer 0 always exists.
  for (let l = layers.length - 1; l >= 0; l--) if (!layers[l]) layers.splice(l, 1);

  const preds = new Map(nodes.map((n) => [n.spec_id, []]));
  edges.forEach((e) => { if (preds.has(e.target) && preds.has(e.source)) preds.get(e.target).push(e.source); });

  const order = new Map();                                // id -> index within layer
  layers[0].forEach((id, i) => order.set(id, i));
  for (let pass = 0; pass < 2; pass++) {                  // barycenter sweeps
    for (let l = 1; l < layers.length; l++) {
      const bary = (id) => {
        const p = preds.get(id).filter((x) => order.has(x));
        return p.length ? p.reduce((a, x) => a + order.get(x), 0) / p.length : order.get(id) ?? 0;
      };
      layers[l].sort((a, b) => bary(a) - bary(b) || a.localeCompare(b));
      layers[l].forEach((id, i) => order.set(id, i));
    }
  }

  const tallest = Math.max(...layers.map((l) => l.length));
  const colH = tallest * NODE_H + (tallest - 1) * GAP_Y;
  const positions = {};
  layers.forEach((ids, l) => {
    const h = ids.length * NODE_H + (ids.length - 1) * GAP_Y;
    const top = PAD + (colH - h) / 2;
    ids.forEach((id, i) => { positions[id] = { x: PAD + l * (NODE_W + GAP_X), y: top + i * (NODE_H + GAP_Y), w: NODE_W, h: NODE_H, layer: l }; });
  });

  const out = [];
  for (const e of edges) {
    const a = positions[e.source], b = positions[e.target];
    if (!a || !b) continue;
    const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
    const dx = Math.max(40, (x2 - x1) * 0.5);
    out.push({ source: e.source, target: e.target, d: `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}` });
  }
  return {
    positions, edges: out,
    width: PAD * 2 + layers.length * NODE_W + (layers.length - 1) * GAP_X,
    height: PAD * 2 + colH,
  };
}

/** Edge look: flowing when it feeds a running node, green when satisfied, dim when dead. */
export function edgeState(sourceStatus, targetStatus) {
  if (targetStatus === 'skipped' || targetStatus === 'cancelled' && sourceStatus !== 'completed') return 'dead';
  if (sourceStatus === 'completed' && targetStatus === 'running') return 'active';
  if (sourceStatus === 'completed') return 'done';
  if (sourceStatus === 'running') return 'active';
  if (['failed', 'cancelled', 'skipped'].includes(sourceStatus)) return 'dead';
  return 'idle';
}
