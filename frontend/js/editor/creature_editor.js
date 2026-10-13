/**
 * Редактор істоти: центральне тіло (circle/polygon), кінцівки та сегменти
 * (PROJECT_SPEC.md §9.1, §10.2). Морфологія не має позиції у світі — істота
 * завжди редагується в центрі координат.
 *
 * Вибір кінцівок: `api.sel.limbs` — множина виділених, `api.sel.limb` —
 * активна (її параметри показує форма). Зміни застосовуються до всіх
 * виділених; `attach_angle` — як зсув, щоб кінцівки не злипалися в одну точку.
 */

import { DEFAULTS, clone } from '../state.js';
import {
  polygon, circle, capsule, line, sector, arc, hexAlpha,
  bodyPerimeterPoint, segmentPoses, creatureReach, drawCreatureGhost,
} from '../render/shapes.js';

export { bodyPerimeterPoint, segmentPoses, creatureReach, drawCreatureGhost };

const $ = (s) => document.querySelector(s);
const TAU = Math.PI * 2;
const round = (v, d = 3) => Number(v.toFixed(d));

// --- Кути ------------------------------------------------------------------
export const toDeg = (rad) => (rad * 180) / Math.PI;

/** «−69°» з типографським мінусом — щоб не плутати з дефісом у моноширинному. */
export function fmtDeg(rad, digits = 0) {
  const d = toDeg(rad);
  const s = Math.abs(d).toFixed(digits);
  return `${d < -0.05 ? '−' : ''}${s}°`;
}

export const jointRangeText = (joint) =>
  `${fmtDeg(joint.angle_min)}…${fmtDeg(joint.angle_max)}`;

export const jointSpanText = (joint) =>
  `Δ${toDeg(joint.angle_max - joint.angle_min).toFixed(0)}°`;

// --- Геометрія -------------------------------------------------------------
export function centroid(verts) {
  const n = verts.length;
  return [
    verts.reduce((a, v) => a + v[0], 0) / n,
    verts.reduce((a, v) => a + v[1], 0) / n,
  ];
}

/** Дзеркало `schemas.is_convex`: усі векторні добутки одного знаку. */
export function isConvex(verts, eps = 1e-9) {
  const n = verts.length;
  if (n < 3) return false;
  let sign = 0;
  for (let i = 0; i < n; i++) {
    const [ax, ay] = verts[i];
    const [bx, by] = verts[(i + 1) % n];
    const [cx, cy] = verts[(i + 2) % n];
    const cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx);
    if (Math.abs(cross) <= eps) return false;
    const s = cross > 0 ? 1 : -1;
    if (sign === 0) sign = s;
    else if (s !== sign) return false;
  }
  return true;
}

// --- Вибір кінцівок --------------------------------------------------------
/** Нормалізований список виділених індексів (активна — завжди в множині). */
export function selectedLimbIndices(api) {
  const n = api.state.creature.limbs.length;
  const set = api.sel.limbs;
  for (const i of [...set]) if (i < 0 || i >= n) set.delete(i);
  if (api.sel.limb >= 0 && api.sel.limb < n) set.add(api.sel.limb);
  return [...set].sort((a, b) => a - b);
}

/** Кінцівки, до яких застосовуються правки форми. */
export const editedLimbs = (api) =>
  selectedLimbIndices(api).map((i) => api.state.creature.limbs[i]);

export function selectLimb(api, index, { additive = false, range = false } = {}) {
  const sel = api.sel;
  const n = api.state.creature.limbs.length;
  if (index < 0 || index >= n) return;

  if (additive) {
    if (sel.limbs.has(index) && sel.limbs.size > 1) {
      sel.limbs.delete(index);
      if (sel.limb === index) sel.limb = [...sel.limbs][0] ?? -1;
    } else {
      sel.limbs.add(index);
      sel.limb = index;
    }
  } else if (range && sel.limb >= 0) {
    const [lo, hi] = [Math.min(sel.limb, index), Math.max(sel.limb, index)];
    for (let i = lo; i <= hi; i++) sel.limbs.add(i);
    sel.limb = index;
  } else {
    sel.limbs.clear();
    sel.limbs.add(index);
    sel.limb = index;
  }
  // Сегмент має існувати в активній кінцівці
  const limb = api.state.creature.limbs[sel.limb];
  if (limb) sel.seg = Math.min(sel.seg, limb.segments.length - 1);
}

