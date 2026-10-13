/**
 * Модель стану фронтенду: CreatureConfig + EnvironmentConfig (§9.1–9.2),
 * збірка ExperimentConfig (§9.3) і збереження сцени у проєкті
 * (`experiments/editor/` через `/api/editor/state`).
 *
 * Окремий модуль, щоб редактор не імпортував `app.js` — інакше виникає
 * циклічний імпорт, який разом із top-level await лишає модуль
 * недоініціалізованим.
 */

import {
  saveEditorState, clearEditorState, savePreset, loadCreature, ApiError,
} from './api.js';

/** Дефолти з PROJECT_SPEC.md §13 — фронтенд не вигадує власних чисел. */
export const DEFAULTS = {
  world: { width: 60, height: 40 },
  fluid: { rho: 1.0, c_d_perp: 2.0, c_d_par: 0.1, c_l: 0.5, linear_damping: 0.98 },
  predator: {
    enabled: true,
    r_body: 0.8,
    v_patrol: 2.0, v_chase: 3.5, r_detect: 8.0, r_aura: 5.0, r_catch: 1.0,
  },
  segment: {
    length: 1.2, width: 0.18, density: 1.0,
    joint: { angle_min: -1.2, angle_max: 1.2, motor_torque: 120.0 },
  },
  spawn: { position: [0, 0], angle: 0.0, label: null },
  limits: { maxLimbs: 6, maxSegments: 4, bodyVerts: [3, 8], obstacleVerts: [3, 10] },
};

export const clone = (o) => JSON.parse(JSON.stringify(o));

/** Чиста сцена: істота без кінцівок, порожній світ. */
export const defaultState = () => ({
  creature: {
    name: 'swimmer-v1',
    body: { shape: 'circle', radius: 0.8, vertices: null, density: 1.0 },
    limbs: [],
  },
  /** Сутність «хижак», що зараз редагується (без маршруту). */
  predator: clone(DEFAULTS.predator),
  predatorName: 'хижак-1',
  /** Назва сцени в бібліотеці — симетрично до `predatorName`. */
  sceneName: 'сцена-1',
  environment: {
    world: clone(DEFAULTS.world),
    fluid: clone(DEFAULTS.fluid),
    spawns: [],
    predators: [],
    obstacles: [],
  },
});

/**
 * Сцени, збережені за старим контрактом (один `predator`, без `spawns`),
 * приводяться до поточного вигляду — так само, як це робить backend.
 */
export function migrateEnvironment(env) {
  if (!env || typeof env !== 'object') return defaultState().environment;
  const out = { ...env };
  if (!Array.isArray(out.predators)) out.predators = [];
  if (out.predator && typeof out.predator === 'object') {
    if (!out.predators.length) out.predators.push(out.predator);
    delete out.predator;
  }
  // Поля, яких немає у старому/ручному файлі, беремо з дефолтів: інакше
  // `undefined` радіус дає NaN у градієнті аури й валить рендер
  out.predators = out.predators.map((p) => ({
    ...clone(DEFAULTS.predator),
    preset: null,
    ...p,
    waypoints: Array.isArray(p.waypoints) ? p.waypoints : [],
  }));
  if (!Array.isArray(out.spawns)) out.spawns = [];
  // Точка появи більше не прив'язана до морфології (§9.2): ГА оцінює популяцію
  out.spawns = out.spawns.map(({ creature, ...rest }) => ({ ...clone(DEFAULTS.spawn), ...rest }));
  if (!Array.isArray(out.obstacles)) out.obstacles = [];
  if (!out.world) out.world = clone(DEFAULTS.world);
  if (!out.fluid) out.fluid = clone(DEFAULTS.fluid);
  return out;
}

/** Морфології з бібліотеки для «привидів» у сцені (ключ — назва істоти). */
export const presetCreatures = new Map();
/** Запити, що вже в польоті: рендер викликається на кожен рух миші. */
const creatureRequests = new Map();

