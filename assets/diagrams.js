// Ramp/hold/cool diagram — the visual heart of the "intuitive" requirement:
// a plain-language picture of what a profile will actually do to the
// glass. Physically-derived (not decorative): x-axis is estimated real
// time, computed from each segment's rate/target/hold exactly as the
// controller would run it — a direct JS port of the same math that used to
// live in the old PySide6 RampDiagram widget (python/ui/widgets/
// ramp_diagram.py, removed once PySide6 was confirmed unworkable in the
// App Lab container; see docs/architecture.md).

const ROOM_TEMP_F = 75;

// All segment data (from the API, and everything in this file's math) is
// in Fahrenheit — that's what the GM LT manual and the factory GlassFire
// tables are specified in (see kiln/protocol.py). What the user sees and
// types is in the app's chosen unit (Settings; GET /api/settings), applied
// at the last moment by showTemp/showRate/readTemp/readRate below and in
// app.js, never stored. The sequencer converts again for the controller.
let TEMP_UNIT = 'C';

function setTempUnit(unit) {
  TEMP_UNIT = unit === 'F' ? 'F' : 'C';
}

// "°C" or "°F", for labels.
function tempUnitLabel() {
  return `°${TEMP_UNIT}`;
}

// °F (stored) -> the chosen unit, for display.
function showTemp(f) {
  return TEMP_UNIT === 'C' ? fToC(f) : f;
}

function showRate(fPerHr) {
  return TEMP_UNIT === 'C' ? rateFToC(fPerHr) : fPerHr;
}

// The chosen unit (typed by the user) -> °F, for the API.
function readTemp(value) {
  return TEMP_UNIT === 'C' ? cToF(value) : value;
}

function readRate(value) {
  return TEMP_UNIT === 'C' ? rateCToF(value) : value;
}

function fToC(f) {
  return ((f - 32) * 5) / 9;
}

// A rate is a difference (°/hr), not an absolute temperature — converting
// it is just the 5/9 scale factor, no -32 offset.
function rateFToC(fPerHr) {
  return (fPerHr * 5) / 9;
}

// Inverse conversions, for the profile editor: it displays and accepts °C,
// but the API (and everything downstream — protocol.py, the sequencer,
// the controller's own button-press programming) is Fahrenheit, so values
// get converted back at the moment they're read out of the form.
function cToF(c) {
  return (c * 9) / 5 + 32;
}

function rateCToF(cPerHr) {
  return (cPerHr * 9) / 5;
}

function formatDuration(minutes) {
  if (minutes >= 60) {
    const hours = minutes / 60;
    return `${hours % 1 === 0 ? hours.toFixed(0) : hours.toFixed(1)}h`;
  }
  return `${minutes.toFixed(0)} min`;
}

function computePoints(segments, startTempF = ROOM_TEMP_F) {
  const points = [{ hours: 0, tempF: startTempF, segIndex: -1 }];
  let t = 0;
  let temp = startTempF;
  segments.forEach((seg, i) => {
    const rate = Math.max(seg.rate_f_per_hr, 1);
    const delta = seg.target_temp_f - temp;
    const durationH = Math.abs(delta) / rate;
    t += durationH;
    temp = seg.target_temp_f;
    points.push({ hours: t, tempF: temp, segIndex: i });
    if (seg.hold_minutes) {
      t += seg.hold_minutes / 60;
      points.push({ hours: t, tempF: temp, segIndex: i });
    }
  });
  return points;
}

