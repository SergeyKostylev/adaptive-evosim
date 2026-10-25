# Adaptive EvoSim — зручні команди для роботи з проєктом.
# Запуск: `make` або `make up`.

.DEFAULT_GOAL := up

## Підняти весь стек у Docker (UI на http://localhost:8080, API під /api)
.PHONY: up
up:
	docker compose up --build
