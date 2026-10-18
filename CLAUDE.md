# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

**Adaptive EvoSim** — evolution of swimming creatures in a 2D viscous medium (genetic algorithm + simplified hydrodynamics); a master's research project.

Two specification documents govern the work (both in Ukrainian):

- `PROJECT_SPEC.md` — the single source of truth: physics formulas, morphology, sensors, GA, fitness, API/WS contracts, UI, Docker, defaults.
- `PLAN.md` — execution order (Stages 1–5). **`PLAN.md` order overrides `PROJECT_SPEC.md` §14 Roadmap** whenever they disagree.

Implementation rule stated in the spec: write code strictly per `PROJECT_SPEC.md`, without inventing math or contracts. Any change to the contracts in §2–9 goes into `PROJECT_SPEC.md` **first**, then into the code.

**Stages 1–3 are implemented.** `stage-3` is tagged with the same kind of annotated deviation as `stage-2`: the «best ×2» criterion is unreachable by construction (`D` saturates) — see the open item at the end of `PLAN.md` §Етап 3.

**Stage 3 (GA, headless)** added: `core/brain.py` (MLP + genome layout), `core/sensors.py` (proprioception + oscillator only), `evolution/genome.py`, `evolution/fitness.py` (`FitnessAccumulator`, `combine`), `evolution/evaluator.py` (spawn-context `Pool`), `evolution/ga.py` (`GeneticAlgorithm`, `run_loop`, CLI), `services/experiment_store.py` (`RunStore`), `services/sim_manager.py` (GA thread + `MetricsHub`), REST `/api/simulation/{start,pause,resume,stop}`, `/api/metrics/generations`, `/api/genome/best`, `WS /ws/metrics`, preview `source: "genome" | "best"`. Frontend: `hud/hud.js` (start/pause/resume/stop, metrics channel, procedural best/avg line) and the **Найкраща особина** button. Tests: `test_genome_ops.py`, `test_fitness.py`, `test_determinism.py`, `test_simulation_api.py`.

**Stage 2 (physics + live view)** added: `core/hydro.py` (scalar `point_force` core + `segment_resultants`; the NumPy `segment_point_forces` is a test-facing wrapper over the same core), `core/creature.py`, `core/obstacles.py`, `core/physics.py`, `core/world.py` (episode facade + `SineController`), predator **bodies only** in `core/predator.py` (FSM is Stage 4), `services/live_stream.py` (`LiveHub`/`PreviewSession`/`PreviewManager`), `POST|DELETE|GET /api/preview` and `WS /ws/live`. Frontend: `ws.js`, live frame drawing in `render/renderer.js` (`showScene`/`showFrame`/`clearLive`, camera follows the creature), live HUD + **Тест-заплив** on the Simulation tab, and a draft **Тест-заплив** button in the editor. Tests: `test_hydro.py`, `test_creature_build.py`, `test_live_preview.py`.

**Stage 1 is implemented and went well past its DoD.** Backend: `config.py`, full `schemas.py`, `POST /api/creature/validate`, `POST /api/predator/validate`, `GET/PUT/DELETE /api/editor/state`, and three libraries under `/api/editor/{creatures,predators,presets}[/{name}]`, all persisted in `experiments/editor/` through `services/editor_store.py`, plus Docker/nginx. Frontend: the three-entity editor, the scene preview on the Simulation tab, and the experiment composer. `export/` and `hud/charts.js` are still docstring-only stubs naming their stage (5). Each stage ends with: green tests → DoD checklist verified → commit tagged `stage-N` (`stage-1` = `c3ca8fc`, `stage-2` = `81cc4bd`, an annotated tag that records the unmet 120k steps/s criterion — `PLAN.md` keeps the criterion unchanged on purpose).

## Commands

```bash
docker compose up --build          # UI :8080 (nginx) → backend :8000; API under /api
docker compose logs backend -f

cd backend && pytest               # all tests
pytest tests/test_schemas.py -k hash            # single test
SERVE_STATIC=1 uvicorn app.main:app --reload    # dev without Docker, serves frontend/ too
```

