# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

**Adaptive EvoSim** — evolution of swimming creatures in a 2D viscous medium (genetic algorithm + simplified hydrodynamics); a master's research project.

Two specification documents govern the work (both in Ukrainian):

- `PROJECT_SPEC.md` — the single source of truth: physics formulas, morphology, sensors, GA, fitness, API/WS contracts, UI, Docker, defaults.
- `PLAN.md` — execution order (Stages 1–5). **`PLAN.md` order overrides `PROJECT_SPEC.md` §14 Roadmap** whenever they disagree.

Implementation rule stated in the spec: write code strictly per `PROJECT_SPEC.md`, without inventing math or contracts. Any change to the contracts in §2–9 goes into `PROJECT_SPEC.md` **first**, then into the code.

**Stage 1 is implemented** (skeleton + entity constructor): `config.py`, full `schemas.py`, `POST /api/creature/validate`, `GET/PUT/DELETE /api/editor/state` plus `/api/editor/presets[/{name}]` (named scenes) and `/api/editor/creatures[/{name}]` (creature library, morphology validated against §9.1), all persisted under `experiments/editor/` via `services/editor_store.py`, the `GET /api/simulation/status` stub, Docker/nginx, and the whole canvas editor. Everything in `core/`, `evolution/`, `services/`, `export/` other than the above is a docstring-only stub naming its stage — physics starts at Stage 2. Each stage ends with: green tests → DoD checklist verified → commit tagged `stage-N`.

## Commands

```bash
docker compose up --build          # UI :8080 (nginx) → backend :8000; API under /api
docker compose logs backend -f

cd backend && pytest               # all tests
pytest tests/test_schemas.py -k hash            # single test
SERVE_STATIC=1 uvicorn app.main:app --reload    # dev without Docker, serves frontend/ too
```

Planned CLI entry points, specified but not yet written: `python -m app.core.world --demo` (Stage 2), `python -m app.evolution.ga --config <json>` / `--resume <run_id>` (Stages 3/5).

There is no frontend build step and no npm in the repo (R8: plain ES modules, Canvas 2D, no frameworks) — editing a file under `frontend/` only needs a `docker compose up --build frontend` to be reflected in the container.

## Architecture (the parts that span files)

Backend is `backend/app/` with three layers plus services; the full tree is `PROJECT_SPEC.md` §1.1.

- `core/hydro.py` — **pure NumPy functions only**, no Pymunk import in the formulas, so drag/lift math is unit-testable standalone. `core/physics.py` owns the `pymunk.Space` and applies hydro forces per sample point via `apply_force_at_world_point`.
- `core/world.py` — facade for **one episode**: `World(creature_cfg, env_cfg, genome, seed)`, `step()` (physics → sensors → brain → predator → fitness accumulation), `alive`, `t`, `snapshot()`. Everything else composes `World`.
- `evolution/evaluator.py` — the headless hot path: multiprocessing Pool, one `World` per genome per episode. No rendering, no sleeps anywhere in this path.
- `services/sim_manager.py` — the **only stateful service**: RUNNING/PAUSED/STOPPED, GA in a background thread, publishes `generation_done` / `state_changed` to asyncio queues consumed by the WebSockets. Pause happens between generations.
- `frontend/` — plain ES modules + Canvas 2D, no frameworks, no raster images (procedural drawing only), Chart.js from CDN pinned to an exact version. The frontend holds **no physics and no evolution logic** — it edits JSON configs and renders frames/charts.
- **UI language is Ukrainian** (tabs, buttons, hints, server-state labels); contract keys (`attach_angle`, `p_mutation`) appear only in `title` tooltips. Angles are shown in degrees, stored in radians. Keep new UI text and code comments Ukrainian.
- `state.js` owns the mutable `state` (`creature`, `environment`), `buildExperimentConfig()` and the debounced autosave to `/api/editor/state`; `app.js` is only tabs + status polling + bootstrap order (restore saved scene → `initEditor` → `setReady(true)`). **Editor modules must never import `app.js`** — that cycle plus top-level `await` leaves a module uninitialized at runtime (`Cannot access '$' before initialization`).
- Editor wiring: `state.js` owns the single mutable `state` (`creature`, `environment`) and `app.js` passes it to `initEditor`; `editor/editor.js` owns the canvas, camera and the `api` context object (`state`, `sel`, `draft`, `render/refresh/setHint/setTool/changed`) that the three per-tool modules receive. `sel.limbs` is a Set of selected limbs and `sel.limb` the active one; form edits apply to every selected limb (`attach_angle` as a delta). Load-JSON **replaces** `state.creature` / `state.environment`, so panel handlers must read them lazily through `api.state` — never capture them in a closure at init time.
- `render/shapes.js` draws in world coordinates inside `withWorld()` (Y-up, flipped via a negative scale), so line widths are world units: use `view.px(pixels)`. Text cannot be drawn there (it would be mirrored): `drawCreature` returns a label list that `editor.js` renders in a screen-space pass via `screenText`.

