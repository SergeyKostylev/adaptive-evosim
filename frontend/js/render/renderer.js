/**
 * Рендер сцени за готовою конфігурацією (PROJECT_SPEC.md §9.2–9.3).
 *
 * На відміну від редактора, малює **не стан редактора, а переданий конфіг** —
 * тому ним однаково показується і композиція експерименту перед запуском, і
 * (з етапу 2) кадри `FramePacket` із `/ws/live`.
 */

import {
  makeView, withWorld, grid, polygon, circle, line, aura, screenText, hexAlpha,
  worldVerts, drawCreatureGhost, creatureReach,
} from './shapes.js';

/** Статичний вміст сцени: межі світу, перешкоди, маршрути, точки появи. */
export function drawExperiment(ctx, view, config, colors, opts = {}) {
  const labels = [];
  const env = config.environment ?? {};
  const world = env.world ?? { width: 60, height: 40 };
  const lw = view.px(1.6);

  // Межі світу — стіни, що теж є перешкодами (§7.2)
  const w = world.width / 2, h = world.height / 2;
  polygon(ctx, [[-w, -h], [w, -h], [w, h], [-w, h]], {
    stroke: hexAlpha(colors.muted, 0.9), fill: hexAlpha(colors.panel, 0.25), lw: view.px(2),
  });

  // Перешкоди
  for (const o of env.obstacles ?? []) {
    const stroke = hexAlpha(colors.warn, 0.85);
    const fill = hexAlpha(colors.warn, 0.12);
    if (o.type === 'static_circle') {
      circle(ctx, o.position[0], o.position[1], o.radius, { stroke, fill, lw });
    } else if (o.type === 'static_polygon') {
      polygon(ctx, worldVerts(o.vertices, o.position, o.angle), { stroke, fill, lw });
    } else if (o.type === 'moving_polygon') {
      polygon(ctx, worldVerts(o.vertices, o.p1, 0), { stroke, fill, lw });
      polygon(ctx, worldVerts(o.vertices, o.p2, 0), { stroke: hexAlpha(colors.warn, 0.4), lw, dash: [1, 1] });
      line(ctx, o.p1[0], o.p1[1], o.p2[0], o.p2[1], { stroke: hexAlpha(colors.warn, 0.5), lw, dash: [1, 1] });
    }
  }

  // Маршрути патрулювання з хижаками
  (env.predators ?? []).forEach((route, i) => {
    const wps = route.waypoints ?? [];
    const dim = route.enabled === false ? 0.35 : 1;
    if (wps.length >= 2) {
      polygon(ctx, wps, { stroke: hexAlpha(colors.danger, 0.5 * dim), lw, dash: [1, 1], close: true });
    }
    wps.forEach(([x, y]) => circle(ctx, x, y, view.px(3), { fill: hexAlpha(colors.danger, dim) }));
    if (!wps.length) return;

    const [px, py] = wps[0];
    if (route.enabled !== false) aura(ctx, px, py, route.r_aura, colors.danger);
    circle(ctx, px, py, route.r_detect, { stroke: hexAlpha(colors.danger, 0.4 * dim), lw, dash: [2, 1] });
    circle(ctx, px, py, route.r_aura, { stroke: hexAlpha(colors.danger, 0.65 * dim), lw });
    circle(ctx, px, py, route.r_body, { fill: hexAlpha(colors.danger, 0.75 * dim), stroke: colors.danger, lw });
    labels.push({
      x: px, y: py + route.r_body + 0.45,
      text: `${route.preset ?? `хижак ${i}`}${route.enabled === false ? ' (вимк.)' : ''}`,
      color: hexAlpha(colors.danger, 0.95), size: 11, bg: hexAlpha(colors.bg, 0.7),
    });
  });

  // Істота на кожній точці появи (порожній список = центр світу, §9.2)
  const creature = config.creature;
  const spawns = (env.spawns ?? []).length
    ? env.spawns
    : [{ position: [0, 0], angle: 0, label: 'старт (центр)' }];
  spawns.forEach((sp, i) => {
    drawCreatureGhost(ctx, view, creature, {
      x: sp.position[0], y: sp.position[1], angle: sp.angle ?? 0,
    }, colors, { color: colors.accent, alpha: i === 0 ? 0.95 : 0.6 });
    labels.push({
      x: sp.position[0],
      y: sp.position[1] - creatureReach(creature) - 0.45,
      text: sp.label || `старт ${i}`,
      color: i === 0 ? colors.accent : hexAlpha(colors.accent, 0.8),
      size: 11, bg: hexAlpha(colors.bg, 0.7),
    });
  });

  if (opts.dangerAtStart) {
    labels.push({
      x: opts.dangerAtStart[0], y: opts.dangerAtStart[1],
      text: '⚠ старт в аурі хижака', color: colors.danger, size: 12,
      bg: hexAlpha(colors.bg, 0.85),
    });
  }
  return labels;
}