```bash
EVOSIM_DATA_DIR=<tmpdir> python -m app.evolution.ga --config exp.json [--generations N] [--workers N]   # headless run, prints a line per generation
```

Specified but not yet written: `python -m app.core.world --demo` (Stage 2 leftover), `python -m app.evolution.ga --resume <run_id>` (Stage 5).

Throwaway backend for any manual/UI check (never point one at :8080, see **Data safety**):

```bash
cd backend && EVOSIM_DATA_DIR=<tmpdir> ../.venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 --port 8099 --reload --reload-dir app
```

There is no frontend build step and no npm in the repo (R8: plain ES modules, Canvas 2D, no frameworks) — editing a file under `frontend/` only needs `docker compose up --build frontend` to reach the container.

**The frontend has no in-repo tests.** It was verified with jsdom scripts kept in the session scratchpad (boot `index.html`, stub `getContext`/`ResizeObserver`, point `fetch` at :8099, drive real clicks). They are disposable; if a change needs frontend verification, write a fresh one the same way rather than assuming coverage exists. Two cheap static checks catch most wiring breakage:

```bash
# every id JS looks for must exist in the markup, and no duplicate ids
python3 - <<'EOF'
import re, pathlib
src = "\n".join(p.read_text() for p in pathlib.Path('frontend/js').rglob('*.js'))
ids = re.findall(r'id="([^"]+)"', pathlib.Path('frontend/index.html').read_text())
used = set(re.findall(r'''[$(]\(?['"]#([A-Za-z0-9_-]+)''', src))
print("missing:", sorted(used - set(ids)) or "—")
EOF
for f in $(find frontend/js -name '*.js'); do cp "$f" /tmp/_c.mjs && node --check /tmp/_c.mjs || echo "FAIL $f"; done
```

## Architecture (the parts that span files)

Backend is `backend/app/` with three layers plus services; the full tree is `PROJECT_SPEC.md` §1.1.

