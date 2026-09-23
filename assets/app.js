// Single-page touchscreen app: navigation between the five "screens" plus
// all REST/WebSocket wiring to python/main.py + python/web/api.py. Kept as
// plain JS/fetch (no framework) — this is a kiosk app with five simple
// views, not something that needs a build step.

const MAX_SEGMENTS = 8;
const MAX_USER_PROFILES = 12; // must match protocol.py MAX_USER_PROFILES

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

// Builds one profile card (icon row, name, mini diagram, peak temp) and
// registers its canvas for deferred drawing (canvases need real layout
// size, so drawing happens after the grid is actually in the DOM — see
// the requestAnimationFrame callers below). Shared by both grids;
// renderCustomGrid adds its own status badge on top afterward.
function buildProfileCard(p, fromScreen, diagramSink) {
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
    diagramSink.push([diagramCanvas, p.segments]);
  }

  card.addEventListener('click', () => openProfile(p.id, fromScreen));
  return card;
}

function renderProfileGrid(containerId, profiles, fromScreen) {
  const list = document.getElementById(containerId);
  list.innerHTML = '';
  const cardsWithDiagrams = [];
  profiles.forEach((p) => list.appendChild(buildProfileCard(p, fromScreen, cardsWithDiagrams)));

  // Canvases need real layout size before drawing, so draw after the
  // browser has laid out the grid, not while building each card.
  requestAnimationFrame(() => {
    cardsWithDiagrams.forEach(([canvas, segments]) =>
      drawRampDiagram(canvas, segments, null, { minimal: true })
    );
  });
}

// Custom screen: always exactly MAX_USER_PROFILES cells, consistent with
// Standard's fixed 9-card layout, rather than a grid that grows/shrinks
// and looks sparse with only a couple of saved profiles. Unfilled cells
// are a distinct "+ Empty" placeholder that opens the new-profile editor.
// Each real card gets a status badge stating exactly what "active" means
// here: assigned to one of the 5 physical PRO slots, or not.
function renderCustomGrid(profiles) {
  const list = document.getElementById('custom-list');
  list.innerHTML = '';
  const cardsWithDiagrams = [];

  const sorted = profiles.slice().sort((a, b) => {
    const aActive = a.pro_slot ? 0 : 1;
    const bActive = b.pro_slot ? 0 : 1;
    if (aActive !== bActive) return aActive - bActive;
    if (a.pro_slot && b.pro_slot) return a.pro_slot - b.pro_slot;
    return a.name.localeCompare(b.name);
  });

  sorted.forEach((p) => {
    const card = buildProfileCard(p, 'screen-custom', cardsWithDiagrams);
    const status = document.createElement('div');
    status.className = p.pro_slot ? 'slot-status active' : 'slot-status inactive';
    status.textContent = p.pro_slot ? `ACTIVE — Slot ${p.pro_slot}` : 'Inactive';
    card.insertBefore(status, card.firstChild);
    list.appendChild(card);
  });

  for (let i = sorted.length; i < MAX_USER_PROFILES; i++) {
    const card = document.createElement('div');
    card.className = 'profile-card empty-slot';
    card.innerHTML = `<div class="empty-slot-inner">${svgIcon('plus', 32)}<div class="name">Empty</div></div>`;
    card.addEventListener('click', openNewProfileEditor);
    list.appendChild(card);
  }

  requestAnimationFrame(() => {
    cardsWithDiagrams.forEach(([canvas, segments]) =>
      drawRampDiagram(canvas, segments, null, { minimal: true })
    );
  });
}

async function openStandard() {
  showScreen('screen-standard');
  refreshKeyHints();
  const { profiles } = await api('GET', '/api/profiles');
  const standard = profiles.filter((p) => p.kind === 'glassfire');
  renderProfileGrid('standard-list', standard, 'screen-standard');
  refreshKeyHints();
}

async function openCustom() {
  showScreen('screen-custom');
  refreshKeyHints();
  const { profiles } = await api('GET', '/api/profiles');
  renderCustomGrid(profiles.filter((p) => p.kind === 'ramp_hold'));
  refreshKeyHints();
}

function goHome() {
  showScreen('screen-home');
  refreshKeyHints();
  refreshBridgeStatus();
}

// -- Profile detail screen ---------------------------------------------------

let currentProfile = null;

