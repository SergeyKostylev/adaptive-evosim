/**
 * Вкладка «Симуляція»: поєднання сутностей, підготовлених у редакторі
 * (PROJECT_SPEC.md §9.3). Істота, сцена та хижак обираються зі своїх бібліотек,
 * після чого збирається `ExperimentConfig`.
 *
 * Важливо: конфіг зберігає **копії**, а не назви. Прогін має лишатися
 * відтворюваним (§11), навіть якщо потім змінити істоту чи хижака в бібліотеці.
 *
 * Керування навчанням і лінія фітнесу — у `hud/hud.js`; звідси він отримує
 * лише готовий конфіг (`runForStart`).
 */

import {
  listCreatures, listPredators, listPresets, loadCreature, loadPredator, loadPreset,
  validateCreature, startPreview, stopPreview, ApiError,
} from './api.js';
import { connect } from './ws.js';
import { state, clone, buildExperimentConfig, migrateEnvironment } from './state.js';
import { createPreview, renderSceneThumb, PREDATOR_STATES } from './render/renderer.js';
import { themeColors } from './render/shapes.js';
import { renderCreatureThumb } from './editor/creature_editor.js';
import { renderPredatorThumb } from './editor/predator_editor.js';
import { initTraining } from './hud/hud.js';

const $ = (s) => document.querySelector(s);
const EMPTY = '__none__';

let preview = null;
let colors = null;
const pickers = {};
/** Відкритий список має бути один: інакше меню накладаються одне на одне. */
const closers = new Set();

/**
 * Випадаючий список із мініатюрами: нативний <select> уміє лише текст, а тут
 * важливо бачити саму форму — істоту, розкладку сцени, розміри хижака.
 */
function createPicker(root, { onPick }) {
  const button = document.createElement('button');
  button.type = 'button';
  const thumb = document.createElement('canvas');
  thumb.className = 'thumb';
  const name = document.createElement('span');
  name.className = 'name';
  const chev = document.createElement('span');
  chev.className = 'chev';
  chev.textContent = '▾';
  button.append(thumb, name, chev);

  const menu = document.createElement('ul');
  menu.className = 'menu list';
  menu.hidden = true;
  root.append(button, menu);

  let items = [];
  let value = null;

  const close = () => { menu.hidden = true; };
  closers.add(close);
  button.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = menu.hidden;
    for (const other of closers) other();
    menu.hidden = !willOpen;
  });
  document.addEventListener('click', (e) => { if (!root.contains(e.target)) close(); });
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

  function renderButton() {
    const item = items.find((i) => i.value === value) ?? items[0];
    name.textContent = item?.label ?? '—';
    thumb.hidden = !item?.thumb;
    item?.thumb?.(thumb, 26);
  }

  return {
    get value() { return value; },
    set(next) {
      value = items.some((i) => i.value === next) ? next : items[0]?.value ?? null;
      renderButton();
    },
    fill(nextItems) {
      items = nextItems;
      if (!items.some((i) => i.value === value)) value = items[0]?.value ?? null;
      menu.innerHTML = '';
      for (const item of items) {
        const li = document.createElement('li');
        li.className = item.value === value ? 'sel' : '';
        if (item.thumb) {
          const c = document.createElement('canvas');
          c.className = 'thumb';
          li.appendChild(c);
          item.thumb(c, 34);
        }
        const text = document.createElement('span');
        text.className = 'grow';
        text.innerHTML = `<span>${item.label}</span>`
          + (item.meta ? `<span class="meta">${item.meta}</span>` : '');
        li.appendChild(text);
        li.addEventListener('click', () => {
          value = item.value;
          renderButton();
          close();
          onPick?.(value);
        });
        menu.appendChild(li);
      }
      renderButton();
    },
  };
}

/** Вибране користувачем (резолвиться ліниво, кешується). */
const picked = { creature: null, environment: null, predator: null };

