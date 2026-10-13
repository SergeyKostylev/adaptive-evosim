/**
 * Редактор перешкод (PROJECT_SPEC.md §9.2, §10.2):
 * static_polygon / static_circle / moving_polygon.
 *
 * Потоки малювання (стан у `api.draft.obs`):
 *   static_polygon : кліки = вершини (3–10) → «Завершити фігуру»
 *   static_circle  : клік = центр → клік = радіус
 *   moving_polygon : вершини → «Завершити фігуру» → клік = точка p2
 */

import { DEFAULTS } from '../state.js';
import { polygon, circle, line, hexAlpha, worldVerts } from '../render/shapes.js';

export { worldVerts };
import { centroid, isConvex } from './creature_editor.js';

const $ = (s) => document.querySelector(s);
const round = (v, d = 3) => Number(v.toFixed(d));

const HINTS = {
  static_polygon: 'Перешкода: клікай вершини (3–10), потім «Завершити фігуру»',
  static_circle: 'Перешкода: клік = центр кола, другий клік задає радіус',
  moving_polygon: 'Рухома перешкода: вершини → «Завершити фігуру» → клік задає p2',
};

export function obstacleHint(api) {
  const d = api.draft.obs;
  if (d?.stage === 'radius') return 'Другий клік фіксує радіус кола';
  if (d?.stage === 'p2') return 'Клікни другу крайню точку маршруту (p2)';
  return HINTS[$('#obs-type').value];
}

export function handleObstacleClick(api, p) {
  const type = $('#obs-type').value;
  const d = (api.draft.obs ??= { type, verts: [], stage: 'verts' });
  if (d.type !== type) { resetDraft(api); return handleObstacleClick(api, p); }

  if (type === 'static_circle') {
    if (d.stage === 'verts') {
      d.center = [round(p.x), round(p.y)];
      d.radius = 0;
      d.stage = 'radius';
      api.setHint(obstacleHint(api));
    } else {
      const r = Math.hypot(p.x - d.center[0], p.y - d.center[1]);
      if (r < 0.1) { api.setHint('Радіус замалий (мін. 0.1)'); return; }
      pushObstacle(api, { type: 'static_circle', radius: round(r), position: d.center });
      resetDraft(api);
    }
    return;
  }

  if (d.stage === 'p2') {
    pushObstacle(api, {
      type: 'moving_polygon',
      vertices: d.vertices,
      p1: d.p1,
      p2: [round(p.x), round(p.y)],
      period: Number($('#obs-period').value),
      phase: Number($('#obs-phase').value),
    });
    resetDraft(api);
    return;
  }

  const [, hi] = DEFAULTS.limits.obstacleVerts;
  if (d.verts.length >= hi) { api.setHint(`Максимум ${hi} вершин — «Завершити фігуру»`); return; }
  d.verts.push([round(p.x), round(p.y)]);
  api.setHint(`Вершин: ${d.verts.length} — «Завершити фігуру»`);
}

/** Кнопка «Завершити фігуру». */
export function finishObstacle(api) {
  const type = $('#obs-type').value;
  const d = api.draft.obs;
  const [lo, hi] = DEFAULTS.limits.obstacleVerts;

  if (type === 'static_circle') { api.setHint('Для кола достатньо двох кліків'); return; }
  if (!d || d.verts.length < lo) { api.setHint(`Потрібно щонайменше ${lo} вершини`); return; }
  if (d.verts.length > hi) { api.setHint(`Максимум ${hi} вершин`); return; }

  const c = centroid(d.verts);
  const rel = d.verts.map(([x, y]) => [round(x - c[0]), round(y - c[1])]);
  if (!isConvex(rel)) { api.setHint('Полігон перешкоди має бути опуклим і невиродженим'); return; }

  if (type === 'static_polygon') {
    pushObstacle(api, {
      type: 'static_polygon', vertices: rel,
      position: [round(c[0]), round(c[1])], angle: 0.0,
    });
    resetDraft(api);
  } else {
    d.vertices = rel;
    d.p1 = [round(c[0]), round(c[1])];
    d.stage = 'p2';
    api.setHint(obstacleHint(api));
    api.render();
  }
}

export function resetDraft(api) {
  api.draft.obs = null;
  api.setHint(obstacleHint(api));
  api.render();
}

function pushObstacle(api, obstacle) {
  api.state.environment.obstacles.push(obstacle);
  api.sel.obstacle = api.state.environment.obstacles.length - 1;
  api.setHint(`Перешкоду додано (${obstacle.type}), усього: ${api.state.environment.obstacles.length}`);
  api.refresh();
}

