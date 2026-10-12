/**
 * Процедурні примітиви Canvas 2D (PROJECT_SPEC.md §10.1: жодних растрових
 * зображень). Усі функції малюють у **світових** координатах — виклик має
 * бути обгорнутий у `withWorld()`, тому товщини ліній задаються у світових
 * одиницях (`view.px(n)` переводить пікселі у світові).
 */

/** Кольори теми з CSS-змінних — одне джерело правди (§10.1). */
export function themeColors() {
  const s = getComputedStyle(document.documentElement);
  const get = (n) => s.getPropertyValue(n).trim();
  return {
    bg: get('--bg'), panel: get('--panel'), panel2: get('--panel-2'),
    text: get('--text'), muted: get('--muted'), border: get('--border'),
    accent: get('--accent'), accent2: get('--accent-2'),
    danger: get('--danger'), warn: get('--warn'),
  };
}

/** Трансформація світ→екран: вісь Y світу спрямована вгору. */
export function makeView(cam, cssW, cssH) {
  return {
    scale: cam.scale,
    px: (n) => n / cam.scale,
    toScreen(wx, wy) {
      return { x: (wx - cam.x) * cam.scale + cssW / 2, y: cssH / 2 - (wy - cam.y) * cam.scale };
    },
    toWorld(sx, sy) {
      return { x: (sx - cssW / 2) / cam.scale + cam.x, y: (cssH / 2 - sy) / cam.scale + cam.y };
    },
  };
}

export function withWorld(ctx, cam, cssW, cssH, fn) {
  ctx.save();
  ctx.translate(cssW / 2, cssH / 2);
  ctx.scale(cam.scale, -cam.scale);
  ctx.translate(-cam.x, -cam.y);
  try {
    fn();
  } finally {
    // Без finally один виняток у малюванні лишив би світову трансформацію на
    // стеку назавжди — наступні кадри рендерились би подвійно зміщеними
    ctx.restore();
  }
}

function stroked(ctx, { stroke, fill, lw = 0.02, dash = null, alpha = 1 }) {
  ctx.globalAlpha = alpha;
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = lw;
    ctx.setLineDash(dash ? dash.map((d) => d * lw * 4) : []);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.globalAlpha = 1;
}

export function polygon(ctx, pts, opts = {}) {
  if (pts.length < 2) return;
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  if (opts.close !== false) ctx.closePath();
  stroked(ctx, opts);
}

export function circle(ctx, x, y, r, opts = {}) {
  ctx.beginPath();
  ctx.arc(x, y, Math.max(r, 1e-6), 0, Math.PI * 2);
  stroked(ctx, opts);
}

export function line(ctx, x1, y1, x2, y2, opts = {}) {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  stroked(ctx, { ...opts, fill: null });
}

/** Сегмент кінцівки: прямокутник length×width з округленими торцями. */
export function capsule(ctx, x, y, angle, length, width, opts = {}) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);
  ctx.beginPath();
  const r = Math.min(width / 2, length / 2);
  if (ctx.roundRect) ctx.roundRect(-length / 2, -width / 2, length, width, r);
  else ctx.rect(-length / 2, -width / 2, length, width);
  stroked(ctx, opts);
  ctx.restore();
}

/** Сектор кутового діапазону (рад, CCW у світових координатах). */
export function sector(ctx, x, y, r, a0, a1, opts = {}) {
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.arc(x, y, r, a0, a1, false);
  ctx.closePath();
  stroked(ctx, opts);
}

/** Лише дуга, без «спиць» сектора. */
export function arc(ctx, x, y, r, a0, a1, opts = {}) {
  ctx.beginPath();
  ctx.arc(x, y, r, a0, a1, false);
  stroked(ctx, { ...opts, fill: null });
}

/**
 * Підпис у **екранних** координатах: текст не можна малювати у світовій
 * системі, бо вісь Y там перевернута (літери були б дзеркальні).
 */
export function screenText(ctx, sx, sy, text, opts = {}) {
  const { color = '#fff', size = 11, align = 'center', baseline = 'middle', bg = null } = opts;
  ctx.save();
  ctx.font = `${size}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.textAlign = align;
  ctx.textBaseline = baseline;
  if (bg) {
    const w = ctx.measureText(text).width;
    const pad = 3;
    const x0 = align === 'center' ? sx - w / 2 : sx;
    ctx.fillStyle = bg;
    ctx.fillRect(x0 - pad, sy - size / 2 - pad, w + pad * 2, size + pad * 2);
  }
  ctx.fillStyle = color;
  ctx.fillText(text, sx, sy);
  ctx.restore();
}

/** Аура хижака: радіальний градієнт --danger → прозорий (§10.2). */
export function aura(ctx, x, y, r, color) {
  const g = ctx.createRadialGradient(x, y, 0, x, y, r);
  g.addColorStop(0, hexAlpha(color, 0.38));
  g.addColorStop(0.65, hexAlpha(color, 0.12));
  g.addColorStop(1, hexAlpha(color, 0));
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = g;
  ctx.fill();
}

export function hexAlpha(hex, a) {
  const h = hex.replace('#', '');
  const v = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const n = parseInt(v, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

/** Сітка 1 од. (дрібна, лише на достатньому зумі) + 5 од. (основна). */
export function grid(ctx, view, cam, cssW, cssH, colors) {
  const tl = view.toWorld(0, 0);
  const br = view.toWorld(cssW, cssH);
  const draw = (step, color, lwPx) => {
    const x0 = Math.floor(tl.x / step) * step;
    const x1 = Math.ceil(br.x / step) * step;
    const y0 = Math.floor(br.y / step) * step;
    const y1 = Math.ceil(tl.y / step) * step;
    ctx.beginPath();
    for (let x = x0; x <= x1; x += step) { ctx.moveTo(x, y0); ctx.lineTo(x, y1); }
    for (let y = y0; y <= y1; y += step) { ctx.moveTo(x0, y); ctx.lineTo(x1, y); }
    ctx.strokeStyle = color;
    ctx.lineWidth = view.px(lwPx);
    ctx.stroke();
  };
  if (cam.scale >= 12) draw(1, hexAlpha(colors.border, 0.55), 1);
  draw(5, hexAlpha(colors.border, 1), 1);
  // Осі світу
  line(ctx, tl.x, 0, br.x, 0, { stroke: hexAlpha(colors.muted, 0.5), lw: view.px(1) });
  line(ctx, 0, br.y, 0, tl.y, { stroke: hexAlpha(colors.muted, 0.5), lw: view.px(1) });
}
