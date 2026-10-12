"""Спільні фікстури тестів."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def demo_creature() -> dict:
    """Демо-істота: 2 кінцівки × 2 сегменти → 4 суглоби (приклад §4.4)."""
    def limb(attach_angle: float) -> dict:
        return {
            "attach_angle": attach_angle,
            "rest_angle": 0.0,
            "segments": [
                {
                    "length": 1.2, "width": 0.18, "density": 1.0,
                    "joint": {"angle_min": -1.2, "angle_max": 1.2, "motor_torque": 120.0},
                },
                {
                    "length": 0.9, "width": 0.12, "density": 1.0,
                    "joint": {"angle_min": -1.5, "angle_max": 1.5, "motor_torque": 80.0},
                },
            ],
        }

    return {
        "name": "swimmer-v1",
        "body": {"shape": "circle", "radius": 0.8, "vertices": None, "density": 1.0},
        "limbs": [limb(2.3562), limb(-2.3562)],
    }