// --- Малювання -------------------------------------------------------------
export function drawObstacles(ctx, view, api, colors) {
  const lw = view.px(1.6);
  api.state.environment.obstacles.forEach((o, i) => {
    const selected = i === api.sel.obstacle;
    const stroke = selected ? colors.warn : hexAlpha(colors.warn, 0.8);
    const fill = hexAlpha(colors.warn, selected ? 0.22 : 0.1);

    if (o.type === 'static_circle') {
      circle(ctx, o.position[0], o.position[1], o.radius, { stroke, fill, lw });
    } else if (o.type === 'static_polygon') {
      polygon(ctx, worldVerts(o.vertices, o.position, o.angle), { stroke, fill, lw });
    } else {
      // moving_polygon: фігура в p1, привид у p2, маршрут між ними (§8.2)
      polygon(ctx, worldVerts(o.vertices, o.p1, 0), { stroke, fill, lw });
      polygon(ctx, worldVerts(o.vertices, o.p2, 0), {
        stroke: hexAlpha(colors.warn, 0.45), lw, dash: [1, 1],
      });
      line(ctx, o.p1[0], o.p1[1], o.p2[0], o.p2[1], {
        stroke: hexAlpha(colors.warn, 0.6), lw, dash: [1, 1],
      });
    }
  });

  // Чернетка
  const d = api.draft.obs;
  if (!d) return;
  const col = colors.warn;
  if (d.stage === 'radius' && d.center) {
    const r = api.draft.cursor
      ? Math.hypot(api.draft.cursor.x - d.center[0], api.draft.cursor.y - d.center[1]) : 0.1;
    circle(ctx, d.center[0], d.center[1], r, { stroke: col, lw, dash: [1, 1] });
    circle(ctx, d.center[0], d.center[1], view.px(3), { fill: col });
  } else if (d.stage === 'p2') {
    polygon(ctx, worldVerts(d.vertices, d.p1, 0), { stroke: col, lw, dash: [1, 1] });
    if (api.draft.cursor) {
      line(ctx, d.p1[0], d.p1[1], api.draft.cursor.x, api.draft.cursor.y,
        { stroke: hexAlpha(col, 0.6), lw, dash: [1, 1] });
      polygon(ctx, worldVerts(d.vertices, [api.draft.cursor.x, api.draft.cursor.y], 0),
        { stroke: hexAlpha(col, 0.45), lw, dash: [1, 1] });
    }
  } else if (d.verts?.length) {
    const ok = d.verts.length < 3 || isConvex(d.verts);
    const c = ok ? col : colors.danger;
    if (d.verts.length >= 2) {
      polygon(ctx, d.verts, { stroke: c, lw, dash: [1, 1], close: d.verts.length > 2 });
    }
    if (api.draft.cursor) {
      const last = d.verts[d.verts.length - 1];
      line(ctx, last[0], last[1], api.draft.cursor.x, api.draft.cursor.y,
        { stroke: hexAlpha(c, 0.6), lw, dash: [1, 1] });
    }
    for (const v of d.verts) circle(ctx, v[0], v[1], view.px(3), { fill: c });
  }
}

// --- Панель ----------------------------------------------------------------
export function initObstaclePanel(api) {
  $('#obs-type').addEventListener('change', () => {
    api.draft.obs = null;
    api.setTool('obstacle');
    api.refresh();
  });
  $('#obs-finish').addEventListener('click', () => finishObstacle(api));
  $('#obs-cancel').addEventListener('click', () => resetDraft(api));
  $('#obs-del').addEventListener('click', () => {
    const list = api.state.environment.obstacles;
    if (api.sel.obstacle < 0 || api.sel.obstacle >= list.length) return;
    list.splice(api.sel.obstacle, 1);
    api.sel.obstacle = Math.min(api.sel.obstacle, list.length - 1);
    api.refresh();
  });
}

export function syncObstaclePanel(api) {
  const list = api.state.environment.obstacles;
  const type = $('#obs-type').value;
  document.querySelectorAll('[data-when-obs]').forEach((el) => {
    el.hidden = el.dataset.whenObs !== type;
  });
  $('#obs-count').textContent = list.length;

  const ul = $('#obs-list');
  ul.innerHTML = '';
  list.forEach((o, i) => {
    const li = document.createElement('li');
    li.className = i === api.sel.obstacle ? 'sel' : '';
    const pos = o.type === 'moving_polygon' ? o.p1 : o.position;
    const label = o.type === 'static_circle'
      ? `r=${o.radius}` : `${o.vertices.length} верш.`;
    li.innerHTML = `<span class="grow">${i}: ${o.type} · ${label} · (${pos[0]}, ${pos[1]})</span>`;
    li.addEventListener('click', () => { api.sel.obstacle = i; api.refresh(); });
    ul.appendChild(li);
  });
}