Data flow: Editor → `POST /api/creature/validate` → `schemas.py`; `POST /api/simulation/start {ExperimentConfig}` → `sim_manager` → `ga.py` → `evaluator.py` → `world/physics/hydro`; generation stats → `experiment_store` (`metrics.csv`) + `/ws/metrics`; `POST /api/preview` → live 60 fps episode → `/ws/live`.

## Data safety

`experiments/editor/` holds the user's **live** editor data: `last_session.json` (the scene currently open in their browser, rewritten by autosave), `creatures/` (their creature library) and `presets/` (their saved scenes). It is gitignored, so anything deleted there is gone for good. Never bulk-delete or reset those collections, and never point a UI/manual test at the running stack on :8080 — the app autosaves on every edit and will overwrite whatever they have open. Run such checks against a throwaway backend instead:

```bash
EVOSIM_DATA_DIR=<tmpdir> .venv/bin/python -m uvicorn app.main:app --port 8099   # from backend/
```

When cleaning up after a test, delete only the exact names that test created.

## Invariants that are easy to break

- **Determinism is the most important property** (it is a research artifact): one `np.random.Generator(PCG64(seed))` per run, episode seeds derived via `SeedSequence.spawn` from `(run_seed, gen, individual_idx, episode_idx)`, fixed `DT`, fixed evaluation order. Same seed must produce a bit-identical `metrics.csv` (`test_determinism.py`).
- **No magic numbers outside `config.py`.** Every constant from §2–8 lives there or comes from the request body.
- Fixed `DT = 1/120`; the brain runs every `CONTROL_EVERY = 6` steps (20 Hz) and `motor.rate` is held between control ticks. Motors are driven by `rate` (target angular velocity), never by direct torque.
- Hydro forces are computed from velocities **at the start of the step**, applied, then `space.step(DT)`. Clamps `F_MAX` per point and `|v| ≤ V_MAX` after the step are what keep bad genomes from exploding.
- Swimming works solely through drag anisotropy `C_d_perp ≫ C_d_par`; `g = (0,0)` and there is no water surface — never add buoyancy or a water level.
- Genome is a flat `float32` vector concatenated strictly as `[W1.flat, b1, W2.flat, b2, W3.flat, b3]`; serialized genomes carry `creature_hash` (SHA-256 of canonical morphology JSON) and must be rejected against a different morphology.
- The scene model was extended beyond the original spec and the spec was updated to match: `EnvironmentConfig.spawns` (0–16 creature placements; `spawns[0]` is where the evolving creature starts, the rest are scene dressing) and `EnvironmentConfig.predators` (0–8). The legacy single `predator` field is still accepted and folded into `predators[0]`, and is never serialized back — keep both the Pydantic `_fold_legacy_predator` validator and the frontend `migrateEnvironment()` in sync. Sensors/fitness take the **nearest** predator (§4.2, §7.1).
- The editor has exactly three entities, edited in isolation: **Істота** (morphology), **Хижак** (`PredatorParams` — size, speeds, radii; no route) and **Сцена** (obstacles, patrol routes, spawn points; sub-tools inside one mode). Each mode renders only its own entity — don't draw world objects in entity modes. Each has its own library under `experiments/editor/` and its own dropdown above the canvas (`#creature-lib` serves creatures or predators depending on the mode, hidden in scene mode).
- A patrol route lives in the scene (`EnvironmentConfig.predators[]`: params copy + `waypoints` + `preset` naming the library predator it came from), because the route is geometry of that particular world. `spawns[]` carry no creature reference: the GA evaluates a population of `ExperimentConfig.creature`, and several spawn points are the different episode starts of §7.3.
- `simulation.js` resolves the three picks into one `ExperimentConfig` with **copies, not names** — a run must stay reproducible after the libraries change (§11).
- Preset and creature names become filenames: `editor_store._safe_path()` (via `item_path`/`preset_path`) is the single gatekeeper (no separators, control chars, `.`/`..`, leading dot, plus a post-resolve containment check). Never build a preset path anywhere else.
- `N_in = N_rays·2 + 3 + N_joints·3 + 5 + 2`, counted only over sensor groups actually enabled at the current stage (Stage 3 has proprioception + oscillator only). `n_joints` = total segment count (one joint per segment, body↔segment₀ included).
- Spec §9.1's example response (`"genome_size": 1330` for `n_joints=2, n_in=40`) contradicts the §5 formula, which gives 962. The code follows the **formula** (`config.genome_size`), asserted in `tests/test_api_contracts.py`.
- Catching the creature ends the episode and costs it `S` and `D` — do **not** add an extra one-off penalty (keeps the early-evolution gradient alive).
- `/ws/live`: static obstacles, world bounds and waypoints are sent **once** in the `scene` packet, never in frames; server keeps a queue depth of 1 and the client renders only the newest frame.
- `metrics.csv` is append-only after each generation (crash-safe); a new `start` with the same `name` creates a new `run_id` and never overwrites an existing run.
- Export figures for the thesis are matplotlib print style: white background, Ukrainian axis labels, `dpi=200`, PNG **and** PDF.
