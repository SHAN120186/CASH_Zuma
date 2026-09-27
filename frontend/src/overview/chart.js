// Geometry for the small cash-flow chart: scale values into a box and draw a
// monotone cubic curve (Fritsch–Carlson), which never overshoots between days,
// so the line cannot suggest a balance that did not exist.

// `include` widens the value range, e.g. so a reserve line stays inside the box.
export function makeScale(values, { width, height, top = 8, bottom = 8, left = 0, right = 0 }, include = []) {
  let hi = Math.max(...values, ...include), lo = Math.min(...values, ...include);
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.12;
  hi += pad; lo -= pad;
  const x = i => (values.length === 1 ? left : left + (i * (width - left - right)) / (values.length - 1));
  const y = v => top + ((hi - v) * (height - top - bottom)) / (hi - lo);
  return { x, y };
}

export function scalePoints(values, box, include = []) {
  if (!values.length) return [];
  const { x, y } = makeScale(values, box, include);
  return values.map((v, i) => [x(i), y(v)]);
}

export function smoothPath(points) {
  const n = points.length;
  if (!n) return '';
  if (n === 1) return `M${points[0][0]} ${points[0][1]}`;
  const dx = [], slope = [];
  for (let i = 0; i < n - 1; i++) {
    dx.push(points[i + 1][0] - points[i][0]);
    slope.push((points[i + 1][1] - points[i][1]) / dx[i]);
  }
  const tangent = [slope[0]];
  for (let i = 1; i < n - 1; i++) {
    tangent.push(slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2);
  }
  tangent.push(slope[n - 2]);
  for (let i = 0; i < n - 1; i++) {
    if (slope[i] === 0) { tangent[i] = 0; tangent[i + 1] = 0; continue; }
    const a = tangent[i] / slope[i], b = tangent[i + 1] / slope[i], s = a * a + b * b;
    if (s > 9) { const t = 3 / Math.sqrt(s); tangent[i] = t * a * slope[i]; tangent[i + 1] = t * b * slope[i]; }
  }
  const f = v => Math.round(v * 10) / 10;
  let d = `M${f(points[0][0])} ${f(points[0][1])}`;
  for (let i = 0; i < n - 1; i++) {
    const [x0, y0] = points[i], [x1, y1] = points[i + 1], h = dx[i] / 3;
    d += ` C${f(x0 + h)} ${f(y0 + tangent[i] * h)} ${f(x1 - h)} ${f(y1 - tangent[i + 1] * h)} ${f(x1)} ${f(y1)}`;
  }
  return d;
}

// Five evenly spaced label positions, like weekly ticks over a month.
export function tickIndexes(count, ticks = 5) {
  if (count <= 1) return [0];
  const out = new Set();
  for (let k = 0; k < ticks; k++) out.add(Math.round((k * (count - 1)) / (ticks - 1)));
  return [...out];
}
