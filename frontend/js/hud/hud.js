/**
 * HUD: Gen, таймер епізоду, running fitness з розкладом компонентів,
 * стан хижака, перемикач Headless↔Live (§10.2). Реалізація — етапи 3–5.
 *
 * Етап 3: кнопки Старт / Пауза / Продовжити / Стоп, канал `/ws/metrics` і
 * проста лінія найкращого та середнього фітнесу по поколіннях. Повні графіки
 * Chart.js — етап 5 (`charts.js`).
 *
 * Модуль не знає, звідки береться конфіг прогону: його віддає `getRun`
 * з `simulation.js` — так вибір складових лишається в одному місці.
 */

import {
  startSimulation, pauseSimulation, resumeSimulation, stopSimulation,
  getStatus, getGenerations, ApiError,
} from '../api.js';
import { connect } from '../ws.js';

const $ = (s) => document.querySelector(s);

const STATE_LABELS = { RUNNING: 'виконується', PAUSED: 'пауза', STOPPED: 'зупинено' };

/** Поточний прогін: стан сервера + історія поколінь для лінії. */
const run = { state: 'STOPPED', runId: null, generation: 0, total: 0, rows: [] };
let colors = null;
let onRunChange = null;

// --- Кнопки ------------------------------------------------------------------
function syncButtons() {
  const s = run.state;
  $('#sim-start').disabled = s !== 'STOPPED';
  $('#sim-pause').disabled = s !== 'RUNNING';
  $('#sim-resume').disabled = s !== 'PAUSED';
  $('#sim-stop').disabled = s === 'STOPPED';
}

function message(text, cls = 'muted') {
  const el = $('#train-msg');
  el.textContent = text;
  el.className = `small ${cls}`;
}

async function control(action, busyText) {
  message(busyText);
  try {
    const res = await action();
    applyState({ state: res.state, run_id: res.run_id ?? run.runId });
    return res;
  } catch (e) {
    message(e instanceof ApiError ? e.message : String(e), 'err');
    return null;
  }
}

// --- Стан прогону --------------------------------------------------------------
async function applyState(packet) {
  const changedRun = packet.run_id && packet.run_id !== run.runId;
  run.state = packet.state ?? run.state;
  if (packet.generation != null) run.generation = packet.generation;
  if (packet.generations_total != null) run.total = packet.generations_total;
  if (changedRun) {
    run.runId = packet.run_id;
    run.rows = [];
    await loadHistory();
  }
  syncButtons();
  $('#st-state').textContent = STATE_LABELS[run.state] ?? run.state;
  $('#st-run').textContent = run.runId ?? '—';
  $('#st-gen').textContent = run.generation;
  showProgress();
  if (packet.error) message(`прогін зупинився з помилкою: ${packet.error}`, 'err');
  else if (run.state === 'RUNNING') message('навчання триває; пауза настає між поколіннями');
  else if (run.state === 'PAUSED') message('пауза: поточне покоління дооцінене й записане');
  else if (packet.archived) message(`прогін ${run.runId} завершено й скопійовано в experiments/${packet.archived}`, 'ok');
  else if (run.runId) message(`прогін ${run.runId} зупинено до кінця — лишився тільки локально в experiments/runs/`);
  onRunChange?.({ ...run });
}

/** Історія з REST: після перепідключення чи перезавантаження сторінки лінія не порожня. */
async function loadHistory() {
  if (!run.runId) return;
  try {
    const res = await getGenerations(run.runId);
    run.rows = res.rows;
  } catch { /* прогін міг ще не записати жодного покоління */ }
  drawChart();
  showLast();
  showProgress();
}

function onGeneration(stats) {
  if (stats.run_id !== run.runId) return;
  if (!run.rows.length || run.rows.at(-1).generation < stats.generation) run.rows.push(stats);
  run.generation = stats.generation;
  $('#st-gen').textContent = run.generation;
  showProgress();
  drawChart();
  showLast();
  onRunChange?.({ ...run });
}

/** Смужка «пройдено / заплановано» і оцінка часу, що лишився. */
function showProgress() {
  const box = $('#st-progress');
  box.hidden = !run.runId || !run.total;
  if (box.hidden) return;
  const done = Math.min(run.generation, run.total);
  const pct = Math.floor((done / run.total) * 100);
  $('#st-progress-bar').style.width = `${pct}%`;
  box.classList.toggle('paused', run.state === 'PAUSED');
  box.classList.toggle('stopped', run.state === 'STOPPED');

  let eta = '';
  const recent = run.rows.slice(-10).map((r) => r.eval_seconds).filter(Number.isFinite);
  if (run.state !== 'STOPPED' && recent.length && done < run.total) {
    const sec = (recent.reduce((a, b) => a + b, 0) / recent.length) * (run.total - done);
    eta = sec < 90 ? ` · лишилось ~${Math.round(sec)} с` : ` · лишилось ~${Math.round(sec / 60)} хв`;
  }
  $('#st-progress-text').textContent = `${done} / ${run.total} · ${pct} %${eta}`;
}

