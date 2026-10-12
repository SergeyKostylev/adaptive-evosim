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
import { radiiOrderOk } from './predator_editor.js';

const $ = (s) => document.querySelector(s);
const round = (v, d = 3) => Number(v.toFixed(d));
const MAX_ROUTES = 8;
const MAX_SPAWNS = 16;
const OWN = '__own__';

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
export function addRoute(api, params = null, preset = null) {
  if (routes(api).length >= MAX_ROUTES) {
    api.setHint(`Максимум ${MAX_ROUTES} маршрутів`);
    return null;
  }
  routes(api).push({
    ...clone(DEFAULTS.predator),
    ...(params ?? {}),
    preset,
    waypoints: [],
  });
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
    const reach = Math.max(hitRadius, route.r_body ?? DEFAULTS.predator.r_body);
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

// --- Малювання --------------------------------------------------------------
export function drawScene(ctx, view, api, colors) {
  const labels = [];
  const subTool = api.sceneTool;

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
    const rBody = route.r_body ?? DEFAULTS.predator.r_body;
    if (route.enabled !== false) aura(ctx, px, py, route.r_aura, colors.danger);
    circle(ctx, px, py, route.r_detect, { stroke: hexAlpha(colors.danger, 0.4 * dim), lw, dash: [2, 1] });
    circle(ctx, px, py, route.r_aura, { stroke: hexAlpha(colors.danger, 0.65 * dim), lw });
    if (selected) {
      circle(ctx, px, py, rBody + view.px(6), { stroke: hexAlpha(colors.danger, 0.6), lw: view.px(1) });
    }
    circle(ctx, px, py, rBody, { fill: hexAlpha(colors.danger, 0.75 * dim), stroke: colors.danger, lw });

    labels.push({
      x: px, y: py + rBody + 0.45,
      text: `М${i}${route.preset ? ` · ${route.preset}` : ''}${route.enabled === false ? ' (вимк.)' : ''}`,
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
  $('#route-predator').addEventListener('change', (e) => {
    const route = activeRoute(api);
    if (!route) return;
    const name = e.target.value;
    if (name === OWN) {
      route.preset = null;
      api.setHint('Маршрут використовує власні параметри');
    } else {
      const params = api.predatorLibrary.get(name);
      if (params) Object.assign(route, clone(params), { preset: name });
      api.setHint(`М${api.sel.predator}: параметри хижака «${name}»`);
    }
    api.refresh();
  });
}

/** Наповнює список хижаків для маршруту (бібліотека + «власні»). */
export function syncRouteSources(api, predators = []) {
  const select = $('#route-predator');
  const route = activeRoute(api);
  select.innerHTML = '';
  const own = document.createElement('option');
  own.value = OWN;
  own.textContent = 'власні параметри';
  select.appendChild(own);
  for (const meta of predators) {
    const o = document.createElement('option');
    o.value = meta.name;
    o.textContent = `${meta.name} · r ${meta.r_body} · аура ${meta.r_aura}`;
    select.appendChild(o);
  }
  select.value = route?.preset && [...select.options].some((o) => o.value === route.preset)
    ? route.preset : OWN;
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
    const bad = radiiOrderOk(route) ? '' : ' ⚠';
    li.innerHTML = `<span class="grow">${i === api.sel.predator ? '◉' : '○'} М${i} · `
      + `${route.waypoints.length} точ. · ${route.preset ?? 'власні'}${bad}`
      + `${route.enabled === false ? ' · вимк.' : ''}</span>`;
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
    $('#route-params').textContent =
      `r ${route.r_body} · патруль ${route.v_patrol} · погоня ${route.v_chase} · `
      + `аура ${route.r_aura} · виявлення ${route.r_detect}`;
    syncRouteSources(api, api.predatorLibraryRows ?? []);

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
