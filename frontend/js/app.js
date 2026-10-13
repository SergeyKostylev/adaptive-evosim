/**
 * Точка входу SPA: роутинг вкладок, індикатор стану сервера та
 * послідовність старту (збережена сцена → редактор).
 */

import { getStatus, getEditorState, ApiError } from './api.js';
import {
  state, applyExperimentForm, refreshExperimentJson, stateChanged,
  setReady, setSavedBadge, localTime, migrateEnvironment, markSnapshotSaved,
} from './state.js';
import { initEditor } from './editor/editor.js';
import { initSimulationPicker, refreshExperimentPreview } from './simulation.js';

const $ = (sel) => document.querySelector(sel);

/**
 * Будь-яка JS-помилка має бути видимою: інакше редактор просто «перестає
 * реагувати» без жодного сліду для користувача.
 */
function showFatal(what, err) {
  const box = $('#js-error');
  if (!box) return;
  const text = err?.stack || err?.message || String(err);
  box.hidden = false;
  box.textContent = `Помилка інтерфейсу (${what}): ${text}`;
  const close = document.createElement('button');
  close.textContent = 'Сховати';
  close.addEventListener('click', () => { box.hidden = true; });
  box.prepend(close);
}

window.addEventListener('error', (e) => showFatal('скрипт', e.error ?? e.message));
window.addEventListener('unhandledrejection', (e) => showFatal('запит', e.reason));

// --- Стан екрана в URL ------------------------------------------------------
// Щоб перезавантаження (а його під час розробки багато) поверталo на той самий
// екран, а посиланням можна було поділитись.
const TABS = ['editor', 'simulation', 'analytics'];
const TOOLS = ['creature', 'predator', 'scene'];
const SCENE_TOOLS = ['obstacle', 'route', 'spawn'];

function readUrl() {
  const p = new URLSearchParams(window.location.search);
  const pick = (key, allowed) => (allowed.includes(p.get(key)) ? p.get(key) : null);
  return { tab: pick('tab', TABS), tool: pick('tool', TOOLS), scene: pick('scene', SCENE_TOOLS) };
}

let urlState = { tab: 'editor', tool: 'creature', scene: 'obstacle' };

function writeUrl(patch = {}) {
  urlState = { ...urlState, ...patch };
  const p = new URLSearchParams(window.location.search);
  p.set('tab', urlState.tab);
  if (urlState.tab === 'editor') {
    p.set('tool', urlState.tool);
    if (urlState.tool === 'scene') p.set('scene', urlState.scene);
    else p.delete('scene');
  } else {
    p.delete('tool');
    p.delete('scene');
  }
  // replaceState, а не pushState: перемикання режимів не має засмічувати
  // історію браузера десятками записів
  window.history.replaceState(null, '', `${window.location.pathname}?${p}`);
}

const STATE_LABELS = { RUNNING: 'виконується', PAUSED: 'пауза', STOPPED: 'зупинено' };
const MODE_LABELS = { headless: 'без рендера', live: 'живий перегляд' };

// --- Вкладки ---------------------------------------------------------------
function activateTab(name, { silent = false } = {}) {
  const buttons = [...document.querySelectorAll('nav button')];
  const btn = buttons.find((b) => b.dataset.tab === name) ?? buttons[0];
  buttons.forEach((b) => b.classList.toggle('active', b === btn));
  document.querySelectorAll('.tab').forEach((t) =>
    t.classList.toggle('active', t.id === `tab-${btn.dataset.tab}`));
  if (!silent) writeUrl({ tab: btn.dataset.tab });
  window.dispatchEvent(new CustomEvent('tabchange', { detail: btn.dataset.tab }));
}

function initTabs() {
  document.querySelectorAll('nav button').forEach((btn) =>
    btn.addEventListener('click', () => activateTab(btn.dataset.tab)));
  // Режим редактора повідомляє про себе подією — щоб editor.js не знав про URL
  window.addEventListener('editortool', (e) =>
    writeUrl({ tool: e.detail.tool, scene: e.detail.sceneTool }));
  // Тест-заплив народжується в редакторі, а показується в «Симуляції»:
  // вкладками відає лише app.js (§10.3)
  window.addEventListener('testswim', () => activateTab('simulation'));
  window.addEventListener('popstate', () => {
    const fromUrl = readUrl();
    if (fromUrl.tab) activateTab(fromUrl.tab, { silent: true });
  });
}

// --- Індикатор стану сервера ----------------------------------------------
async function pollStatus() {
  const badge = $('#api-badge');
  try {
    const s = await getStatus();
    badge.textContent = `Сервер: ${STATE_LABELS[s.state] ?? s.state}`;
    badge.className = 'badge ok';
    $('#st-state').textContent = STATE_LABELS[s.state] ?? s.state;
    $('#st-run').textContent = s.run_id ?? '—';
    $('#st-gen').textContent = s.generation;
    $('#st-mode').textContent = MODE_LABELS[s.mode] ?? s.mode;
  } catch (e) {
    badge.textContent = e instanceof ApiError && e.code === 'NETWORK_ERROR'
      ? 'Сервер: недоступний' : 'Сервер: помилка';
    badge.className = 'badge err';
  }
}

/**
 * Чи придатна відновлена морфологія до рендера. `/api/editor/state` навмисно
 * зберігає чернетку **без** валідації (§9.4), тож обірваний або руками
 * виправлений файл цілком може не мати `body` — і тоді перший же `drawCreature`
 * валить редактор ще до того, як користувач щось побачить.
 */
function usableCreature(creature) {
  return Boolean(creature && typeof creature === 'object'
    && creature.body && typeof creature.body === 'object'
    && Array.isArray(creature.limbs));
}

/** Збережена сцена має потрапити у стан ДО першого рендера редактора. */
async function restoreSavedState() {
  try {
    const res = await getEditorState();
    if (!res?.exists || !res.state) return;
    if (usableCreature(res.state.creature)) {
      state.creature = res.state.creature;
    } else if (res.state.creature) {
      setSavedBadge('збережена істота пошкоджена — відкрито типову', 'err');
    }
    if (res.state.environment) state.environment = migrateEnvironment(res.state.environment);
    applyExperimentForm(res.state);
    if (usableCreature(res.state.creature)) {
      setSavedBadge(`відкрито збережене ${localTime(res.saved_at)}`, 'ok');
    }
  } catch {
    setSavedBadge('збережене недоступне', 'err');
  }
}

async function bootstrap() {
  const fromUrl = readUrl();
  initTabs();
  await restoreSavedState();
  const api = initEditor(state);

  // Режим редактора з URL — до першого рендера, щоб не блимало типовим
  if (fromUrl.tool) api.setTool(fromUrl.tool);
  if (fromUrl.scene) api.setSceneTool(fromUrl.scene);
  writeUrl({
    tab: fromUrl.tab ?? 'editor',
    tool: fromUrl.tool ?? 'creature',
    scene: fromUrl.scene ?? 'obstacle',
  });

  markSnapshotSaved();        // те, що відкрили, вже лежить на сервері
  setReady(true);
  refreshExperimentJson();
  [...document.querySelectorAll('#tab-simulation input')]
    .forEach((i) => i.addEventListener('change', () => { stateChanged(); refreshExperimentPreview(); }));
  initSimulationPicker()
    .then(() => { if (fromUrl.tab && fromUrl.tab !== 'editor') activateTab(fromUrl.tab); })
    .catch((e) => showFatal('симуляція', e));
  await pollStatus();
  setInterval(pollStatus, 5000);
}

bootstrap().catch((e) => showFatal('запуск', e));
