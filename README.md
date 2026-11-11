# Adaptive EvoSim

Еволюція плаваючих істот у 2D в'язкому середовищі: генетичний алгоритм +
спрощена гідродинаміка. Магістерське наукове дослідження.

- `PROJECT_SPEC.md` — єдине джерело істини (фізика, контракти, UI).
- `PLAN.md` — порядок виконання етапів і критерії готовності (DoD).
- `docs/thesis/` — журнал дослідження для дипломної: хронологія
  (`research-log.md`) і експерименти етапів (`stage4-experiments.md`).

Реалізовано етапи 1–5: конструктор істоти, хижака й сцени; фізика в'язкої
рідини з живим переглядом; ГА без рендера (multiprocessing); хижак, сенсори й
повна фітнес-функція; аналітика, чекпоінти, продовження прогонів, експорт
графіків. Відхилення від DoD записані в `PLAN.md` і в анотаціях тегів
`stage-2` … `stage-4`.

## Від `git clone` до графіка в дипломі

Потрібні лише Git і Docker (Compose v2).

```bash
git clone https://github.com/SergeyKostylev/adaptive-evosim.git
cd adaptive-evosim
docker compose up --build -d                     # UI → http://localhost:8080
docker compose exec backend python -m app.export.plots dod4-spokes-v07-shared__20261029-1325
open experiments/archive/dod4-spokes-v07-shared__20261029-1325/figures/figures.pdf   # Linux: xdg-open
```

Це графіки вже збереженого прогону з архіву (у git): фітнес, різноманіття,
внесок компонентів фітнесу, частки спійманих — PNG і PDF (білий фон, підписи
українською, dpi 200). Те саме — на вкладці **Аналітика** кнопками експорту.

Власний прогін без UI (прогрес — рядок на покоління), потім його графіки:

```bash
docker compose exec backend python -m app.evolution.ga \
    --config experiments/archive/dod4-spokes-v07-shared__20261029-1325/config.json --generations 50
docker compose exec backend python -m app.export.plots <run_id із першого рядка виводу>
```

Графіки лягають у `experiments/runs/<run_id>/figures/`. Той самий `config.json` і
сід у тому самому Docker-образі дають **побітово той самий** `metrics.csv` (R7).

## Інтерфейс

- **Конструктор** — три сутності, кожна зі своєю бібліотекою: **Істота**
  (тіло й кінцівки), **Хижак** (розмір, швидкості, радіуси) і **Сцена**
  (перешкоди, маршрути патрулювання, точки появи). Усе перетягується мишею;
  кнопка **Тест-заплив** показує істоту з тестовою синусоїдою.
- **Симуляція** — вибір збережених істоти, сцени й хижака, параметри ГА і ваг
  фітнесу, **Старт / Пауза / Продовжити / Стоп**, прогрес-бар із часом, що
  лишився. **Найкраща особина** — рекорд прогону наживо; **Наживо** — найкраща
  особина останнього покоління під час навчання.
- **Аналітика** — усі прогони (локальні й архівні), графіки вибраного (для
  поточного — наживо), експорт CSV / PDF / PNG і найкращого геному,
  **Продовжити** (з чекпоінта або подовжити завершений) і **В архів** (для
  цінного зупиненого прогону).

## Експерименти

| Що | Як |
|---|---|
| Прогін з UI | «Симуляція» → вибрати складові → **Старт** |
| Прогін без UI | `python -m app.evolution.ga --config <json> [--generations N] [--workers N]` |
| Продовжити перерваний / подовжити | «Аналітика» → **Продовжити**, або `python -m app.evolution.ga --resume <run_id> [--generations N]` |
| Графіки | «Аналітика» → **PDF графіків**, або `python -m app.export.plots <run_id> [--out DIR]` |
| Кілька прогонів на одному рисунку | `python -m app.export.compare --out DIR --name X "підпис=run_a,run_b" "підпис2=run_c"` — середнє по сідах і розкид min–max |
| CSV метрик | «Аналітика» → **CSV метрик**, або `python -m app.export.csv_export <run_id> [out.csv]` |
| Чемпіон прогону | `python -m app.export.champion <run_id> [--episodes 40]` — уся фінальна популяція + рекорд грають однакові тестові епізоди → `champion.json` |
| Абляція «зрячий / сліпий» | `python -m app.export.reaction experiments/archive/<run_id> [N=40] [out.json]` — бере чемпіона, якщо він є |

Команди `python -m …` виконуються в контейнері: `docker compose exec backend …`.

**Чекпоінти.** Стан ГА (популяція + стан генератора + рекорд) пишеться в
`checkpoints/gen_NNN.npz` кожні 25 поколінь, на останньому й при зупинці.
Продовжений прогін побітово збігається з неперервним — навіть після аварійного
вбивства процесу (перевірено тестом з `kill -9`).

**Mac засинає — прогін стоїть.** Docker на macOS зупиняється разом із системою.
Для довгого прогону запускайте до старту `caffeinate -dimsu` і не закривайте
кришку на батареї; якщо все ж перервалось — **Продовжити**.

## Де лежать дані

```
experiments/editor/{creatures,predators,presets}/   # бібліотеки сутностей — у git
experiments/editor/last_session.json                # чернетка редактора — поза git
experiments/runs/<run_id>/                          # усі прогони — лише локально
    config.json  metrics.csv  timing.csv  best_genome.json
    checkpoints/gen_NNN.npz   figures/
experiments/archive/<run_id>/                       # завершені прогони — у git
```

Прогін, що пройшов усі покоління, копіюється в архів автоматично (без
чекпоінтів і графіків); застосунок git не викликає — архів треба закомітити.
Каталог даних задає `EVOSIM_DATA_DIR` (у Docker — змонтований `./experiments`).

## Тести

```bash
docker compose exec backend sh -c 'pip install -q pytest httpx && python -m pytest -q'
```

Або локально: `cd backend && pip install -e ".[dev]" && pytest`. Числа фізики
між macOS і Linux-контейнером розходяться в 7-му знаку (libm), тому прогони для
роботи — лише в Docker.

## Без Docker

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
SERVE_STATIC=1 EVOSIM_DATA_DIR=../experiments uvicorn app.main:app --reload   # UI → http://localhost:8000
```
