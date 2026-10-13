/**
 * Режим «Сцена» (PROJECT_SPEC.md §9.2): перешкоди, маршрути патрулювання та
 * точки появи популяції.
 *
 * Сцена не прив'язана до конкретної морфології: ГА оцінює популяцію істоти з
 * `ExperimentConfig.creature`, а точки появи задають лише **де** вона стартує
 * (кілька точок = різні старти епізодів, §7.3). Маршрут зберігає назву хижака
 * з бібліотеки (`preset`) і копію його параметрів — щоб прогін лишався
 * відтворюваним, навіть якщо бібліотеку згодом змінять.
 */

import { DEFAULTS, clone } from '../state.js';
import { circle, line, polygon, aura, hexAlpha } from '../render/shapes.js';
import { fmtDeg } from './creature_editor.js';

const $ = (s) => document.querySelector(s);
const round = (v, d = 3) => Number(v.toFixed(d));
const MAX_ROUTES = 8;
const MAX_SPAWNS = 16;

const spawns = (api) => api.state.environment.spawns;
const routes = (api) => api.state.environment.predators;
const activeSpawn = (api) => spawns(api)[api.sel.spawn] ?? null;
const activeRoute = (api) => routes(api)[api.sel.predator] ?? null;
const routePos = (route) => (route.waypoints.length ? route.waypoints[0] : null);

// --- Точки появи ------------------------------------------------------------
export function handleSpawnClick(api, p, { hitRadius = 0.6 } = {}) {
  let hit = -1;
  let best = hitRadius;
  spawns(api).forEach((sp, i) => {
    const d = Math.hypot(p.x - sp.position[0], p.y - sp.position[1]);
    if (d <= best) { best = d; hit = i; }
  });
  if (hit >= 0) {
    api.sel.spawn = hit;
    api.setHint(`Точку появи ${hit} вибрано`);
    api.refresh();
    return;
  }
  if (spawns(api).length >= MAX_SPAWNS) {
    api.setHint(`Максимум ${MAX_SPAWNS} точок появи`);
    return;
  }
  spawns(api).push({ ...clone(DEFAULTS.spawn), position: [round(p.x), round(p.y)] });
  api.sel.spawn = spawns(api).length - 1;
  api.setHint(`Точку появи додано у (${round(p.x)}, ${round(p.y)})`);
  api.refresh();
}

// --- Маршрути ---------------------------------------------------------------
export function addRoute(api) {
  if (routes(api).length >= MAX_ROUTES) {
    api.setHint(`Максимум ${MAX_ROUTES} маршрутів`);
    return null;
  }
  // Маршрут зберігає лише шлях; параметри хижака підставляє «Симуляція»,
  // а для прев'ю беруться з поточної сутності в режимі «Хижак»
  routes(api).push({ enabled: true, waypoints: [] });
  api.sel.predator = routes(api).length - 1;
  api.sel.waypoint = -1;
  api.setHint(`Маршрут М${api.sel.predator} додано — клікай його точки`);
  api.refresh();
  return api.sel.predator;
}

export function handleRouteClick(api, p, { hitRadius = 0.8 } = {}) {
  let hit = -1;
  let best = Infinity;
  routes(api).forEach((route, i) => {
    const pos = routePos(route);
    if (!pos || i === api.sel.predator) return;
    const reach = Math.max(hitRadius, api.state.predator?.r_body ?? DEFAULTS.predator.r_body);
    const d = Math.hypot(p.x - pos[0], p.y - pos[1]);
    if (d <= reach && d < best) { best = d; hit = i; }
  });
  if (hit >= 0) {
    api.sel.predator = hit;
    api.setHint(`Вибрано маршрут М${hit}`);
    api.refresh();
    return;
  }

  if (!routes(api).length && addRoute(api) === null) return;
  const route = activeRoute(api);
  if (!route) return;
  route.waypoints.push([round(p.x), round(p.y)]);
  api.setHint(`М${api.sel.predator}: точок ${route.waypoints.length}`
    + (route.waypoints.length < 2 ? ' (потрібно ≥2)' : ''));
  api.refresh();
}

// --- Перетягування ----------------------------------------------------------
/**
 * Усі точки сцени, які можна тягнути мишею. Для перешкод це їхня позиція
 * (у рухомих — обидві крайні точки маршруту).
 */