async function fillSelects() {
  const [creatures, predators, presets] = await Promise.all([
    listCreatures().catch(() => ({ creatures: [] })),
    listPredators().catch(() => ({ predators: [] })),
    listPresets().catch(() => ({ presets: [] })),
  ]);

  const creatureThumb = (c) => (canvas, size) => renderCreatureThumb(canvas, c, colors, { size });
  const sceneThumb = (env) => (canvas, size) => renderSceneThumb(canvas, env, colors, { size });
  const predThumb = (p) => (canvas, size) => renderPredatorThumb(canvas, p, colors, { size });

  // В експерименті беруть участь лише **збережені** сутності: чернетка з
  // редактора змінюється щосекунди, а прогін має бути відтворюваним (§11)
  const orEmpty = (items, hint) =>
    (items.length ? items : [{ value: EMPTY, label: '— немає —', meta: hint, thumb: null }]);

  pickers.creature.fill(orEmpty(creatures.creatures.map((m) => ({
    value: m.name, label: m.name,
    meta: `${m.limbs} кінц. · ${m.segments} сегм.`,
    thumb: creatureThumb(m.creature),
  })), 'збережи істоту в бібліотеці (список над полотном)'));

  pickers.scene.fill(orEmpty(presets.presets.map((m) => ({
    value: m.name, label: m.name,
    meta: `${m.obstacles} переш. · ${m.waypoints} точок маршрутів`,
    thumb: sceneThumb(m.environment),
  })), 'збережи сцену як варіант у режимі «Сцена»'));

  // Сцена задає лише маршрути — хто саме ними бігає, вирішується тут
  pickers.predator.fill(orEmpty(predators.predators.map((m) => ({
    value: m.name, label: m.name,
    meta: `r ${m.r_body} · аура ${m.r_aura} · погоня ${m.v_chase}`,
    thumb: predThumb(m.predator),
  })), 'збережи хижака в бібліотеці (режим «Хижак»)'));
}

async function resolve() {
  const cName = pickers.creature.value;
  picked.creature = cName && cName !== EMPTY
    ? (await loadCreature(cName).catch(() => null))?.creature ?? null
    : null;

  const sName = pickers.scene.value;
  const scene = sName && sName !== EMPTY ? await loadPreset(sName).catch(() => null) : null;
  picked.environment = scene?.state?.environment
    ? migrateEnvironment(scene.state.environment) : null;

  const pName = pickers.predator.value;
  picked.predator = pName && pName !== EMPTY
    ? (await loadPredator(pName).catch(() => null))?.predator ?? null
    : null;
}

/** Чого бракує, щоб експеримент узагалі можна було зібрати. */
function missing() {
  const out = [];
  if (!picked.creature) out.push('істоту');
  if (!picked.environment) out.push('сцену');
  if (!picked.predator) out.push('хижака');
  return out;
}

/**
 * Збирає ExperimentConfig: вибраний хижак накладається на маршрути, які не
 * мають власного (`preset: null`); маршрути коротші за 2 точки відкидаються —
 * схема §9.2 їх не прийме.
 */
export function assembleExperiment() {
  const base = buildExperimentConfig();
  const env = clone(picked.environment ?? defaultEnvironment());
  const dropped = [];

  env.predators = (env.predators ?? []).filter((route, i) => {
    if ((route.waypoints?.length ?? 0) >= 2) return true;
    dropped.push(i);
    return false;
  }).map((route) => {
    // Параметри хижака задає експеримент, у сцені лишається тільки маршрут
    return { ...route, ...clone(picked.predator ?? {}), preset: pickers.predator.value };
  });

  return {
    config: { ...base, creature: clone(picked.creature ?? state.creature), environment: env },
    dropped,
  };
}

/** Порожній світ — щоб прев'ю не падало, поки нічого не вибрано. */
const defaultEnvironment = () => ({
  world: clone(state.environment.world),
  fluid: clone(state.environment.fluid),
  spawns: [], predators: [], obstacles: [],
});

function summarize({ config, dropped }) {
  const env = config.environment;
  const gaps = missing();
  if (gaps.length) {
    setSummary(`Немає з чого зібрати експеримент: вибери ${gaps.join(', ')}`);
    $('#sim-warn').textContent = 'сутності беруться лише зі збережених — збережи їх у редакторі';
    return;
  }
  const predName = pickers.predator.value;
  setSummary([
    `істота: ${config.creature.name ?? '—'} (${config.creature.limbs.length} кінц.)`,
    `хижак: ${predName} × ${env.predators.length} маршр.`,
    `перешкод: ${env.obstacles.length}`,
    `стартів: ${env.spawns.length || '1 (центр світу)'}`,
  ].join(' · '));
  $('#sim-warn').textContent = warnings(config, dropped).join(' · ');
}