- `core/hydro.py` — **formulas only, no Pymunk import**, so drag/lift math is unit-testable standalone. The core is scalar (plain floats), not NumPy — see the invariant below. `core/physics.py` owns the `pymunk.Space` and applies, per segment, the resultant of its sample-point forces plus their torque.
- `core/world.py` — facade for **one episode**: `World(creature_cfg, env_cfg, genome, seed)`, `step()` (physics → sensors → brain → predator → fitness accumulation), `alive`, `t`, `snapshot()`. Everything else composes `World`.
- `evolution/evaluator.py` — the headless hot path: multiprocessing Pool, one `World` per genome per episode. No rendering, no sleeps anywhere in this path.
- `services/sim_manager.py` — the **only stateful service**: RUNNING/PAUSED/STOPPED, GA in a background thread, publishes `generation_done` / `state_changed` to asyncio queues consumed by the WebSockets. Pause happens between generations.
- `frontend/` — plain ES modules + Canvas 2D, no frameworks, no raster images (procedural drawing only), Chart.js from CDN pinned to an exact version. The frontend holds **no physics and no evolution logic** — it edits JSON configs and renders frames/charts.
- **UI language is Ukrainian** (tabs, buttons, hints, server-state labels); contract keys (`attach_angle`, `p_mutation`) appear only in `title` tooltips. Angles are shown in degrees, stored in radians. Keep new UI text and code comments Ukrainian.
- `state.js` owns the mutable `state` (`creature`, `predator`, `predatorName`, `environment`), `buildExperimentConfig()` and the autosave to `/api/editor/state`; `app.js` is only tabs + URL state + status polling + bootstrap order (restore saved state → `initEditor` → `markSnapshotSaved()` → `setReady(true)` → `initSimulationPicker`). **Editor modules must never import `app.js`** — that cycle plus top-level `await` leaves a module uninitialized at runtime (`Cannot access '$' before initialization`).
- Autosave is a serialized chain (`saveChain`), never a drop-if-busy: a change made while a `PUT` is in flight must still reach the server. `stateChanged()` compares the snapshot with the last saved one and stays silent when nothing actually changed, so pure selection clicks don't dirty the project.
- `simulation.js` owns the Simulation tab: three thumbnail pickers (custom dropdowns — a native `<select>` cannot show shapes), resolution of the picks into one `ExperimentConfig`, the scene preview through `render/renderer.js`, and the sanity line (start inside a predator's aura, start outside the world, unfinished routes). Only **saved** entities are offered; the editor draft never enters a run.
- `/api/editor/state` stores the draft **unvalidated** by design (§9.4), so a truncated or hand-edited `last_session.json` can lack `creature.body`. `app.js:restoreSavedState` therefore screens it through `usableCreature()` before it reaches `drawCreature` — without that the editor dies on first paint, before the user sees anything.
- `app.js` keeps the active screen in the URL (`?tab=…&tool=…&scene=…`) via `replaceState`; the editor announces mode changes with the `editortool` event instead of knowing about URLs.
- Editor wiring: `app.js` passes the state to `initEditor`; `editor/editor.js` owns the canvas, camera and the `api` context object (`state`, `sel`, `draft`, `render/refresh/setHint/setTool/changed`) that the three per-tool modules receive. `sel.limbs` is a Set of selected limbs and `sel.limb` the active one; form edits apply to every selected limb (`attach_angle` as a delta). Loading an entity from its library **replaces** `state.creature` / `state.environment` wholesale, so panel handlers must read them lazily through `api.state` — never capture them in a closure at init time.
- `render/shapes.js` holds both the primitives **and** the creature geometry (`segmentPoses`, `bodyPerimeterPoint`, `drawCreatureGhost`, `worldVerts`), so neither `render/renderer.js` nor the live view depends on editor modules; the editor re-exports them for its own callers. `render/renderer.js` draws from a **config object**, not editor state — Stage 2's live view feeds it `FramePacket` frames instead.
- Scene objects are dragged, not retyped: `scene_editor.js` exposes `sceneHandles/findSceneHandle/moveSceneHandle`, `editor.js` drives them from pointer events (snap to 0.5, `Alt` for free placement, hover highlight). A pointerdown that grabs a handle must not fall through to the click handler that creates new objects.
- `render/shapes.js` draws in world coordinates inside `withWorld()` (Y-up, flipped via a negative scale), so line widths are world units: use `view.px(pixels)`. Text cannot be drawn there (it would be mirrored): `drawCreature` returns a label list that `editor.js` renders in a screen-space pass via `screenText`.

Data flow: Editor → `POST /api/creature/validate` → `schemas.py`; `POST /api/simulation/start {ExperimentConfig}` → `sim_manager` → `ga.py` → `evaluator.py` → `world/physics/hydro`; generation stats → `experiment_store` (`metrics.csv`) + `/ws/metrics`; `POST /api/preview` → live 60 fps episode → `/ws/live`.

## Data safety

`experiments/editor/` holds the user's **live** editor data: `last_session.json` (the working state open in their browser, rewritten by autosave) and the three libraries `creatures/`, `predators/`, `presets/`.

The three libraries are **tracked in git** on purpose: hand-built entities are research input, and a gitignored `experiments/editor/` once died together with a deleted worktree. Commit new entities instead of leaving them untracked. GA runs under `experiments/runs/` are tracked too (§11): they are tiny, and R7 holds only within one environment, so a stored `metrics.csv` is the only way to keep the exact numbers after an image/library upgrade. Still ignored: `last_session.json` (autosave churn), `runs/*/checkpoints/`, `runs/*/figures/` and `*.tmp`. Deleting a run is the user's call, same as entity files.

Deleting an entity file is still a real loss of the user's work (git only helps once it is committed). Never bulk-delete or reset those collections, and never point a UI/manual test at the running stack on :8080 — the app autosaves on every edit and will overwrite whatever they have open. Run such checks against a throwaway backend instead:

```bash
EVOSIM_DATA_DIR=<tmpdir> .venv/bin/python -m uvicorn app.main:app --port 8099   # from backend/
```

When cleaning up after a test, delete only the exact names that test created.

## Invariants that are easy to break

- **Determinism is the most important property** (it is a research artifact) — bit-identical **within one environment** only: macOS and the Linux container diverge in the 7th digit of the physics (libm), so thesis runs go through Docker (R7). One `np.random.Generator(PCG64(seed))` per run, episode seeds derived via `SeedSequence.spawn` from `(run_seed, gen, individual_idx, episode_idx)`, fixed `DT`, fixed evaluation order. Same seed must produce a bit-identical `metrics.csv` (`test_determinism.py`).
- **No magic numbers outside `config.py`.** Every constant from §2–8 lives there or comes from the request body.
- Hydro's single source of truth is the **scalar** `hydro.point_force` (plain floats, no NumPy): sample arrays are tiny (`N_s = 3`) and NumPy's per-call overhead dwarfs the arithmetic there — switching to it was an 11× speedup of the headless path. `segment_point_forces` (NumPy-shaped) and `segment_resultants` (per-segment resultant + torque for `physics.py`) both call it; never fork the math.
- `physics.apply_fluid_forces` assigns `body.force`/`body.torque` rather than accumulating: `space.step` zeroes them each step and hydro is the only force source (motors act through constraint impulses). Applying the resultant at the CoM plus `Σ r_k × F_k` is exactly equivalent to applying each point force at its point — but the per-point `F_MAX` clamp must still happen **before** summing.
- The demo sinusoid's phase is per **segment within a limb** (`φ = (π/2)·k`), not per flat joint index: a flat index puts a symmetric limb pair in antiphase, and the creature yaws in place (56 units of path, 1.4 units of displacement) instead of swimming (5.9 units). See §2.4.
- `space.damping` is Pymunk's *per-second* retention, not per-step. Reading `0.98` as per-step would mean 0.09/s and kill all swimming.
- A `TestClient` used **without** `with` spins a fresh event loop per request, so a preview task created by `POST /api/preview` dies immediately and `/ws/live` blocks forever. Websocket tests must use `with TestClient(app) as client:` (see `tests/test_live_preview.py`).
- Fixed `DT = 1/120`; the brain runs every `CONTROL_EVERY = 6` steps (20 Hz) and `motor.rate` is held between control ticks. Motors are driven by `rate` (target angular velocity), never by direct torque.
- Hydro forces are computed from velocities **at the start of the step**, applied, then `space.step(DT)`. Clamps `F_MAX` per point and `|v| ≤ V_MAX` after the step are what keep bad genomes from exploding.
- Swimming works solely through drag anisotropy `C_d_perp ≫ C_d_par`; `g = (0,0)` and there is no water surface — never add buoyancy or a water level.
- Genome is a flat `float32` vector concatenated strictly as `[W1.flat, b1, W2.flat, b2, W3.flat, b3]`; serialized genomes carry `creature_hash` (SHA-256 of canonical morphology JSON) and must be rejected against a different morphology.
- The scene model was extended beyond the original spec and the spec was updated to match: `EnvironmentConfig.spawns` (0–16 population start poses, several of them being the different episode starts of §7.3 — they carry **no** creature reference) and `EnvironmentConfig.predators` (0–8 patrol routes). The legacy single `predator` field is still accepted, folded into `predators[0]` and never serialized back (`Field(exclude=True)` + `_fold_legacy_predator`) — keep that validator and the frontend `migrateEnvironment()` in sync, and note that `migrateEnvironment` must fill `DEFAULTS.predator` into every route or an undefined radius reaches `createRadialGradient` and kills the render. Sensors/fitness take the **nearest** predator (§4.2, §7.1).
- The editor has exactly three entities, edited in isolation: **Істота** (morphology), **Хижак** (`PredatorParams` — size, speeds, radii; no route) and **Сцена** (obstacles, patrol routes, spawn points; sub-tools inside one mode). Each mode renders only its own entity — don't draw world objects in entity modes. Each has its own library under `experiments/editor/` and all three share the dropdown above the canvas: `#creature-lib` serves creatures, predators **or scenes** depending on the mode — it is never hidden. Create-new, delete and the saved list live there for every entity; each entity's own panel keeps only its name field, its save button and its dirty mark. There is **no file export/import** (§10.2): the libraries are the single save channel, and they are in git — a second channel only raises the question of what actually goes into a run.
- A patrol route is **geometry only**: the scene editor writes `{enabled, waypoints}` and nothing else, because the path belongs to that particular world while the predator is reusable. The Simulation tab injects the chosen predator's params into every route and records its name in `preset`; the remaining `PredatorConfig` fields in a stored scene are therefore just schema defaults. While laying out a route the canvas previews radii from the currently edited predator entity.
- `simulation.js` resolves the three picks into one `ExperimentConfig` with **copies, not names** — a run must stay reproducible after the libraries change (§11).
- Saving is per entity: the creature button writes only to `creatures/`, the predator button only to `predators/`, the scene only to `presets/`. `EditorState` carries `predator_name` and `scene_name` so the dropdown still shows the right entity after a reload and the dirty mark has something to compare against. The `last_session.json` autosave is a separate mechanism and must never be confused with them; each entity panel shows whether its current state differs from the library copy.
- Each editor mode draws only its own entity. Tool ids are exactly `creature | predator | scene`, with sub-tools `obstacle | route | spawn` inside the scene — calling `setTool('obstacle')` (a tool that stopped existing when the editor was reorganized) silently drops the app into a non-existent mode and hides every panel; use `setSceneTool`.
- A `<canvas>` with no CSS size lays out at 300×150. Thumbnails must get their size from CSS (`.thumb`), not only from the drawing code, or an empty one blows up its row.
- Preset and creature names become filenames: `editor_store._safe_path()` (via `item_path`/`preset_path`) is the single gatekeeper (no separators, control chars, `.`/`..`, leading dot, plus a post-resolve containment check). Never build a preset path anywhere else.
- `N_in = N_rays·2 + 3 + N_joints·3 + 5 + 2`, counted only over sensor groups actually enabled (`config.SENSE_RADAR`/`SENSE_AURA`, both `False` until Stage 4 → `N_in = 3·N_joints + 7`). Flipping a flag changes every genome size, so stage-3 genomes are rejected afterwards (`genome.from_json` → `GenomeMismatch`, preview answers 422). `n_joints` = total segment count (one joint per segment, body↔segment₀ included).
- Spec §9.1's example response (`"genome_size": 1330` for `n_joints=2, n_in=40`) contradicts the §5 formula, which gives 962. The code follows the **formula** (`config.genome_size`), asserted in `tests/test_api_contracts.py` with the sensor flags monkeypatched on.
- Fitness terms are staged the same way: `config.FITNESS_COMPONENTS` (Stage 3: `D`, `Pas`, `E_n`) decides what enters `F`; inactive terms are reported as `0`, never as their "true" value — otherwise `S ≡ 1` would silently add `w_surv` to every number.
- Episode seeds come from `SeedSequence(run_seed, spawn_key=(gen, i, e))` — identical to the nested `spawn` chain, cheaper. The GA generator is touched only by init, diversity sampling and reproduction, never by evaluation, so the worker count cannot change results (`test_worker_count_does_not_change_results`). Episode `e` starts at `spawns[e % len]`.
- `metrics.csv` holds only deterministic columns; `eval_seconds`/`sim_fps_equivalent` go to `timing.csv` and `run_id` is the directory name (§9.6). Putting wall-clock time back into `metrics.csv` breaks the bit-identical guarantee.
- The evaluator pool uses the `spawn` context even on Linux: forking the uvicorn process (event loop + GA thread) can inherit held locks. `sim_manager.stop()` cancels mid-generation (pool terminated, partial generation not written); pause waits for the current generation.
- Catching the creature ends the episode and costs it `S` and `D` — do **not** add an extra one-off penalty (keeps the early-evolution gradient alive).
- `/ws/live`: static obstacles, world bounds and waypoints are sent **once** in the `scene` packet, never in frames; server keeps a queue depth of 1 and the client renders only the newest frame.
- `metrics.csv` is append-only after each generation (crash-safe); a new `start` with the same `name` creates a new `run_id` and never overwrites an existing run.
- Export figures for the thesis are matplotlib print style: white background, Ukrainian axis labels, `dpi=200`, PNG **and** PDF.
