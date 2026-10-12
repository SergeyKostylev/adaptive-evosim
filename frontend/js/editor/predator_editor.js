/**
 * Режим «Хижак» — редагування **сутності** (PROJECT_SPEC.md §8.1): розмір тіла,
 * швидкості патруля й погоні, радіуси виявлення / аури / спіймання.
 *
 * Маршруту тут немає: він — геометрія конкретної сцени, тому живе в
 * `EnvironmentConfig.predators[].waypoints` і редагується в режимі «Сцена».
 * Збережені набори параметрів лежать у бібліотеці `/api/editor/predators`.
 */

import { DEFAULTS, clone } from '../state.js';
import { circle, line, aura, hexAlpha } from '../render/shapes.js';

const $ = (s) => document.querySelector(s);

export const PARAM_FIELDS = [
  ['#pred-rbody', '#pred-rbody-v', 'r_body'],
  ['#pred-vpatrol', '#pred-vpatrol-v', 'v_patrol'],
  ['#pred-vchase', '#pred-vchase-v', 'v_chase'],
  ['#pred-rdetect', '#pred-rdetect-v', 'r_detect'],
  ['#pred-raura', '#pred-raura-v', 'r_aura'],
  ['#pred-rcatch', '#pred-rcatch-v', 'r_catch'],
];

export const radiiOrderOk = (pred) =>
  pred.r_catch <= pred.r_aura && pred.r_aura <= pred.r_detect;

/** Параметри без маршруту — саме вони зберігаються в бібліотеку. */
export function predatorParams(pred) {
  const out = { enabled: pred.enabled !== false };
  for (const [, , field] of PARAM_FIELDS) out[field] = pred[field] ?? DEFAULTS.predator[field];
  return out;
}

/** Діаметр поруч із радіусом: «розмір» зазвичай мислять саме так. */
function formatValue(field, value) {
  const v = Number(value);
  return field === 'r_aura' || field === 'r_body'
    ? `${v.toFixed(2)} (⌀${(v * 2).toFixed(1)})`
    : v.toFixed(2);
}

// --- Прев'ю сутності --------------------------------------------------------
/** Хижак малюється в центрі координат: у цьому режимі світу ще немає. */
export function drawPredatorEntity(ctx, view, api, colors) {
  const pred = api.state.predator;
  const lw = view.px(1.8);
  const dim = pred.enabled === false ? 0.4 : 1;

  if (pred.enabled !== false) aura(ctx, 0, 0, pred.r_aura, colors.danger);
  circle(ctx, 0, 0, pred.r_detect, { stroke: hexAlpha(colors.danger, 0.45 * dim), lw, dash: [2, 1] });
  circle(ctx, 0, 0, pred.r_aura, { stroke: hexAlpha(colors.danger, 0.7 * dim), lw });
  circle(ctx, 0, 0, pred.r_catch, { stroke: hexAlpha(colors.danger, 0.9 * dim), lw, dash: [1, 1] });
  circle(ctx, 0, 0, pred.r_body, {
    fill: hexAlpha(colors.danger, 0.75 * dim), stroke: colors.danger, lw,
  });

  // Напрямок руху та масштаб швидкостей — стрілки завдовжки v (од./с)
  const arrow = (len, color, dashed) => {
    line(ctx, 0, 0, len, 0, { stroke: color, lw, dash: dashed ? [1, 1] : null });
    line(ctx, len, 0, len - 0.3, 0.2, { stroke: color, lw });
    line(ctx, len, 0, len - 0.3, -0.2, { stroke: color, lw });
  };
  arrow(pred.v_patrol, hexAlpha(colors.warn, 0.9 * dim), true);
  arrow(pred.v_chase, hexAlpha(colors.danger, 0.9 * dim), false);

  return [
    { x: 0, y: pred.r_body + 0.4, text: api.state.predatorName || 'хижак',
      color: colors.danger, size: 12, bg: hexAlpha(colors.bg, 0.7) },
    { x: pred.v_chase, y: 0.45, text: `погоня ${pred.v_chase.toFixed(1)}`,
      color: hexAlpha(colors.danger, 0.9), size: 11, bg: hexAlpha(colors.bg, 0.7) },
    { x: pred.v_patrol, y: -0.45, text: `патруль ${pred.v_patrol.toFixed(1)}`,
      color: hexAlpha(colors.warn, 0.9), size: 11, bg: hexAlpha(colors.bg, 0.7) },
    { x: 0, y: pred.r_aura + 0.35, text: `аура ⌀${(pred.r_aura * 2).toFixed(1)}`,
      color: hexAlpha(colors.danger, 0.8), size: 11, bg: hexAlpha(colors.bg, 0.7) },
    { x: 0, y: pred.r_detect + 0.35, text: `виявлення ⌀${(pred.r_detect * 2).toFixed(1)}`,
      color: hexAlpha(colors.muted, 0.9), size: 11, bg: hexAlpha(colors.bg, 0.7) },
  ];
}