/**
 * Перевірки, які видно оком на сцені, але не видно в JSON: старт усередині
 * аури (істота гине на першій секунді), старт поза межами світу, порожня сцена.
 */
function warnings(config, dropped) {
  const out = [];
  if (dropped.length) out.push(`відкинуто незавершених маршрутів: ${dropped.length}`);

  const env = config.environment;
  const w = env.world.width / 2, h = env.world.height / 2;
  const spawns = env.spawns.length ? env.spawns : [{ position: [0, 0] }];

  for (const [i, sp] of spawns.entries()) {
    const [x, y] = sp.position;
    if (Math.abs(x) > w || Math.abs(y) > h) out.push(`старт ${i} поза межами світу`);
    for (const route of env.predators) {
      if (route.enabled === false || !route.waypoints?.length) continue;
      const [px, py] = route.waypoints[0];
      if (Math.hypot(x - px, y - py) < route.r_aura) out.push(`старт ${i} в аурі хижака`);
    }
  }
  if (!env.predators.length) out.push('у сцені немає хижаків');
  return out;
}

/** Похідні розміру мережі для вибраної істоти (§4.4, §5). */
async function describeCreature(config) {
  const set = (id, v) => { $(id).textContent = v; };
  if (!picked.creature) {
    for (const id of ['#sim-joints', '#sim-nin', '#sim-genome', '#sim-cost']) set(id, '—');
    return;
  }
  try {
    const res = await validateCreature(config.creature);
    if (!res.valid) {
      set('#sim-joints', '—'); set('#sim-nin', '—');
      set('#sim-genome', 'істота невалідна');
      $('#sim-warn').textContent = `істота не пройде валідацію: ${res.errors[0]}`;
      return;
    }
    set('#sim-joints', res.n_joints);
    set('#sim-nin', res.n_in);
    set('#sim-genome', res.genome_size);
  } catch {
    set('#sim-genome', 'сервер недоступний');
  }
  const ga = config.ga;
  set('#sim-cost', `${(ga.population * ga.episodes_per_individual * ga.episode_seconds)
    .toLocaleString('uk-UA')} с`);
}

/** Підсумок видно і в панелі, і в рядку під полотном. */
function setSummary(text) {
  for (const sel of ['#sim-summary', '#sim-status-line']) {
    const el = $(sel);
    if (el) el.textContent = text;
  }
}

// ---------------------------------------------------------------------------
// Живий перегляд (§9.4–9.5). Канал один на застосунок, тож і тут один сокет:
// кадри малюються на тому самому полотні, що й статична композиція.
// ---------------------------------------------------------------------------
let socket = null;
/** Що саме зараз показуємо наживо — щоб зміна темпу перезапустила те саме. */
let liveRequest = null;
/** Прогін, чию найкращу особину можна показати (з'являється після 1-го покоління). */
let bestRunId = null;

/** Компоненти фітнесу в HUD (§7.1); з етапу 4 активні всі шість. */
const SHOWN_COMPONENTS = ['S', 'D', 'G_aura', 'C', 'Pas', 'E_n'];
/** Поточний прогін (з `hud.js`) — для перемикача Headless ↔ Live. */
let trainingRun = null;

const liveSpeed = () => Number($('#live-speed')?.value ?? 1);

function setLiveUi(active) {
  $('#live-stop').disabled = !active;
  $('#live-hud').hidden = !active;
  $('#hud-state').textContent = active ? 'живий перегляд' : 'зупинено';
  syncLiveTrain();
}

/** Кнопка «Наживо» підсвічена, поки йде трансляція навчання; доступна, коли є оцінене покоління. */
function syncLiveTrain() {
  const on = liveRequest?.body?.source === 'live_training';
  const btn = $('#live-train');
  btn.classList.toggle('active', on);
  btn.setAttribute('aria-pressed', String(on));
  btn.disabled = !on && !(trainingRun?.runId && trainingRun.state !== 'STOPPED'
    && trainingRun.generation >= 1);
}

