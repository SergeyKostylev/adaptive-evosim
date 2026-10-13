/**
 * Вкладка «Симуляція»: поєднання сутностей, підготовлених у редакторі
 * (PROJECT_SPEC.md §9.3). Істота, сцена та хижак обираються зі своїх бібліотек,
 * після чого збирається `ExperimentConfig`.
 *
 * Важливо: конфіг зберігає **копії**, а не назви. Прогін має лишатися
 * відтворюваним (§11), навіть якщо потім змінити істоту чи хижака в бібліотеці.
 */

import {
  listCreatures, listPredators, listPresets, loadCreature, loadPredator, loadPreset,
  validateCreature,
} from './api.js';
import { state, clone, buildExperimentConfig, migrateEnvironment } from './state.js';
import { createPreview, renderSceneThumb } from './render/renderer.js';
import { themeColors } from './render/shapes.js';
import { renderCreatureThumb } from './editor/creature_editor.js';
import { renderPredatorThumb } from './editor/predator_editor.js';

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
    $('#sim-summary').textContent = `Немає з чого зібрати експеримент: вибери ${gaps.join(', ')}`;
    $('#sim-warn').textContent = 'сутності беруться лише зі збережених — збережи їх у редакторі';
    return;
  }
  const predName = pickers.predator.value;
  $('#sim-summary').textContent = [
    `істота: ${config.creature.name ?? '—'} (${config.creature.limbs.length} кінц.)`,
    `хижак: ${predName} × ${env.predators.length} маршр.`,
    `перешкод: ${env.obstacles.length}`,
    `стартів: ${env.spawns.length || '1 (центр світу)'}`,
  ].join(' · ');
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

export async function refreshExperimentPreview() {
  await resolve();
  const assembled = assembleExperiment();
  summarize(assembled);
  preview?.draw(assembled.config);
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
}