/** Мініатюра хижака для списку бібліотеки: концентричні радіуси. */
export function renderPredatorThumb(canvas, params, colors, { size = 30 } = {}) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(size * dpr);
  canvas.height = Math.round(size * dpr);
  canvas.style.width = `${size}px`;
  canvas.style.height = `${size}px`;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size, size);
  if (!params) return;

  const reach = Math.max(params.r_detect ?? 8, 0.5);
  const scale = (size / 2 - 1.5) / reach;
  ctx.translate(size / 2, size / 2);
  ctx.scale(scale, -scale);
  const lw = 1.4 / scale;
  circle(ctx, 0, 0, params.r_detect, { stroke: hexAlpha(colors.danger, 0.4), lw, dash: [2, 1] });
  circle(ctx, 0, 0, params.r_aura, { stroke: hexAlpha(colors.danger, 0.7), lw });
  circle(ctx, 0, 0, params.r_body, {
    fill: hexAlpha(colors.danger, 0.8), stroke: colors.danger, lw,
  });
}

// --- Панель -----------------------------------------------------------------
export function initPredatorPanel(api) {
  const pred = () => api.state.predator;

  $('#pred-enabled').addEventListener('change', (e) => {
    pred().enabled = e.target.checked;
    api.refresh();
  });

  $('#pred-name').addEventListener('input', (e) => {
    api.state.predatorName = e.target.value.trim() || 'хижак';
    api.render();
    api.changed();
  });

  for (const [inputSel, labelSel, field] of PARAM_FIELDS) {
    $(inputSel).addEventListener('input', (e) => {
      pred()[field] = Number(e.target.value);
      $(labelSel).textContent = formatValue(field, pred()[field]);
      warn(api);
      api.render();
      api.changed();
    });
    $(inputSel).addEventListener('change', () => api.refresh());
  }
}

function warn(api) {
  const el = $('#pred-warn');
  if (!el) return;
  const ok = radiiOrderOk(api.state.predator);
  el.className = ok ? 'small muted' : 'small err';
  el.textContent = ok
    ? 'Спіймання ⌀' + (api.state.predator.r_catch * 2).toFixed(1)
      + ' · аура ⌀' + (api.state.predator.r_aura * 2).toFixed(1)
      + ' · виявлення ⌀' + (api.state.predator.r_detect * 2).toFixed(1)
    : 'Порушено r_catch ≤ r_aura ≤ r_detect — сервер відхилить такого хижака';
}

export function syncPredatorPanel(api) {
  const pred = api.state.predator;
  $('#pred-enabled').checked = pred.enabled !== false;
  $('#pred-name').value = api.state.predatorName ?? '';
  for (const [inputSel, labelSel, field] of PARAM_FIELDS) {
    const value = pred[field] ?? DEFAULTS.predator[field];
    $(inputSel).value = value;
    $(labelSel).textContent = formatValue(field, value);
  }
  warn(api);
}

/** Параметри з бібліотеки → у редактор сутності. */
export function applyPredatorParams(api, name, params) {
  api.state.predator = { ...clone(DEFAULTS.predator), ...params };
  delete api.state.predator.waypoints;
  api.state.predatorName = name;
}
