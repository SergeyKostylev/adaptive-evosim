"""Єдиний stateful-сервіс: стани RUNNING/PAUSED/STOPPED, ГА у фоновому
потоці, публікація подій у asyncio-черги для WebSocket (§1.2). Реалізація — етап 3."""
