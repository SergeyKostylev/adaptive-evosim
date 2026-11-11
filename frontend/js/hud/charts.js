/**
 * Вкладка «Аналітика» (§10.2): таблиця прогонів з `/api/experiments`, чотири
 * графіки Chart.js вибраного прогону, експорт для дипломної, продовження з
 * чекпоінта й ручне архівування.
 *
 * Графіки ті самі, що в експорті (`app/export/plots.py`): фітнес, різноманіття,
 * внесок компонентів `w_k·c_k` у F найкращої особини, частки спійманих. Для
 * поточного прогону вони ростуть наживо з `/ws/metrics`; після перепідключення
 * історія дочитується з REST.
 *
 * Chart.js підключено з cdnjs (`index.html`); якщо CDN недоступний, вкладка
 * показує таблицю й експорт, а замість графіків — пояснення.
 */

import {
  listExperiments, getGenerations, continueSimulation, archiveExperiment, setRunHidden, deleteExperiment,
  exportUrl, bestGenomeUrl, ApiError,
} from '../api.js';
import { connect } from '../ws.js';

const $ = (s) => document.querySelector(s);

/** Компонент → [поле ваги, знак у формулі §7.1] — як `fitness.TERMS` на сервері. */
const TERMS = {
  S: ['survival', 1], D: ['distance', 1], G_aura: ['danger', -1],
  C: ['collision', -1], Pas: ['passivity', -1], E_n: ['energy', -1],
};
const LABELS = {
  S: 'виживання S', D: 'дистанція D', G_aura: 'небезпека G_aura',
  C: 'колізії C', Pas: 'пасивність Pas', E_n: 'енергія E_n',
};
/** Категоріальна палітра для темного фону — ті самі слоти й порядок, що в PDF. */
const SERIES = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300'];
const STATE_LABELS = {
  RUNNING: 'виконується', PAUSED: 'пауза', STOPPED: 'зупинено', COMPLETED: 'завершено',
};
const SMOOTH = 10;            // ковзне середнє спійманих, поколінь (C.PLOT_SMOOTH_WINDOW)

let colors = null;
let runs = [];
let selected = null;          // run_id
let rows = [];
const charts = {};
let busy = false;             // менеджер зараз веде якийсь прогін

/** Фільтр списку: 'visible' | 'hidden' | 'all'; запам'ятовується лише в цьому браузері. */
const FILTER_KEY = 'evosim.analytics.filter';
const FILTERS = { visible: 'Не приховані', hidden: 'Приховані', all: 'Усі' };
let filter = 'visible';
try {
  const saved = localStorage.getItem(FILTER_KEY);
  if (saved in FILTERS) filter = saved;
} catch { /* приватне вікно / заблоковане сховище — типовий фільтр */ }

function shown() {
  if (filter === 'all') return runs;
  return runs.filter((r) => r.hidden === (filter === 'hidden'));
}

/** Вибраний прогін має бути у видимому списку; інакше — перший (поточний, якщо він там). */
async function ensureSelection() {
  const list = shown();
  if (list.some((r) => r.run_id === selected)) return;
  selected = (list.find((r) => r.state === 'RUNNING' || r.state === 'PAUSED') ?? list[0])?.run_id ?? null;
  await loadRows();
}

function renderFilter() {
  for (const btn of document.querySelectorAll('#an-filter button')) {
    const f = btn.dataset.filter;
    const n = f === 'all' ? runs.length : runs.filter((r) => r.hidden === (f === 'hidden')).length;
    btn.textContent = `${FILTERS[f]} (${n})`;
    btn.classList.toggle('active', f === filter);
  }
}

// --- Таблиця прогонів -------------------------------------------------------------
function message(text, cls = 'muted') {
  const el = $('#an-msg');
  el.textContent = text;
  el.className = `small ${cls}`;
}

async function refreshRuns() {
  try {
    runs = (await listExperiments()).runs;
  } catch (e) {
    $('#an-runs').innerHTML = '';
    const p = document.createElement('p');
    p.className = 'small err';
    p.textContent = e instanceof ApiError ? e.message : String(e);
    $('#an-runs').append(p);
    return;
  }
  busy = runs.some((r) => r.state === 'RUNNING' || r.state === 'PAUSED');
  await ensureSelection();
  renderFilter();
  renderRuns();
  renderActions();
}

function fmtDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('uk-UA', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function renderRuns() {
  const box = $('#an-runs');
  box.innerHTML = '';
  const list = shown();
  if (!list.length) {
    const p = document.createElement('p');
    p.className = 'small muted';
    p.textContent = !runs.length ? 'прогонів ще немає — запустіть навчання на вкладці «Симуляція»'
      : filter === 'hidden' ? 'прихованих прогонів немає' : 'усі прогони приховані — див. фільтр «Приховані»';
    box.append(p);
    return;
  }
  for (const r of list) {
    const item = document.createElement('button');
    item.className = `run-item${r.run_id === selected ? ' selected' : ''}${r.hidden ? ' is-hidden' : ''}`;
    item.title = r.run_id;
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = r.name;
    const state = document.createElement('span');
    state.className = `state ${r.state}`;
    state.textContent = STATE_LABELS[r.state] ?? r.state;
    const meta = document.createElement('span');
    meta.className = 'meta';
    const best = r.best_fitness == null ? '—' : r.best_fitness.toFixed(3);
    meta.textContent = `${fmtDate(r.created_at)} · пок. ${r.generations_done}/${r.generations_total}`
      + ` · F ${best}${r.archived ? ' · в архіві' : ''}${r.hidden ? ' · приховано' : ''}`;
    item.append(name, state, meta);
    item.addEventListener('click', async () => {
      if (selected === r.run_id) return;
      selected = r.run_id;
      message('');
      renderRuns();
      renderActions();
      await loadRows();
    });
    box.append(item);
  }
}

function current() {
  return runs.find((r) => r.run_id === selected) ?? null;
}

function setLink(id, href) {
  const a = $(id);
  a.classList.toggle('disabled', !href);
  if (href) a.href = href; else a.removeAttribute('href');
}

function renderActions() {
  const r = current();
  $('#an-actions').hidden = !r;
  if (!r) return;
  const kv = $('#an-kv');
  kv.innerHTML = '';
  const pairs = [
    ['прогін', r.run_id],
    ['створено', fmtDate(r.created_at)],
    ['стан', STATE_LABELS[r.state] ?? r.state],
    ['поколінь', `${r.generations_done} / ${r.generations_total}`],
    ['рекорд F', r.best_fitness == null ? '—' : `${r.best_fitness.toFixed(4)} (пок. ${r.best_generation})`],
    ['чекпоінт', r.last_checkpoint == null ? 'немає' : `покоління ${r.last_checkpoint}`],
    ['де лежить', r.location === 'runs' ? 'experiments/runs/' : 'лише в архіві'],
    ['архів', r.archived ? 'є (git)' : 'немає'],
  ];
  for (const [k, v] of pairs) {
    const a = document.createElement('span');
    a.textContent = k;
    const b = document.createElement('span');
    b.textContent = v;
    kv.append(a, b);
  }
  kv.children[1].title = r.run_id;

  const hasRows = r.generations_done > 0;
  setLink('#an-csv', exportUrl(r.run_id, 'csv'));
  setLink('#an-pdf', hasRows ? exportUrl(r.run_id, 'pdf') : null);
  setLink('#an-genome', hasRows ? bestGenomeUrl(r.run_id) : null);
  $('#an-genome').download = `${r.run_id}__best_genome.json`;
  syncPng();

  // Продовжувати можна лише локальну копію з чекпоінтом і коли нічого не виконується
  const canContinue = r.location === 'runs' && r.last_checkpoint != null && !busy;
  const gens = $('#an-gens');
  const done = r.state === 'COMPLETED';
  gens.min = r.generations_total;
  gens.value = done ? r.generations_total + 100 : r.generations_total;
  gens.disabled = !canContinue;
  $('#an-continue').disabled = !canContinue;
  $('#an-continue').title = canContinue
    ? `з покоління ${r.last_checkpoint + 1}; той самий run_id, побітово як без перерви`
    : (busy ? 'зараз виконується інший прогін'
      : r.location !== 'runs' ? 'в архіві чекпоінтів немає — продовжувати нема з чого'
        : 'у прогоні немає чекпоінта');
  $('#an-archive').disabled = !(r.location === 'runs' && !r.archived && r.state === 'STOPPED');
  $('#an-hide').textContent = r.hidden ? 'Показати' : 'Приховати';
  $('#an-delete').disabled = !(r.location === 'runs' && !r.archived && r.state === 'STOPPED');
  $('#an-delete').title = r.archived ? 'прогін в архіві (git) — кнопкою не видаляється'
    : 'видалити зупинений прогін з experiments/runs/ (метрики й чекпоінти)';
}

function syncPng() {
  const r = current();
  const chart = $('#an-png-chart').value;
  const ok = r && r.generations_done > 0 && (chart !== 'caught' || hasCaught());
  setLink('#an-png', ok ? exportUrl(r.run_id, 'png', chart) : null);
  if (r) $('#an-png').download = `${r.run_id}__${chart}.png`;
}

async function onContinue() {
  const r = current();
  const generations = Number($('#an-gens').value);
  if (!Number.isInteger(generations) || generations < r.generations_total) {
    message(`поколінь має бути ціле число не менше ${r.generations_total}`, 'warn');
    return;
  }
  message('продовження…');
  try {
    await continueSimulation(r.run_id, generations);
    message(`прогін продовжено до покоління ${generations}; хід видно на вкладці «Симуляція» й тут`, 'ok');
  } catch (e) {
    message(e instanceof ApiError ? e.message : String(e), 'err');
  }
  await refreshRuns();
}

async function onDelete() {
  const r = current();
  const ok = window.confirm(`Видалити прогін ${r.run_id}?\n\nКаталог experiments/runs/${r.run_id} `
    + 'зникне разом з метриками й чекпоінтами. Відмінити це не можна.');
  if (!ok) return;
  try {
    await deleteExperiment(r.run_id);
    message(`прогін ${r.run_id} видалено`, 'ok');
  } catch (e) {
    message(e instanceof ApiError ? e.message : String(e), 'err');
  }
  await refreshRuns();
}

async function onHide() {
  const r = current();
  try {
    await setRunHidden(r.run_id, !r.hidden);
    message(r.hidden ? `прогін ${r.name} знову в списку` : `прогін ${r.name} приховано — його видно у фільтрі «Приховані»`, 'ok');
  } catch (e) {
    message(e instanceof ApiError ? e.message : String(e), 'err');
  }
  await refreshRuns();
}

async function onArchive() {
  const r = current();
  try {
    const res = await archiveExperiment(r.run_id);
    message(`скопійовано в experiments/${res.archived} — не забудьте закомітити`, 'ok');
  } catch (e) {
    message(e instanceof ApiError ? e.message : String(e), 'err');
  }
  await refreshRuns();
}

// --- Графіки ------------------------------------------------------------------------
function hasCaught() {
  return rows.some((r) => r.caught_top10 != null);
}

async function loadRows() {
  rows = [];
  if (selected) {
    try {
      rows = (await getGenerations(selected)).rows;
    } catch { /* прогін без жодного покоління */ }
  }
  drawAll();
  syncPng();
}

function baseOptions(yTitle, extra = {}) {
  const tick = { color: colors.muted, font: { family: 'ui-monospace, monospace', size: 11 } };
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    parsing: false,
    normalized: true,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { labels: { color: colors.text, boxWidth: 14, boxHeight: 3, font: { size: 11 },
                          filter: (item, data) => !data.datasets[item.datasetIndex].helper } },
      tooltip: { filter: (item) => !item.dataset.helper },
    },
    scales: {
      x: { type: 'linear', title: { display: true, text: 'Покоління', color: colors.muted },
           ticks: { ...tick, precision: 0 }, grid: { color: colors.border } },
      y: { title: { display: true, text: yTitle, color: colors.muted },
           ticks: tick, grid: { color: colors.border }, ...extra },
    },
  };
}

