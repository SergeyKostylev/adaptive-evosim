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

const STATE_LABELS = { RUNNING: 'виконується', PAUSED: 'пауза', STOPPED: 'зупинено' };
const MODE_LABELS = { headless: 'без рендера', live: 'живий перегляд' };

// --- Вкладки ---------------------------------------------------------------
function initTabs() {
  const buttons = [...document.querySelectorAll('nav button')];
  buttons.forEach((btn) => {
    btn.addEventListener('click', () => {
      buttons.forEach((b) => b.classList.toggle('active', b === btn));
      document.querySelectorAll('.tab').forEach((t) =>
        t.classList.toggle('active', t.id === `tab-${btn.dataset.tab}`));
      if (btn.dataset.tab === 'simulation') refreshExperimentPreview();
      window.dispatchEvent(new CustomEvent('tabchange', { detail: btn.dataset.tab }));
    });
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

/** Збережена сцена має потрапити у стан ДО першого рендера редактора. */
async function restoreSavedState() {
  try {
    const res = await getEditorState();
    if (!res?.exists || !res.state) return;
    if (res.state.creature) state.creature = res.state.creature;
    if (res.state.environment) state.environment = migrateEnvironment(res.state.environment);
    applyExperimentForm(res.state);
    setSavedBadge(`відкрито збережене ${localTime(res.saved_at)}`, 'ok');
  } catch {
    setSavedBadge('збережене недоступне', 'err');
  }
}

async function bootstrap() {
  initTabs();
  await restoreSavedState();
  initEditor(state);
  markSnapshotSaved();        // те, що відкрили, вже лежить на сервері
  setReady(true);
  refreshExperimentJson();
  [...document.querySelectorAll('#tab-simulation input')]
    .forEach((i) => i.addEventListener('change', () => { stateChanged(); refreshExperimentPreview(); }));
  initSimulationPicker().catch((e) => showFatal('симуляція', e));
  await pollStatus();
  setInterval(pollStatus, 5000);
}

bootstrap().catch((e) => showFatal('запуск', e));
