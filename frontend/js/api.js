/**
 * REST-клієнт (PROJECT_SPEC.md §9.4). Один формат помилок:
 * {"error": {"code", "message", "details"}} → ApiError.
 */

const BASE = '/api';

export class ApiError extends Error {
  constructor(code, message, details = [], status = 0) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.details = details;
    this.status = status;
  }
}

async function request(path, { method = 'GET', body } = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError('NETWORK_ERROR', `Backend недоступний: ${e.message}`);
  }

  const text = await res.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = null; }
  }

  if (!res.ok) {
    const err = payload && payload.error;
    throw new ApiError(
      err?.code ?? 'HTTP_ERROR',
      err?.message ?? `HTTP ${res.status}`,
      err?.details ?? [],
      res.status,
    );
  }
  return payload;
}

/** POST /api/creature/validate → {valid, errors[], n_joints, n_in, genome_size, creature_hash} */
export const validateCreature = (creature) =>
  request('/creature/validate', { method: 'POST', body: creature });

/** POST /api/predator/validate → {valid, errors[], r_catch_effective, aura_diameter} */
export const validatePredator = (predator) =>
  request('/predator/validate', { method: 'POST', body: predator });

/** GET /api/simulation/status → {state, run_id?, generation, population, elapsed_s, mode} */
export const getStatus = () => request('/simulation/status');

// --- Живий перегляд (§9.4) --------------------------------------------------
/**
 * POST /api/preview — стартує live-епізод; кадри йдуть у /ws/live.
 * На етапі 2 доступне лише `source: 'demo'` (контролер-синусоїда, §2.4).
 */
export const startPreview = (body) =>
  request('/preview', { method: 'POST', body });

/** DELETE /api/preview → {active:false} */
export const stopPreview = () => request('/preview', { method: 'DELETE' });

/** GET /api/preview → {active, preview_id?, source?} */
export const getPreviewState = () => request('/preview');

/** GET /api/editor/state → {exists, saved_at?, state?} — сцена з experiments/editor/ */
export const getEditorState = () => request('/editor/state');

/** PUT /api/editor/state — зберігає поточну сцену у проєкті */
export const saveEditorState = (state) =>
  request('/editor/state', { method: 'PUT', body: state });

/** DELETE /api/editor/state — прибирає збережену сцену */
export const clearEditorState = () => request('/editor/state', { method: 'DELETE' });

// --- Іменовані варіанти сцени ---------------------------------------------
const presetUrl = (name) => `/editor/presets/${encodeURIComponent(name)}`;

/** GET /api/editor/presets → {presets:[PresetMeta], limit} */
export const listPresets = () => request('/editor/presets');

/** PUT /api/editor/presets/{name} → PresetMeta */
export const savePreset = (name, state) =>
  request(presetUrl(name), { method: 'PUT', body: state });

/** GET /api/editor/presets/{name} → {exists, saved_at, state} */
export const loadPreset = (name) => request(presetUrl(name));

/** DELETE /api/editor/presets/{name} → {presets, limit} */
export const deletePreset = (name) => request(presetUrl(name), { method: 'DELETE' });

// --- Бібліотека істот -------------------------------------------------------
const creatureUrl = (name) => `/editor/creatures/${encodeURIComponent(name)}`;

/** GET /api/editor/creatures → {creatures:[CreatureMeta], limit} */
export const listCreatures = () => request('/editor/creatures');

/** PUT /api/editor/creatures/{name} — морфологія валідується за §9.1 */
export const saveCreature = (name, creature) =>
  request(creatureUrl(name), { method: 'PUT', body: { creature } });

/** GET /api/editor/creatures/{name} → {creature, saved_at} */
export const loadCreature = (name) => request(creatureUrl(name));

/** DELETE /api/editor/creatures/{name} → {creatures, limit} */
export const deleteCreature = (name) => request(creatureUrl(name), { method: 'DELETE' });

// --- Бібліотека хижаків -----------------------------------------------------
const predatorUrl = (name) => `/editor/predators/${encodeURIComponent(name)}`;

/** GET /api/editor/predators → {predators:[PredatorMeta], limit} */
export const listPredators = () => request('/editor/predators');

/** PUT /api/editor/predators/{name} — параметри без маршруту */
export const savePredator = (name, predator) =>
  request(predatorUrl(name), { method: 'PUT', body: { predator } });

/** GET /api/editor/predators/{name} → {predator, saved_at} */
export const loadPredator = (name) => request(predatorUrl(name));

/** DELETE /api/editor/predators/{name} → {predators, limit} */
export const deletePredator = (name) => request(predatorUrl(name), { method: 'DELETE' });