export function clearLimbSelection(api) {
  api.sel.limbs.clear();
  api.sel.limb = -1;
  api.sel.seg = 0;
}

// --- Клік по канвасу -------------------------------------------------------
export function handleBodyClick(api, p) {
  if (!api.draft.body) return false;
  const [lo, hi] = DEFAULTS.limits.bodyVerts;
  if (api.draft.body.length >= hi) {
    api.setHint(`Полігон тіла: максимум ${hi} вершин — натисни «Завершити»`);
    return true;
  }
  api.draft.body.push([round(p.x), round(p.y)]);
  api.setHint(`Полігон тіла: ${api.draft.body.length} верш. (мін. ${lo}) — «Завершити»`);
  return true;
}

/** Додає кінцівку під кутом `attach` і робить її активною. */
export function addLimb(api, attach, { additive = false } = {}) {
  const creature = api.state.creature;
  if (creature.limbs.length >= DEFAULTS.limits.maxLimbs) {
    api.setHint(`Максимум ${DEFAULTS.limits.maxLimbs} кінцівок (§3)`);
    return null;
  }
  creature.limbs.push({
    attach_angle: round(attach, 4),
    rest_angle: 0.0,
    segments: [clone(DEFAULTS.segment)],
  });
  const index = creature.limbs.length - 1;
  selectLimb(api, index, { additive });
  api.sel.seg = 0;
  api.setHint(`Кінцівку L${index} додано (кут кріплення ${fmtDeg(attach)})`);
  api.refresh();
  return index;
}

/** Кут у середині найбільшого вільного проміжку між наявними кінцівками. */
export function freeAttachAngle(api) {
  const angles = api.state.creature.limbs
    .map((l) => Math.atan2(Math.sin(l.attach_angle), Math.cos(l.attach_angle)))
    .sort((a, b) => a - b);
  if (!angles.length) return Math.PI / 4;
  let best = angles[0] + Math.PI;        // протилежний бік для єдиної кінцівки
  let bestGap = -1;
  for (let i = 0; i < angles.length; i++) {
    const a = angles[i];
    const b = angles[(i + 1) % angles.length] + (i + 1 === angles.length ? TAU : 0);
    const gap = b - a;
    if (gap > bestGap) { bestGap = gap; best = a + gap / 2; }
  }
  return Math.atan2(Math.sin(best), Math.cos(best));
}

/**
 * Де опинився клік відносно центрального тіла: `perimeter` — у смузі `tol`
 * навколо периметра (там і кріпляться кінцівки), `inside` — глибоко всередині,
 * `outside` — відкрита вода.
 *
 * Саме смуга, а не весь диск: внутрішність тіла — велика площа, по якій легко
 * влучити випадково, а допуск `tol` рахується в пікселях, тож на малому зумі
 * смуга однаково накриває майже все тіло.
 */
function bodyZone(body, p, tol) {
  const [px, py] = bodyPerimeterPoint(body, Math.atan2(p.y, p.x));
  const reach = Math.hypot(px, py);
  if (reach <= 0) return 'outside';
  const dist = Math.hypot(p.x, p.y);
  if (Math.abs(dist - reach) <= tol) return 'perimeter';
  return dist < reach ? 'inside' : 'outside';
}

/** Сегмент наявної кінцівки під точкою (прямокутник length×width + допуск). */
function limbAtPoint(creature, p, tol) {
  for (const s of segmentPoses(creature)) {
    const dx = p.x - s.x, dy = p.y - s.y;
    const ca = Math.cos(s.angle), sa = Math.sin(s.angle);
    const along = dx * ca + dy * sa;          // локальна вісь сегмента
    const across = -dx * sa + dy * ca;
    if (Math.abs(along) <= s.length / 2 + tol * 0.5
      && Math.abs(across) <= s.width / 2 + tol * 0.5) {
      return { limb: s.limb, seg: s.idx };
    }
  }
  return null;
}

/**
 * Клік інструментом «Істота». Пріоритет цілей: точка кріплення → сегмент
 * наявної кінцівки → центральне тіло (нова кінцівка).
 *
 * Клік по порожньому місцю **нічого не робить**: раніше він додавав кінцівку
 * будь-де на полотні, тож спроба вибрати кінцівку чи просто клік по воді
 * непомітно нарощували морфологію.
 */
