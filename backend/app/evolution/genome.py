"""Геном: плоский вектор float32, init / crossover / mutate,
(де)серіалізація з `creature_hash` (§6.1, §6.2).

Усі випадкові операції беруть генератор ззовні: на прогін він один
(`PCG64(seed)`), і саме порядок звертань до нього визначає відтворюваність.
"""

from __future__ import annotations

import numpy as np

from .. import config as C
from ..core.brain import bias_mask
from ..schemas import CreatureConfig, GenomeJSON

DTYPE = np.float32


class GenomeMismatch(ValueError):
    """Геном не підходить до морфології (інший `creature_hash` або розмір)."""


def init_genome(n_in: int, n_joints: int, rng: np.random.Generator) -> np.ndarray:
    """Ваги `N(0, GENOME_INIT_SIGMA)`, біаси — нулі (§6.1)."""
    genome = rng.normal(0.0, C.GENOME_INIT_SIGMA, C.genome_size(n_in, n_joints))
    genome[bias_mask(n_in, n_joints)] = 0.0
    return genome.astype(DTYPE)


def init_population(size: int, n_in: int, n_joints: int, rng: np.random.Generator) -> np.ndarray:
    """Популяція `[P × G]`; особини генеруються по черзі тим самим генератором."""
    return np.stack([init_genome(n_in, n_joints, rng) for _ in range(size)])


def uniform_crossover(a: np.ndarray, b: np.ndarray, rng: np.random.Generator,
                      p_swap: float = C.GA_P_SWAP) -> np.ndarray:
    """Кожен ген із ймовірністю `p_swap` береться від `b`, інакше від `a` (§6.2)."""
    take_b = rng.random(a.size) < p_swap
    return np.where(take_b, b, a).astype(DTYPE)


def mutate(genome: np.ndarray, rng: np.random.Generator,
           p_mutation: float = C.GA_P_MUTATION,
           sigma: float = C.GA_MUTATION_SIGMA,
           clamp: float = C.GENOME_CLAMP) -> np.ndarray:
    """Гаусова мутація **in-place**: ген із ймовірністю `p_mutation` отримує
    `+N(0, σ)`; після цього весь вектор клампиться в `[−clamp, clamp]` (§6.2)."""
    hit = rng.random(genome.size) < p_mutation
    n_hit = int(hit.sum())
    if n_hit:
        genome[hit] += rng.normal(0.0, sigma, n_hit).astype(DTYPE)
    np.clip(genome, -clamp, clamp, out=genome)
    return genome


def diversity(population: np.ndarray, rng: np.random.Generator,
              sample: int = C.GA_DIVERSITY_SAMPLE) -> float:
    """Середня попарна L2 між `sample` випадковими геномами, поділена на `√G` (§6.2)."""
    n = len(population)
    idx = rng.choice(n, size=min(sample, n), replace=False)
    pick = population[idx].astype(np.float64)
    if len(pick) < 2:
        return 0.0
    diff = pick[:, None, :] - pick[None, :, :]
    dist = np.sqrt((diff * diff).sum(axis=-1))
    iu = np.triu_indices(len(pick), k=1)
    return float(dist[iu].mean() / np.sqrt(population.shape[1]))


# --- (де)серіалізація (§6.1) -------------------------------------------------
def to_json(genome: np.ndarray, creature: CreatureConfig) -> dict:
    """GenomeJSON: `float32` → `float` без втрат (кожне float32 точно є float64)."""
    return GenomeJSON(
        creature_hash=creature.creature_hash,
        n_in=creature.n_in,
        n_joints=creature.n_joints,
        weights=[float(w) for w in np.asarray(genome, dtype=DTYPE)],
    ).model_dump(mode="json")


def from_json(data: dict | GenomeJSON, creature: CreatureConfig) -> np.ndarray:
    """Геном для **цієї** морфології; чужий `creature_hash` чи інший набір
    сенсорів (`n_in`) — `GenomeMismatch`."""
    g = data if isinstance(data, GenomeJSON) else GenomeJSON.model_validate(data)
    if g.creature_hash != creature.creature_hash:
        raise GenomeMismatch("геном побудований для іншої морфології (creature_hash)")
    if (g.n_in, g.n_joints) != (creature.n_in, creature.n_joints):
        raise GenomeMismatch(
            f"геном має n_in={g.n_in}, n_joints={g.n_joints}, а істота з поточним "
            f"набором сенсорів — n_in={creature.n_in}, n_joints={creature.n_joints}")
    return np.asarray(g.weights, dtype=DTYPE)