function renderProfileStats(segments) {
  const holdMinutes = segments.reduce((sum, s) => sum + (s.hold_minutes || 0), 0);
  const items = [
    { icon: 'thermometer', text: `${fToC(peakTempF(segments)).toFixed(0)}°C peak` },
    { icon: 'clock', text: `${totalHours(segments).toFixed(1)}h total` },
    { icon: 'clock', text: `${holdMinutes.toFixed(0)} min hold` },
  ];
  document.getElementById('profile-stats').innerHTML = items
    .map(
      (t) => `<span class="stat-item">${svgIcon(t.icon, 17)} ${t.text}</span>`
    )
    .join('<span class="stat-sep">·</span>');
}

let profileBackTarget = goHome; // set per-call below; a function, not a screen id

async function openProfile(id, fromScreen) {
  const profile = await api('GET', `/api/profiles/${id}`);
  currentProfile = profile;
  profileBackTarget = fromScreen === 'screen-custom' ? openCustom : fromScreen === 'screen-standard' ? openStandard : goHome;
  document.getElementById('profile-title').innerHTML = `${svgIcon(processIcon(profile.process), 24)} ${profile.name}`;
  document.getElementById('profile-description').textContent = profile.description;
  document.getElementById('profile-segments').textContent = describeSegments(profile.segments);
  renderProfileStats(profile.segments);
  // Only PRO-mode-based profiles (kind ramp_hold: the factory Casting
  // program in slot 5, and any user-created profile) are genuinely
  // reprogrammable on the controller — editing them overwrites that same
  // id/slot. AUTO/GlassFire presets are permanently fixed on the physical
  // controller except for a per-run MGF temp/hold override (pp.16-17); the
  // UI shouldn't offer "Edit" as if the preset itself could be rewritten.
  // "Duplicate as Custom" is the honest equivalent: start a new, separate
  // PRO-mode profile from this preset's numbers.
  const isReprogrammable = profile.kind === 'ramp_hold';
  document.getElementById('profile-edit-btn').style.visibility = isReprogrammable ? 'visible' : 'hidden';
  document.getElementById('profile-duplicate-btn').style.visibility = isReprogrammable ? 'hidden' : 'visible';
  document.getElementById('mgf-section').hidden = isReprogrammable;
  document.getElementById('slot-section').hidden = !isReprogrammable;
  if (!isReprogrammable) {
    populateMgfFields(profile);
  } else {
    await renderSlotPicker(profile);
  }
  showScreen('screen-profile');
  refreshKeyHints();
  requestAnimationFrame(() =>
    drawRampDiagram(document.getElementById('profile-diagram'), profile.segments)
  );
}

// -- MGF override (GlassFire profiles only) ----------------------------------
//
// The app-level equivalent of the controller's MGF feature: the process
// (segment 2) peak temp + hold time, persisted rather than re-prompted
// every run. Segments 1/3/4 are never shown here as editable — they're
// permanently fixed on the controller (see docs/controller_protocol.md).

function populateMgfFields(profile) {
  const seg = profile.segments[1];
  document.getElementById('mgf-temp').value = Math.round(fToC(seg.target_temp_f));
  document.getElementById('mgf-hold').value = Math.round(seg.hold_minutes);
}

async function saveMgfOverride() {
  const tempC = parseFloat(document.getElementById('mgf-temp').value);
  const holdMin = parseFloat(document.getElementById('mgf-hold').value);
  if (!Number.isFinite(tempC) || !Number.isFinite(holdMin)) {
    alert('Enter a temperature and hold time.');
    return;
  }
  try {
    const profile = await api('POST', `/api/profiles/${currentProfile.id}/mgf_override`, {
      temp_override_f: cToF(tempC),
      hold_override_minutes: holdMin,
    });
    currentProfile = profile;
    renderProfileStats(profile.segments);
    document.getElementById('profile-segments').textContent = describeSegments(profile.segments);
    drawRampDiagram(document.getElementById('profile-diagram'), profile.segments);
  } catch (e) {
    alert(`Could not save override: ${e.message}`);
  }
}

async function clearMgfOverride() {
  try {
    const profile = await api('POST', `/api/profiles/${currentProfile.id}/mgf_override`, {
      temp_override_f: null,
      hold_override_minutes: null,
    });
    currentProfile = profile;
    populateMgfFields(profile);
    renderProfileStats(profile.segments);
    document.getElementById('profile-segments').textContent = describeSegments(profile.segments);
    drawRampDiagram(document.getElementById('profile-diagram'), profile.segments);
  } catch (e) {
    alert(`Could not clear override: ${e.message}`);
  }
}

// -- Active PRO slot picker (Ramp & Hold profiles only) ----------------------

