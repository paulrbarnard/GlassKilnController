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
    peak.innerHTML = `${showTemp(peakTempF(p.segments)).toFixed(0)}<span class="unit">${tempUnitLabel()} peak</span>`;
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
// Each real card gets a badge saying whether the kiln holds it in one of its
// 5 program slots (the app manages those itself: starting a profile that
// isn't on the kiln loads it into the least-used slot), and how often it
// has been fired.
function renderCustomGrid(profiles) {
  const list = document.getElementById('custom-list');
  list.innerHTML = '';
  const cardsWithDiagrams = [];

  // Most-fired first, so the profiles in regular use are at the top.
  const sorted = profiles.slice().sort((a, b) => (b.uses || 0) - (a.uses || 0) || a.name.localeCompare(b.name));

  sorted.forEach((p) => {
    const card = buildProfileCard(p, 'screen-custom', cardsWithDiagrams);
    const status = document.createElement('div');
    const fired = p.uses ? ` · fired ${p.uses}×` : '';
    status.className = p.kiln_slot ? 'slot-status active' : 'slot-status inactive';
    status.textContent = p.kiln_slot ? `On kiln · slot ${p.kiln_slot}${fired}` : `Not on kiln${fired}`;
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
  const { profiles } = await api('GET', '/api/profiles');
  const standard = profiles.filter((p) => p.kind === 'glassfire');
  renderProfileGrid('standard-list', standard, 'screen-standard');
}

async function openCustom() {
  showScreen('screen-custom');
  const { profiles } = await api('GET', '/api/profiles');
  renderCustomGrid(profiles.filter((p) => p.kind === 'ramp_hold'));
}

function goHome() {
  document.getElementById('cooling-popup').hidden = true;
  showScreen('screen-home');
  refreshBridgeStatus();
  loadKilnTemp();
}

// -- Profile detail screen ---------------------------------------------------

let currentProfile = null;

function renderProfileStats(segments) {
  const holdMinutes = segments.reduce((sum, s) => sum + (s.hold_minutes || 0), 0);
  const items = [
    { icon: 'thermometer', text: `${showTemp(peakTempF(segments)).toFixed(0)}${tempUnitLabel()} peak` },
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
  document.getElementById('profile-delete-btn').hidden = !isReprogrammable;
  document.getElementById('profile-duplicate-btn').style.visibility = isReprogrammable ? 'hidden' : 'visible';
  document.getElementById('mgf-section').hidden = isReprogrammable;
  document.getElementById('slot-section').hidden = !isReprogrammable;
  if (!isReprogrammable) {
    populateMgfFields(profile);
  } else {
    renderKilnStatus(profile);
  }
  showScreen('screen-profile');
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
  document.getElementById('mgf-temp').value = Math.round(showTemp(seg.target_temp_f));
  document.getElementById('mgf-hold').value = Math.round(seg.hold_minutes);
}

async function saveMgfOverride() {
  const temp = parseFloat(document.getElementById('mgf-temp').value);
  const holdMin = parseFloat(document.getElementById('mgf-hold').value);
  if (!Number.isFinite(temp) || !Number.isFinite(holdMin)) {
    alert('Enter a temperature and hold time.');
    return;
  }
  try {
    const profile = await api('POST', `/api/profiles/${currentProfile.id}/mgf_override`, {
      temp_override_f: readTemp(temp),
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

// -- Kiln status (Ramp & Hold profiles only) -----------------------------------

function renderKilnStatus(profile) {
  const n = profile.uses || 0;
  const fired = `Fired ${n} time${n === 1 ? '' : 's'}.`;
  document.getElementById('slot-section').textContent = profile.kiln_slot
    ? `On the kiln in program slot ${profile.kiln_slot}, so it starts straight away. ${fired}`
    : `Not on the kiln yet: starting it loads it into a program slot first (about 2 minutes). ${fired}`;
}

async function startFiring() {
  if (!currentProfile) return;
  try {
    await api('POST', `/api/run/${currentProfile.id}`);
  } catch (e) {
    alert(`Could not start: ${e.message}`);
    return;
  }
  firingProfile = currentProfile;
  document.getElementById('run-log').innerHTML = '';
  openRunScreen();
}

// -- Firing progress -------------------------------------------------------------
//
// The backend's FiringMonitor (kiln/firing.py) reads the controller's display
// while a firing runs and pushes its status as 'firing' messages; GET
// /api/firing returns the same, so a reloaded page picks the firing up again.

let firingProfile = null; // the profile being fired (with its segments)
let firingStatus = null;

const ACTIVE_PHASES = ['programming', 'firing', 'stopping'];
const PHASE_LABELS = {
  programming: 'Programming',
  firing: 'Firing',
  stopping: 'Stopping…',
  complete: 'Complete',
  stopped: 'Stopped',
  error: 'Error',
  ended: 'Ended',
};

function firingActive() {
  return !!firingStatus && ACTIVE_PHASES.includes(firingStatus.phase);
}

function formatHoursMinutes(hours) {
  const minutes = Math.max(0, Math.round(hours * 60));
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
}

// Pops up over the firing screen when a firing has ended with the kiln at or
// above 40 °C / 104 °F: the firing has finished, the kiln is cooling, and
// about how long until it's below 40 °C (revised with every reading). It
// clears itself once the kiln is below 40 °C (and never opens if it never
// was that hot), leaving the finished firing screen. Closed by hand, it
// stays closed for that firing; the home screen still shows the estimate.
let coolingPopupClosedFor = null; // fired_at of the firing whose popup was closed

const FINISHED_TITLES = {
  complete: 'Firing complete',
  ended: 'Program ended',
  stopped: 'Firing stopped',
  error: 'Kiln shut itself off',
};

function renderCoolingPopup(st, live) {
  const popup = document.getElementById('cooling-popup');
  const onRunScreen = document.getElementById('screen-run').classList.contains('active');
  // Until the first reading after the firing, go by the firing's last one.
  let reading = live;
  if (!reading && st && st.temp !== null && st.temp !== undefined) {
    const tempC = st.unit === 'F' ? ((st.temp - 32) * 5) / 9 : st.temp;
    reading = { temp: st.temp, unit: st.unit, hot: tempC >= 40, off: false, cooling: true, cool_by: null };
  }
  const show =
    onRunScreen &&
    st &&
    st.fired_at &&
    !firingActive() &&
    coolingPopupClosedFor !== st.fired_at &&
    reading &&
    !reading.off &&
    reading.hot;
  popup.hidden = !show;
  if (!show) return;
  document.getElementById('cooling-title').textContent = FINISHED_TITLES[st.phase] || 'Firing finished';
  document.getElementById('cooling-temp').textContent = `${Math.round(reading.temp)}°${reading.unit}`;
  document.getElementById('cooling-eta').textContent = coolingText(reading) || 'Estimating cooling time…';
}

function closeCoolingPopup() {
  document.getElementById('cooling-popup').hidden = true;
  if (firingStatus) coolingPopupClosedFor = firingStatus.fired_at;
}

function renderFiring() {
  const st = firingStatus;
  const banner = document.getElementById('firing-banner');
  banner.hidden = !firingActive();
  if (firingActive()) banner.textContent = `Firing in progress: ${st.profile_name} — tap to view`;
  if (!st || st.phase === 'idle') return;

  const name = (firingProfile && firingProfile.name) || st.profile_name || '';
  const icon = firingProfile ? svgIcon(processIcon(firingProfile.process), 24) : '';
  document.getElementById('run-title').innerHTML = `${icon} Firing: ${name}`;
  // An error before the kiln ever started firing means programming failed:
  // say so, and offer to try again (the app returns the controller to IdLE).
  const programmingFailed = st.phase === 'error' && !st.fired_at;
  document.getElementById('run-phase').textContent = programmingFailed
    ? 'Programming failed'
    : PHASE_LABELS[st.phase] || st.phase;
  document.getElementById('run-retry-btn').hidden = !programmingFailed || !firingProfile;
  document.getElementById('run-message').textContent = st.message || '';
  // Once the firing is over the monitor stops reading; the home-screen
  // reader carries on, so show its (newer) temperature and the cooling time.
  const finished = !firingActive() && st.fired_at;
  const live = finished && latestKilnTemp && latestKilnTemp.at > (st.ended_at || 0) ? latestKilnTemp : null;
  const shown = live && !live.off ? live : st;
  document.getElementById('run-temp').textContent =
    shown.temp !== null && shown.temp !== undefined ? `${Math.round(shown.temp)}°${shown.unit}` : '–';

  let elapsedHours = null;
  if (st.fired_at) {
    const end = st.ended_at || Date.now() / 1000;
    elapsedHours = (end - st.fired_at) / 3600;
    document.getElementById('run-elapsed').textContent = formatHoursMinutes(elapsedHours);
  } else {
    document.getElementById('run-elapsed').textContent = '–';
  }
  const segments = firingProfile && firingProfile.segments;
  const remaining = document.getElementById('run-remaining');
  const remainingLabel = document.getElementById('run-remaining-label');
  remainingLabel.textContent = finished ? 'Cooling' : 'Remaining';
  if (st.phase === 'firing' && segments && elapsedHours !== null) {
    // Estimate from the profile's own timeline; the real kiln may lag it.
    remaining.textContent = `~${formatHoursMinutes(Math.max(0, totalHours(segments) - elapsedHours))}`;
  } else if (finished && live) {
    if (live.off) remaining.textContent = 'kiln off';
    else if (!live.hot) remaining.textContent = 'safe to open';
    else if (live.cool_by) remaining.textContent = `~${formatHoursMinutes(Math.max(0, (live.cool_by - Date.now() / 1000) / 3600))} to ${live.unit === 'F' ? '104°F' : '40°C'}`;
    else remaining.textContent = 'estimating…';
  } else {
    remaining.textContent = '–';
  }
  renderCoolingPopup(st, live);

  const stopBtn = document.getElementById('stop-btn');
  stopBtn.hidden = !firingActive();
  stopBtn.disabled = st.phase === 'stopping';
  stopBtn.textContent = st.phase === 'stopping' ? 'Stopping…' : 'STOP';
  document.getElementById('run-done-btn').hidden = firingActive();

  if (document.getElementById('screen-run').classList.contains('active')) {
    drawRampDiagram(document.getElementById('run-diagram'), segments || [], null, {
      elapsedHours: st.phase === 'firing' ? elapsedHours : null,
      trace: st.trace || [],
    });
  }
}

async function setFiring(status) {
  const previousPhase = firingStatus && firingStatus.phase;
  firingStatus = status;
  // A confirmed stop goes straight back to the main screen. Any other end
  // (complete, ended at the kiln, error) stays on the firing screen until
  // Done, so the result can be read.
  if (status.phase === 'stopped' && previousPhase !== 'stopped') {
    renderFiring();
    goHome();
    return;
  }
  if (status.profile_id && (!firingProfile || firingProfile.id !== status.profile_id)) {
    try {
      firingProfile = await api('GET', `/api/profiles/${status.profile_id}`);
    } catch (e) {
      firingProfile = null;
    }
  }
  renderFiring();
}

function openRunScreen() {
  showScreen('screen-run');
  requestAnimationFrame(renderFiring);
}

async function loadFiring() {
  try {
    await setFiring(await api('GET', '/api/firing'));
    if (firingActive()) openRunScreen(); // e.g. the kiosk restarted mid-firing
  } catch (e) {
    console.warn('firing status unavailable', e);
  }
}

// Elapsed/remaining keep moving between status messages.
setInterval(() => {
  if (firingActive()) renderFiring();
}, 30000);

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
  // The status shows "Stopping…" until the controller confirms StOP/IdLE,
  // then setFiring returns to the main screen (or shows an error to act on
  // by hand, staying here).
  if (firingStatus) {
    firingStatus.phase = 'stopping';
    renderFiring();
  }
  runAction('/api/stop', 'stop');
}

// -- Editor screen --------------------------------------------------------------

let editingProfileId = null;

function editorReadSegments() {
  // Inputs are in the app's unit (matching the visible column headers);
  // converted back to °F here since that's what the API stores — see
  // diagrams.js's readTemp/readRate.
  const rows = document.querySelectorAll('#editor-rows tr');
  const segments = [];
  rows.forEach((row) => {
    const [rate, temp, hold] = row.querySelectorAll('input');
    segments.push({
      rate_f_per_hr: readRate(parseFloat(rate.value) || 0),
      target_temp_f: readTemp(parseFloat(temp.value) || 0),
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
  // row's inputs are in the app's unit, so convert once here for display.
  const values = segF || { rate_f_per_hr: 300, target_temp_f: 1000, hold_minutes: 10 };
  row.innerHTML = `
    <td><input type="number" value="${showRate(values.rate_f_per_hr).toFixed(0)}" /></td>
    <td><input type="number" value="${showTemp(values.target_temp_f).toFixed(0)}" /></td>
    <td><input type="number" value="${values.hold_minutes}" /></td>
    <td><button class="remove-row">&minus;</button></td>
  `;
  row.querySelectorAll('input').forEach((i) => i.addEventListener('input', editorRedraw));
  row.querySelector('.remove-row').addEventListener('click', () => {
    row.remove();
    editorRedraw();
  });
  tbody.appendChild(row);
}

// The Custom screen holds MAX_USER_PROFILES and no more (no scrolling or
// extra pages), so a new profile needs a free place. When the app is full,
// asks up front which profile the new one replaces (least used first), so
// nobody fills in the editor only to be refused. Resolves to
// { replaceId } (null when there's room), or null if cancelled.
async function makeRoomForProfile(what) {
  const customs = (await api('GET', '/api/profiles')).profiles.filter((p) => p.kind === 'ramp_hold');
  if (customs.length < MAX_USER_PROFILES) return { replaceId: null };
  const leastUsed = customs
    .slice()
    .sort((a, b) => (a.uses || 0) - (b.uses || 0) || !!a.kiln_slot - !!b.kiln_slot || a.name.localeCompare(b.name));
  return new Promise((resolve) => {
    document.getElementById('send-picker-title').textContent =
      `The app holds ${MAX_USER_PROFILES} custom profiles. Which one should ${what} replace?`;
    const list = document.getElementById('send-list');
    list.innerHTML = '';
    leastUsed.forEach((profile) => {
      const btn = document.createElement('button');
      const where = profile.kiln_slot ? `on kiln slot ${profile.kiln_slot}` : 'not on the kiln';
      const fired = profile.uses ? `fired ${profile.uses}×` : 'never fired';
      btn.innerHTML = `${profile.name}<span class="send-summary">${fired}, ${where} · ${profileSummary(profile)}</span>`;
      btn.addEventListener('click', () => {
        if (!confirm(`Replace “${profile.name}”? It's deleted from the app (anything on the kiln stays until that slot is reused).`)) return;
        pickerCancel = null;
        closeSendPicker();
        resolve({ replaceId: profile.id });
      });
      list.appendChild(btn);
    });
    pickerCancel = () => resolve(null);
    document.getElementById('send-picker').hidden = false;
  });
}

let pendingReplaceId = null; // the profile a new one replaces (see makeRoomForProfile)

async function openNewProfileEditor() {
  const room = await makeRoomForProfile('the new profile');
  if (!room) return;
  pendingReplaceId = room.replaceId;
  editingProfileId = null;
  document.getElementById('editor-name').value = '';
  document.getElementById('editor-description').value = '';
  document.getElementById('editor-rows').innerHTML = '';
  editorAddRow();
  showScreen('screen-editor');
  requestAnimationFrame(() => {
    editorRedraw();
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
  pendingReplaceId = null;
  editingProfileId = profile.id;
  document.getElementById('editor-name').value = profile.name;
  document.getElementById('editor-description').value = profile.description;
  document.getElementById('editor-rows').innerHTML = '';
  profile.segments.forEach((seg) => editorAddRow(seg));
  showScreen('screen-editor');
  requestAnimationFrame(() => {
    editorRedraw();
  });
}

async function openDuplicateProfileEditor(profile) {
  // For GlassFire presets (Edit is hidden for these — see openProfile()):
  // starts a brand new, separate PRO-mode profile pre-filled with this
  // preset's current numbers. Saving creates a new id via slugify(name),
  // it never touches the original preset.
  const room = await makeRoomForProfile('the copy');
  if (!room) return;
  pendingReplaceId = room.replaceId;
  editingProfileId = null;
  document.getElementById('editor-name').value = `${profile.name} (Custom)`;
  document.getElementById('editor-description').value = profile.description;
  document.getElementById('editor-rows').innerHTML = '';
  profile.segments.forEach((seg) => editorAddRow(seg));
  showScreen('screen-editor');
  requestAnimationFrame(() => {
    editorRedraw();
  });
}

async function deleteProfile() {
  const profile = currentProfile;
  if (!profile || profile.kind !== 'ramp_hold') return;
  const onKiln = profile.kiln_slot
    ? ` It stays in kiln slot ${profile.kiln_slot} until that slot is needed for another profile.`
    : '';
  if (!confirm(`Delete “${profile.name}” from the app?${onKiln}`)) return;
  try {
    await api('DELETE', `/api/profiles/${profile.id}`);
  } catch (e) {
    alert(`Could not delete: ${e.message}`);
    return;
  }
  currentProfile = null;
  showToast(`Deleted “${profile.name}”.`);
  openCustom();
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
  if (!editingProfileId && pendingReplaceId) body.replace_id = pendingReplaceId;
  let profile;
  try {
    profile = await api('POST', '/api/profiles', body);
  } catch (e) {
    alert(`Could not save: ${e.message}`);
    return;
  }
  pendingReplaceId = null;
  currentProfile = profile;
  openProfile(profile.id, 'screen-custom');
}

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
let toastTimer = null;

function showToast(message) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.hidden = true), 6000);
}

// -- Home: kiln temperature ----------------------------------------------------
// Read from the controller's display by the app (python/kiln/kiln_temp.py)
// whenever nothing else is using it. Red at or above 40 °C (104 °F), green
// below; "Off" when the display is dark. Shown in the controller's unit.

function clockTime(epochS) {
  return new Date(epochS * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

let latestKilnTemp = null;

// After a firing, while a hot kiln cools on its own: how long until it's
// below 40 °C / 104 °F (estimated by python/kiln/kiln_temp.py and revised
// with every reading). Null when there's nothing to say.
function coolingText(d) {
  if (!d || !d.hot || d.off || !d.cooling) return null;
  if (!d.cool_by) return 'Estimating cooling time…';
  const limit = d.unit === 'F' ? '104°F' : '40°C';
  const hours = Math.max(0, (d.cool_by - Date.now() / 1000) / 3600);
  return `Below ${limit} in about ${formatHoursMinutes(hours)} (around ${clockTime(d.cool_by)})`;
}

function renderKilnTemp(d) {
  latestKilnTemp = d;
  const cooling = document.getElementById('kiln-temp-cooling');
  const coolText = coolingText(d);
  cooling.hidden = !coolText;
  cooling.textContent = coolText || '';
  const box = document.getElementById('kiln-temp');
  const value = document.getElementById('kiln-temp-value');
  const note = document.getElementById('kiln-temp-note');
  const reading = d && d.temp !== null && d.temp !== undefined ? `${Math.round(d.temp)}°${d.unit}` : null;
  box.className = 'kiln-temp';
  if (d && d.off) {
    box.classList.add('off');
    value.textContent = 'Off';
    note.textContent = reading ? `Last read ${reading} at ${clockTime(d.at)}` : 'The kiln is switched off';
    if (reading && d.hot) box.classList.add('hot');
    return;
  }
  if (!reading) {
    box.classList.add('unknown');
    value.textContent = '…';
    note.textContent = 'Reading the kiln temperature';
    return;
  }
  box.classList.add(d.hot ? 'hot' : 'cool');
  value.textContent = reading;
  const ageMin = (Date.now() / 1000 - d.at) / 60;
  const age = ageMin >= 3 ? ` (read at ${clockTime(d.at)})` : '';
  note.textContent = (d.hot ? 'Danger: kiln hot. Open with caution' : 'Safe to open') + age;
}

async function loadKilnTemp() {
  try {
    renderKilnTemp(await api('GET', '/api/kiln_temp'));
  } catch (e) {
    // Keep whatever is showing; the next pushed reading will update it.
  }
}

ui.on_message('kiln_temp', (d) => {
  renderKilnTemp(d);
  if (firingStatus && !firingActive()) renderFiring();
});

ui.on_message('progress', (data) => {
  if (document.getElementById('screen-run').classList.contains('active')) {
    appendLog(data.message);
  } else {
    showToast(data.message);
  }
});
// The temperature unit is an app setting (Settings screen), applied to
// every label and input. It only changes once the controller has been set
// to match (see web/api.py set_temperature_scale).
function applyTempUnit(unit) {
  setTempUnit(unit);
  document.querySelectorAll('.temp-unit').forEach((el) => (el.textContent = tempUnitLabel()));
  document.getElementById('add-temp-step').textContent = TEMP_UNIT === 'C' ? '+3°C' : '+5°F';
  setScaleBusy(false);
  // Profile lists and diagrams are rendered when each screen opens, so
  // they pick the new unit up from there.
}

async function loadSettings() {
  try {
    const settings = await api('GET', '/api/settings');
    applyTempUnit(settings.temperature_scale);
  } catch (e) {
    console.warn('settings unavailable; showing °C', e);
  }
}

ui.on_message('firing', (data) => {
  setFiring(data);
});

ui.on_message('settings', (data) => {
  applyTempUnit(data.temperature_scale);
  setScaleBusy(false);
  alert(`Temperature unit is now ${tempUnitLabel()} on the app and the controller.`);
});

ui.on_message('error', (data) => {
  setScaleBusy(false);
  setKilnReading(false);
  if (writingSlot !== null) {
    writingSlot = null;
    renderKilnPrograms(kilnPrograms);
  }
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
  refreshBridgeStatus();
  checkControllerUnit();
});
document.getElementById('kiln-programs-btn').addEventListener('click', () => {
  showScreen('screen-kiln');
  loadKilnPrograms();
});
document.getElementById('kiln-back-btn').addEventListener('click', () => showScreen('screen-settings'));
document.getElementById('kiln-read-btn').addEventListener('click', readKilnPrograms);
document.getElementById('send-cancel-btn').addEventListener('click', closeSendPicker);

document.getElementById('profile-back-btn').addEventListener('click', () => profileBackTarget());
document.getElementById('profile-edit-btn').addEventListener('click', () => {
  if (currentProfile) openEditProfileEditor(currentProfile);
});
document.getElementById('profile-duplicate-btn').addEventListener('click', () => {
  if (currentProfile) openDuplicateProfileEditor(currentProfile);
});
document.getElementById('profile-delete-btn').addEventListener('click', deleteProfile);
document.getElementById('start-firing-btn').addEventListener('click', startFiring);
document.getElementById('mgf-save-btn').addEventListener('click', saveMgfOverride);
document.getElementById('mgf-clear-btn').addEventListener('click', clearMgfOverride);

document.getElementById('skip-btn').addEventListener('click', () => runAction('/api/skip', 'skip segment'));
document.getElementById('add-time-btn').addEventListener('click', () => runAction('/api/add_time', 'add 5 min'));
document.getElementById('add-temp-btn').addEventListener('click', () =>
  runAction('/api/add_temp', `add ${TEMP_UNIT === 'C' ? '3°C' : '5°F'}`)
);
document.getElementById('stop-btn').addEventListener('click', stopFiring);
document.getElementById('run-done-btn').addEventListener('click', goHome);
document.getElementById('cooling-close-btn').addEventListener('click', closeCoolingPopup);
document.getElementById('run-retry-btn').addEventListener('click', () => {
  currentProfile = firingProfile;
  startFiring();
});
document.getElementById('firing-banner').addEventListener('click', openRunScreen);

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
// Changing the unit takes ~20 s of button presses on the controller. Lock
// both buttons and say so until the controller confirms ('settings') or
// fails ('error'), so a second tap can't send it twice.
function setScaleBusy(busy, text) {
  // While idle, only the other unit's button can be pressed.
  document.getElementById('scale-f-btn').disabled = busy || TEMP_UNIT === 'F';
  document.getElementById('scale-c-btn').disabled = busy || TEMP_UNIT === 'C';
  document.getElementById('scale-current').textContent = text || `Temperature unit: ${tempUnitLabel()}`;
}

// The controller's own unit, read from its display, can differ from the
// app's (e.g. changed by hand at the kiln). If so, say so and let the app's
// unit be set on the controller again.
async function checkControllerUnit() {
  try {
    const { unit } = await api('GET', '/api/settings/controller_unit');
    if (unit && unit !== TEMP_UNIT) {
      document.getElementById('scale-current').textContent =
        `The controller is in °${unit} but the app uses °${TEMP_UNIT}. Tap Set °${TEMP_UNIT} to match.`;
      document.getElementById(TEMP_UNIT === 'C' ? 'scale-c-btn' : 'scale-f-btn').disabled = false;
    }
  } catch (e) {
    console.warn('controller unit unavailable', e);
  }
}

// -- Kiln programs (Settings) ----------------------------------------------------
//
// The kiln's 5 Ramp & Hold slots, read off its display (LoAd > slot > Review,
// each slot twice; see kiln/programs.py). Read-only on the kiln; Import
// saves a slot as an app custom profile assigned to that slot.

// One line per segment, e.g. "1. 300°C/hr → 650°C, 5 min"; the card has
// room for about five.
function kilnSegmentLines(entry) {
  const unit = tempUnitLabel();
  const lines = entry.segments.map((s, i) => {
    const hold = s.hold_minutes ? `, ${formatDuration(s.hold_minutes)}` : '';
    return `${i + 1}. ${showRate(s.rate_f_per_hr).toFixed(0)}${unit}/hr → ${showTemp(s.target_temp_f).toFixed(0)}${unit}${hold}`;
  });
  return lines.length > 5 ? [...lines.slice(0, 4), `+ ${lines.length - 4} more`] : lines;
}

let kilnPrograms = [];

function renderKilnPrograms(programs) {
  kilnPrograms = programs;
  const grid = document.getElementById('kiln-programs');
  grid.innerHTML = '';
  const bySlot = Object.fromEntries(programs.map((p) => [p.slot, p]));
  const toDraw = [];
  for (let slot = 1; slot <= 5; slot++) {
    const entry = bySlot[slot];
    const card = document.createElement('div');
    card.className = 'kiln-card';
    const label = document.createElement('div');
    label.className = 'slot-label';
    label.textContent = `Slot ${slot}`;
    card.appendChild(label);

    if (!entry) {
      const note = document.createElement('div');
      note.className = 'slot-note';
      note.textContent = 'Not read yet';
      card.appendChild(note);
    } else if (entry.error) {
      const err = document.createElement('div');
      err.className = 'slot-error';
      err.textContent = `Couldn't read: ${entry.error}`;
      card.appendChild(err);
    } else {
      const canvas = document.createElement('canvas');
      card.appendChild(canvas);
      toDraw.push([canvas, entry.segments]);
      const text = document.createElement('div');
      text.className = 'slot-text';
      text.innerHTML = kilnSegmentLines(entry).map((l) => `<div>${l}</div>`).join('');
      card.appendChild(text);

      const app = entry.app_profile;
      const note = document.createElement('div');
      note.className = 'slot-note';
      if (app && app.matches) {
        note.textContent = `In the app as “${app.name}” ✓${app.uses ? ` · fired ${app.uses}×` : ''}`;
        card.appendChild(note);
      } else {
        if (app) {
          note.textContent = `Differs from “${app.name}”`;
          card.appendChild(note);
        }
        const btn = document.createElement('button');
        btn.textContent = app ? 'Import (replace)' : 'Import';
        btn.disabled = writingSlot !== null;
        btn.addEventListener('click', () => importKilnProgram(entry.slot, btn));
        card.appendChild(btn);
      }
    }
    const send = document.createElement('button');
    send.textContent = writingSlot === slot ? 'Writing… (about 2 min)' : 'Send…';
    send.disabled = writingSlot !== null;
    send.addEventListener('click', () => openSendPicker(slot));
    card.appendChild(send);
    grid.appendChild(card);
  }
  requestAnimationFrame(() =>
    toDraw.forEach(([canvas, segments]) => drawRampDiagram(canvas, segments, null, { minimal: true }))
  );
}

// -- Sending an app profile to a kiln slot ------------------------------------

let writingSlot = null; // the slot being written, while a write runs

function profileSummary(profile) {
  const unit = tempUnitLabel();
  const peak = Math.max(...profile.segments.map((s) => s.target_temp_f));
  const n = profile.segments.length;
  return `${n} segment${n === 1 ? '' : 's'}, peak ${showTemp(peak).toFixed(0)}${unit}, ${totalHours(profile.segments).toFixed(1)}h`;
}

async function openSendPicker(slot) {
  let profiles;
  try {
    profiles = (await api('GET', '/api/profiles')).profiles.filter((p) => p.kind === 'ramp_hold');
  } catch (e) {
    alert(`Error: ${e.message}`);
    return;
  }
  document.getElementById('send-picker-title').textContent = `Send an app profile to kiln slot ${slot}`;
  const list = document.getElementById('send-list');
  list.innerHTML = '';
  if (!profiles.length) list.textContent = 'There are no custom profiles in the app yet.';
  profiles.forEach((profile) => {
    const btn = document.createElement('button');
    btn.innerHTML = `${profile.name}<span class="send-summary">${profileSummary(profile)}</span>`;
    btn.addEventListener('click', () => sendToSlot(slot, profile));
    list.appendChild(btn);
  });
  document.getElementById('send-picker').hidden = false;
}

let pickerCancel = null; // set while the picker is choosing a profile to replace

function closeSendPicker() {
  document.getElementById('send-picker').hidden = true;
  if (pickerCancel) {
    const cancel = pickerCancel;
    pickerCancel = null;
    cancel();
  }
}

async function sendToSlot(slot, profile) {
  if (!confirm(`Replace kiln slot ${slot} with “${profile.name}”? This takes about 2 minutes.`)) return;
  closeSendPicker();
  writingSlot = slot;
  renderKilnPrograms(kilnPrograms);
  try {
    await api('POST', `/api/kiln_programs/${slot}/write`, { profile_id: profile.id });
  } catch (e) {
    writingSlot = null;
    renderKilnPrograms(kilnPrograms);
    alert(`Could not send: ${e.message}`);
  }
}

async function importKilnProgram(slot, btn) {
  let room;
  try {
    room = await makeRoomForProfile(`the program from slot ${slot}`);
  } catch (e) {
    alert(`Could not import: ${e.message}`);
    return;
  }
  if (!room) return;
  btn.disabled = true;
  try {
    const body = room.replaceId ? { replace_id: room.replaceId } : {};
    const profile = await api('POST', `/api/kiln_programs/${slot}/import`, body);
    showToast(`Slot ${slot} saved as the custom profile “${profile.name}”.`);
    loadKilnPrograms();
  } catch (e) {
    btn.disabled = false;
    alert(`Could not import: ${e.message}`);
  }
}

function setKilnReading(reading) {
  const btn = document.getElementById('kiln-read-btn');
  btn.disabled = reading;
  btn.textContent = reading ? 'Reading… (about 2 min)' : 'Read programs from kiln';
}

async function loadKilnPrograms() {
  try {
    const result = await api('GET', '/api/kiln_programs');
    setKilnReading(result.reading);
    renderKilnPrograms(result.programs);
  } catch (e) {
    console.warn('kiln programs unavailable', e);
  }
}

function readKilnPrograms() {
  setKilnReading(true);
  api('POST', '/api/kiln_programs/read', {}).catch((e) => {
    setKilnReading(false);
    alert(`Error: ${e.message}`);
  });
}

ui.on_message('kiln_programs', (data) => {
  if (data.done) {
    setKilnReading(false);
    writingSlot = null;
  }
  renderKilnPrograms(data.programs);
});

function requestScale(celsius) {
  setScaleBusy(true, `Changing to °${celsius ? 'C' : 'F'} on the controller… (about 20 s)`);
  api('POST', '/api/settings/scale', { celsius }).catch((e) => {
    setScaleBusy(false);
    alert(`Error: ${e.message}`);
  });
}

document.getElementById('scale-f-btn').addEventListener('click', () => requestScale(false));
document.getElementById('scale-c-btn').addEventListener('click', () => requestScale(true));
document.getElementById('factory-reset-btn').addEventListener('click', factoryReset);

window.addEventListener('resize', () => {
  if (document.getElementById('screen-kiln').classList.contains('active')) {
    renderKilnPrograms(kilnPrograms);
  }
  if (document.getElementById('screen-standard').classList.contains('active')) {
    openStandard();
  }
  if (document.getElementById('screen-custom').classList.contains('active')) {
    openCustom();
  }
  if (document.getElementById('screen-profile').classList.contains('active') && currentProfile) {
    drawRampDiagram(document.getElementById('profile-diagram'), currentProfile.segments);
  }
  if (document.getElementById('screen-run').classList.contains('active')) {
    renderFiring();
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

document.getElementById('kiln-temp-icon').innerHTML = svgIcon('thermometer', 30);

goHome();
loadKilnTemp();
loadSettings();
loadFiring();
// http://<board>:7000/#kiln opens the Kiln programs screen directly (for
// checking its layout with a headless screenshot).
if (location.hash === '#kiln') {
  showScreen('screen-kiln');
  loadKilnPrograms();
}