function showLast() {
  const last = run.rows.at(-1);
  const best = run.rows.reduce((m, r) => Math.max(m, r.best_fitness), -Infinity);
  const set = (id, v) => { $(id).textContent = v; };
  set('#tr-best', Number.isFinite(best) ? best.toFixed(4) : '—');
  set('#tr-avg', last ? last.avg_fitness.toFixed(4) : '—');
  set('#tr-div', last ? last.diversity.toFixed(3) : '—');
  const pct = (v) => (v == null ? '—' : `${Math.round(v * 100)} %`);
  set('#tr-caught', last ? `${pct(last.caught_top10)} / ${pct(last.caught_all)}` : '—');
  set('#tr-eval', last ? `${last.eval_seconds.toFixed(1)} с` : '—');
  set('#tr-fps', last ? Math.round(last.sim_fps_equivalent).toLocaleString('uk-UA') : '—');
}

// --- Лінія фітнесу -----------------------------------------------------------------
/** Процедурна лінія best/avg (§10.1: без растрів). Повні графіки — етап 5. */
function drawChart() {
  const canvas = $('#fit-chart');
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (!w || !h) return;                    // вкладка прихована — намалюємо при показі
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const rows = run.rows;
  ctx.font = '11px ui-monospace, monospace';
  ctx.fillStyle = colors.muted;
  if (rows.length < 1) {
    ctx.fillText('поки немає поколінь', 8, h / 2);
    return;
  }
  const pad = { l: 34, r: 6, t: 8, b: 16 };
  const values = rows.flatMap((r) => [r.best_fitness, r.avg_fitness]);
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (hi - lo < 1e-9) { hi += 0.5; lo -= 0.5; }
  const n = rows.length;
  const x = (i) => pad.l + (n === 1 ? 0.5 : i / (n - 1)) * (w - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (h - pad.t - pad.b);

  // Нуль — орієнтир: штрафи роблять фітнес від'ємним на старті
  ctx.strokeStyle = colors.border;
  ctx.lineWidth = 1;
  if (lo < 0 && hi > 0) {
    ctx.beginPath(); ctx.moveTo(pad.l, y(0)); ctx.lineTo(w - pad.r, y(0)); ctx.stroke();
  }
  ctx.fillText(hi.toFixed(2), 2, pad.t + 8);
  ctx.fillText(lo.toFixed(2), 2, h - pad.b);
  ctx.fillText(`пок. ${rows[0].generation}…${rows.at(-1).generation}`, pad.l, h - 3);

  const line = (key, color) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    rows.forEach((r, i) => (i ? ctx.lineTo(x(i), y(r[key])) : ctx.moveTo(x(i), y(r[key]))));
    ctx.stroke();
  };
  line('avg_fitness', colors.accent2);
  line('best_fitness', colors.accent);
}

// --- Ініціалізація ------------------------------------------------------------------
/**
 * @param {object} opts
 * @param {object} opts.theme             кольори теми (`themeColors()`)
 * @param {() => {config?:object, problem?:string}} opts.getRun  конфіг прогону або причина, чому його нема
 * @param {(run:object) => void} [opts.onChange]  зміна прогону/покоління (для кнопки «Найкраща особина»)
 */
export async function initTraining({ theme, getRun, onChange }) {
  colors = theme;
  onRunChange = onChange;

  $('#sim-start').addEventListener('click', async () => {
    const { config, problem } = await getRun();
    if (problem) { message(problem, 'warn'); return; }
    await control(() => startSimulation(config), 'запуск…');
  });
  $('#sim-pause').addEventListener('click', () => control(pauseSimulation, 'пауза настане після поточного покоління…'));
  $('#sim-resume').addEventListener('click', () => control(resumeSimulation, 'продовження…'));
  $('#sim-stop').addEventListener('click', () => control(stopSimulation, 'зупинка…'));

  // Канал подій живе весь час: прогін міг стартувати з іншої вкладки браузера
  connect('/ws/metrics', {
    onPacket: (packet) => {
      if (packet.type === 'state') applyState(packet);
      else if (packet.type === 'generation') onGeneration(packet);
    },
    onState: (s) => { if (s === 'open') loadHistory(); },
  });
  window.addEventListener('tabchange', (e) => { if (e.detail === 'simulation') drawChart(); });
  window.addEventListener('resize', drawChart);

  try {
    const s = await getStatus();
    await applyState(s);
  } catch {
    syncButtons();
  }
  drawChart();
}
