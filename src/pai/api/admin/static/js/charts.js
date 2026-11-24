// Tiny dependency-free SVG charts (bar, stacked bar, line). Every mark carries a <title> (hover tooltip and
// screen-reader text); the SVG has role="img" and an aria-label. Colours come from CSS classes (.ch-s0..s3).
import { s } from './dom.js';
import { niceTicks } from './usage_data.js';

const M = { left: 52, right: 10, top: 10, bottom: 24 };

function frame(width, height, ariaLabel) {
  return s('svg', { class: 'ch', viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'xMidYMid meet', role: 'img', 'aria-label': ariaLabel || 'Chart' });
}

function axes(svg, { width, height, max, fmt, labels, stride }) {
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1] || 1;
  const innerH = height - M.top - M.bottom;
  const y = (v) => M.top + innerH - (v / top) * innerH;
  for (const t of ticks) {
    svg.append(s('line', { class: 'ch-grid', x1: M.left, x2: width - M.right, y1: y(t), y2: y(t) }));
    svg.append(s('text', { class: 'ch-axis', x: M.left - 6, y: y(t) + 4, 'text-anchor': 'end' }, fmt(t)));
  }
  return { y, top, innerH };
}

/** Which x labels to print so they do not overlap (at most ~8). */
export function labelStride(n, max = 8) { return Math.max(1, Math.ceil(n / max)); }

/**
 * Bars. series: [{ name, values, cls }]; stacked puts series on top of each other (e.g. prompt + completion),
 * otherwise they sit side by side. titles (optional) is the tooltip heading per category.
 */
export function barChart({ labels, titles, series, stacked = true, width = 720, height = 190, fmt = String, ariaLabel, onBar }) {
  const svg = frame(width, height, ariaLabel);
  const n = labels.length;
  const totals = labels.map((_, i) => (stacked ? series.reduce((a, x) => a + (Number(x.values[i]) || 0), 0)
    : Math.max(...series.map((x) => Number(x.values[i]) || 0), 0)));
  const max = Math.max(...totals, 0);
  const { y, innerH } = axes(svg, { width, height, max, fmt, labels });
  if (!n) return svg;
  const innerW = width - M.left - M.right;
  const group = innerW / n;
  const barW = Math.min(group * 0.72, 44);
  const stride = labelStride(n);
  labels.forEach((label, i) => {
    const gx = M.left + i * group + (group - barW) / 2;
    let base = 0;
    series.forEach((ser, k) => {
      const v = Number(ser.values[i]) || 0;
      if (v <= 0) return;
      const w = stacked ? barW : barW / series.length;
      const x = stacked ? gx : gx + k * w;
      const top = stacked ? base + v : v;
      const rect = s('rect', { class: `ch-bar ${ser.cls || `ch-s${k}`}`, x: x.toFixed(1), y: y(top).toFixed(1), width: Math.max(1, w - 1).toFixed(1),
        height: Math.max(1, y(stacked ? base : 0) - y(top)).toFixed(1), rx: 2 },
      s('title', {}, `${(titles && titles[i]) || label}\n${ser.name}: ${fmt(v)}`));
      if (onBar) { rect.addEventListener('click', () => onBar(i)); rect.classList.add('clickable'); }
      svg.append(rect);
      if (stacked) base += v;
    });
    if (i % stride === 0) svg.append(s('text', { class: 'ch-axis', x: (M.left + i * group + group / 2).toFixed(1), y: height - 6, 'text-anchor': 'middle' }, label));
  });
  return svg;
}

/** Lines over categories. series: [{ name, values, cls }]. */
export function lineChart({ labels, titles, series, width = 720, height = 190, fmt = String, ariaLabel }) {
  const svg = frame(width, height, ariaLabel);
  const n = labels.length;
  const max = Math.max(...series.flatMap((x) => x.values.map((v) => Number(v) || 0)), 0);
  const { y } = axes(svg, { width, height, max, fmt, labels });
  if (!n) return svg;
  const innerW = width - M.left - M.right;
  const x = (i) => M.left + (n === 1 ? innerW / 2 : (i / (n - 1)) * innerW);
  series.forEach((ser, k) => {
    const cls = ser.cls || `ch-s${k}`;
    const pts = ser.values.map((v, i) => [x(i), y(Number(v) || 0)]);
    if (pts.length > 1) svg.append(s('path', { class: `ch-line ${cls}`, d: pts.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ') }));
    pts.forEach(([px, py], i) => svg.append(s('circle', { class: `ch-dot ${cls}`, cx: px.toFixed(1), cy: py.toFixed(1), r: 3 },
      s('title', {}, `${(titles && titles[i]) || labels[i]}\n${ser.name}: ${fmt(Number(ser.values[i]) || 0)}`))));
  });
  const stride = labelStride(n);
  labels.forEach((label, i) => { if (i % stride === 0) svg.append(s('text', { class: 'ch-axis', x: x(i).toFixed(1), y: height - 6, 'text-anchor': 'middle' }, label)); });
  return svg;
}