export function handleLimbClick(api, p, { additive = false, hitRadius = 0.4 } = {}) {
  const creature = api.state.creature;

  let hit = -1;
  let bestDist = hitRadius;
  creature.limbs.forEach((limb, i) => {
    const [ax, ay] = bodyPerimeterPoint(creature.body, limb.attach_angle);
    const d = Math.hypot(p.x - ax, p.y - ay);
    if (d <= bestDist) { bestDist = d; hit = i; }
  });

  // Клік по сегменту — теж вибір його кінцівки (і саме того сегмента у формі)
  const onLimb = hit < 0 ? limbAtPoint(creature, p, hitRadius) : null;
  if (onLimb) hit = onLimb.limb;

  if (hit >= 0) {
    selectLimb(api, hit, { additive });
    if (onLimb && api.sel.limb === onLimb.limb) api.sel.seg = onLimb.seg;
    const sel = selectedLimbIndices(api);
    api.setHint(sel.length > 1
      ? `Виділено ${sel.length} кінцівок: ${sel.map((i) => `L${i}`).join(', ')}`
      : `Кінцівка L${hit} — редагується`);
    api.refresh();
    return;
  }

  const zone = bodyZone(creature.body, p, hitRadius);
  if (zone === 'perimeter') {
    addLimb(api, Math.atan2(p.y, p.x), { additive });
    return;
  }

  api.setHint(zone === 'inside'
    ? 'Форма тіла задається слайдерами; кінцівка додається кліком по його периметрі'
    : 'Кінцівка додається кліком по периметру тіла (або кнопкою «+ Кінцівка»); '
      + 'клік по сегменту вибирає кінцівку');
}

// --- Малювання -------------------------------------------------------------
/**
 * Малює істоту й повертає підписи для екранного проходу (текст не можна
 * малювати у світовій системі — вісь Y перевернута).
 */
export function drawCreature(ctx, view, api, colors) {
  const creature = api.state.creature;
  const labels = [];
  const lw = view.px(1.6);
  const body = creature.body;
  const selected = new Set(selectedLimbIndices(api));

  // Сегменти кінцівок + суглоби
  for (const s of segmentPoses(creature)) {
    const isPrimary = s.limb === api.sel.limb;
    const isSel = selected.has(s.limb);
    const activeSeg = isPrimary && s.idx === api.sel.seg;

    if (isPrimary) { ctx.shadowColor = colors.accent; ctx.shadowBlur = 12; }
    capsule(ctx, s.x, s.y, s.angle, s.length, s.width, {
      fill: hexAlpha(colors.accent, isSel ? (activeSeg ? 0.42 : 0.28) : 0.1),
      stroke: isSel ? colors.accent : hexAlpha(colors.accent, 0.45),
      lw: isSel ? lw * 1.5 : lw,
    });
    ctx.shadowBlur = 0;

    circle(ctx, s.jointX, s.jointY, view.px(isSel ? 3.5 : 2.5), {
      fill: isSel ? colors.accent2 : hexAlpha(colors.accent2, 0.5),
    });
  }

  // Центральне тіло
  if (body.shape === 'circle') {
    circle(ctx, 0, 0, body.radius, {
      fill: hexAlpha(colors.accent, 0.14), stroke: colors.accent, lw,
    });
  } else if (body.vertices?.length >= 3) {
    polygon(ctx, body.vertices, {
      fill: hexAlpha(colors.accent, 0.14), stroke: colors.accent, lw,
    });
  }
  circle(ctx, 0, 0, view.px(2.5), { fill: colors.accent });

  // Точки кріплення + маркування кінцівок
  creature.limbs.forEach((limb, li) => {
    const [ax, ay] = bodyPerimeterPoint(body, limb.attach_angle);
    const isPrimary = li === api.sel.limb;
    const isSel = selected.has(li);
    circle(ctx, ax, ay, view.px(isPrimary ? 6 : isSel ? 5 : 3.5), {
      stroke: isSel ? colors.accent : hexAlpha(colors.accent, 0.6),
      fill: hexAlpha(colors.bg, 0.9),
      lw: view.px(isSel ? 2 : 1.4),
    });
    if (isPrimary) {
      circle(ctx, ax, ay, view.px(10), { stroke: hexAlpha(colors.accent, 0.5), lw: view.px(1) });
    }
    const out = 1.0 + (isSel ? 0.1 : 0);
    labels.push({
      x: ax * out * 1.25, y: ay * out * 1.25,
      text: `L${li}`,
      color: isSel ? colors.accent : colors.muted,
      size: isPrimary ? 12 : 11,
      bg: hexAlpha(colors.bg, 0.65),
    });
  });

  // Діапазони руху суглобів виділених кінцівок (без числових підписів)
  for (const li of selected) drawLimbAngles(ctx, view, api, colors, li);

  // Чернетка полігона тіла
  if (api.draft.body) {
    const pts = [...api.draft.body];
    const ok = pts.length < 3 || isConvex(pts);
    const col = ok ? colors.accent : colors.danger;
    if (pts.length >= 2) polygon(ctx, pts, { stroke: col, lw, dash: [1, 1], close: pts.length > 2 });
    if (pts.length >= 1 && api.draft.cursor) {
      const last = pts[pts.length - 1];
      line(ctx, last[0], last[1], api.draft.cursor.x, api.draft.cursor.y,
        { stroke: hexAlpha(col, 0.6), lw, dash: [1, 1] });
    }
    for (const v of pts) circle(ctx, v[0], v[1], view.px(3), { fill: col });
  }

  return labels;
}

