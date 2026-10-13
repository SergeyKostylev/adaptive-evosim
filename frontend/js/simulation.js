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
import { createPreview } from './render/renderer.js';
import { themeColors } from './render/shapes.js';

const $ = (s) => document.querySelector(s);
const CURRENT = '__current__';
const FROM_SCENE = '__scene__';

let preview = null;

/** Вибране користувачем (резолвиться ліниво, кешується). */
const picked = { creature: null, environment: null, predator: null };

function option(value, label) {
  const o = document.createElement('option');
  o.value = value;
  o.textContent = label;
  return o;
}

async function fillSelects() {
  const [creatures, predators, presets] = await Promise.all([
    listCreatures().catch(() => ({ creatures: [] })),
    listPredators().catch(() => ({ predators: [] })),
    listPresets().catch(() => ({ presets: [] })),
  ]);

  const cs = $('#sim-creature');
  const keepC = cs.value;
  cs.innerHTML = '';
  cs.appendChild(option(CURRENT, 'поточна з редактора'));
  for (const m of creatures.creatures) {
    cs.appendChild(option(m.name, `${m.name} · ${m.limbs} кінц.`));
  }
  if ([...cs.options].some((o) => o.value === keepC)) cs.value = keepC;

  const ss = $('#sim-scene');
  const keepS = ss.value;
  ss.innerHTML = '';
  ss.appendChild(option(CURRENT, 'поточна з редактора'));
  for (const m of presets.presets) {
    ss.appendChild(option(m.name, `${m.name} · ${m.obstacles} переш.`));
  }
  if ([...ss.options].some((o) => o.value === keepS)) ss.value = keepS;

  const ps = $('#sim-predator');
  const keepP = ps.value;
  ps.innerHTML = '';
  ps.appendChild(option(FROM_SCENE, 'як задано у сцені'));
  ps.appendChild(option(CURRENT, 'поточний з редактора'));
  for (const m of predators.predators) {
    ps.appendChild(option(m.name, `${m.name} · r ${m.r_body} · аура ${m.r_aura}`));
  }
  if ([...ps.options].some((o) => o.value === keepP)) ps.value = keepP;
}

async function resolve() {
  const cName = $('#sim-creature').value;
  picked.creature = cName === CURRENT || !cName
    ? clone(state.creature)
    : (await loadCreature(cName).catch(() => null))?.creature ?? clone(state.creature);

  const sName = $('#sim-scene').value;
  if (sName === CURRENT || !sName) {
    picked.environment = clone(state.environment);
  } else {
    const res = await loadPreset(sName).catch(() => null);
    picked.environment = res?.state?.environment
      ? migrateEnvironment(res.state.environment) : clone(state.environment);
  }

  const pName = $('#sim-predator').value;
  if (pName === FROM_SCENE || !pName) picked.predator = null;
  else if (pName === CURRENT) picked.predator = clone(state.predator);
  else picked.predator = (await loadPredator(pName).catch(() => null))?.predator ?? null;
}

/**
 * Збирає ExperimentConfig: вибраний хижак накладається на маршрути, які не
 * мають власного (`preset: null`); маршрути коротші за 2 точки відкидаються —
 * схема §9.2 їх не прийме.
 */
export function assembleExperiment() {
  const base = buildExperimentConfig();
  const env = clone(picked.environment ?? state.environment);
  const dropped = [];

  env.predators = (env.predators ?? []).filter((route, i) => {
    if ((route.waypoints?.length ?? 0) >= 2) return true;
    dropped.push(i);
    return false;
  }).map((route) => {
    if (!picked.predator || route.preset) return route;
    return { ...route, ...clone(picked.predator), preset: $('#sim-predator').value === CURRENT
      ? null : $('#sim-predator').value };
  });

  return {
    config: { ...base, creature: clone(picked.creature ?? state.creature), environment: env },
    dropped,
  };
}

function summarize({ config, dropped }) {
  const env = config.environment;
  $('#sim-summary').textContent = [
    `істота: ${config.creature.name ?? '—'} (${config.creature.limbs.length} кінц.)`,
    `перешкод: ${env.obstacles.length}`,
    `маршрутів: ${env.predators.length}`,
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
  preview = createPreview($('#sim-canvas'), themeColors());
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
  // Перемальовуємо при поверненні на вкладку: канвас міг бути прихований
  window.addEventListener('tabchange', (e) => {
    if (e.detail === 'simulation') refreshExperimentPreview();
  });
  await fillSelects();
  for (const sel of ['#sim-creature', '#sim-scene', '#sim-predator']) {
    $(sel).addEventListener('change', refreshExperimentPreview);
  }
  $('#sim-refresh').addEventListener('click', async () => {
    await fillSelects();
    await refreshExperimentPreview();
  });
  await refreshExperimentPreview();
}
