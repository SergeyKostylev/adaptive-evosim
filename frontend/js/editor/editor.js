/**
 * Канвас-редактор сцени (PROJECT_SPEC.md §10.2): світ 60×40, сітка, камера
 * (пан/зум), перемикання інструментів, Validate / Save / Load / Use in Experiment.
 *
 * Фізики тут немає — редактор лише формує CreatureConfig (§9.1) та
 * EnvironmentConfig (§9.2); уся математика живе на backend.
 */

import {
  validateCreature, listPresets, deletePreset, loadPreset,
  listCreatures, saveCreature, loadCreature, deleteCreature,
  listPredators, savePredator, loadPredator, deletePredator, ApiError,
} from '../api.js';
import {
  stateChanged, saveToProject, resetProjectState, saveAsPreset, migrateEnvironment,
  applyExperimentForm, normalizeCreature, invalidateCreatureCache,
  presetCreatures, localTime,
} from '../state.js';

import {
  makeView, withWorld, themeColors, grid, polygon, screenText, hexAlpha,
} from '../render/shapes.js';
import {
  initCreaturePanel, syncCreaturePanel, drawCreature, renderCreatureThumb,
  handleBodyClick, handleLimbClick, clearLimbSelection, selectedLimbIndices,
} from './creature_editor.js';
import {
  initObstaclePanel, syncObstaclePanel, drawObstacles,
  handleObstacleClick, obstacleHint,
} from './obstacle_editor.js';
import {
  initPredatorPanel, syncPredatorPanel, drawPredatorEntity, renderPredatorThumb,
  predatorParams, applyPredatorParams, radiiOrderOk,
} from './predator_editor.js';
import {
  initScenePanel, syncScenePanel, drawScene,
  handleSpawnClick, handleRouteClick,
  findSceneHandle, moveSceneHandle, handleTitle,
} from './scene_editor.js';

const $ = (s) => document.querySelector(s);

const TOOL_HINTS = {
  creature: 'Істота: клік по периметру тіла додає кінцівку (Ctrl/⌘ — вибір кількох); '
    + 'форму тіла задають слайдери або «Малювати полігон»',
  predator: 'Хижак: параметри сутності; маршрут малюється у «Сцені»',
  scene: 'Сцена: вибери, що розставляти — перешкоди, маршрути чи точки появи',
};
const SCENE_HINTS = {
  obstacle: 'Перешкоди: вибери тип і клікай по полотну',
  route: 'Маршрути: клік додає точку вибраному маршруту; клік по хижаку вибирає його',
  spawn: 'Точки появи: клік ставить старт популяції; клік по наявній вибирає її',
};