async function renderSlotPicker(profile) {
  const { profiles } = await api('GET', '/api/profiles');
  const occupants = {};
  profiles.forEach((p) => {
    if (p.pro_slot) occupants[p.pro_slot] = p;
  });

  const container = document.getElementById('slot-buttons');
  container.innerHTML = '';
  for (let slot = 1; slot <= 5; slot++) {
    const occupant = occupants[slot];
    const isCurrent = profile.pro_slot === slot;
    const btn = document.createElement('button');
    btn.className = isCurrent ? 'slot-btn current' : 'slot-btn';
    const occupantLabel = isCurrent ? 'this profile' : occupant ? occupant.name : '(empty)';
    btn.innerHTML = `Slot ${slot}<span class="occupant">${occupantLabel}</span>`;
    btn.addEventListener('click', () => handleSlotClick(profile, slot, isCurrent, occupant));
    container.appendChild(btn);
  }
}

async function handleSlotClick(profile, slot, isCurrent, occupant) {
  if (isCurrent) {
    if (!confirm(`Deactivate "${profile.name}" from slot ${slot}?`)) return;
    try {
      const updated = await api('POST', `/api/profiles/${profile.id}/deactivate`);
      currentProfile = updated;
      await renderSlotPicker(updated);
      refreshKeyHints();
    } catch (e) {
      alert(`Could not deactivate: ${e.message}`);
    }
    return;
  }
  if (occupant && !confirm(`Slot ${slot} is currently "${occupant.name}". Replace it with "${profile.name}"?`)) {
    return;
  }
  try {
    const updated = await api('POST', `/api/profiles/${profile.id}/activate`, { slot });
    currentProfile = updated;
    await renderSlotPicker(updated);
    refreshKeyHints();
  } catch (e) {
    alert(`Could not activate: ${e.message}`);
  }
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
  // Only for kind ramp_hold — the profile screen hides this button for
  // GlassFire presets (see openProfile()), but guard here too in case
  // this is ever reached another way. Editing and saving reprograms that
  // same id/slot (source becomes "user"), it doesn't fork a separate
  // copy — that's only valid because ramp_hold profiles genuinely live
  // in reprogrammable PRO-mode memory on the controller.
  if (profile.kind !== 'ramp_hold') return;
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

function openDuplicateProfileEditor(profile) {
  // For GlassFire presets (Edit is hidden for these — see openProfile()):
  // starts a brand new, separate PRO-mode profile pre-filled with this
  // preset's current numbers. Saving creates a new id via slugify(name),
  // it never touches the original preset.
  editingProfileId = null;
  document.getElementById('editor-name').value = `${profile.name} (Custom)`;
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
  openProfile(profile.id, 'screen-custom');
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

document.getElementById('nav-standard-btn').addEventListener('click', openStandard);
document.getElementById('nav-custom-btn').addEventListener('click', openCustom);
document.getElementById('standard-back-btn').addEventListener('click', goHome);
document.getElementById('custom-back-btn').addEventListener('click', goHome);
document.getElementById('new-profile-btn').addEventListener('click', openNewProfileEditor);
document.getElementById('settings-btn').addEventListener('click', () => {
  showScreen('screen-settings');
  refreshKeyHints();
  refreshBridgeStatus();
});

document.getElementById('profile-back-btn').addEventListener('click', () => profileBackTarget());
document.getElementById('profile-edit-btn').addEventListener('click', () => {
  if (currentProfile) openEditProfileEditor(currentProfile);
});
document.getElementById('profile-duplicate-btn').addEventListener('click', () => {
  if (currentProfile) openDuplicateProfileEditor(currentProfile);
});
document.getElementById('start-firing-btn').addEventListener('click', startFiring);
document.getElementById('mgf-save-btn').addEventListener('click', saveMgfOverride);
document.getElementById('mgf-clear-btn').addEventListener('click', clearMgfOverride);

document.getElementById('skip-btn').addEventListener('click', () => runAction('/api/skip', 'skip segment'));
document.getElementById('add-time-btn').addEventListener('click', () => runAction('/api/add_time', 'add 5 min'));
document.getElementById('add-temp-btn').addEventListener('click', () => runAction('/api/add_temp', 'add 3°C'));
document.getElementById('stop-btn').addEventListener('click', stopFiring);

document.getElementById('editor-back-btn').addEventListener('click', openCustom);
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
  if (document.getElementById('screen-standard').classList.contains('active')) {
    openStandard();
  }
  if (document.getElementById('screen-custom').classList.contains('active')) {
    openCustom();
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
