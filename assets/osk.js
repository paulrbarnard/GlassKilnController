// On-screen keyboard: a QWERTY pad for text fields and a numeric keypad for
// number fields, popped up from the bottom when an input gains focus. The
// kiosk has no OS-level virtual keyboard, so this is the app's own.

(function () {
  const QWERTY_ROWS = [
    '1234567890'.split(''),
    'qwertyuiop'.split(''),
    'asdfghjkl'.split(''),
    ['shift', ...'zxcvbnm'.split(''), 'back'],
    ['-', 'space', '.', 'done'],
  ];
  const NUMPAD_ROWS = [
    ['1', '2', '3'],
    ['4', '5', '6'],
    ['7', '8', '9'],
    ['clear', '0', 'back'],
    ['done'],
  ];
  const LABELS = { shift: '⇧', back: '⌫', space: 'space', done: 'Done', clear: 'Clear' };

  const osk = document.createElement('div');
  osk.id = 'osk';
  osk.hidden = true;
  document.body.appendChild(osk);

  let target = null;
  let shift = false;
  let fresh = false; // first key after focus replaces the field's existing value
  let mode = null;

  function isTextTarget(el) {
    return (
      el &&
      (el.tagName === 'TEXTAREA' ||
        (el.tagName === 'INPUT' && ['text', 'number', 'search', ''].includes(el.type)))
    );
  }

  function render() {
    osk.className = mode;
    osk.innerHTML = '';
    (mode === 'numpad' ? NUMPAD_ROWS : QWERTY_ROWS).forEach((row) => {
      const rowEl = document.createElement('div');
      rowEl.className = 'osk-row';
      row.forEach((key) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.dataset.key = key;
        const special = Object.prototype.hasOwnProperty.call(LABELS, key);
        btn.className = `osk-key osk-${special ? key : 'char'}`;
        btn.textContent = special ? LABELS[key] : shift ? key.toUpperCase() : key;
        if (key === 'shift' && shift) btn.classList.add('on');
        rowEl.appendChild(btn);
      });
      osk.appendChild(rowEl);
    });
  }

  function fire() {
    target.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function press(key) {
    if (!target) return;
    if (key === 'done') return hide();
    if (key === 'shift') {
      shift = !shift;
      return render();
    }
    if (key === 'clear') {
      target.value = '';
      fresh = false;
      return fire();
    }
    if (key === 'back') {
      target.value = fresh ? '' : target.value.slice(0, -1);
      fresh = false;
      return fire();
    }
    let ch = key === 'space' ? ' ' : key;
    if (shift && ch.length === 1) {
      ch = ch.toUpperCase();
      shift = false;
      render();
    }
    target.value = (fresh ? '' : target.value) + ch;
    fresh = false;
    fire();
  }

  function show(el) {
    target = el;
    fresh = true;
    shift = false;
    mode = el.type === 'number' ? 'numpad' : 'qwerty';
    render();
    osk.hidden = false;
    document.body.classList.add('osk-open');
    document.documentElement.style.setProperty('--osk-h', `${osk.offsetHeight}px`);
    el.scrollIntoView({ block: 'nearest' });
  }

  function hide() {
    target = null;
    osk.hidden = true;
    document.body.classList.remove('osk-open');
    if (isTextTarget(document.activeElement)) document.activeElement.blur();
  }

  // Keep focus on the field while a key is tapped.
  osk.addEventListener('mousedown', (e) => e.preventDefault());
  osk.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-key]');
    if (btn) press(btn.dataset.key);
  });

  document.addEventListener('focusin', (e) => {
    if (isTextTarget(e.target)) show(e.target);
  });

  document.addEventListener('pointerdown', (e) => {
    if (osk.hidden || osk.contains(e.target) || isTextTarget(e.target)) return;
    hide();
  });
})();