const line = (label, data, color, more = {}) => ({
  label, data, borderColor: color, backgroundColor: color,
  borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0, ...more,
});

function points(key, scale = 1) {
  return rows.map((r) => ({ x: r.generation, y: r[key] == null ? null : r[key] * scale }));
}

function movingMean(pts, w) {
  return pts.map((p, i) => {
    const win = pts.slice(Math.max(0, i - w + 1), i + 1).filter((q) => q.y != null);
    return { x: p.x, y: win.length ? win.reduce((a, q) => a + q.y, 0) / win.length : null };
  });
}

function fitnessData() {
  const lo = rows.map((r) => ({ x: r.generation, y: r.avg_fitness - r.std_fitness }));
  const hi = rows.map((r) => ({ x: r.generation, y: r.avg_fitness + r.std_fitness }));
  return [
    line('середній ± σ', hi, 'transparent', { helper: true, fill: '+1',
      backgroundColor: hexAlpha(colors.accent2, 0.22), borderWidth: 0, pointHoverRadius: 0 }),
    line('', lo, 'transparent', { helper: true, borderWidth: 0, pointHoverRadius: 0 }),
    line('середній по популяції', points('avg_fitness'), colors.accent2),
    line('найкращий у поколінні', points('best_fitness'), colors.accent),
  ];
}

/** Стоси зі знаком формули: S, D — над нулем, штрафи — під нулем; межі рахуємо самі. */
function componentsData(weights) {
  const datasets = [];
  for (const sign of [1, -1]) {
    let base = rows.map(() => 0);
    let first = true;
    Object.keys(TERMS).forEach((key, slot) => {
      const [field, s] = TERMS[key];
      if (s !== sign) return;
      const w = weights?.[field] ?? 0;
      const contrib = rows.map((r) => s * w * r.best_components[key]);
      const top = base.map((b, i) => b + contrib[i]);
      datasets.push(line(LABELS[key], rows.map((r, i) => ({ x: r.generation, y: top[i] })),
        SERIES[slot], {
          fill: first ? 'origin' : '-1', borderWidth: 0,
          backgroundColor: hexAlpha(SERIES[slot], 0.8), contrib,
        }));
      base = top;
      first = false;
    });
  }
  datasets.push(line('сума = найкращий F', points('best_fitness'), colors.text, { borderWidth: 1.5 }));
  return datasets;
}

