// Single-page touchscreen app: navigation between the five "screens" plus
// all REST/WebSocket wiring to python/main.py + python/web/api.py. Kept as
// plain JS/fetch (no framework) — this is a kiosk app with five simple
// views, not something that needs a build step.

const MAX_SEGMENTS = 8;

async function api(method, path, body) {
  const opts = { method, headers: {} };
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

async function loadProfiles() {
  const { profiles } = await api('GET', '/api/profiles');
  const list = document.getElementById('profile-list');
  list.innerHTML = '';
  profiles
    .slice()
    .sort((a, b) => (a.source !== 'user') - (b.source !== 'user') || a.name.localeCompare(b.name))
    .forEach((p) => {
      const li = document.createElement('li');
      li.className = p.source === 'user' ? 'badge-user' : '';
      const name = document.createElement('div');
      name.className = 'name';
      name.textContent = p.name;
      const desc = document.createElement('div');
      desc.className = 'desc';
      desc.textContent = p.description;
      li.append(name, desc);
      li.addEventListener('click', () => openProfile(p.id));
      list.appendChild(li);
    });
}

function goHome() {
  showScreen('screen-home');
  loadProfiles();
  refreshBridgeStatus();
}

// -- Profile detail screen ---------------------------------------------------

let currentProfile = null;

async function openProfile(id) {
  const profile = await api('GET', `/api/profiles/${id}`);
  currentProfile = profile;
  document.getElementById('profile-title').textContent = profile.name;
  document.getElementById('profile-description').textContent = profile.description;
  document.getElementById('profile-segments').textContent = describeSegments(profile.segments);
  document.getElementById('profile-edit-btn').style.visibility =
    profile.source === 'user' ? 'visible' : 'hidden';
  showScreen('screen-profile');
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
  document.getElementById('run-title').textContent = `Firing: ${currentProfile.name}`;
  document.getElementById('run-log').innerHTML = '';
  showScreen('screen-run');
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
  const rows = document.querySelectorAll('#editor-rows tr');
  const segments = [];
  rows.forEach((row) => {
    const [rate, temp, hold] = row.querySelectorAll('input');
    segments.push({
      rate_f_per_hr: parseFloat(rate.value) || 0,
      target_temp_f: parseFloat(temp.value) || 0,
      hold_minutes: parseFloat(hold.value) || 0,
    });
  });
  return segments;
}

function editorRedraw() {
  drawRampDiagram(document.getElementById('editor-diagram'), editorReadSegments());
}

function editorAddRow(seg) {
  const tbody = document.getElementById('editor-rows');
  if (tbody.children.length >= MAX_SEGMENTS) {
    alert(`Controller supports at most ${MAX_SEGMENTS} segments.`);
    return;
  }
  const row = document.createElement('tr');
  const values = seg || { rate_f_per_hr: 300, target_temp_f: 1000, hold_minutes: 10 };
  row.innerHTML = `
    <td><input type="number" value="${values.rate_f_per_hr}" /></td>
    <td><input type="number" value="${values.target_temp_f}" /></td>
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

function openNewProfileEditor() {
  editingProfileId = null;
  document.getElementById('editor-name').value = '';
  document.getElementById('editor-description').value = '';
  document.getElementById('editor-rows').innerHTML = '';
  editorAddRow();
  showScreen('screen-editor');
  requestAnimationFrame(editorRedraw);
}

function openEditProfileEditor(profile) {
  if (profile.kind !== 'ramp_hold') {
    alert("Built-in GlassFire profiles can't be edited directly.");
    return;
  }
  editingProfileId = profile.id;
  document.getElementById('editor-name').value = profile.name;
  document.getElementById('editor-description').value = profile.description;
  document.getElementById('editor-rows').innerHTML = '';
  profile.segments.forEach((seg) => editorAddRow(seg));
  showScreen('screen-editor');
  requestAnimationFrame(editorRedraw);
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
  refreshBridgeStatus();
});

document.getElementById('profile-back-btn').addEventListener('click', goHome);
document.getElementById('profile-edit-btn').addEventListener('click', () => {
  if (currentProfile) openEditProfileEditor(currentProfile);
});
document.getElementById('start-firing-btn').addEventListener('click', startFiring);

document.getElementById('skip-btn').addEventListener('click', () => runAction('/api/skip', 'skip segment'));
document.getElementById('add-time-btn').addEventListener('click', () => runAction('/api/add_time', 'add 5 min'));
document.getElementById('add-temp-btn').addEventListener('click', () => runAction('/api/add_temp', 'add 5°F'));
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

goHome();
