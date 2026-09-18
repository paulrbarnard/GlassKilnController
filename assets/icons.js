// Minimal inline SVG icon set — no external icon font/CDN (this app should
// work with no internet access in a workshop). Simple line icons,
// stroke=currentColor so they inherit theme color automatically.

const ICONS = {
  flame: `<path d="M12 2c1 3-2 4-2 7a4 4 0 0 0 8 0c0-2-1-3-1-3 1 4-1 5-2 5a2.5 2.5 0 0 1-2.5-2.5C12.5 6 15 5 12 2Z"/>
          <path d="M8.5 13.5A4.5 4.5 0 0 0 12 21a5 5 0 0 0 5-5c0-1.5-.5-2.5-1-3.2"/>`,
  droplet: `<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11Z"/>
            <path d="M9 15a3 3 0 0 0 3 3"/>`,
  bowl: `<path d="M4 11h16a8 8 0 0 1-16 0Z"/>
         <path d="M9 11c0-2 .5-4 3-4s3 2 3 4"/>
         <path d="M2 11h20"/>`,
  clock: `<circle cx="12" cy="12" r="9"/>
          <path d="M12 7v5l3.5 2"/>`,
  thermometer: `<path d="M12 14.5V5a2 2 0 1 0-4 0v9.5a4 4 0 1 0 4 0Z"/>
                <circle cx="10" cy="17" r="1.4" fill="currentColor" stroke="none"/>`,
  speedFast: `<path d="M4 16a8 8 0 0 1 16 0"/><path d="M12 16l5-5"/><circle cx="12" cy="16" r="1.3" fill="currentColor" stroke="none"/>`,
  speedMedium: `<path d="M4 16a8 8 0 0 1 16 0"/><path d="M12 16l3-6"/><circle cx="12" cy="16" r="1.3" fill="currentColor" stroke="none"/>`,
  speedSlow: `<path d="M4 16a8 8 0 0 1 16 0"/><path d="M12 16l1-7"/><circle cx="12" cy="16" r="1.3" fill="currentColor" stroke="none"/>`,
  wrench: `<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2-2Z"/>`,
  gear: `<circle cx="12" cy="12" r="3"/>
         <path d="M12 3v2M12 19v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M3 12h2M19 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>`,
  star: `<path d="M12 3l2.6 5.6 6.1.6-4.6 4 1.3 6-5.4-3.2L6.6 19.2l1.3-6-4.6-4 6.1-.6Z"/>`,
  skip: `<path d="M5 5l7 7-7 7Z"/><path d="M13 5l7 7-7 7Z"/>`,
  plus: `<path d="M12 5v14M5 12h14"/>`,
};

function svgIcon(name, size = 28) {
  const body = ICONS[name] || ICONS.gear;
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
}

function processIcon(process) {
  // process values are the raw controller tokens (protocol.Process): FULL,
  // tAC, SLP — see kiln/protocol.py.
  if (process === 'FULL') return 'flame';
  if (process === 'tAC') return 'droplet';
  if (process === 'SLP') return 'bowl';
  return 'wrench'; // custom Ramp & Hold profile
}

function speedIcon(speed) {
  if (speed === 'FASt') return 'speedFast';
  if (speed === 'MEd') return 'speedMedium';
  return 'speedSlow';
}