export function sceneHandles(api) {
  const out = [];
  spawns(api).forEach((sp, i) => out.push({
    kind: 'spawn', index: i, x: sp.position[0], y: sp.position[1], r: 0.9,
  }));
  routes(api).forEach((route, i) => (route.waypoints ?? []).forEach(([x, y], k) => out.push({
    kind: 'waypoint', index: i, sub: k, x, y, r: 0.5,
  })));
  (api.state.environment.obstacles ?? []).forEach((o, i) => {
    if (o.type === 'moving_polygon') {
      out.push({ kind: 'obstacle_p1', index: i, x: o.p1[0], y: o.p1[1], r: 0.7 });
      out.push({ kind: 'obstacle_p2', index: i, x: o.p2[0], y: o.p2[1], r: 0.7 });
    } else {
      const r = o.type === 'static_circle' ? Math.max(0.7, o.radius * 0.6) : 0.7;
      out.push({ kind: 'obstacle', index: i, x: o.position[0], y: o.position[1], r });
    }
  });
  return out;
}

/** Найближча точка під курсором; об'єкти активного підрежиму мають перевагу. */
export function findSceneHandle(api, p, pickRadius = 0.5) {
  const preferred = { obstacle: 'obstacle', route: 'waypoint', spawn: 'spawn' }[api.sceneTool];
  let best = null;
  let bestScore = Infinity;
  for (const h of sceneHandles(api)) {
    const reach = Math.max(pickRadius, h.r);
    const d = Math.hypot(p.x - h.x, p.y - h.y);
    if (d > reach) continue;
    const score = h.kind.startsWith(preferred) ? d - reach : d;
    if (score < bestScore) { bestScore = score; best = h; }
  }
  return best;
}

/** Переміщує точку й робить її об'єкт вибраним (щоб панель показувала його). */
export function moveSceneHandle(api, handle, x, y) {
  const env = api.state.environment;
  const pos = [round(x), round(y)];
  switch (handle.kind) {
    case 'spawn':
      env.spawns[handle.index].position = pos;
      api.sel.spawn = handle.index;
      break;
    case 'waypoint':
      env.predators[handle.index].waypoints[handle.sub] = pos;
      api.sel.predator = handle.index;
      api.sel.waypoint = handle.sub;
      break;
    case 'obstacle':
      env.obstacles[handle.index].position = pos;
      api.sel.obstacle = handle.index;
      break;
    case 'obstacle_p1':
      env.obstacles[handle.index].p1 = pos;
      api.sel.obstacle = handle.index;
      break;
    case 'obstacle_p2':
      env.obstacles[handle.index].p2 = pos;
      api.sel.obstacle = handle.index;
      break;
    default:
      return false;
  }
  return true;
}

export const handleTitle = (h) => ({
  spawn: `точку появи ${h.index}`,
  waypoint: `точку W${h.sub} маршруту М${h.index}`,
  obstacle: `перешкоду ${h.index}`,
  obstacle_p1: `початок руху перешкоди ${h.index}`,
  obstacle_p2: `кінець руху перешкоди ${h.index}`,
}[h.kind] ?? 'об\'єкт');