/** Підвантажує морфологію з бібліотеки (один раз) і повертає її або null. */
export function ensurePresetCreature(name) {
  if (!name) return Promise.resolve(null);
  if (presetCreatures.has(name)) return Promise.resolve(presetCreatures.get(name));
  if (creatureRequests.has(name)) return creatureRequests.get(name);

  const request = loadCreature(name)
    .then((res) => res?.creature ?? null)
    .catch(() => null)
    .then((creature) => {
      presetCreatures.set(name, creature);
      creatureRequests.delete(name);
      return creature;
    });
  creatureRequests.set(name, request);
  return request;
}

/** Після зміни бібліотеки кеш «привидів» більше не актуальний. */
export function invalidateCreatureCache() {
  presetCreatures.clear();
  creatureRequests.clear();
}

/**
 * Доповнює морфологію з файлу дефолтами (density, rest_angle), яких Pydantic
 * підставляє на сервері: без цього панель падає на `body.density.toFixed()`.
 */
export function normalizeCreature(raw) {
  if (!raw || typeof raw !== 'object') throw new Error('очікувався об\'єкт CreatureConfig');
  const body = raw.body;
  if (!body || typeof body !== 'object') throw new Error('немає поля body');
  if (!Array.isArray(raw.limbs) || !raw.limbs.length) throw new Error('немає жодної кінцівки');

  const limbs = raw.limbs.map((limb, i) => {
    if (!limb || !Array.isArray(limb.segments) || !limb.segments.length) {
      throw new Error(`кінцівка ${i} без сегментів`);
    }
    return {
      ...limb,
      attach_angle: Number(limb.attach_angle) || 0,
      rest_angle: Number(limb.rest_angle) || 0,
      segments: limb.segments.map((seg) => ({
        ...DEFAULTS.segment,
        ...seg,
        joint: { ...DEFAULTS.segment.joint, ...(seg?.joint ?? {}) },
      })),
    };
  });

  return {
    ...raw,
    name: raw.name || 'creature',
    body: {
      shape: body.shape === 'polygon' ? 'polygon' : 'circle',
      radius: body.shape === 'polygon' ? null : (body.radius ?? 0.8),
      vertices: body.shape === 'polygon' ? (body.vertices ?? null) : null,
      density: body.density ?? 1.0,
    },
    limbs,
  };
}

/** Зберігає поточну сцену як іменований варіант. */
export async function saveAsPreset(name) {
  const meta = await savePreset(name, editorSnapshot());
  return meta;
}

/** Єдине джерело правди фронтенду. Істота та сцена зберігаються окремо. */
export const state = {
  ...defaultState(),
  /** Останній успішний результат /api/creature/validate */
  validation: null,
};

const $ = (sel) => document.querySelector(sel);

// --- ExperimentConfig (§9.3) ----------------------------------------------
const GA_FIELDS = [
  'population', 'generations', 'elitism', 'tournament_k',
  'p_crossover', 'p_mutation', 'mutation_sigma',
  'episodes_per_individual', 'episode_seconds',
];
const FW_FIELDS = ['survival', 'distance', 'danger', 'collision', 'passivity', 'energy'];

export function buildExperimentConfig() {
  const ga = { crossover: 'uniform' };
  for (const f of GA_FIELDS) ga[f] = Number($(`#ga-${f}`).value);
  const fitness_weights = {};
  for (const f of FW_FIELDS) fitness_weights[f] = Number($(`#fw-${f}`).value);
  return {
    name: $('#ga-name').value.trim(),
    seed: Number($('#ga-seed').value),
    creature: clone(state.creature),
    environment: clone(state.environment),
    ga,
    fitness_weights,
  };
}

export function refreshExperimentJson() {
  const el = $('#exp-json');
  if (el) el.textContent = JSON.stringify(buildExperimentConfig(), null, 2);
}