/**
 * Сектор [angle_min, angle_max] кожного суглоба кінцівки + «привиди» сегмента
 * у крайніх положеннях. Числа градусів на полотні не пишемо — вони є в панелі
 * (поля кутів, список сегментів і рядок «Діапазони руху суглобів»).
 */
function drawLimbAngles(ctx, view, api, colors, limbIndex) {
  const isPrimary = limbIndex === api.sel.limb;
  const lw = view.px(1.2);
  const poses = segmentPoses(api.state.creature).filter((s) => s.limb === limbIndex);

  for (const s of poses) {
    const j = s.seg.joint;
    const a0 = s.angle + j.angle_min;
    const a1 = s.angle + j.angle_max;
    const r = Math.max(view.px(26), Math.min(s.length * 0.75, 1.4));
    const emph = isPrimary && s.idx === api.sel.seg;
    const alpha = emph ? 1 : isPrimary ? 0.7 : 0.4;

    sector(ctx, s.jointX, s.jointY, r, a0, a1, {
      fill: hexAlpha(colors.accent2, (emph ? 0.22 : 0.12) * (isPrimary ? 1 : 0.6)),
    });
    arc(ctx, s.jointX, s.jointY, r, a0, a1, {
      stroke: hexAlpha(colors.accent2, 0.9 * alpha), lw: emph ? lw * 1.6 : lw,
    });

    // «Спиці» крайніх положень і нейтраль
    for (const a of [a0, a1]) {
      line(ctx, s.jointX, s.jointY,
        s.jointX + Math.cos(a) * r, s.jointY + Math.sin(a) * r,
        { stroke: hexAlpha(colors.accent2, 0.85 * alpha), lw });
    }
    line(ctx, s.jointX, s.jointY,
      s.jointX + Math.cos(s.angle) * r * 0.75, s.jointY + Math.sin(s.angle) * r * 0.75,
      { stroke: hexAlpha(colors.muted, 0.8 * alpha), lw, dash: [1, 1] });

    // Привиди сегмента в крайніх кутах
    if (isPrimary) {
      for (const a of [a0, a1]) {
        capsule(ctx,
          s.jointX + Math.cos(a) * s.length / 2,
          s.jointY + Math.sin(a) * s.length / 2,
          a, s.length, s.width,
          { stroke: hexAlpha(colors.accent2, emph ? 0.55 : 0.3), lw, dash: [1, 1] });
      }
    }

  }
}

/** Мініатюра морфології у списку бібліотеки (процедурно, без растрів). */
export function renderCreatureThumb(canvas, creature, colors, { size = 30 } = {}) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(size * dpr);
  canvas.height = Math.round(size * dpr);
  canvas.style.width = `${size}px`;
  canvas.style.height = `${size}px`;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size, size);
  if (!creature?.body) return;

  const scale = (size / 2 - 1.5) / Math.max(creatureReach(creature), 0.3);
  ctx.translate(size / 2, size / 2);
  ctx.scale(scale, -scale);
  drawCreatureGhost(ctx, { scale, px: (n) => n / scale }, creature,
    { x: 0, y: 0, angle: 0 }, colors, { alpha: 0.95, arrow: false });
}