// --- Малювання --------------------------------------------------------------
export function drawScene(ctx, view, api, colors) {
  const labels = [];
  const subTool = api.sceneTool;
  const active = api.drag ?? api.hover;
  // Параметри для прев'ю — з редагованої сутності «Хижак»: у сцені їх немає
  const shape = { ...clone(DEFAULTS.predator), ...(api.state.predator ?? {}) };

  // Маршрути з хижаками
  routes(api).forEach((route, i) => {
    const wps = route.waypoints ?? [];
    const selected = i === api.sel.predator && subTool === 'route';
    const lw = view.px(selected ? 2 : 1.6);
    const dim = (route.enabled === false ? 0.35 : 1) * (subTool === 'route' ? 1 : 0.7);

    if (wps.length >= 2) {
      polygon(ctx, wps, { stroke: hexAlpha(colors.danger, 0.5 * dim), lw, dash: [1, 1], close: true });
    } else if (wps.length === 1 && api.draft.cursor && selected) {
      line(ctx, wps[0][0], wps[0][1], api.draft.cursor.x, api.draft.cursor.y,
        { stroke: hexAlpha(colors.danger, 0.4), lw, dash: [1, 1] });
    }
    wps.forEach(([x, y], k) => {
      circle(ctx, x, y, view.px(selected && k === api.sel.waypoint ? 5 : 3.5), {
        fill: hexAlpha(colors.danger, dim), stroke: colors.danger, lw: view.px(1.2),
      });
    });

    const pos = routePos(route);
    if (!pos) return;
    const [px, py] = pos;
    const rBody = shape.r_body;
    if (route.enabled !== false) aura(ctx, px, py, shape.r_aura, colors.danger);
    circle(ctx, px, py, shape.r_detect, { stroke: hexAlpha(colors.danger, 0.4 * dim), lw, dash: [2, 1] });
    circle(ctx, px, py, shape.r_aura, { stroke: hexAlpha(colors.danger, 0.65 * dim), lw });
    if (selected) {
      circle(ctx, px, py, rBody + view.px(6), { stroke: hexAlpha(colors.danger, 0.6), lw: view.px(1) });
    }
    circle(ctx, px, py, rBody, { fill: hexAlpha(colors.danger, 0.75 * dim), stroke: colors.danger, lw });

    labels.push({
      x: px, y: py + rBody + 0.45,
      text: `М${i}${route.enabled === false ? ' (вимк.)' : ''}`,
      color: selected ? colors.danger : hexAlpha(colors.danger, 0.85),
      size: selected ? 12 : 11, bg: hexAlpha(colors.bg, 0.7),
    });
  });

  // Точки появи популяції
  spawns(api).forEach((sp, i) => {
    const selected = i === api.sel.spawn && subTool === 'spawn';
    const dim = subTool === 'spawn' ? 1 : 0.65;
    const [x, y] = sp.position;
    const r = 0.9;
    circle(ctx, x, y, r, {
      fill: hexAlpha(colors.accent, 0.12 * dim),
      stroke: hexAlpha(colors.accent, (selected ? 1 : 0.7) * dim),
      lw: view.px(selected ? 2.2 : 1.5),
      dash: selected ? null : [1, 1],
    });
    circle(ctx, x, y, view.px(3), { fill: hexAlpha(colors.accent, dim) });
    // Напрямок старту
    line(ctx, x, y, x + Math.cos(sp.angle) * r, y + Math.sin(sp.angle) * r,
      { stroke: hexAlpha(colors.accent, dim), lw: view.px(1.6) });
    labels.push({
      x, y: y - r - 0.4,
      text: sp.label || `старт ${i}`,
      color: selected ? colors.accent : hexAlpha(colors.accent, 0.8),
      size: selected ? 12 : 11, bg: hexAlpha(colors.bg, 0.7),
    });
  });

  // Точка під курсором або та, яку тягнемо
  if (active) {
    circle(ctx, active.x, active.y, Math.max(active.r * 0.8, view.px(9)), {
      stroke: hexAlpha(colors.accent2, api.drag ? 1 : 0.7),
      lw: view.px(api.drag ? 2.4 : 1.6),
      dash: api.drag ? null : [1, 1],
    });
  }

  return labels;
}

// --- Панелі -----------------------------------------------------------------
export function initScenePanel(api) {
  // Точки появи
  $('#spawn-angle').addEventListener('input', (e) => {
    const sp = activeSpawn(api);
    if (!sp) return;
    sp.angle = round(Number(e.target.value), 4);
    $('#spawn-angle-v').textContent = fmtDeg(sp.angle);
    api.render();
    api.changed();
  });
  $('#spawn-label').addEventListener('input', (e) => {
    const sp = activeSpawn(api);
    if (!sp) return;
    sp.label = e.target.value.trim() || null;
    api.render();
    api.changed();
  });
  $('#spawn-del').addEventListener('click', () => {
    const list = spawns(api);
    if (api.sel.spawn < 0 || api.sel.spawn >= list.length) return;
    list.splice(api.sel.spawn, 1);
    api.sel.spawn = Math.min(api.sel.spawn, list.length - 1);
    api.refresh();
  });

  // Маршрути
  $('#route-add').addEventListener('click', () => addRoute(api));
  $('#route-del').addEventListener('click', () => {
    const list = routes(api);
    if (api.sel.predator < 0 || api.sel.predator >= list.length) return;
    list.splice(api.sel.predator, 1);
    api.sel.predator = Math.min(api.sel.predator, list.length - 1);
    api.setHint('Маршрут видалено');
    api.refresh();
  });
  $('#route-clear').addEventListener('click', () => {
    const route = activeRoute(api);
    if (!route) return;
    route.waypoints = [];
    api.sel.waypoint = -1;
    api.refresh();
  });
  $('#route-enabled').addEventListener('change', (e) => {
    const route = activeRoute(api);
    if (!route) return;
    route.enabled = e.target.checked;
    api.refresh();
  });
}

