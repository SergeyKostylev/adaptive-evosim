"""Геном і оператори ГА (§5, §6)."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from app import config as C
from app.core.brain import Brain, bias_mask, layer_shapes
from app.evolution import genome as G
from app.evolution.ga import GeneticAlgorithm
from app.schemas import CreatureConfig, ExperimentConfig


def rng(seed: int = 0) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(seed))


@pytest.fixture
def creature(demo_creature) -> CreatureConfig:
    return CreatureConfig.model_validate(demo_creature)


def test_genome_layout_matches_formula(creature):
    """`[W1.flat, b1, W2.flat, b2, W3.flat, b3]` дає рівно G із §5."""
    n_in, n_j = creature.n_in, creature.n_joints
    total = sum(r * c + r for r, c in layer_shapes(n_in, n_j))
    assert total == C.genome_size(n_in, n_j) == creature.genome_size
    mask = bias_mask(n_in, n_j)
    assert mask.size == total
    assert mask.sum() == C.HIDDEN_1 + C.HIDDEN_2 + n_j


def test_init_weights_gaussian_biases_zero(creature):
    g = G.init_genome(creature.n_in, creature.n_joints, rng())
    assert g.dtype == np.float32
    mask = bias_mask(creature.n_in, creature.n_joints)
    assert np.all(g[mask] == 0.0)
    assert abs(float(g[~mask].std()) - C.GENOME_INIT_SIGMA) < 0.05


def test_brain_unpacks_in_spec_order(creature):
    """Кожен шар читається зі свого місця геному: `b3` — останні N_joints чисел."""
    n_in, n_j = creature.n_in, creature.n_joints
    g = np.zeros(C.genome_size(n_in, n_j), dtype=np.float32)
    g[-n_j:] = np.linspace(-1, 1, n_j)             # лише b3
    u = Brain(g, n_in, n_j).forward(np.ones(n_in))
    assert np.allclose(u, np.tanh(np.linspace(-1, 1, n_j)))

    g = np.zeros_like(g)
    g[0] = 1.0                                      # W1[0, 0]
    brain = Brain(g, n_in, n_j)
    assert brain.w1[0, 0] == 1.0 and brain.w1.sum() == 1.0


def test_brain_output_bounded_and_deterministic(creature):
    g = G.init_genome(creature.n_in, creature.n_joints, rng(3)) * 10
    brain = Brain(g, creature.n_in, creature.n_joints)
    x = rng(4).normal(0, 5, creature.n_in)
    u = brain.forward(x)
    assert u.shape == (creature.n_joints,)
    assert np.all(np.abs(u) <= 1.0)
    assert np.array_equal(u, brain.forward(x.copy()))


def test_brain_rejects_wrong_size(creature):
    with pytest.raises(ValueError):
        Brain(np.zeros(10, dtype=np.float32), creature.n_in, creature.n_joints)


def test_uniform_crossover_takes_each_gene_from_a_parent():
    a = np.zeros(10_000, dtype=np.float32)
    b = np.ones(10_000, dtype=np.float32)
    child = G.uniform_crossover(a, b, rng())
    assert set(np.unique(child)) <= {0.0, 1.0}
    assert abs(child.mean() - C.GA_P_SWAP) < 0.02


def test_mutation_rate_sigma_and_clamp():
    g = np.zeros(100_000, dtype=np.float32)
    G.mutate(g, rng(), p_mutation=0.05, sigma=0.15)
    changed = g != 0
    assert abs(changed.mean() - 0.05) < 0.005
    assert abs(float(g[changed].std()) - 0.15) < 0.01

    big = np.full(1000, 4.99, dtype=np.float32)
    G.mutate(big, rng(), p_mutation=1.0, sigma=10.0)
    assert np.all(np.abs(big) <= C.GENOME_CLAMP)


def test_mutation_is_in_place_and_seeded():
    a = np.zeros(500, dtype=np.float32)
    b = np.zeros(500, dtype=np.float32)
    out = G.mutate(a, rng(9))
    G.mutate(b, rng(9))
    assert out is a
    assert np.array_equal(a, b)


def test_diversity_zero_for_clones_positive_otherwise():
    clones = np.ones((30, 50), dtype=np.float32)
    assert G.diversity(clones, rng()) == 0.0
    spread = rng(1).normal(0, 1, (30, 50)).astype(np.float32)
    # Дві незалежні N(0,1)-точки: E‖a−b‖ ≈ √(2G), поділене на √G → ≈ √2
    assert 1.2 < G.diversity(spread, rng()) < 1.6


def test_genome_json_round_trip(creature):
    g = G.init_genome(creature.n_in, creature.n_joints, rng())
    data = G.to_json(g, creature)
    assert data["creature_hash"] == creature.creature_hash
    assert (data["n_in"], data["n_joints"]) == (creature.n_in, creature.n_joints)
    assert np.array_equal(G.from_json(data, creature), g)


def test_genome_rejected_for_other_morphology(creature, demo_creature):
    data = G.to_json(G.init_genome(creature.n_in, creature.n_joints, rng()), creature)
    other_cfg = copy.deepcopy(demo_creature)
    other_cfg["limbs"][0]["segments"][0]["length"] += 0.1
    with pytest.raises(G.GenomeMismatch):
        G.from_json(data, CreatureConfig.model_validate(other_cfg))


def test_genome_rejected_for_other_sensor_set(creature):
    """Геном етапу 3 не підходить, коли ввімкнено радар (інший `n_in`, §4.4)."""
    bigger = creature.n_in + C.N_RAYS * C.N_RAY_CHANNELS
    data = {"creature_hash": creature.creature_hash, "n_in": bigger,
            "n_joints": creature.n_joints,
            "weights": [0.0] * C.genome_size(bigger, creature.n_joints)}
    with pytest.raises(G.GenomeMismatch):
        G.from_json(data, creature)


# --- оператори ГА в зборі --------------------------------------------------
def make_ga(demo_creature, **ga) -> GeneticAlgorithm:
    return GeneticAlgorithm(ExperimentConfig.model_validate({
        "name": "ops", "seed": 5, "creature": demo_creature,
        "ga": {"population": 12, **ga}}))


def test_elites_survive_unchanged(demo_creature):
    ga = make_ga(demo_creature, elitism=2)
    fitness = np.arange(12, dtype=np.float64)       # найкращі — 11 і 10
    top = [ga.population[11].copy(), ga.population[10].copy()]
    new = ga.reproduce(fitness)
    assert new.shape == ga.population.shape
    assert np.array_equal(new[0], top[0]) and np.array_equal(new[1], top[1])


def test_tournament_prefers_fitter(demo_creature):
    ga = make_ga(demo_creature, tournament_k=3)
    fitness = np.arange(12, dtype=np.float64)
    wins = [ga.tournament(fitness) for _ in range(2000)]
    # Переможець турніру з 3 — у середньому помітно вище медіани 5.5
    assert np.mean(wins) > 7.5
    assert 0 not in wins or wins.count(0) < 10       # найгірший майже ніколи


def test_same_seed_same_reproduction(demo_creature):
    a, b = make_ga(demo_creature), make_ga(demo_creature)
    fitness = np.linspace(0, 1, 12)
    assert np.array_equal(a.reproduce(fitness), b.reproduce(fitness))