function caughtData() {
  const out = [];
  [['caught_top10', 'топ-10 за F'], ['caught_all', 'уся популяція']].forEach(([key, label], i) => {
    const raw = points(key, 100);
    out.push(line(`${label}, сире`, raw, hexAlpha(SERIES[i], 0.3), { borderWidth: 1, helper: true }));
    out.push(line(`${label} (ковзне, ${SMOOTH} пок.)`, movingMean(raw, SMOOTH), SERIES[i]));
  });
  return out;
}

function hexAlpha(color, a) {
  const m = /^#([0-9a-f]{6})$/i.exec(color.trim());
  if (!m) return color;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

function makeChart(id, datasets, options) {
  charts[id]?.destroy();
  charts[id] = new window.Chart($(`#${id}`), { type: 'line', data: { datasets }, options });
}

function drawAll() {
  const empty = $('#an-empty');
  if (!window.Chart) {
    empty.hidden = false;
    empty.textContent = 'Chart.js не завантажився (немає доступу до cdnjs) — графіки доступні через «PDF графіків».';
    return;
  }
  if (!$('#tab-analytics').classList.contains('active')) return;   // приховане полотно не має розміру
  const weights = current()?.fitness_weights;
  empty.hidden = rows.length > 0;
  empty.textContent = selected ? 'у прогоні ще немає поколінь' : '';

  makeChart('an-fitness', fitnessData(), baseOptions('Фітнес F'));
  makeChart('an-diversity', [line('різноманіття', points('diversity'), colors.warn)],
    { ...baseOptions('сер. L2 / √G', { min: 0 }),
      plugins: { ...baseOptions('').plugins, legend: { display: false } } });

  const comp = baseOptions('Внесок w·компонент');
  comp.plugins.tooltip.callbacks = {
    label: (ctx) => {
      const ds = ctx.dataset;
      const v = ds.contrib ? ds.contrib[ctx.dataIndex] : ctx.parsed.y;
      return `${ds.label}: ${v.toFixed(3)}`;
    },
  };
  makeChart('an-components', componentsData(weights), comp);

  const caughtCard = $('#an-caught').closest('.chart-card');
  caughtCard.hidden = !hasCaught();
  if (hasCaught()) makeChart('an-caught', caughtData(), baseOptions('Спіймано, % епізодів', { min: 0, max: 100 }));
}

/** Нове покоління вибраного прогону — дописати точку без перемальовування з нуля. */
function onGeneration(stats) {
  if (stats.run_id !== selected) return;
  if (rows.length && rows.at(-1).generation >= stats.generation) return;
  rows.push(stats);
  const r = current();
  if (r) {
    r.generations_done = stats.generation;
    if (r.best_fitness == null || stats.best_fitness > r.best_fitness) {
      r.best_fitness = stats.best_fitness;
      r.best_generation = stats.generation;
    }
    renderRuns();
    renderActions();
  }
  drawAll();
}

// --- Ініціалізація ------------------------------------------------------------------
/** @param {object} opts  @param {object} opts.theme  кольори теми (`themeColors()`) */
export async function initAnalytics({ theme }) {
  colors = theme;
  $('#an-refresh').addEventListener('click', refreshRuns);
  $('#an-continue').addEventListener('click', onContinue);
  $('#an-archive').addEventListener('click', onArchive);
  $('#an-hide').addEventListener('click', onHide);
  $('#an-delete').addEventListener('click', onDelete);
  for (const btn of document.querySelectorAll('#an-filter button')) {
    btn.addEventListener('click', async () => {
      filter = btn.dataset.filter;
      try { localStorage.setItem(FILTER_KEY, filter); } catch { /* лише зручність */ }
      renderFilter();
      await ensureSelection();
      renderRuns();
      renderActions();
    });
  }
  $('#an-png-chart').addEventListener('change', syncPng);

  let refreshTimer = null;
  connect('/ws/metrics', {
    onPacket: (packet) => {
      if (packet.type === 'generation') onGeneration(packet);
      else if (packet.type === 'state') {
        // Стан змінився (старт, продовження, зупинка, архів) — список перечитати
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(refreshRuns, 200);
      }
    },
    onState: (s) => { if (s === 'open' && selected) loadRows(); },
  });
  window.addEventListener('tabchange', (e) => {
    if (e.detail !== 'analytics') return;
    refreshRuns();
    drawAll();
  });
  await refreshRuns();
}