export function initEditor(state) {
  const canvas = $('#editor-canvas');
  const ctx = canvas.getContext('2d');
  const colors = themeColors();

  const cam = { x: 0, y: 0, scale: 16 };
  let fitScale = 16;          // масштаб, за якого світ вписано у вікно = 1×
  let showGrid = true;
  let cssW = 1, cssH = 1;

  const api = {
    state,
    tool: 'creature',
    sceneTool: 'obstacle',
    hover: null,          // точка сцени під курсором
    drag: null,           // точка, яку зараз тягнемо
    // `limbs` — множина вибраних кінцівок, `limb` — активна (її показує форма)
    sel: {
      limb: -1, limbs: new Set(), seg: 0,
      obstacle: -1, spawn: -1, predator: 0, waypoint: -1,
    },
    draft: { body: null, obs: null, cursor: null },
    render,
    refresh,
    setHint,
    setTool,
    setSceneTool: (sub) => setSceneTool(sub),
    changed: stateChanged,
  };

  // --- Камера й канвас ----------------------------------------------------
  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    cssW = Math.max(1, rect.width);
    cssH = Math.max(1, rect.height);
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    render();
  }

  function resetCamera() {
    const { width, height } = state.environment.world;
    cam.x = 0;
    cam.y = 0;
    cam.scale = Math.min(cssW / (width + 4), cssH / (height + 4)) || 16;
    fitScale = cam.scale;
    render();
  }

  // --- Рендер -------------------------------------------------------------
  function render() {
    const view = makeView(cam, cssW, cssH);
    ctx.save();
    ctx.setTransform(window.devicePixelRatio || 1, 0, 0, window.devicePixelRatio || 1, 0, 0);
    ctx.fillStyle = colors.bg;
    ctx.fillRect(0, 0, cssW, cssH);
    ctx.restore();

    let labels = [];
    withWorld(ctx, cam, cssW, cssH, () => {
      if (showGrid) grid(ctx, view, cam, cssW, cssH, colors);
      // Сутності редагуються ізольовано: у режимі істоти немає ні хижака, ні
      // сцени, і навпаки — інакше редактор перетворюється на кашу
      if (api.tool === 'creature') {
        labels.push(...(drawCreature(ctx, view, api, colors) ?? []));
      } else if (api.tool === 'predator') {
        labels.push(...(drawPredatorEntity(ctx, view, api, colors) ?? []));
      } else {
        drawWorldBounds(view);
        drawObstacles(ctx, view, api, colors);
        labels.push(...(drawScene(ctx, view, api, colors) ?? []));
      }
    });

    // Текст — лише в екранних координатах (у світі вісь Y перевернута)
    for (const l of labels) {
      const s = view.toScreen(l.x, l.y);
      screenText(ctx, s.x, s.y, l.text, l);
    }

    $('#zoom').textContent = `×${(cam.scale / fitScale).toFixed(2)}`;
  }

  function drawWorldBounds(view) {
    const { width, height } = state.environment.world;
    const w = width / 2, h = height / 2;
    polygon(ctx, [[-w, -h], [w, -h], [w, h], [-w, h]], {
      stroke: hexAlpha(colors.muted, 0.9),
      fill: hexAlpha(colors.panel, 0.25),
      lw: view.px(2),
    });
  }

  // --- Панелі -------------------------------------------------------------
  function setTool(tool) {
    api.tool = tool;
    api.hover = null;
    api.drag = null;
    canvas.style.cursor = '';
    document.querySelectorAll('[data-tool]').forEach((b) =>
      b.classList.toggle('active', b.dataset.tool === tool));
    applyPanelVisibility();
    setHint(tool === 'scene' ? sceneHint() : TOOL_HINTS[tool]);
    syncLibraryBar();
    announceTool();
    render();
  }

  function setSceneTool(sub) {
    api.sceneTool = sub;
    document.querySelectorAll('[data-scene]').forEach((b) => {
      if (b.tagName === 'BUTTON') b.classList.toggle('active', b.dataset.scene === sub);
    });
    applyPanelVisibility();
    setHint(sceneHint());
    announceTool();
    render();
  }

  /** Режим редактора відображається в URL — цим займається app.js. */
  function announceTool() {
    window.dispatchEvent(new CustomEvent('editortool', {
      detail: { tool: api.tool, sceneTool: api.sceneTool },
    }));
  }

  const sceneHint = () =>
    api.sceneTool === 'obstacle' ? obstacleHint(api) : SCENE_HINTS[api.sceneTool];

  /** Групи показуються за інструментом, а всередині сцени — ще й за підрежимом. */
  function applyPanelVisibility() {
    document.querySelectorAll('[data-panel]').forEach((p) => {
      const tools = p.dataset.panel.split(/\s+/);
      const sub = p.dataset.scene;
      const visible = tools.includes(api.tool)
        && (!sub || api.tool !== 'scene' || sub === api.sceneTool);
      p.style.display = visible ? '' : 'none';
    });
  }

  function setHint(text) {
    $('#hint').textContent = text ?? '';
  }

  function refresh() {
    syncLibraryBar();
    syncCreaturePanel(api);
    syncObstaclePanel(api);
    syncScenePanel(api);
    syncPredatorPanel(api);
    stateChanged();
    render();
  }

  // --- Події канваса ------------------------------------------------------
  let panning = false;
  let last = null;
  let down = null;

  canvas.addEventListener('contextmenu', (e) => e.preventDefault());

  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId);
    if (e.button === 2 || e.button === 1 || e.shiftKey) {
      panning = true;
      last = { x: e.clientX, y: e.clientY };
      return;
    }
    // У сцені ліва кнопка на наявній точці — перетягування, а не новий об'єкт
    const handle = api.tool === 'scene' ? findSceneHandle(api, worldAt(e), pickRadius()) : null;
    if (handle) {
      api.drag = handle;
      api.hover = handle;
      canvas.style.cursor = 'grabbing';
      setHint(`Перетягни ${handleTitle(handle)} · Alt — без прилипання до сітки`);
      render();
      return;
    }
    down = { x: e.clientX, y: e.clientY };
  });

  canvas.addEventListener('pointermove', (e) => {
    const p = worldAt(e);
    api.draft.cursor = p;
    $('#cursor').textContent = `x ${p.x.toFixed(2)}  y ${p.y.toFixed(2)}`;

    if (api.drag) {
      // Прилипання до півклітинки: сцени зручніше складати з рівних чисел
      const snap = (v) => (e.altKey ? v : Math.round(v * 2) / 2);
      moveSceneHandle(api, api.drag, snap(p.x), snap(p.y));
      api.drag = { ...api.drag, x: snap(p.x), y: snap(p.y) };
      render();
      return;
    }

    if (panning && last) {
      cam.x -= (e.clientX - last.x) / cam.scale;
      cam.y += (e.clientY - last.y) / cam.scale;
      last = { x: e.clientX, y: e.clientY };
    } else if (api.tool === 'scene') {
      const hover = findSceneHandle(api, p, pickRadius());
      const changed = (hover?.kind !== api.hover?.kind) || (hover?.index !== api.hover?.index)
        || (hover?.sub !== api.hover?.sub);
      api.hover = hover;
      canvas.style.cursor = hover ? 'grab' : '';
      if (changed && hover) setHint(`Перетягни ${handleTitle(hover)}`);
    }
    render();
  });

  canvas.addEventListener('pointerup', (e) => {
    canvas.releasePointerCapture?.(e.pointerId);
    if (api.drag) {
      const moved = api.drag;
      api.drag = null;
      canvas.style.cursor = 'grab';
      setHint(`Переміщено ${handleTitle(moved)} у (${moved.x}, ${moved.y})`);
      refresh();                 // списки + автозбереження вже після відпускання
      return;
    }
    if (panning) { panning = false; last = null; return; }
    if (!down) return;
    const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
    down = null;
    if (moved > 4) return;              // це був драг, не клік

    const rect = canvas.getBoundingClientRect();
    const view = makeView(cam, cssW, cssH);
    const p = view.toWorld(e.clientX - rect.left, e.clientY - rect.top);
    onClick(p, { additive: e.ctrlKey || e.metaKey, hitRadius: Math.max(0.18, view.px(10)) });
  });

  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const view = makeView(cam, cssW, cssH);
    const before = view.toWorld(e.clientX - rect.left, e.clientY - rect.top);
    cam.scale = Math.min(140, Math.max(3, cam.scale * Math.exp(-e.deltaY * 0.0015)));
    const after = makeView(cam, cssW, cssH).toWorld(e.clientX - rect.left, e.clientY - rect.top);
    cam.x += before.x - after.x;        // світова точка під курсором не рухається
    cam.y += before.y - after.y;
    render();
  }, { passive: false });

  const worldAt = (e) => {
    const rect = canvas.getBoundingClientRect();
    return makeView(cam, cssW, cssH).toWorld(e.clientX - rect.left, e.clientY - rect.top);
  };

  /** Радіус захоплення у світових одиницях: ~12 пікселів на будь-якому зумі. */
  const pickRadius = () => Math.max(0.15, makeView(cam, cssW, cssH).px(12));

  function onClick(p, mods = {}) {
    if (api.tool === 'creature') {
      // Поки малюється полігон тіла — кліки йдуть у вершини, інакше в кінцівки
      if (api.draft.body) { handleBodyClick(api, p); render(); }
      else handleLimbClick(api, p, mods);
      return;
    }
    if (api.tool === 'predator') {
      setHint('Хижак редагується слайдерами; маршрут — у режимі «Сцена»');
      return;
    }
    switch (api.sceneTool) {
      case 'obstacle': handleObstacleClick(api, p); render(); break;
      case 'route': handleRouteClick(api, p, mods); break;
      case 'spawn': handleSpawnClick(api, p, mods); break;
    }
  }

  // --- Дії: Validate / Save / Load / Use ----------------------------------
  function showValidation(res) {
    const fmt = (v) => (v == null ? '—' : String(v));
    $('#v-joints').textContent = fmt(res?.n_joints);
    $('#v-nin').textContent = fmt(res?.n_in);
    $('#v-genome').textContent = fmt(res?.genome_size);
    const hash = res?.creature_hash;
    $('#v-hash').textContent = hash ? `${hash.slice(0, 18)}…` : '—';
    $('#v-hash').title = hash ?? '';
  }

  function message(text, cls = '') {
    const el = $('#validate-msg');
    el.className = `small ${cls}`;
    el.textContent = text;
  }

  async function doValidate({ quiet = false } = {}) {
    try {
      const res = await validateCreature(state.creature);
      state.validation = res.valid ? res : null;
      showValidation(res.valid ? res : null);
      if (res.valid) {
        if (!quiet) message(`Валідно: n_in=${res.n_in}, genome_size=${res.genome_size}`, 'ok');
      } else {
        message(`Невалідна морфологія:\n• ${res.errors.join('\n• ')}`, 'err');
      }
      return res;
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.code}: ${e.message}` : String(e);
      state.validation = null;
      showValidation(null);
      message(msg, 'err');
      return { valid: false, errors: [msg] };
    }
  }

  function download(filename, data) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    // Відкликати одразу не можна: Firefox/Safari можуть не встигнути
    // прочитати blob — файл збережеться порожнім або не збережеться взагалі
    setTimeout(() => URL.revokeObjectURL(a.href), 0);
  }

  let loadTarget = null;
  const fileInput = $('#file-input');

  fileInput.addEventListener('change', async () => {
    const file = fileInput.files?.[0];
    fileInput.value = '';
    if (!file) return;
    let data;
    try {
      data = JSON.parse(await file.text());
    } catch (e) {
      message(`Файл не є валідним JSON: ${e.message}`, 'err');
      return;
    }

    if (loadTarget === 'predator') {
      const params = data?.predator ?? data;
      if (!params || typeof params !== 'object' || params.r_aura === undefined) {
        message('Очікувався файл хижака (поле predator або самі параметри)', 'err');
        return;
      }
      applyPredatorParams(api, data?.name ?? api.state.predatorName, params);
      refresh();
      message('Хижака завантажено з файлу', 'ok');
      return;
    }

    if (loadTarget === 'creature') {
      let imported;
      try {
        imported = normalizeCreature(data);
      } catch (err) {
        message(`Очікувався CreatureConfig: ${err.message}`, 'err');
        return;
      }
      state.creature = imported;
      clearLimbSelection(api);
      if (imported.limbs.length) {
        api.sel.limb = 0;
        api.sel.limbs.add(0);
      }
      api.sel.seg = 0;
      api.draft.body = null;
      refresh();
      const res = await doValidate({ quiet: true });
      message(res.valid
        ? `Істоту завантажено: ${data.limbs.length} кінцівок, genome_size=${res.genome_size}`
        : `Істоту завантажено, але є помилки:\n• ${res.errors.join('\n• ')}`,
        res.valid ? 'ok' : 'err');
    } else {
      if (!data?.world || !Array.isArray(data?.obstacles)) {
        message('Очікувався EnvironmentConfig (world, fluid, predators, obstacles)', 'err');
        return;
      }
      state.environment = migrateEnvironment(data);
      resetSceneSelection();
      refresh();
      resetCamera();
      const env = state.environment;
      message(`Сцену завантажено: ${env.obstacles.length} перешкод, `
        + `${env.predators.length} хижаків, ${env.spawns.length} істот`, 'ok');
    }
  });

  $('#btn-validate').addEventListener('click', () => doValidate());

  $('#btn-save-scene').addEventListener('click', () => {
    download('scene.environment.json', state.environment);
    message('EnvironmentConfig збережено', 'ok');
  });
  $('#btn-load-scene').addEventListener('click', () => { loadTarget = 'scene'; fileInput.click(); });

  function resetSceneSelection() {
    clearLimbSelection(api);
    api.sel.obstacle = state.environment.obstacles.length ? 0 : -1;
    api.sel.spawn = state.environment.spawns.length ? 0 : -1;
    api.sel.predator = state.environment.predators.length ? 0 : -1;
    api.sel.waypoint = -1;
    api.draft.body = null;
    api.draft.obs = null;
  }

  // --- Бібліотека сутностей (випадаючий список над полотном) --------------
  // Один елемент інтерфейсу обслуговує дві бібліотеки: істот у режимі «Істота»
  // і хижаків у режимі «Хижак». У «Сцені» він ховається — там свої списки.
  let libraryRows = [];

  const libraryKind = () => (api.tool === 'predator' ? 'predators' : 'creatures');

  function syncLibraryBar() {
    const bar = $('#creature-lib');
    if (!bar) return;
    bar.hidden = api.tool === 'scene';
    if (bar.hidden) { $('#lib-menu').hidden = true; return; }
    if (api.tool === 'predator') {
      $('#lib-current-name').textContent = api.state.predatorName || 'хижак';
      renderPredatorThumb($('#lib-current-thumb'), api.state.predator, colors);
    } else {
      $('#lib-current-name').textContent = state.creature.name || 'без назви';
      renderCreatureThumb($('#lib-current-thumb'), state.creature, colors);
    }
  }

  async function syncLibrary() {
    const ul = $('#lib-list');
    const kind = libraryKind();
    try {
      const data = kind === 'predators' ? await listPredators() : await listCreatures();
      libraryRows = kind === 'predators' ? data.predators : data.creatures;
      $('#lib-count').textContent = `${libraryRows.length}`;
    } catch {
      ul.innerHTML = '<li class="muted" style="border-style:dashed">бібліотека недоступна</li>';
      return;
    }

    ul.innerHTML = '';
    for (const meta of libraryRows) {
      const li = document.createElement('li');
      const thumb = document.createElement('canvas');
      thumb.className = 'thumb';
      li.appendChild(thumb);
      if (kind === 'predators') renderPredatorThumb(thumb, meta.predator, colors, { size: 34 });
      else renderCreatureThumb(thumb, meta.creature, colors, { size: 34 });

      const text = document.createElement('span');
      text.className = 'grow';
      const sub = kind === 'predators'
        ? `r ${meta.r_body} · аура ${meta.r_aura} · погоня ${meta.v_chase}`
        : `${meta.limbs} кінц. · ${meta.segments} сегм.`;
      text.innerHTML = `<span>${meta.name}</span><span class="meta">${sub}`
        + `${meta.saved_at ? ' · ' + localTime(meta.saved_at) : ''}</span>`;
      li.appendChild(text);

      const del = document.createElement('button');
      del.textContent = '\u2715';
      del.title = 'Видалити з бібліотеки';
      del.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!window.confirm(`Видалити «${meta.name}» з бібліотеки?`)) return;
        try {
          await (kind === 'predators' ? deletePredator(meta.name) : deleteCreature(meta.name));
          message(`«${meta.name}» видалено з бібліотеки`, 'ok');
        } catch (err) {
          message(`Не вдалося видалити: ${err.message}`, 'err');
        }
        syncLibrary();
      });
      li.appendChild(del);
      li.addEventListener('click', () => applyLibraryItem(meta.name));
      ul.appendChild(li);
    }
    if (!libraryRows.length) {
      ul.innerHTML = `<li class="muted" style="border-style:dashed">${
        kind === 'predators' ? 'хижаків' : 'істот'} ще немає — збережи поточного</li>`;
    }
  }

  async function applyLibraryItem(name) {
    try {
      if (libraryKind() === 'predators') {
        const res = await loadPredator(name);
        applyPredatorParams(api, name, res.predator);
        $('#lib-menu').hidden = true;
        refresh();
        message(`Завантажено хижака «${name}»`, 'ok');
        return;
      }
      const res = await loadCreature(name);
      state.creature = res.creature;
      presetCreatures.set(name, res.creature);
      clearLimbSelection(api);
      if (state.creature.limbs.length) {
        api.sel.limb = 0;
        api.sel.limbs.add(0);
      }
      api.sel.seg = 0;
      $('#lib-menu').hidden = true;
      refresh();
      message(`Завантажено істоту «${name}»`, 'ok');
      await doValidate({ quiet: true });
    } catch (e) {
      message(`Не вдалося завантажити «${name}»: ${e.message}`, 'err');
    }
  }

  $('#lib-toggle').addEventListener('click', (e) => {
    e.stopPropagation();
    const menu = $('#lib-menu');
    menu.hidden = !menu.hidden;
    if (!menu.hidden) {
      $('#lib-save-name').value = libraryKind() === 'predators'
        ? (api.state.predatorName ?? '') : (state.creature.name ?? '');
      syncLibrary();
    }
  });
  document.addEventListener('click', (e) => {
    const lib = $('#creature-lib');
    if (lib && !lib.contains(e.target)) $('#lib-menu').hidden = true;
  });

  $('#lib-save').addEventListener('click', async (e) => {
    e.stopPropagation();
    const input = $('#lib-save-name');
    const kind = libraryKind();
    const name = input.value.trim()
      || (kind === 'predators' ? api.state.predatorName : state.creature.name);
    if (!name) { message('Вкажи назву', 'err'); input.focus(); return; }

    if (kind === 'predators') {
      if (!radiiOrderOk(api.state.predator)) {
        message('Хижак не валідний: має виконуватись r_catch ≤ r_aura ≤ r_detect', 'err');
        return;
      }
      try {
        await savePredator(name, predatorParams(api.state.predator));
        api.state.predatorName = name;
        message(`Хижака «${name}» збережено в бібліотеку`, 'ok');
        syncLibrary();
        refresh();
      } catch (err) {
        message(`Не вдалося зберегти: ${err.message}`, 'err');
      }
      return;
    }

    const check = await doValidate({ quiet: true });
    if (!check.valid) { message(`Істота не валідна:\n• ${check.errors.join('\n• ')}`, 'err'); return; }
    try {
      await saveCreature(name, state.creature);
      invalidateCreatureCache();
      message(`Істоту «${name}» збережено в бібліотеку`, 'ok');
      syncLibrary();
    } catch (err) {
      message(`Не вдалося зберегти: ${err.message}`, 'err');
    }
  });

  $('#lib-export').addEventListener('click', (e) => {
    e.stopPropagation();
    if (libraryKind() === 'predators') {
      download(`${api.state.predatorName || 'predator'}.predator.json`,
        { predator: predatorParams(api.state.predator) });
    } else {
      download(`${state.creature.name || 'creature'}.creature.json`, state.creature);
    }
    message('Збережено у файл', 'ok');
  });
  $('#lib-import').addEventListener('click', (e) => {
    e.stopPropagation();
    loadTarget = libraryKind() === 'predators' ? 'predator' : 'creature';
    fileInput.click();
  });

  /** Список збережених варіантів + джерела для розстановки істот. */
  async function syncPresetList() {
    const ul = $('#preset-list');
    let data;
    try {
      data = await listPresets();
    } catch (e) {
      ul.innerHTML = '<li class="muted" style="border-style:dashed">список недоступний</li>';
      return;
    }
    ul.innerHTML = '';
    for (const meta of data.presets) {
      const li = document.createElement('li');
      li.innerHTML = `<span class="grow">${meta.name} · ${meta.limbs} кінц. · `
        + `${meta.obstacles} переш. · ${meta.saved_at ? localTime(meta.saved_at) : ''}</span>`;
      const del = document.createElement('button');
      del.textContent = '\u2715';
      del.title = 'Видалити варіант';
      del.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!window.confirm(`Видалити варіант «${meta.name}»?`)) return;
        try {
          await deletePreset(meta.name);
          message(`Варіант «${meta.name}» видалено`, 'ok');
        } catch (err) {
          message(`Не вдалося видалити: ${err.message}`, 'err');
        }
        syncPresetList();
      });
      li.appendChild(del);
      li.addEventListener('click', () => applyPreset(meta.name));
      ul.appendChild(li);
    }
    $('#preset-hint').textContent = data.presets.length
      ? `Збережено ${data.presets.length} із ${data.limit}. Клік по рядку завантажує варіант.`
      : 'Варіантів ще немає: збережи поточну сцену під назвою.';
  }

  async function applyPreset(name) {
    try {
      const res = await loadPreset(name);
      const saved = res.state;
      if (saved.creature) state.creature = saved.creature;
      if (saved.environment) state.environment = migrateEnvironment(saved.environment);
      applyExperimentForm(saved);
      invalidateCreatureCache();   // морфології у варіанті можуть бути іншими
      resetSceneSelection();
      refresh();
      resetCamera();
      message(`Завантажено варіант «${name}»`, 'ok');
      await doValidate({ quiet: true });
    } catch (e) {
      message(`Не вдалося завантажити «${name}»: ${e.message}`, 'err');
    }
  }

  $('#preset-save').addEventListener('click', async () => {
    const input = $('#preset-name');
    const name = input.value.trim();
    if (!name) { message('Вкажи назву варіанта', 'err'); input.focus(); return; }
    try {
      const meta = await saveAsPreset(name);
      message(`Варіант «${meta.name}» збережено`, 'ok');
      input.value = '';
      syncPresetList();
    } catch (e) {
      message(`Не вдалося зберегти: ${e.message}`, 'err');
    }
  });

  $('#preset-refresh').addEventListener('click', () => syncPresetList());

  $('#btn-save-project').addEventListener('click', async () => {
    const res = await saveToProject();
    message(res
      ? 'Сцену збережено у проєкті (experiments/editor/last_session.json)'
      : 'Не вдалося зберегти: сервер недоступний', res ? 'ok' : 'err');
  });

  $('#btn-reset-project').addEventListener('click', async () => {
    if (!window.confirm('Видалити збережену сцену з проєкту і почати з чистої?')) return;
    await resetProjectState();
    resetSceneSelection();
    refresh();
    resetCamera();
    message('Збережену сцену видалено, редактор скинуто до типових значень', 'ok');
  });

  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (api.draft.body || api.draft.obs) {
      api.draft.body = null;
      api.draft.obs = null;
      setHint('Малювання скасовано');
    } else if (selectedLimbIndices(api).length) {
      clearLimbSelection(api);
      setHint('Вибір кінцівок знято');
    }
    refresh();
  });

  $('#view-reset').addEventListener('click', resetCamera);
  $('#view-grid').addEventListener('click', (e) => {
    showGrid = !showGrid;
    e.currentTarget.classList.toggle('active', showGrid);
    render();
  });

  document.querySelectorAll('[data-tool]').forEach((b) =>
    b.addEventListener('click', () => setTool(b.dataset.tool)));
  document.querySelectorAll('button[data-scene]').forEach((b) =>
    b.addEventListener('click', () => setSceneTool(b.dataset.scene)));

  // --- Старт --------------------------------------------------------------
  initCreaturePanel(api);
  initObstaclePanel(api);
  initScenePanel(api);
  initPredatorPanel(api);

  new ResizeObserver(resize).observe(canvas);
  window.addEventListener('tabchange', (e) => { if (e.detail === 'editor') resize(); });
  resize();
  resetCamera();
  setTool('creature');
  refresh();
  syncPresetList();

  return api;
}