// --- Панель ----------------------------------------------------------------
export function initCreaturePanel(api) {
  // Істота читається лениво: Load JSON замінює `state.creature` новим об'єктом
  const creature = () => api.state.creature;

  $('#creature-name').addEventListener('input', (e) => {
    creature().name = e.target.value.trim() || 'creature';
    api.changed();
  });

  $('#body-shape').addEventListener('change', (e) => {
    const shape = e.target.value;
    creature().body.shape = shape;
    if (shape === 'circle') {
      creature().body.vertices = null;
      creature().body.radius = Number($('#body-radius').value);
      api.draft.body = null;
    } else {
      creature().body.radius = null;
      if (!creature().body.vertices) creature().body.vertices = regularPolygon(5, 0.9);
    }
    api.refresh();
  });

  bindRange('#body-radius', '#body-radius-v', (v) => {
    if (creature().body.shape === 'circle') { creature().body.radius = v; api.refresh(); }
  });
  bindRange('#body-density', '#body-density-v', (v) => { creature().body.density = v; api.changed(); });

  $('#poly-start').addEventListener('click', () => {
    api.setTool('creature');
    api.draft.body = [];
    api.setHint('Клікай вершини полігона тіла (3–8), потім «Завершити»');
    api.render();
  });
  $('#poly-cancel').addEventListener('click', () => {
    api.draft.body = null;
    api.setHint('Малювання полігона скасовано');
    api.render();
  });
  $('#poly-finish').addEventListener('click', () => {
    const pts = api.draft.body;
    const [lo, hi] = DEFAULTS.limits.bodyVerts;
    if (!pts || pts.length < lo) { api.setHint(`Потрібно щонайменше ${lo} вершин`); return; }
    if (pts.length > hi) { api.setHint(`Максимум ${hi} вершин`); return; }
    const c = centroid(pts);
    const rel = pts.map(([x, y]) => [round(x - c[0]), round(y - c[1])]);
    if (!isConvex(rel)) { api.setHint('Полігон має бути опуклим і невиродженим'); return; }
    creature().body.shape = 'polygon';
    creature().body.radius = null;
    creature().body.vertices = rel;
    api.draft.body = null;
    $('#body-shape').value = 'polygon';
    api.setHint(`Полігон тіла: ${rel.length} вершин`);
    api.refresh();
  });

  // attach_angle застосовується як ЗСУВ: інакше всі виділені кінцівки
  // злилися б в одну точку кріплення
  $('#limb-attach').addEventListener('input', (e) => {
    const v = Number(e.target.value);
    const primary = activeLimb(api);
    if (!primary) return;
    const delta = v - primary.attach_angle;
    for (const limb of editedLimbs(api)) limb.attach_angle = round(limb.attach_angle + delta, 4);
    $('#limb-attach-v').textContent = fmtDeg(primary.attach_angle);
    api.render();
    api.changed();
  });

  bindRange('#limb-rest', '#limb-rest-v', (v) => {
    for (const limb of editedLimbs(api)) limb.rest_angle = round(v, 4);
    api.render();
    api.changed();
  }, fmtDeg);

  $('#limb-add').addEventListener('click', () => {
    addLimb(api, freeAttachAngle(api));
  });

  $('#limb-select-all').addEventListener('click', () => {
    const n = creature().limbs.length;
    if (!n) return;
    api.sel.limbs = new Set(Array.from({ length: n }, (_, i) => i));
    if (api.sel.limb < 0) api.sel.limb = 0;
    api.setHint(`Виділено всі кінцівки (${n})`);
    api.refresh();
  });
  $('#limb-select-none').addEventListener('click', () => {
    clearLimbSelection(api);
    api.setHint('Виділення знято');
    api.refresh();
  });

  $('#limb-del').addEventListener('click', () => {
    const idx = selectedLimbIndices(api);
    if (!idx.length) return;
    for (const i of idx.slice().reverse()) creature().limbs.splice(i, 1);
    clearLimbSelection(api);
    if (creature().limbs.length) selectLimb(api, Math.min(idx[0], creature().limbs.length - 1));
    api.setHint(`Видалено кінцівок: ${idx.length}`);
    api.refresh();
  });

  $('#seg-add').addEventListener('click', () => {
    const limbs = editedLimbs(api);
    if (!limbs.length) return;
    const max = DEFAULTS.limits.maxSegments;
    let added = 0;
    for (const limb of limbs) {
      if (limb.segments.length >= max) continue;
      const prev = limb.segments[limb.segments.length - 1];
      limb.segments.push({
        length: round(Math.max(0.3, prev.length * 0.75)),
        width: round(Math.max(0.05, prev.width * 0.7)),
        density: prev.density,
        joint: clone(prev.joint),
      });
      added++;
    }
    if (!added) { api.setHint(`Максимум ${max} сегменти на кінцівку (§3)`); return; }
    api.sel.seg = (activeLimb(api)?.segments.length ?? 1) - 1;
    api.setHint(`Додано сегмент у ${added} кінцівк${added === 1 ? 'у' : 'ах'}`);
    api.refresh();
  });

  $('#seg-del').addEventListener('click', () => {
    // Видаляємо рівно той індекс, що й редагується; коротші кінцівки
    // пропускаємо — так само, як це робить правка полів сегмента
    const limbs = editedLimbs(api).filter(
      (l) => l.segments.length > 1 && api.sel.seg < l.segments.length);
    if (!limbs.length) {
      api.setHint('У цих кінцівках немає сегмента S' + api.sel.seg + ' або він останній');
      return;
    }
    for (const limb of limbs) limb.segments.splice(api.sel.seg, 1);
    api.sel.seg = Math.max(0, Math.min(api.sel.seg, (activeLimb(api)?.segments.length ?? 1) - 1));
    api.refresh();
  });

  const segFields = {
    '#seg-length': (s, v) => { s.length = v; },
    '#seg-width': (s, v) => { s.width = v; },
    '#seg-density': (s, v) => { s.density = v; },
    '#seg-amin': (s, v) => { s.joint.angle_min = v; },
    '#seg-amax': (s, v) => { s.joint.angle_max = v; },
    '#seg-torque': (s, v) => { s.joint.motor_torque = v; },
  };
  for (const [sel, apply] of Object.entries(segFields)) {
    $(sel).addEventListener('input', (e) => {
      // Порожнє поле — це «користувач стирає, щоб ввести нове», а не нуль:
      // Number('') === 0 пройшов би як валідне значення й зіпсував сегмент
      if (e.target.value.trim() === '') return;
      const v = Number(e.target.value);
      if (!Number.isFinite(v)) return;
      let touched = 0;
      for (const limb of editedLimbs(api)) {
        const seg = limb.segments[api.sel.seg];
        if (!seg) continue;             // у коротшій кінцівці такого сегмента немає
        apply(seg, v);
        touched++;
      }
      if (touched > 1) api.setHint(`Застосовано до ${touched} кінцівок (сегмент S${api.sel.seg})`);
      syncAngleReadouts(api);
      api.render();
      api.changed();
    });
    $(sel).addEventListener('change', () => api.refresh());
  }
}

