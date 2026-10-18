"""MLP N_in→16→16→N_joints на NumPy (§5).

Геном — плоский `float32`-вектор `[W1.flat, b1, W2.flat, b2, W3.flat, b3]`
саме в такому порядку (§6.1). Матриці зберігаються рядками: `W1` має форму
`[16 × N_in]`, тож `W1.flat` — це 16 рядків по `N_in` ваг.

Мережа без внутрішнього стану: однаковий вхід завжди дає однаковий вихід,
уся «пам'ять» — в осциляторі та фізиці.
"""

from __future__ import annotations

import numpy as np

from .. import config as C


def layer_shapes(n_in: int, n_joints: int) -> list[tuple[int, int]]:
    """Форми ваг `[(рядки, стовпці)]` трьох шарів у порядку геному."""
    return [(C.HIDDEN_1, n_in), (C.HIDDEN_2, C.HIDDEN_1), (n_joints, C.HIDDEN_2)]


def bias_mask(n_in: int, n_joints: int) -> np.ndarray:
    """Булева маска позицій біасів у геномі — для ініціалізації (§6.1)."""
    parts = []
    for rows, cols in layer_shapes(n_in, n_joints):
        parts.append(np.zeros(rows * cols, dtype=bool))
        parts.append(np.ones(rows, dtype=bool))
    return np.concatenate(parts)


class Brain:
    """Прямий прохід MLP; ваги розпаковуються з геному один раз на епізод."""

    def __init__(self, genome: np.ndarray, n_in: int, n_joints: int):
        genome = np.asarray(genome, dtype=np.float32)
        expected = C.genome_size(n_in, n_joints)
        if genome.shape != (expected,):
            raise ValueError(f"довжина геному {genome.size}, очікувалось {expected}")
        self.n_in = n_in
        self.n_joints = n_joints
        # Обчислення у float64: розпакування одне на епізод, а точність
        # проміжних сум не залежить від порядку BLAS-редукцій у float32
        weights: list[np.ndarray] = []
        offset = 0
        for rows, cols in layer_shapes(n_in, n_joints):
            w = genome[offset:offset + rows * cols].astype(np.float64).reshape(rows, cols)
            offset += rows * cols
            b = genome[offset:offset + rows].astype(np.float64)
            offset += rows
            weights += [w, b]
        self.w1, self.b1, self.w2, self.b2, self.w3, self.b3 = weights

    def forward(self, x: np.ndarray) -> np.ndarray:
        """`u = tanh(W3·tanh(W2·tanh(W1·x + b1) + b2) + b3)`, `u ∈ [−1, 1]^N_joints`."""
        h1 = np.tanh(self.w1 @ x + self.b1)
        h2 = np.tanh(self.w2 @ h1 + self.b2)
        return np.tanh(self.w3 @ h2 + self.b3)
