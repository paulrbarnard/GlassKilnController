// Single-page touchscreen app: navigation between the five "screens" plus
// all REST/WebSocket wiring to python/main.py + python/web/api.py. Kept as
// plain JS/fetch (no framework) — this is a kiosk app with five simple
// views, not something that needs a build step.

const MAX_SEGMENTS = 8;

async function api(method, path, body) {
  // cache: 'no-store' — without this, Chromium can serve a stale cached
  // GET /api/profiles response after a save/delete instead of hitting the
  // server, since we never send a cache-busting query string and the
  // server doesn't set explicit no-cache headers on this route. Confirmed
  // this was the cause of a just-created profile not appearing in the
  // home grid even though it was genuinely saved to disk correctly.
  const opts = { method, headers: {}, cache: 'no-store' };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (data && data.error) {
    throw new Error(data.error);
  }
  return data;
}

function showScreen(id) {
  document.querySelectorAll('.screen').forEach((el) => el.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

function slugify(name) {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return slug || 'custom_profile';
}

// -- Home screen ------------------------------------------------------------

async function refreshBridgeStatus() {
  try {
    const status = await api('GET', '/api/status');
    const text = `Bridge: ${status.bridge_ok ? 'connected' : 'unreachable'}`;
    document.getElementById('bridge-status').textContent = text;
    document.getElementById('settings-bridge-status').textContent = text;
  } catch (e) {
    document.getElementById('bridge-status').textContent = 'Bridge: error checking status';
  }
}

function peakTempF(segments) {
  return Math.max(...segments.map((s) => s.target_temp_f));
}

function totalHours(segments) {
  const points = computePoints(segments);
  return points[points.length - 1].hours;
}

async function loadProfiles() {
  const { profiles } = await api('GET', '/api/profiles');
  const list = document.getElementById('profile-list');
  list.innerHTML = '';
  const cardsWithDiagrams = [];

  profiles
    .slice()
    .sort((a, b) => (a.source !== 'user') - (b.source !== 'user') || a.name.localeCompare(b.name))
    .forEach((p) => {
      const card = document.createElement('div');
      card.className = 'profile-card';

      const iconRow = document.createElement('div');
      iconRow.className = 'icon-row';
      iconRow.innerHTML = svgIcon(processIcon(p.process), 26);
      if (p.speed) {
        const speedSpan = document.createElement('span');
        speedSpan.className = 'speed';
        speedSpan.innerHTML = svgIcon(speedIcon(p.speed), 20);
        iconRow.appendChild(speedSpan);
      } else if (p.source === 'user') {
        const star = document.createElement('span');
        star.className = 'badge-user';
        star.innerHTML = svgIcon('star', 18);
        iconRow.appendChild(star);
      }

      const name = document.createElement('div');
      name.className = 'name';
      name.textContent = p.name;

      const diagramWrap = document.createElement('div');
      diagramWrap.className = 'card-diagram-wrap';
      const diagramCanvas = document.createElement('canvas');
      diagramCanvas.className = 'card-diagram';
      diagramWrap.appendChild(diagramCanvas);

      card.append(iconRow, name, diagramWrap);

      if (p.segments) {
        const peak = document.createElement('div');
        peak.className = 'peak';
        peak.innerHTML = `${fToC(peakTempF(p.segments)).toFixed(0)}<span class="unit">°C peak</span>`;
        card.appendChild(peak);
        cardsWithDiagrams.push([diagramCanvas, p.segments]);
      }

      card.addEventListener('click', () => openProfile(p.id));
      list.appendChild(card);
    });

  // Canvases need real layout size before drawing, so draw after the
  // browser has laid out the grid, not while building each card.
  requestAnimationFrame(() => {
    cardsWithDiagrams.forEach(([canvas, segments]) =>
      drawRampDiagram(canvas, segments, null, { minimal: true })
    );
  });
}

function goHome() {
  showScreen('screen-home');
  loadProfiles().then(refreshKeyHints);
  refreshBridgeStatus();
}

// -- Profile detail screen ---------------------------------------------------

let currentProfile = null;

function renderProfileStats(segments) {
  const holdMinutes = segments.reduce((sum, s) => sum + (s.hold_minutes || 0), 0);
  const tiles = [
    { icon: 'thermometer', value: `${fToC(peakTempF(segments)).toFixed(0)}°C`, label: 'Peak Temp' },
    { icon: 'clock', value: `${totalHours(segments).toFixed(1)}h`, label: 'Total Time' },
    { icon: 'clock', value: `${holdMinutes.toFixed(0)} min`, label: 'Total Hold' },
  ];
  document.getElementById('profile-stats').innerHTML = tiles
    .map(
      (t) => `
    <div class="stat-tile">
      <span class="icon">${svgIcon(t.icon, 26)}</span>
      <span class="value">${t.value}</span>
      <span class="label">${t.label}</span>
    </div>`
    )
    .join('');
}

async function openProfile(id) {
  const profile = await api('GET', `/api/profiles/${id}`);
  currentProfile = profile;
  document.getElementById('profile-title').innerHTML = `${svgIcon(processIcon(profile.process), 24)} ${profile.name}`;
  document.getElementById('profile-description').textContent = profile.description;
  document.getElementById('profile-segments').textContent = describeSegments(profile.segments);
  renderProfileStats(profile.segments);
  // Editable regardless of source: editing a factory profile reprograms
  // that same slot (same id, saved under source "user"), it doesn't fork
  // a separate copy — see openEditProfileEditor().
  showScreen('screen-profile');
  refreshKeyHints();
  requestAnimationFrame(() =>
    drawRampDiagram(document.getElementById('profile-diagram'), profile.segments)
  );
}

async function startFiring() {
  if (!currentProfile) return;
  try {
    await api('POST', `/api/run/${currentProfile.id}`);
  } catch (e) {
    alert(`Could not start: ${e.message}`);
    return;
  }
  document.getElementById('run-title').innerHTML = `${svgIcon(processIcon(currentProfile.process), 24)} Firing: ${currentProfile.name}`;
  document.getElementById('run-log').innerHTML = '';
  showScreen('screen-run');
  refreshKeyHints();
  requestAnimationFrame(() =>
    drawRampDiagram(document.getElementById('run-diagram'), currentProfile.segments)
  );
}

// -- Run screen ---------------------------------------------------------------

function appendLog(message, isError = false) {
  const log = document.getElementById('run-log');
  const line = document.createElement('div');
  if (isError) line.className = 'error';
  line.textContent = message;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

async function runAction(path, label) {
  appendLog(`requested: ${label}`);
  try {
    await api('POST', path);
  } catch (e) {
    appendLog(`ERROR: ${e.message}`, true);
  }
}

function stopFiring() {
  if (!confirm('Stop the current program? This cannot be undone.')) return;
  runAction('/api/stop', 'stop');
  goHome();
}

// -- Editor screen --------------------------------------------------------------

let editingProfileId = null;

function editorReadSegments() {
  // Inputs are °C (matching the visible column headers); converted back to
  // °F here since that's what the API/backend/controller programming uses
  // — see diagrams.js's cToF/rateCToF for why.
  const rows = document.querySelectorAll('#editor-rows tr');
  const segments = [];
  rows.forEach((row) => {
    const [rate, temp, hold] = row.querySelectorAll('input');
    segments.push({
      rate_f_per_hr: rateCToF(parseFloat(rate.value) || 0),
      target_temp_f: cToF(parseFloat(temp.value) || 0),
      hold_minutes: parseFloat(hold.value) || 0,
    });
  });
  return segments;
}

function editorRedraw() {
  drawRampDiagram(document.getElementById('editor-diagram'), editorReadSegments());
}

function editorAddRow(segF) {
  const tbody = document.getElementById('editor-rows');
  if (tbody.children.length >= MAX_SEGMENTS) {
    alert(`Controller supports at most ${MAX_SEGMENTS} segments.`);
    return;
  }
  const row = document.createElement('tr');
  // segF (when editing an existing profile) is °F, matching the API — the
  // row's inputs are °C, so convert once here for display.
  const values = segF || { rate_f_per_hr: 300, target_temp_f: 1000, hold_minutes: 10 };
  const rateC = rateFToC(values.rate_f_per_hr);
  const tempC = fToC(values.target_temp_f);
  row.innerHTML = `
    <td><input type="number" value="${rateC.toFixed(0)}" /></td>
    <td><input type="number" value="${tempC.toFixed(0)}" /></td>
    <td><input type="number" value="${values.hold_minutes}" /></td>
    <td><button class="remove-row">&minus;</button></td>
  `;
  row.querySelectorAll('input').forEach((i) => i.addEventListener('input', editorRedraw));
  row.querySelector('.remove-row').addEventListener('click', () => {
    row.remove();
    editorRedraw();
    refreshKeyHints();
  });
  tbody.appendChild(row);
  refreshKeyHints();
}

function focusFirstEditorField() {
  // None of the editor's fields are reachable through the number/letter
  // key-hint system (those only target buttons/cards, not inputs — see
  // keyboard.js), so without this a keyboard-only user would have no way
  // to get focus into the form at all. Starts at Name — first in
  // editorFieldList()'s order — so arrowing/typing flows naturally into
  // Description and then the segment grid.
  const first = editorFieldList()[0];
  if (first) {
    first.focus();
    first.select();
  }
}

function openNewProfileEditor() {
  editingProfileId = null;
  document.getElementById('editor-name').value = '';
  document.getElementById('editor-description').value = '';
  document.getElementById('editor-rows').innerHTML = '';
  editorAddRow();
  showScreen('screen-editor');
  refreshKeyHints();
  requestAnimationFrame(() => {
    editorRedraw();
    focusFirstEditorField();
  });
}

function openEditProfileEditor(profile) {
  // Any profile is editable, including factory GlassFire ones — editing
  // and saving reprograms that same id/slot (source becomes "user"), it
  // doesn't fork a separate copy. profile.segments is already resolved
  // (from the API's _detail()) regardless of the original kind.
  editingProfileId = profile.id;
  document.getElementById('editor-name').value = profile.name;
  document.getElementById('editor-description').value = profile.description;
  document.getElementById('editor-rows').innerHTML = '';
  profile.segments.forEach((seg) => editorAddRow(seg));
  showScreen('screen-editor');
  refreshKeyHints();
  requestAnimationFrame(() => {
    editorRedraw();
    focusFirstEditorField();
  });
}

async function saveProfile() {
  const name = document.getElementById('editor-name').value.trim();
  if (!name) {
    alert('Give this profile a name.');
    return;
  }
  const segments = editorReadSegments();
  if (segments.length === 0) {
    alert('Add at least one segment.');
    return;
  }
  const id = editingProfileId || slugify(name);
  const body = {
    id,
    name,
    description: document.getElementById('editor-description').value.trim(),
    segments,
  };
  let profile;
  try {
    profile = await api('POST', '/api/profiles', body);
  } catch (e) {
    alert(`Could not save: ${e.message}`);
    return;
  }
  currentProfile = profile;
  await loadProfiles();
  openProfile(profile.id);
}

// -- Editor keyboard navigation -----------------------------------------
//
// Left/Right tab through every field in order — Name, Description, then
// each segment row's rate/temp/hold (like Tab/Shift+Tab, not spatial/
// mouse-like movement). Up/Down on a numeric (segment grid) field spins
// its value, accelerating the longer it's held, clamped to what the
// controller will actually accept (protocol.py MAX_PROCESS_TEMP_F /
// MAX_HOLD_MINUTES_TOTAL, pp.16-17 — mirrored here since this is a separate
// runtime with no access to the Python constants); Up/Down do nothing on
// Name/Description since there's no numeric meaning there.

const EDITOR_MIN_TEMP_C = 0;
const EDITOR_MAX_TEMP_C = Math.round(fToC(1650)); // manual p.16: max enterable temp
const EDITOR_MIN_RATE_C = 1;
const EDITOR_MAX_RATE_C = Math.round(rateFToC(9999)); // AS_FAST_AS_POSSIBLE, protocol.py
const EDITOR_MIN_HOLD_MIN = 0;
const EDITOR_MAX_HOLD_MIN = 99 * 60 + 99; // manual p.17: 99 hours AND 99 minutes

function editorFieldList() {
  const nameDescription = [
    document.getElementById('editor-name'),
    document.getElementById('editor-description'),
  ];
  return nameDescription.concat(Array.from(document.querySelectorAll('#editor-rows input')));
}

function editorFieldLimits(input) {
  const cell = input.closest('td');
  if (!cell) return null;
  const cellIndex = Array.from(cell.parentElement.children).indexOf(cell);
  if (cellIndex === 0) return { min: EDITOR_MIN_RATE_C, max: EDITOR_MAX_RATE_C };
  if (cellIndex === 1) return { min: EDITOR_MIN_TEMP_C, max: EDITOR_MAX_TEMP_C };
  if (cellIndex === 2) return { min: EDITOR_MIN_HOLD_MIN, max: EDITOR_MAX_HOLD_MIN };
  return null;
}

function editorAdjustValue(input, direction, magnitude) {
  const limits = editorFieldLimits(input);
  if (!limits) return;
  const current = parseFloat(input.value);
  const base = Number.isFinite(current) ? current : 0;
  const next = Math.min(limits.max, Math.max(limits.min, base + direction * magnitude));
  input.value = Math.round(next);
  input.dispatchEvent(new Event('input', { bubbles: true })); // redraws the preview diagram
}

let editorSpin = null; // { interval, startTime } — the currently-held Up/Down spin, if any
let lastEditorFieldIndex = 0; // remembered so Escape (blur) -> arrow key re-enters, not no-ops

function stopEditorSpin() {
  if (editorSpin) {
    clearInterval(editorSpin.interval);
    editorSpin = null;
  }
}

document.addEventListener('focusin', (e) => {
  const fields = editorFieldList();
  const idx = fields.indexOf(e.target);
  if (idx !== -1) lastEditorFieldIndex = idx;
});

document.addEventListener('keydown', (e) => {
  const activeScreen = document.querySelector('.screen.active');
  if (!activeScreen || activeScreen.id !== 'screen-editor') return;

  const el = document.activeElement;
  const fields = editorFieldList();
  const curIdx = fields.indexOf(el);
  const inFieldList = curIdx !== -1;

  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    e.preventDefault();
    if (fields.length === 0) return;
    let idx;
    if (inFieldList) {
      idx = curIdx + (e.key === 'ArrowLeft' ? -1 : 1);
    } else {
      // Nothing focused (e.g. just Escaped out of a field) — re-select
      // wherever we last were instead of doing nothing.
      idx = lastEditorFieldIndex;
    }
    idx = Math.max(0, Math.min(fields.length - 1, idx));
    fields[idx].focus();
    fields[idx].select();
    return;
  }

  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
    if (!inFieldList || !editorFieldLimits(el)) return; // no-op on Name/Description
    e.preventDefault();
    if (e.repeat) return; // our own interval drives continued holding, not OS key-repeat
    const direction = e.key === 'ArrowUp' ? 1 : -1;
    editorAdjustValue(el, direction, 1);
    const startTime = Date.now();
    editorSpin = {
      interval: setInterval(() => {
        const heldMs = Date.now() - startTime;
        const magnitude = Math.min(50, 1 + Math.floor(heldMs / 250));
        editorAdjustValue(el, direction, magnitude);
      }, 80),
    };
  }
});

document.addEventListener('keyup', (e) => {
  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') stopEditorSpin();
});

// -- Settings screen ------------------------------------------------------------

function factoryReset() {
  if (
    !confirm(
      'This wipes stored Ramp & Hold programs in slots 1-4 and resets MGF/temperature-scale options. This cannot be undone.'
    )
  )
    return;
  api('POST', '/api/settings/reset').catch((e) => alert(`Error: ${e.message}`));
}

// -- Live progress/error stream --------------------------------------------------

const ui = new WebUI();
ui.on_message('progress', (data) => {
  if (document.getElementById('screen-run').classList.contains('active')) {
    appendLog(data.message);
  }
});
ui.on_message('error', (data) => {
  if (document.getElementById('screen-run').classList.contains('active')) {
    appendLog(`ERROR: ${data.message}`, true);
  } else {
    alert(`Error: ${data.message}`);
  }
});

// -- Wire up controls -------------------------------------------------------------

document.getElementById('new-profile-btn').addEventListener('click', openNewProfileEditor);
document.getElementById('settings-btn').addEventListener('click', () => {
  showScreen('screen-settings');
  refreshKeyHints();
  refreshBridgeStatus();
});

document.getElementById('profile-back-btn').addEventListener('click', goHome);
document.getElementById('profile-edit-btn').addEventListener('click', () => {
  if (currentProfile) openEditProfileEditor(currentProfile);
});
document.getElementById('start-firing-btn').addEventListener('click', startFiring);

document.getElementById('skip-btn').addEventListener('click', () => runAction('/api/skip', 'skip segment'));
document.getElementById('add-time-btn').addEventListener('click', () => runAction('/api/add_time', 'add 5 min'));
document.getElementById('add-temp-btn').addEventListener('click', () => runAction('/api/add_temp', 'add 3°C'));
document.getElementById('stop-btn').addEventListener('click', stopFiring);

document.getElementById('editor-back-btn').addEventListener('click', goHome);
document.getElementById('editor-add-row-btn').addEventListener('click', () => {
  editorAddRow();
  editorRedraw();
});
document.getElementById('editor-save-btn').addEventListener('click', saveProfile);

document.getElementById('settings-back-btn').addEventListener('click', goHome);
document.getElementById('mgf-toggle-btn').addEventListener('click', () =>
  api('POST', '/api/settings/mgf').catch((e) => alert(`Error: ${e.message}`))
);
document.getElementById('scale-f-btn').addEventListener('click', () =>
  api('POST', '/api/settings/scale', { celsius: false }).catch((e) => alert(`Error: ${e.message}`))
);
document.getElementById('scale-c-btn').addEventListener('click', () =>
  api('POST', '/api/settings/scale', { celsius: true }).catch((e) => alert(`Error: ${e.message}`))
);
document.getElementById('factory-reset-btn').addEventListener('click', factoryReset);

window.addEventListener('resize', () => {
  if (document.getElementById('screen-home').classList.contains('active')) {
    loadProfiles().then(refreshKeyHints);
  }
  if (document.getElementById('screen-profile').classList.contains('active') && currentProfile) {
    drawRampDiagram(document.getElementById('profile-diagram'), currentProfile.segments);
  }
  if (document.getElementById('screen-run').classList.contains('active') && currentProfile) {
    drawRampDiagram(document.getElementById('run-diagram'), currentProfile.segments);
  }
  if (document.getElementById('screen-editor').classList.contains('active')) {
    editorRedraw();
  }
});

// -- Static icons -----------------------------------------------------------

document.getElementById('home-icon').innerHTML = svgIcon('flame', 26);
document.getElementById('new-icon').innerHTML = svgIcon('plus', 18);
document.getElementById('settings-icon').innerHTML = svgIcon('gear', 18);
document.getElementById('skip-icon').innerHTML = svgIcon('skip', 18);
document.getElementById('add-time-icon').innerHTML = svgIcon('clock', 18);
document.getElementById('add-temp-icon').innerHTML = svgIcon('thermometer', 18);
document.getElementById('settings-header-icon').innerHTML = svgIcon('gear', 22);
document.getElementById('mgf-icon').innerHTML = svgIcon('wrench', 18);

goHome();