/** Заповнює форми симуляції зі збереженого стану. */
export function applyExperimentForm(saved) {
  if (saved?.predator) {
    state.predator = { ...clone(DEFAULTS.predator), ...saved.predator };
    state.predatorName = saved.predator_name ?? state.predatorName;
  }
  if (saved?.scene_name) state.sceneName = saved.scene_name;
  if (saved?.ga) {
    for (const f of GA_FIELDS) if (saved.ga[f] != null) $(`#ga-${f}`).value = saved.ga[f];
    if (saved.ga.name) $('#ga-name').value = saved.ga.name;
    if (saved.ga.seed != null) $('#ga-seed').value = saved.ga.seed;
  }
  if (saved?.fitness_weights) {
    for (const f of FW_FIELDS) {
      if (saved.fitness_weights[f] != null) $(`#fw-${f}`).value = saved.fitness_weights[f];
    }
  }
}

// --- Збереження сцени у проєкті -------------------------------------------
const SAVE_DEBOUNCE_MS = 900;
let saveTimer = null;
let ready = false;          // до завершення старту автозбереження не чіпаємо
let lastSavedSnapshot = null;   // що вже лежить на сервері (для дедуплікації)
let saveChain = Promise.resolve(null);   // збереження строго послідовні

export function setSavedBadge(text, cls = '') {
  const el = $('#project-saved');
  if (el) { el.textContent = text; el.className = `badge ${cls}`; }
}

export const localTime = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString('uk-UA');
};

/** Поточний знімок редактора для `experiments/editor/last_session.json`. */
function editorSnapshot() {
  const exp = buildExperimentConfig();
  return {
    creature: exp.creature,
    environment: exp.environment,
    predator: clone(state.predator),
    predator_name: state.predatorName,
    scene_name: state.sceneName,
    ga: { ...exp.ga, name: exp.name, seed: exp.seed },
    fitness_weights: exp.fitness_weights,
  };
}

async function doSave(silent) {
  const snapshot = editorSnapshot();
  const blob = JSON.stringify(snapshot);
  if (!silent) setSavedBadge('збереження…');
  try {
    const res = await saveEditorState(snapshot);
    lastSavedSnapshot = blob;
    setSavedBadge(`збережено ${localTime(res.saved_at)}`, 'ok');
    return res;
  } catch (e) {
    setSavedBadge(e instanceof ApiError ? `не збережено: ${e.code}` : 'не збережено', 'err');
    return null;
  }
}

/**
 * Збереження ставиться в чергу, а не відкидається: інакше зміна, зроблена під
 * час запиту, не потрапила б на сервер, а бейдж показував би «збережено».
 */
export function saveToProject({ silent = false } = {}) {
  saveChain = saveChain.catch(() => null).then(() => doSave(silent));
  return saveChain;
}

/** Після відновлення сесії вважаємо збережений стан актуальним. */
export function markSnapshotSaved() {
  lastSavedSnapshot = JSON.stringify(editorSnapshot());
}

export async function resetProjectState() {
  try {
    await clearEditorState();
  } catch { /* файлу могло й не бути — не критично */ }
  Object.assign(state, defaultState());
  state.validation = null;
  // Форми ГА та ваг теж повертаємо до типових — інакше автозбереження
  // одразу відтворить файл зі старими параметрами прогону
  document.querySelectorAll('#tab-simulation input').forEach((el) => {
    if (el.defaultValue !== undefined) el.value = el.defaultValue;
  });
  lastSavedSnapshot = null;
  setSavedBadge('не збережено');
}

/** Будь-яка зміна стану: оновити прев'ю JSON і запланувати автозбереження. */
export function stateChanged() {
  refreshExperimentJson();
  if (!ready) return;       // початковий рендер — це не зміна користувача
  // Вибір кінцівки чи рядка списку теж проходить через refresh(), але нічого
  // не змінює у знімку — такі виклики не мають бруднити стан і писати на диск
  if (JSON.stringify(editorSnapshot()) === lastSavedSnapshot) return;
  setSavedBadge('є незбережені зміни', 'warn');
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveToProject({ silent: true }), SAVE_DEBOUNCE_MS);
}


/** Старт/кінець ініціалізації: до готовності автозбереження не чіпаємо. */
export function setReady(value) { ready = value; }