/** Синхронізація панелі зі станом (викликається з `api.refresh()`). */
export function syncCreaturePanel(api) {
  const creature = api.state.creature;
  const body = creature.body;
  const selected = selectedLimbIndices(api);

  $('#creature-name').value = creature.name;
  $('#body-shape').value = body.shape;
  document.querySelectorAll('[data-when-shape]').forEach((el) => {
    el.hidden = el.dataset.whenShape !== body.shape;
  });
  if (body.shape === 'circle' && body.radius != null) {
    $('#body-radius').value = body.radius;
    $('#body-radius-v').textContent = body.radius.toFixed(2);
  }
  const density = Number(body.density ?? 1);
  $('#body-density').value = density;
  $('#body-density-v').textContent = density.toFixed(2);

  $('#limb-count').textContent = creature.limbs.length;
  const list = $('#limb-list');
  list.innerHTML = '';
  creature.limbs.forEach((limb, i) => {
    const isPrimary = i === api.sel.limb;
    const isSel = selected.includes(i);
    const li = document.createElement('li');
    li.className = isSel ? 'sel' : '';
    if (isPrimary) li.style.boxShadow = 'inset 2px 0 0 var(--accent)';
    const marker = isPrimary ? '◉' : isSel ? '●' : '○';
    const spans = (limb.segments ?? []).map((s) => s.joint.angle_max - s.joint.angle_min);
    const span = spans.length ? Math.max(...spans) : 0;
    li.innerHTML = `<span class="grow">${marker} L${i} · ${limb.segments.length} сег · `
      + `${fmtDeg(limb.attach_angle)} · Δ${toDeg(span).toFixed(0)}°</span>`;
    const del = document.createElement('button');
    del.textContent = '✕';
    del.title = 'Видалити кінцівку';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      creature.limbs.splice(i, 1);
      clearLimbSelection(api);
      if (creature.limbs.length) selectLimb(api, Math.min(i, creature.limbs.length - 1));
      api.refresh();
    });
    li.appendChild(del);
    li.addEventListener('click', (e) => {
      selectLimb(api, i, { additive: e.ctrlKey || e.metaKey, range: e.shiftKey });
      api.refresh();
    });
    list.appendChild(li);
  });

  const limb = activeLimb(api);
  $('#limb-form').hidden = !limb;
  $('#limb-sel-info').textContent = selected.length > 1
    ? `Редагується ${selected.length} кінцівок: ${selected.map((i) => `L${i}`).join(', ')} `
      + `(активна L${api.sel.limb})`
    : limb ? `Редагується L${api.sel.limb}` : 'Кінцівку не вибрано';

  if (limb) {
    $('#limb-attach').value = limb.attach_angle;
    $('#limb-attach-v').textContent = fmtDeg(limb.attach_angle);
    $('#limb-rest').value = limb.rest_angle;
    $('#limb-rest-v').textContent = fmtDeg(limb.rest_angle);

    $('#limb-range').innerHTML = limb.segments.map((s, i) =>
      `J${i}: ${jointRangeText(s.joint)} (${jointSpanText(s.joint)})`).join(' · ');

    const segList = $('#seg-list');
    segList.innerHTML = '';
    limb.segments.forEach((seg, i) => {
      const li = document.createElement('li');
      li.className = i === api.sel.seg ? 'sel' : '';
      li.innerHTML = `<span class="grow">S${i} · L=${seg.length.toFixed(2)} · `
        + `${jointRangeText(seg.joint)} · τ=${seg.joint.motor_torque}</span>`;
      li.addEventListener('click', () => { api.sel.seg = i; api.refresh(); });
      segList.appendChild(li);
    });

    const seg = activeSegment(api);
    $('#seg-form').hidden = !seg;
    if (seg) {
      $('#seg-length').value = seg.length;
      $('#seg-width').value = seg.width;
      $('#seg-density').value = seg.density;
      $('#seg-amin').value = seg.joint.angle_min;
      $('#seg-amax').value = seg.joint.angle_max;
      $('#seg-torque').value = seg.joint.motor_torque;
      syncAngleReadouts(api);
    }
  }
}

