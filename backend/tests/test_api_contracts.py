"""Контрактні тести ендпоінтів етапу 1 (PROJECT_SPEC.md §9.4).

Повне покриття §9.4 додається на етапі 5.
"""

from __future__ import annotations

import copy

from app import config as C


def test_status_stub(client):
    r = client.get("/api/simulation/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "STOPPED"
    assert set(body) == {"state", "run_id", "generation", "population", "elapsed_s", "mode"}
    assert body["mode"] == "headless"


def test_validate_returns_derived_values(client, demo_creature):
    r = client.post("/api/creature/validate", json=demo_creature)
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True and body["errors"] == []
    assert body["n_joints"] == 4
    assert body["n_in"] == 46                      # §4.4: 24 + 3 + 3·4 + 7
    assert body["n_in"] == C.n_in_from_joints(4)
    assert body["genome_size"] == C.genome_size(46, 4)
    assert body["creature_hash"].startswith("sha256:")


def test_single_limb_two_segments_matches_spec_example(client):
    """§9.1: 1 кінцівка × 2 сегменти → n_joints=2, n_in=40."""
    creature = {
        "body": {"shape": "circle", "radius": 0.8, "density": 1.0},
        "limbs": [
            {
                "attach_angle": 2.3562,
                "segments": [
                    {"length": 1.2, "width": 0.18,
                     "joint": {"angle_min": -1.2, "angle_max": 1.2, "motor_torque": 120.0}},
                    {"length": 0.9, "width": 0.12,
                     "joint": {"angle_min": -1.5, "angle_max": 1.5, "motor_torque": 80.0}},
                ],
            }
        ],
    }
    body = client.post("/api/creature/validate", json=creature).json()
    assert (body["n_joints"], body["n_in"]) == (2, 40)
    assert body["genome_size"] == C.genome_size(40, 2) == 962


def test_validate_rejects_seven_limbs_with_readable_error(client, demo_creature):
    """DoD етапу 1: 7 кінцівок → зрозуміла помилка, статус 200."""
    bad = copy.deepcopy(demo_creature)
    bad["limbs"] = [copy.deepcopy(bad["limbs"][0]) for _ in range(7)]
    r = client.post("/api/creature/validate", json=bad)
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["n_joints"] is None
    assert any("limbs" in e for e in body["errors"]), body["errors"]


def test_validate_rejects_non_convex_polygon_body(client, demo_creature):
    bad = copy.deepcopy(demo_creature)
    bad["body"] = {
        "shape": "polygon",
        "vertices": [[-1, -1], [1, -1], [0, 0], [1, 1], [-1, 1]],   # увігнута
        "density": 1.0,
    }
    body = client.post("/api/creature/validate", json=bad).json()
    assert body["valid"] is False
    assert any("опукл" in e for e in body["errors"]), body["errors"]


def test_validate_rejects_out_of_range_and_unknown_fields(client, demo_creature):
    bad = copy.deepcopy(demo_creature)
    bad["body"]["radius"] = 9.0                       # > 1.5
    bad["limbs"][0]["segments"][0]["turbo"] = True     # невідоме поле
    body = client.post("/api/creature/validate", json=bad).json()
    assert body["valid"] is False
    assert len(body["errors"]) >= 2
    # шлях помилки має вказувати на поле, а не на «(корінь)»
    assert any(e.startswith("body:") for e in body["errors"]), body["errors"]
    assert any("segments.0.turbo" in e for e in body["errors"]), body["errors"]


def test_error_envelope_on_malformed_body(client):
    r = client.post("/api/creature/validate", json=[1, 2, 3])
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR" and err["message"]


def test_validate_predator_ok(client):
    r = client.post("/api/predator/validate", json={"r_body": 1.2, "r_catch": 1.0,
                                                    "r_aura": 6.0, "r_detect": 9.0})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True and body["errors"] == []
    # дотик до тіла теж ловить (§8.1), тож ефективний радіус — більший із двох
    assert body["r_catch_effective"] == 1.2
    assert body["aura_diameter"] == 12.0


def test_validate_predator_reports_broken_radii(client):
    r = client.post("/api/predator/validate", json={"r_catch": 6.0, "r_aura": 5.0, "r_detect": 8.0})
    body = r.json()
    assert body["valid"] is False
    assert any("r_catch" in e for e in body["errors"]), body["errors"]
    assert body["r_catch_effective"] is None


def test_validate_predator_rejects_waypoints(client):
    """Маршрут — не частина сутності: §9.2 тримає його у сцені."""
    body = client.post("/api/predator/validate",
                       json={"waypoints": [[0, 0], [1, 1]]}).json()
    assert body["valid"] is False
    assert any("waypoints" in e for e in body["errors"]), body["errors"]


def test_validate_predator_out_of_range(client):
    body = client.post("/api/predator/validate", json={"r_body": 5.0}).json()
    assert body["valid"] is False
    assert any("r_body" in e for e in body["errors"]), body["errors"]