function showConnection(state) {
  const labels = { open: 'канал: підключено', closed: 'канал: перепідключення…', error: 'канал: помилка' };
  const el = $('#live-conn');
  if (el) {
    el.textContent = labels[state] ?? 'канал: —';
    el.className = `small ${state === 'open' ? 'ok' : 'warn'}`;
  }
}

function openSocket() {
  if (socket) return;
  socket = connect('/ws/live', {
    onPacket: (packet) => { if (packet.type === 'scene') preview?.showScene(packet); },
    onFrame: (frame) => {
      preview?.showFrame(frame);
      const hud = frame.hud ?? {};
      $('#live-t').textContent = `t ${(frame.t ?? 0).toFixed(1)} с`;
      $('#live-disp').textContent = `зміщення ${(hud.displacement ?? 0).toFixed(2)}`;
      $('#live-speed-v').textContent = `|v| ${(hud.speed ?? 0).toFixed(2)}`;
      const comps = hud.components ?? {};
      $('#live-fit').textContent = hud.fitness_running == null ? 'F —'
        : `F ${hud.fitness_running.toFixed(3)} · `
          + SHOWN_COMPONENTS.map((k) => `${k} ${(comps[k] ?? 0).toFixed(2)}`).join(' ');
      const preds = (frame.predators ?? []).filter((p) => p.enabled !== false);
      const pred = $('#live-pred');
      pred.hidden = !preds.length;
      if (preds.length) {
        const aura = frame.sensors?.aura ?? 0;
        pred.textContent = `хижак: ${preds.map((p) => PREDATOR_STATES[p.state] ?? p.state).join(', ')}`
          + (aura > 0 ? ` · аура ${aura.toFixed(2)}` : '');
        pred.className = `mono ${preds.some((p) => p.state === 'CHASE') ? 'err' : ''}`;
      }
      const ended = hud.ended === 'caught' ? ' · спіймано' : '';
      const gen = hud.generation != null ? ` · пок. ${hud.generation}` : '';
      $('#live-episode').textContent = `епізод ${(frame.episode ?? 0) + 1}${gen}${ended}`;
      $('#live-episode').className = `mono ${ended ? 'err' : 'muted'}`;
    },
    onState: showConnection,
  });
}

function closeSocket() {
  socket?.close();
  socket = null;
  showConnection(null);
}

/**
 * Запускає live-епізод (`body` — тіло `POST /api/preview` без темпу).
 *
 * `origin` — звідки взято те, що пливе («чернетка», «збережене» чи
 * «найкраща»): це єдине, що розрізняє кнопки запуску, і єдине, чого немає в
 * панелі праворуч. Назва істоти, ідентифікатор прев'ю та контролер туди не
 * йдуть: назва вже є в панелі, а `pv-…` потрібен лише для налагодження — усе
 * це лишається в `title`.
 */
async function startLive(body, { origin, label }) {
  liveRequest = { body, origin, label };
  try {
    const res = await startPreview({ ...body, speed: liveSpeed(), loop: true });
    openSocket();
    setLiveUi(true);
    const run = $('#hud-run');
    run.textContent = origin ?? '';
    run.title = `${label} · ${origin ?? 'прев\'ю'} · ${res.preview_id} · `
      + `епізод ${res.episode_seconds} с`;
    $('#sim-warn').textContent = '';
    return res;
  } catch (e) {
    liveRequest = null;
    $('#sim-warn').textContent = e instanceof ApiError
      ? `не вдалося запустити прев'ю: ${e.message}` : String(e);
    return null;
  }
}

/**
 * Епізод із тестовою синусоїдою. `creature`/`environment` — копії, бо прев'ю
 * не має залежати від подальших правок у редакторі.
 */
export async function startDemoPreview(creature, environment, { origin } = {}) {
  if (!creature?.limbs?.length) {
    $('#sim-warn').textContent = 'тест-заплив неможливий: в істоти немає жодної кінцівки';
    return null;
  }
  return startLive(
    { source: 'demo', creature: clone(creature), environment: clone(environment ?? {}) },
    { origin, label: `${creature.name || 'істота'} · контролер-синусоїда (§2.4)` },
  );
}

/** Найкращий геном прогону; сервер сам підхоплює новий рекорд на кожному колі. */
async function startBestPreview() {
  if (!bestRunId) return null;
  return startLive({ source: 'best', run_id: bestRunId },
    { origin: 'найкраща', label: `прогін ${bestRunId} · мозок із геному` });
}

