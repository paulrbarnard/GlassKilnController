// Ramp/hold/cool diagram — the visual heart of the "intuitive" requirement:
// a plain-language picture of what a profile will actually do to the
// glass. Physically-derived (not decorative): x-axis is estimated real
// time, computed from each segment's rate/target/hold exactly as the
// controller would run it — a direct JS port of the same math that used to
// live in the old PySide6 RampDiagram widget (python/ui/widgets/
// ramp_diagram.py, removed once PySide6 was confirmed unworkable in the
// App Lab container; see docs/architecture.md).

const ROOM_TEMP_F = 75;
const PRE_ANNEALING_TARGET_F = 960; // see kiln/protocol.py
const ANNEALING_END_F = 800;

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

function drawRampDiagram(canvas, segments, currentSegmentIndex = null) {
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
  const annealBand = styles.getPropertyValue('--diagram-anneal').trim() || 'rgba(80,140,255,0.16)';

  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, cssWidth, cssHeight);

  if (!segments || segments.length === 0) {
    ctx.fillStyle = label;
    ctx.font = '14px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('No profile selected', cssWidth / 2, cssHeight / 2);
    return;
  }

  const points = computePoints(segments);
  const marginLeft = 56;
  const marginRight = 16;
  const marginTop = 16;
  const marginBottom = 36;
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

  // Annealing band (p.24: 960 -> 800 F), for context, when in range.
  if (!(minTemp > PRE_ANNEALING_TARGET_F || maxTemp < ANNEALING_END_F)) {
    const yTop =
      marginTop + plotH - ((Math.min(PRE_ANNEALING_TARGET_F, maxTemp) - minTemp) / tempSpan) * plotH;
    const yBot =
      marginTop + plotH - ((Math.max(ANNEALING_END_F, minTemp) - minTemp) / tempSpan) * plotH;
    ctx.fillStyle = annealBand;
    ctx.fillRect(marginLeft, yTop, plotW, yBot - yTop);
  }

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
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'top';
  ctx.fillText(`${maxTemp.toFixed(0)}°F`, marginLeft - 6, marginTop);
  ctx.fillText(`${minTemp.toFixed(0)}°F`, marginLeft - 6, marginTop + plotH - 12);
  ctx.textAlign = 'left';
  ctx.fillText('0h', marginLeft, marginTop + plotH + 6);
  ctx.textAlign = 'right';
  ctx.fillText(`${maxHours.toFixed(1)}h total`, marginLeft + plotW, marginTop + plotH + 6);

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
}

function describeSegments(segments) {
  return segments
    .map((seg, i) => {
      const hold = seg.hold_minutes ? `, hold ${seg.hold_minutes.toFixed(0)} min` : '';
      return `${i + 1}. Ramp at ${seg.rate_f_per_hr.toFixed(0)}°F/hr to ${seg.target_temp_f.toFixed(0)}°F${hold}`;
    })
    .join('\n');
}