/** Мініатюра сцени для списків вибору: світ, перешкоди, маршрути, старти. */
export function renderSceneThumb(canvas, env, colors, { size = 34 } = {}) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(size * dpr);
  canvas.height = Math.round(size * dpr);
  canvas.style.width = `${size}px`;
  canvas.style.height = `${size}px`;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size, size);
  if (!env) return;

  const world = env.world ?? { width: 60, height: 40 };
  const scale = Math.min(size / (world.width + 2), size / (world.height + 2));
  ctx.translate(size / 2, size / 2);
  ctx.scale(scale, -scale);
  const lw = 1.2 / scale;

  polygon(ctx, [
    [-world.width / 2, -world.height / 2], [world.width / 2, -world.height / 2],
    [world.width / 2, world.height / 2], [-world.width / 2, world.height / 2],
  ], { stroke: hexAlpha(colors.muted, 0.8), fill: hexAlpha(colors.panel, 0.5), lw });

  for (const o of env.obstacles ?? []) {
    const style = { fill: hexAlpha(colors.warn, 0.75), stroke: null };
    if (o.type === 'static_circle') circle(ctx, o.position[0], o.position[1], o.radius, style);
    else if (o.type === 'static_polygon') polygon(ctx, worldVerts(o.vertices, o.position, o.angle), style);
    else if (o.type === 'moving_polygon') polygon(ctx, worldVerts(o.vertices, o.p1, 0), style);
  }
  for (const route of env.predators ?? []) {
    const wps = route.waypoints ?? [];
    if (wps.length >= 2) {
      polygon(ctx, wps, { stroke: hexAlpha(colors.danger, 0.8), lw, close: true });
    }
    if (wps.length) {
      circle(ctx, wps[0][0], wps[0][1], Math.max(route.r_body ?? 0.8, 1.2),
        { fill: hexAlpha(colors.danger, 0.9) });
    }
  }
  for (const sp of env.spawns ?? []) {
    circle(ctx, sp.position[0], sp.position[1], 1.4, { fill: hexAlpha(colors.accent, 0.95) });
  }
  if (!(env.spawns ?? []).length) circle(ctx, 0, 0, 1.4, { fill: hexAlpha(colors.accent, 0.6) });
}

/**
 * Перегляд сцени на окремому канвасі: власна камера (пан/зум), автопідгонка
 * під світ, перемалювання за `draw(config)`.
 */
export function createPreview(canvas, colors) {
  const ctx = canvas.getContext('2d');
  const cam = { x: 0, y: 0, scale: 14 };
  let cssW = 1, cssH = 1, current = null, fitScale = 14;

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    cssW = Math.max(1, rect.width);
    cssH = Math.max(1, rect.height);
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    paint();
  }

  function fit(config = current) {
    const world = config?.environment?.world ?? { width: 60, height: 40 };
    cam.x = 0;
    cam.y = 0;
    cam.scale = Math.min(cssW / (world.width + 4), cssH / (world.height + 4)) || 14;
    fitScale = cam.scale;
    paint();
  }

  function paint() {
    const dpr = window.devicePixelRatio || 1;
    ctx.save();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = colors.bg;
    ctx.fillRect(0, 0, cssW, cssH);
    ctx.restore();
    if (!current) return;

    const view = makeView(cam, cssW, cssH);
    let labels = [];
    withWorld(ctx, cam, cssW, cssH, () => {
      grid(ctx, view, cam, cssW, cssH, colors);
      labels = drawExperiment(ctx, view, current, colors) ?? [];
    });
    for (const l of labels) {
      const s = view.toScreen(l.x, l.y);
      screenText(ctx, s.x, s.y, l.text, l);
    }
  }

  // --- камера ---
  let panning = false, last = null;
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  canvas.addEventListener('pointerdown', (e) => {
    panning = true;
    last = { x: e.clientX, y: e.clientY };
    canvas.setPointerCapture?.(e.pointerId);
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!panning || !last) return;
    cam.x -= (e.clientX - last.x) / cam.scale;
    cam.y += (e.clientY - last.y) / cam.scale;
    last = { x: e.clientX, y: e.clientY };
    paint();
  });
  canvas.addEventListener('pointerup', (e) => {
    panning = false;
    last = null;
    canvas.releasePointerCapture?.(e.pointerId);
  });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const before = makeView(cam, cssW, cssH).toWorld(e.clientX - rect.left, e.clientY - rect.top);
    cam.scale = Math.min(140, Math.max(3, cam.scale * Math.exp(-e.deltaY * 0.0015)));
    const after = makeView(cam, cssW, cssH).toWorld(e.clientX - rect.left, e.clientY - rect.top);
    cam.x += before.x - after.x;
    cam.y += before.y - after.y;
    paint();
  }, { passive: false });

  new ResizeObserver(resize).observe(canvas);
  resize();

  return {
    draw(config) {
      const first = current === null;
      current = config;
      if (first) fit(config);
      else paint();
    },
    fit,
    zoom: () => cam.scale / fitScale,
  };
}