/** Градуси поруч із радіанами для полів кутів активного сегмента. */
function syncAngleReadouts(api) {
  const seg = activeSegment(api);
  if (!seg) return;
  $('#seg-amin-deg').textContent = fmtDeg(seg.joint.angle_min);
  $('#seg-amax-deg').textContent = fmtDeg(seg.joint.angle_max);
  $('#seg-range').textContent =
    `Розмах руху: ${jointRangeText(seg.joint)} (${jointSpanText(seg.joint)}) від нейтралі`;
  const limb = activeLimb(api);
  if (limb) {
    $('#limb-range').innerHTML = limb.segments.map((s, i) =>
      `J${i}: ${jointRangeText(s.joint)} (${jointSpanText(s.joint)})`).join(' · ');
  }
}

// --- Допоміжне -------------------------------------------------------------
const activeLimb = (api) => api.state.creature.limbs[api.sel.limb] ?? null;
const activeSegment = (api) => activeLimb(api)?.segments[api.sel.seg] ?? null;

function bindRange(inputSel, labelSel, onChange, format = (v) => v.toFixed(2)) {
  const input = $(inputSel);
  input.addEventListener('input', (e) => {
    const v = Number(e.target.value);
    const label = $(labelSel);
    if (label) label.textContent = format(v);
    onChange(v);
  });
}

function regularPolygon(n, r) {
  return Array.from({ length: n }, (_, i) => {
    const a = (i / n) * TAU + Math.PI / 2;
    return [round(r * Math.cos(a)), round(r * Math.sin(a))];
  });
}
