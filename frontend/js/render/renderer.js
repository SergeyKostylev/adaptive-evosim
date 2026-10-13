/**
 * Рендер сцени за готовою конфігурацією (PROJECT_SPEC.md §9.2–9.3).
 *
 * На відміну від редактора, малює **не стан редактора, а переданий конфіг** —
 * тому ним однаково показується і композиція експерименту перед запуском, і
 * (з етапу 2) кадри `FramePacket` із `/ws/live`.
 */

import {
  makeView, withWorld, grid, polygon, circle, line, capsule, aura, screenText, hexAlpha,
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

// ---------------------------------------------------------------------------
// Живі кадри /ws/live (§9.5). Статика сцени приходить **один раз** пакетом
// `scene`, у кадрах її немає — тому малювання розділене на два аргументи.
// ---------------------------------------------------------------------------

/** Статика епізоду: межі світу, нерухомі перешкоди, маршрути патрулювання. */
function drawLiveScene(ctx, view, scene, colors) {
  const world = scene.world ?? { width: 60, height: 40 };
  const lw = view.px(1.6);
  const w = world.width / 2, h = world.height / 2;
  polygon(ctx, [[-w, -h], [w, -h], [w, h], [-w, h]], {
    stroke: hexAlpha(colors.muted, 0.9), fill: hexAlpha(colors.panel, 0.25), lw: view.px(2),
  });

  const stroke = hexAlpha(colors.warn, 0.85);
  const fill = hexAlpha(colors.warn, 0.12);
  for (const o of scene.obstacles_static ?? []) {
    // Вершини статичних перешкод уже у світових координатах (§9.5)
    if (o.type === 'static_circle') circle(ctx, o.x, o.y, o.radius, { stroke, fill, lw });
    else if (o.vertices?.length) polygon(ctx, o.vertices, { stroke, fill, lw });
  }

  for (const route of scene.waypoints ?? []) {
    if (route.length >= 2) {
      polygon(ctx, route, { stroke: hexAlpha(colors.danger, 0.35), lw, dash: [1, 1], close: true });
    }
    for (const [x, y] of route) {
      circle(ctx, x, y, view.px(2.5), { fill: hexAlpha(colors.danger, 0.6) });
    }
  }
}

/** Кадр: істота, хижаки з аурою, рухомі перешкоди, промені сенсорів. */
function drawLiveFrame(ctx, view, scene, frame, colors) {
  const labels = [];
  const lw = view.px(1.6);

  // Рухомі перешкоди: форма зі `scene`, поза — з кадру (§9.5)
  const shapes = new Map((scene.obstacles_dynamic ?? []).map((o) => [o.id, o.vertices]));
  for (const o of frame.obstacles_dynamic ?? []) {
    const verts = shapes.get(o.id);
    if (!verts) continue;
    polygon(ctx, worldVerts(verts, [o.x, o.y], o.angle), {
      stroke: hexAlpha(colors.warn, 0.95), fill: hexAlpha(colors.warn, 0.18), lw,
    });
  }

  // Хижаки: аура градієнтом, далі радіуси, далі саме тіло
  for (const p of frame.predators ?? []) {
    if (p.enabled === false) continue;
    aura(ctx, p.x, p.y, p.r_aura, colors.danger);
    circle(ctx, p.x, p.y, p.r_detect, { stroke: hexAlpha(colors.danger, 0.35), lw, dash: [2, 1] });
    circle(ctx, p.x, p.y, p.r_aura, { stroke: hexAlpha(colors.danger, 0.6), lw });
    circle(ctx, p.x, p.y, p.r_body, { fill: hexAlpha(colors.danger, 0.8), stroke: colors.danger, lw });
    labels.push({
      x: p.x, y: p.y + p.r_body + 0.5, text: p.state, size: 11,
      color: hexAlpha(colors.danger, 0.95), bg: hexAlpha(colors.bg, 0.7),
    });
  }

  // Промені радара (етап 4; до того список порожній)
  for (const ray of frame.sensors?.rays ?? []) {
    line(ctx, ray.x1, ray.y1, ray.x2, ray.y2, {
      stroke: hexAlpha(ray.hit === 'predator' ? colors.danger : colors.accent2, 0.55),
      lw: view.px(1),
    });
  }

  const creature = frame.creature;
  if (!creature) return labels;
  const alive = creature.alive !== false;
  const tint = alive ? colors.accent : colors.muted;

  for (const s of creature.segments ?? []) {
    capsule(ctx, s.x, s.y, s.angle, s.length, s.width, {
      fill: hexAlpha(tint, 0.3), stroke: hexAlpha(tint, 0.9), lw,
    });
  }

  const body = creature.body ?? {};
  ctx.save();
  ctx.translate(body.x ?? 0, body.y ?? 0);
  ctx.rotate(body.angle ?? 0);
  if (body.shape === 'polygon' && body.vertices?.length >= 3) {
    polygon(ctx, body.vertices, { fill: hexAlpha(tint, 0.28), stroke: tint, lw });
  } else {
    circle(ctx, 0, 0, body.radius ?? 0.8, { fill: hexAlpha(tint, 0.28), stroke: tint, lw });
  }
  // Напрямок «вперед» — інакше на колі не видно, куди істота обернена
  const nose = (body.radius ?? 0.8) + 0.45;
  line(ctx, 0, 0, nose, 0, { stroke: hexAlpha(tint, 0.9), lw });
  line(ctx, nose, 0, nose - 0.22, 0.16, { stroke: hexAlpha(tint, 0.9), lw });
  line(ctx, nose, 0, nose - 0.22, -0.16, { stroke: hexAlpha(tint, 0.9), lw });
  ctx.restore();

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
  //: Живий епізод: статика з пакета `scene` + найсвіжіший `frame` (§9.5).
  //  Поки він заданий, статична композиція експерименту не малюється.
  let live = null;
  //: Камера йде за істотою (§10.2). Будь-яке перетягування передає
  //  керування користувачу — інакше камера «виривала б» мишу з рук.
  let follow = true;
  // Вкладка на старті прихована → канвас має нульовий розмір, і підгонка за
  // ним дала б безглуздий масштаб. Тому підганяємо при першому реальному
  // вимірюванні, а не при першому малюванні.
  let fitted = false;

  const measured = () => cssW > 2 && cssH > 2;

  function maybeFit() {
    if (fitted || !measured() || !current) return;
    fit();
  }

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    cssW = Math.max(1, rect.width);
    cssH = Math.max(1, rect.height);
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    maybeFit();
    paint();
  }

  function fit(config = current) {
    const world = live?.scene?.world
      ?? config?.environment?.world ?? { width: 60, height: 40 };
    cam.x = 0;
    cam.y = 0;
    cam.scale = measured()
      ? Math.min(cssW / (world.width + 4), cssH / (world.height + 4))
      : 14;
    fitScale = cam.scale;
    fitted = measured();
    paint();
  }

  function paint() {
    const dpr = window.devicePixelRatio || 1;
    ctx.save();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = colors.bg;
    ctx.fillRect(0, 0, cssW, cssH);
    ctx.restore();
    if (!current && !live) return;

    const view = makeView(cam, cssW, cssH);
    let labels = [];
    withWorld(ctx, cam, cssW, cssH, () => {
      grid(ctx, view, cam, cssW, cssH, colors);
      if (live?.scene) {
        drawLiveScene(ctx, view, live.scene, colors);
        if (live.frame) labels = drawLiveFrame(ctx, view, live.scene, live.frame, colors) ?? [];
      } else if (current) {
        labels = drawExperiment(ctx, view, current, colors) ?? [];
      }
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
    follow = false;                 // користувач узяв камеру на себе
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
    /** Статична композиція експерименту (§9.3) — поза живим епізодом. */
    draw(config) {
      current = config;
      if (live) return;             // живі кадри мають пріоритет над композицією
      maybeFit();
      paint();
    },
    /** Пакет `scene`: статика епізоду, приходить один раз (§9.5). */
    showScene(scene) {
      const firstTime = !live;
      live = { scene, frame: live?.frame ?? null };
      if (firstTime) {
        follow = true;
        fit();
      }
      paint();
    },
    /** Найсвіжіший кадр; камера за потреби їде за істотою. */
    showFrame(frame) {
      if (!live) live = { scene: null, frame: null };
      live.frame = frame;
      const body = frame?.creature?.body;
      if (follow && body) {
        cam.x = body.x;
        cam.y = body.y;
      }
      paint();
    },
    /** Повернення до статичної композиції після зупинки прев'ю. */
    clearLive() {
      live = null;
      follow = true;
      paint();
    },
    get live() { return Boolean(live); },
    /** Повернути камеру на істоту й знову за нею стежити. */
    followCreature() {
      follow = true;
      const body = live?.frame?.creature?.body;
      if (body) { cam.x = body.x; cam.y = body.y; }
      paint();
    },
    get following() { return follow; },
    fit,
    zoom: () => cam.scale / fitScale,
  };
}