function drawRampDiagram(canvas, segments, currentSegmentIndex = null, options = {}) {
  const minimal = !!options.minimal;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const cssWidth = canvas.clientWidth;
  const cssHeight = canvas.clientHeight;
  canvas.width = cssWidth * dpr;
  canvas.height = cssHeight * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const styles = getComputedStyle(document.documentElement);
  const bg = styles.getPropertyValue('--diagram-bg').trim() || '#1b1b1f';
  const axis = styles.getPropertyValue('--diagram-axis').trim() || '#55555c';
  const line = styles.getPropertyValue('--diagram-line').trim() || '#ff7a3d';
  const current = styles.getPropertyValue('--diagram-current').trim() || '#ffd23d';
  const label = styles.getPropertyValue('--diagram-label').trim() || '#e8e8ea';

  if (!minimal) {
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, cssWidth, cssHeight);
  }

  if (!segments || segments.length === 0) {
    if (!minimal) {
      ctx.fillStyle = label;
      ctx.font = '18px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('No profile selected', cssWidth / 2, cssHeight / 2);
    }
    return;
  }

  const points = computePoints(segments);
  const marginLeft = minimal ? 4 : 70;
  const marginRight = minimal ? 4 : 16;
  const marginTop = minimal ? 4 : 46;
  const marginBottom = minimal ? 4 : 46;
  const plotW = Math.max(1, cssWidth - marginLeft - marginRight);
  const plotH = Math.max(1, cssHeight - marginTop - marginBottom);

  const maxHours = Math.max(...points.map((p) => p.hours)) || 1;
  const maxTemp = Math.max(...points.map((p) => p.tempF)) || 1;
  const minTemp = Math.min(0, ...points.map((p) => p.tempF));
  const tempSpan = maxTemp - minTemp || 1;

  const toXY = (p) => {
    const x = marginLeft + (p.hours / maxHours) * plotW;
    const y = marginTop + plotH - ((p.tempF - minTemp) / tempSpan) * plotH;
    return [x, y];
  };

  if (!minimal) {
    // Axes
    ctx.strokeStyle = axis;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(marginLeft, marginTop + plotH);
    ctx.lineTo(marginLeft + plotW, marginTop + plotH);
    ctx.moveTo(marginLeft, marginTop);
    ctx.lineTo(marginLeft, marginTop + plotH);
    ctx.stroke();

    ctx.fillStyle = label;
    ctx.font = 'bold 17px system-ui, sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'top';
    ctx.fillText(`${showTemp(maxTemp).toFixed(0)}${tempUnitLabel()}`, marginLeft - 8, marginTop);
    ctx.fillText(`${showTemp(minTemp).toFixed(0)}${tempUnitLabel()}`, marginLeft - 8, marginTop + plotH - 18);
    ctx.textAlign = 'left';
    ctx.fillText('0h', marginLeft, marginTop + plotH + 8);
    ctx.textAlign = 'right';
    ctx.fillText(`${maxHours.toFixed(1)}h total`, marginLeft + plotW, marginTop + plotH + 8);
  }

  // Line
  ctx.strokeStyle = line;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  points.forEach((p, i) => {
    const [x, y] = toXY(p);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Per-segment annotations: the rate along each ramp leg, and — at the
  // vertex it ramps to — the resulting temperature, combined with the
  // dwell time in a single label when that segment holds (one label per
  // vertex, not two competing for the same spot). This is what makes the
  // diagram self-explanatory without opening the collapsed segment
  // breakdown text. Skipped in minimal (card-thumbnail) mode: too little
  // room to render legibly at that size.
  if (!minimal) {
    ctx.fillStyle = label;
    ctx.textAlign = 'center';
    for (let j = 1; j < points.length; j++) {
      const prev = points[j - 1];
      const cur = points[j];
      if (cur.segIndex === prev.segIndex) continue; // flat hold leg, already labeled at its start
      const seg = segments[cur.segIndex];
      const rising = cur.tempF >= prev.tempF;

      const [mx, my] = toXY({ hours: (prev.hours + cur.hours) / 2, tempF: (prev.tempF + cur.tempF) / 2 });
      ctx.font = '16px system-ui, sans-serif';
      ctx.textBaseline = rising ? 'bottom' : 'top';
      ctx.fillText(`${showRate(seg.rate_f_per_hr).toFixed(0)}${tempUnitLabel()}/hr`, mx, my + (rising ? -6 : 6));

      const [ex, ey] = toXY(cur);
      const tempLabel = `${showTemp(cur.tempF).toFixed(0)}${tempUnitLabel()}`;
      const vertexLabel = seg.hold_minutes ? `${tempLabel}, ${formatDuration(seg.hold_minutes)} hold` : tempLabel;
      ctx.font = 'bold 17px system-ui, sans-serif';
      ctx.textBaseline = rising ? 'bottom' : 'top';
      ctx.fillText(vertexLabel, ex, ey + (rising ? -9 : 9));
    }
  }

  // Current segment highlight
  if (currentSegmentIndex !== null && currentSegmentIndex !== undefined) {
    const segPoints = points.filter((p) => p.segIndex === currentSegmentIndex);
    if (segPoints.length >= 2) {
      ctx.strokeStyle = current;
      ctx.lineWidth = 4;
      ctx.beginPath();
      segPoints.forEach((p, i) => {
        const [x, y] = toXY(p);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
  }

  // Live firing overlays (run screen): the measured temperature trace,
  // [[minutes since start, °F], ...], and a "now" line at elapsedHours.
  const trace = options.trace || [];
  if (trace.length) {
    ctx.fillStyle = current;
    trace.forEach(([minutes, tempF]) => {
      const hours = minutes / 60;
      if (hours > maxHours) return;
      const [x, y] = toXY({ hours, tempF: Math.min(Math.max(tempF, minTemp), maxTemp) });
      ctx.beginPath();
      ctx.arc(x, y, 3, 0, Math.PI * 2);
      ctx.fill();
    });
  }
  if (options.elapsedHours !== undefined && options.elapsedHours !== null) {
    const hours = Math.min(options.elapsedHours, maxHours);
    const [x] = toXY({ hours, tempF: minTemp });
    ctx.strokeStyle = label;
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 5]);
    ctx.beginPath();
    ctx.moveTo(x, marginTop);
    ctx.lineTo(x, marginTop + plotH);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

function describeSegments(segments) {
  return segments
    .map((seg, i) => {
      const hold = seg.hold_minutes ? `, hold ${seg.hold_minutes.toFixed(0)} min` : '';
      const unit = tempUnitLabel();
      return `${i + 1}. Ramp at ${showRate(seg.rate_f_per_hr).toFixed(0)}${unit}/hr to ${showTemp(seg.target_temp_f).toFixed(0)}${unit}${hold}`;
    })
    .join('\n');
}
