/**
 * WebSocket-клієнт (PROJECT_SPEC.md §10.4): автоперепідключення з
 * експоненційною затримкою (0.5 → 8 с) і backpressure — зберігається лише
 * **останній** кадр, а малюється він у `requestAnimationFrame`.
 *
 * Навіщо тримати лише останній: сервер шле до 60 пакетів/с і сам тримає чергу
 * глибиною 1 (§9.5). Якщо вкладка була у фоні й rAF не викликався, черга на
 * клієнті перетворилася б на відтворення минулого в сповільненій зйомці.
 */

const RECONNECT_MIN_MS = 500;
const RECONNECT_MAX_MS = 8000;

/** Абсолютний URL каналу з урахуванням https → wss. */
export function wsUrl(path) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${window.location.host}${path}`;
}

/**
 * Відкриває канал і тримає його живим.
 *
 * @param {string} path            шлях каналу, напр. `/ws/live`
 * @param {object} handlers
 * @param {(packet:object)=>void} handlers.onPacket  нетипізований пакет (напр. `scene`)
 * @param {(frame:object)=>void}  handlers.onFrame   найсвіжіший кадр, у темпі rAF
 * @param {(state:string)=>void}  handlers.onState   'open' | 'closed' | 'error'
 */
export function connect(path, { onPacket, onFrame, onState } = {}) {
  let socket = null;
  let closedByUs = false;
  let delay = RECONNECT_MIN_MS;
  let reconnectTimer = null;

  // Остання «гаряча» корисна навантага: кадри перезаписують одна одну
  let pendingFrame = null;
  let rafId = null;

  function pump() {
    rafId = null;
    const frame = pendingFrame;
    pendingFrame = null;
    if (frame) onFrame?.(frame);
  }

  function schedulePump() {
    if (rafId === null) rafId = requestAnimationFrame(pump);
  }

  function open() {
    reconnectTimer = null;
    try {
      socket = new WebSocket(wsUrl(path));
    } catch {
      scheduleReconnect();
      return;
    }

    socket.addEventListener('open', () => {
      delay = RECONNECT_MIN_MS;          // успішне з'єднання скидає затримку
      onState?.('open');
    });

    socket.addEventListener('message', (event) => {
      let packet;
      try {
        packet = JSON.parse(event.data);
      } catch {
        return;                          // сміття в каналі не має валити рендер
      }
      if (packet.type === 'frame') {
        pendingFrame = packet;
        schedulePump();
      } else {
        onPacket?.(packet);
      }
    });

    socket.addEventListener('close', () => {
      onState?.('closed');
      if (!closedByUs) scheduleReconnect();
    });

    // 'error' завжди супроводжується 'close' — перепідключення планує саме він
    socket.addEventListener('error', () => onState?.('error'));
  }

  function scheduleReconnect() {
    if (closedByUs || reconnectTimer !== null) return;
    reconnectTimer = setTimeout(open, delay);
    delay = Math.min(delay * 2, RECONNECT_MAX_MS);
  }

  open();

  return {
    close() {
      closedByUs = true;
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
      if (rafId !== null) cancelAnimationFrame(rafId);
      socket?.close();
    },
    get ready() {
      return socket?.readyState === WebSocket.OPEN;
    },
  };
}
