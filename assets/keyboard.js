// Interim keyboard-hint navigation — added because the touchscreen's touch
// controller isn't working yet (board ships stuck in USB ISP/bootloader
// mode; see chat history, not something fixable in software) and only a
// keyboard is available for input in the meantime.
//
// Every clickable element in the *currently active* screen gets a visible
// key badge (1-9, then a-z); pressing that key clicks it, same as tapping
// it would. Re-run refreshKeyHints() any time a screen's visible button/
// card set changes (screen switch, profile list reload, editor row add/
// remove) — it always rebuilds from scratch, so it never goes stale.

const KEY_SEQUENCE = '123456789abcdefghijklmnopqrstuvwxyz'.split('');

let keyHintMap = new Map();

function clearKeyHints(root) {
  root.querySelectorAll('.key-hint').forEach((el) => el.remove());
  root.querySelectorAll('.hintable').forEach((el) => el.classList.remove('hintable'));
}

function refreshKeyHints() {
  const activeScreen = document.querySelector('.screen.active');
  keyHintMap = new Map();
  if (!activeScreen) return;

  clearKeyHints(activeScreen);

  const targets = Array.from(
    activeScreen.querySelectorAll('button:not([disabled]), .profile-card')
  ).filter((el) => el.offsetParent !== null && el.style.visibility !== 'hidden');

  targets.forEach((el, i) => {
    const key = KEY_SEQUENCE[i];
    if (!key) return; // more targets than available keys: shouldn't happen on any screen here
    el.classList.add('hintable');
    const badge = document.createElement('span');
    badge.className = 'key-hint';
    badge.textContent = key.toUpperCase();
    el.appendChild(badge);
    keyHintMap.set(key, el);
  });
}

document.addEventListener('keydown', (e) => {
  if (e.ctrlKey || e.altKey || e.metaKey) return;
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') {
    // Escape exits the field (blur) so the number/letter hints work again —
    // without this there's no keyboard-only way back out of a field once
    // focused, since every other key is legitimately typed into it. Not
    // needed once touch works, but harmless to leave in either way.
    if (e.key === 'Escape') {
      e.preventDefault();
      document.activeElement.blur();
    }
    return; // let all other keys type normally
  }
  const el = keyHintMap.get(e.key.toLowerCase());
  if (el) {
    e.preventDefault();
    el.click();
  }
});
