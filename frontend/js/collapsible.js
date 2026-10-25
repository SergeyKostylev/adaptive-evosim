/**
 * Згортання панелей: група `.group[data-collapsible="<ключ>"]` ховає все,
 * крім заголовка, за кліком по ньому (стрілочка в заголовку малюється CSS).
 * Поля згорнутої панелі лишаються в DOM, тож конфіг експерименту читається
 * з них як завжди. Стан запам'ятовується у localStorage за ключем панелі.
 */

const STORAGE_KEY = 'evosim.collapsed';

function load() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; } catch { return {}; }
}

function save(map) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(map)); } catch { /* приватний режим тощо */ }
}

export function initCollapsibles(root = document) {
  const remembered = load();
  for (const group of root.querySelectorAll('.group[data-collapsible]')) {
    const head = group.querySelector(':scope > h3');
    if (!head) continue;
    const key = group.dataset.collapsible;

    const apply = (collapsed) => {
      group.classList.toggle('collapsed', collapsed);
      head.setAttribute('aria-expanded', String(!collapsed));
    };
    apply(remembered[key] === true);

    head.setAttribute('role', 'button');
    head.tabIndex = 0;
    if (!head.title) head.title = 'Згорнути / розгорнути';

    const toggle = () => {
      const collapsed = !group.classList.contains('collapsed');
      apply(collapsed);
      remembered[key] = collapsed;
      save(remembered);
    };
    head.addEventListener('click', toggle);
    head.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
  }
}