/**
 * Перемикач Headless ↔ Live (§10.2): трансляція найкращої особини останнього
 * покоління. Вимкнення лише гасить прев'ю — навчання триває.
 */
async function toggleLiveTraining() {
  if (liveRequest?.body?.source === 'live_training') {
    await stopLivePreview();
    return null;
  }
  const res = await startLive({ source: 'live_training' },
    { origin: 'наживо', label: `прогін ${trainingRun?.runId ?? ''} · найкраща особина останнього покоління` });
  syncLiveTrain();
  return res;
}

/**
 * Конфіг для `POST /api/simulation/start` або причина, чому стартувати не можна.
 * Як і для прев'ю, беруться лише **збережені** сутності — чернетка редактора
 * в прогін не потрапляє (§11).
 */
async function runForStart() {
  await resolve();
  const gaps = missing();
  if (gaps.length) return { problem: `щоб почати навчання, вибери збережені: ${gaps.join(', ')}` };
  const { config } = assembleExperiment();
  return { config };
}

export async function stopLivePreview() {
  liveRequest = null;
  closeSocket();
  try {
    await stopPreview();
  } catch { /* сервер міг і так не мати активного прев'ю */ }
  setLiveUi(false);
  const run = $('#hud-run');
  run.textContent = '';
  run.title = '';
  preview?.clearLive();
  refreshExperimentPreview();
}

export async function refreshExperimentPreview() {
  await resolve();
  const assembled = assembleExperiment();
  summarize(assembled);
  preview?.draw(assembled.config);      // у живому режимі `draw` сам поступиться кадрам
  describeCreature(assembled.config);
  const el = $('#exp-json');
  if (el) el.textContent = JSON.stringify(assembled.config, null, 2);
}

export async function initSimulationPicker() {
  colors = themeColors();
  preview = createPreview($('#sim-canvas'), colors);
  for (const kind of ['creature', 'scene', 'predator']) {
    pickers[kind] = createPicker($(`#pick-${kind}`), { onPick: refreshExperimentPreview });
  }
  $('#sim-fit').addEventListener('click', () => preview.fit());
  $('#live-follow').addEventListener('click', () => preview.followCreature());
  $('#live-stop').addEventListener('click', () => stopLivePreview());
  $('#live-start').addEventListener('click', async () => {
    const { config } = assembleExperiment();
    await startDemoPreview(config.creature, config.environment, { origin: 'збережене' });
  });
  $('#live-best').addEventListener('click', () => startBestPreview());
  $('#live-train').addEventListener('click', () => toggleLiveTraining());
  // Темп змінюється на сервері (пауза між кадрами), тож прев'ю перезапускається
  $('#live-speed').addEventListener('change', () => {
    if (liveRequest) {
      const { body, origin, label } = liveRequest;
      startLive(body, { origin, label });
    }
  });
  // Чернетка з редактора: кнопка там, а канал тут — редактор не знає про вкладки
  window.addEventListener('testswim', (e) => {
    startDemoPreview(e.detail.creature, e.detail.environment, { origin: 'чернетка' });
  });
  $('#exp-copy').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('#exp-json').textContent);
      $('#exp-copy').textContent = 'Скопійовано';
      setTimeout(() => { $('#exp-copy').textContent = 'Копіювати'; }, 1500);
    } catch {
      $('#exp-copy').textContent = 'Не вдалося';
    }
  });
  // При поверненні на вкладку списки могли застаріти (щойно збережена істота,
  // новий хижак), а канвас був прихований — оновлюємо і те, і те
  window.addEventListener('tabchange', async (e) => {
    if (e.detail !== 'simulation') return;
    await fillSelects();
    await refreshExperimentPreview();
  });
  await fillSelects();
  $('#sim-refresh').addEventListener('click', async () => {
    await fillSelects();
    await refreshExperimentPreview();
  });
  await refreshExperimentPreview();
  await initTraining({
    theme: colors,
    getRun: runForStart,
    onChange: (run) => {
      trainingRun = run;
      bestRunId = run.runId && run.rows.length ? run.runId : null;
      $('#live-best').disabled = !bestRunId;
      syncLiveTrain();
    },
  });
}
