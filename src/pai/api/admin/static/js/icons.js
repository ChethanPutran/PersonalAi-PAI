import { s } from './dom.js';

// 24x24 stroke icons (inline SVG — no CDN, no font files).
const P = {
  overview: ['M3 3h7v9H3z', 'M14 3h7v5h-7z', 'M14 12h7v9h-7z', 'M3 16h7v5H3z'],
  tasks: ['M9 6h12', 'M9 12h12', 'M9 18h12', 'M3 6l1.5 1.5L7 5', 'M3 12l1.5 1.5L7 11', 'M3 18l1.5 1.5L7 17'],
  dags: ['M5 5a2 2 0 100 .01', 'M5 19a2 2 0 100 .01', 'M19 12a2 2 0 100 .01', 'M7 5h4a3 3 0 013 3v1', 'M7 19h4a3 3 0 003-3v-1', 'M14 12h3'],
  play: ['M6 4l14 8-14 8z'],
  flask: ['M9 3h6', 'M10 3v6L4 19a2 2 0 001.7 3h12.6A2 2 0 0020 19l-6-10V3', 'M7 15h10'],
  activity: ['M3 12h4l3-8 4 16 3-8h4'],
  stop: ['M6 6h12v12H6z'],
  close: ['M6 6l12 12', 'M18 6L6 18'],
  pause: ['M8 5v14', 'M16 5v14'],
  refresh: ['M21 12a9 9 0 10-3 6.7', 'M21 4v6h-6'],
  retry: ['M3 12a9 9 0 103-6.7', 'M3 4v6h6'],
  logout: ['M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4', 'M16 17l5-5-5-5', 'M21 12H9'],
  user: ['M20 21a8 8 0 10-16 0', 'M12 11a4 4 0 100-8 4 4 0 000 8'],
  search: ['M11 19a8 8 0 100-16 8 8 0 000 16', 'M21 21l-4.3-4.3'],
  cpu: ['M7 7h10v10H7z', 'M9 1v3', 'M15 1v3', 'M9 20v3', 'M15 20v3', 'M20 9h3', 'M20 14h3', 'M1 9h3', 'M1 14h3'],
  devices: ['M5 3h14v14H5z', 'M2 21h20'],
  mic: ['M12 2a3 3 0 00-3 3v6a3 3 0 006 0V5a3 3 0 00-3-3', 'M19 11a7 7 0 01-14 0', 'M12 18v4'],
  speaker: ['M11 5L6 9H2v6h4l5 4z', 'M15.5 8.5a5 5 0 010 7', 'M18.5 5.5a9 9 0 010 13'],
  bolt: ['M13 2L4 14h7l-1 8 9-12h-7z'],
  alert: ['M12 3l10 18H2z', 'M12 10v5', 'M12 18v.01'],
  info: ['M12 21a9 9 0 100-18 9 9 0 000 18', 'M12 11v6', 'M12 7v.01'],
  check: ['M20 6L9 17l-5-5'],
  plus: ['M12 5v14', 'M5 12h14'],
  minus: ['M5 12h14'],
  fit: ['M4 9V4h5', 'M20 9V4h-5', 'M4 15v5h5', 'M20 15v5h-5'],
  trash: ['M3 6h18', 'M8 6V4h8v2', 'M6 6l1 14h10l1-14'],
  copy: ['M9 9h11v11H9z', 'M5 15V5h10'],
  chevron: ['M9 6l6 6-6 6'],
  empty: ['M3 7l9-4 9 4v10l-9 4-9-4z', 'M3 7l9 4 9-4', 'M12 11v10'],
  puzzle: ['M10 4a2 2 0 114 0v1h4a1 1 0 011 1v4h-1a2 2 0 100 4h1v4a1 1 0 01-1 1h-4v-1a2 2 0 10-4 0v1H6a1 1 0 01-1-1v-4h1a2 2 0 100-4H5V6a1 1 0 011-1h4z'],
  users: ['M17 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2', 'M9.5 11a4 4 0 100-8 4 4 0 000 8', 'M22 21v-2a4 4 0 00-3-3.9', 'M16 3.1a4 4 0 010 7.8'],
  code: ['M8 7l-5 5 5 5', 'M16 7l5 5-5 5', 'M14 4l-4 16'],
  chat: ['M21 12a8 8 0 01-11.5 7.2L3 21l1.8-5.5A8 8 0 1121 12z'],
  wrench: ['M14.7 6.3a4 4 0 005 5L21 13l-8 8a2.8 2.8 0 01-4-4l8-8z', 'M15 5l4 4'],
  xcircle: ['M12 21a9 9 0 100-18 9 9 0 000 18', 'M9 9l6 6', 'M15 9l-6 6'],
  dot: ['M12 12.01'],
  edit: ['M12 20h9', 'M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z'],
  power: ['M12 2v10', 'M18.4 6.6a9 9 0 11-12.8 0'],
  coins: ['M12 8a5 3 0 100-.01', 'M7 8v4a5 3 0 0010 0V8', 'M7 12v4a5 3 0 0010 0v-4'],
  sliders: ['M4 6h10', 'M18 6h2', 'M4 12h4', 'M12 12h8', 'M4 18h12', 'M20 18h0', 'M16 4v4', 'M10 10v4', 'M18 16v4'],
  lock: ['M6 11h12v10H6z', 'M8 11V7a4 4 0 018 0v4'],
  clock: ['M12 21a9 9 0 100-18 9 9 0 000 18', 'M12 7v5l3 2'],
  logo: ['M12 2l8 4.5v9L12 20l-8-4.5v-9z', 'M12 8l4 2.3v4.4L12 17l-4-2.3v-4.4z'],
};

export function icon(name, cls = '') {
  const svg = s('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8,
                         'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', class: cls });
  for (const d of (P[name] || P.info)) svg.append(s('path', { d }));
  return svg;
}