export function syncScenePanel(api) {
  // --- точки появи ---
  const list = spawns(api);
  $('#spawn-count').textContent = list.length;
  const ul = $('#spawn-list');
  ul.innerHTML = '';
  list.forEach((sp, i) => {
    const li = document.createElement('li');
    li.className = i === api.sel.spawn ? 'sel' : '';
    li.innerHTML = `<span class="grow">${i}: ${sp.label || 'старт'} · `
      + `(${sp.position[0]}, ${sp.position[1]}) · ${fmtDeg(sp.angle)}</span>`;
    const del = document.createElement('button');
    del.textContent = '✕';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      list.splice(i, 1);
      api.sel.spawn = Math.min(api.sel.spawn, list.length - 1);
      api.refresh();
    });
    li.appendChild(del);
    li.addEventListener('click', () => { api.sel.spawn = i; api.refresh(); });
    ul.appendChild(li);
  });
  if (!list.length) {
    ul.innerHTML = '<li class="muted" style="border-style:dashed">точок немає — '
      + 'старт у центрі світу</li>';
  }
  const sp = activeSpawn(api);
  $('#spawn-form').hidden = !sp;
  if (sp) {
    $('#spawn-angle').value = sp.angle;
    $('#spawn-angle-v').textContent = fmtDeg(sp.angle);
    $('#spawn-pos').textContent = `(${sp.position[0]}, ${sp.position[1]})`;
    $('#spawn-label').value = sp.label ?? '';
  }

  // --- маршрути ---
  const rlist = routes(api);
  $('#route-count').textContent = rlist.length;
  const rul = $('#route-list');
  rul.innerHTML = '';
  rlist.forEach((route, i) => {
    const li = document.createElement('li');
    li.className = i === api.sel.predator ? 'sel' : '';
    li.innerHTML = `<span class="grow">${i === api.sel.predator ? '◉' : '○'} М${i} · `
      + `${route.waypoints.length} точ.${route.enabled === false ? ' · вимк.' : ''}</span>`;
    const del = document.createElement('button');
    del.textContent = '✕';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      rlist.splice(i, 1);
      api.sel.predator = Math.min(api.sel.predator, rlist.length - 1);
      api.refresh();
    });
    li.appendChild(del);
    li.addEventListener('click', () => { api.sel.predator = i; api.refresh(); });
    rul.appendChild(li);
  });
  if (!rlist.length) {
    rul.innerHTML = '<li class="muted" style="border-style:dashed">маршрутів немає</li>';
  }

  const route = activeRoute(api);
  $('#route-form').hidden = !route;
  if (route) {
    $('#route-enabled').checked = route.enabled !== false;

    const wpList = $('#route-wp-list');
    wpList.innerHTML = '';
    route.waypoints.forEach(([x, y], i) => {
      const li = document.createElement('li');
      li.className = i === api.sel.waypoint ? 'sel' : '';
      li.innerHTML = `<span class="grow">W${i}: (${x}, ${y})</span>`;
      const del = document.createElement('button');
      del.textContent = '✕';
      del.addEventListener('click', (e) => {
        e.stopPropagation();
        route.waypoints.splice(i, 1);
        api.sel.waypoint = Math.min(api.sel.waypoint, route.waypoints.length - 1);
        api.refresh();
      });
      li.appendChild(del);
      li.addEventListener('click', () => { api.sel.waypoint = i; api.refresh(); });
      wpList.appendChild(li);
    });
    if (!route.waypoints.length) {
      wpList.innerHTML = '<li class="muted" style="border-style:dashed">маршрут порожній</li>';
    }
  }
}
