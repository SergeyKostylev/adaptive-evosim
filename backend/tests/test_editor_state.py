"""Стан конструктора між сеансами: GET/PUT/DELETE /api/editor/state."""

from __future__ import annotations

import json

import pytest

from app import config as C
from app.services import editor_store


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Кожен тест пише у свій каталог, а не в experiments/ проєкту."""
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def payload(demo_creature) -> dict:
    return {
        "creature": demo_creature,
        "environment": {
            "world": {"width": 60, "height": 40},
            "obstacles": [{"type": "static_circle", "radius": 1.5, "position": [-8, 2]}],
            "predator": {"enabled": True, "waypoints": [[-15, -10], [15, -10]]},
        },
        "ga": {"population": 100, "generations": 200},
        "fitness_weights": {"survival": 3.0},
    }


def test_empty_state_reports_absence(client):
    r = client.get("/api/editor/state")
    assert r.status_code == 200
    assert r.json() == {"exists": False, "saved_at": None, "state": None}


def test_save_then_load_roundtrip(client, payload, isolated_data_dir):
    put = client.put("/api/editor/state", json=payload)
    assert put.status_code == 200
    saved_at = put.json()["saved_at"]
    assert saved_at

    # Файл лежить у проєкті, у читабельному вигляді
    path = isolated_data_dir / C.EDITOR_SUBDIR / editor_store.STATE_FILENAME
    assert path.is_file()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["creature"] == payload["creature"]

    got = client.get("/api/editor/state").json()
    assert got["exists"] is True
    assert got["saved_at"] == saved_at
    assert got["state"]["environment"]["obstacles"] == payload["environment"]["obstacles"]
    assert got["state"]["ga"] == payload["ga"]


def test_save_overwrites_atomically_without_leftovers(client, payload, isolated_data_dir):
    client.put("/api/editor/state", json=payload)
    payload["creature"]["name"] = "second"
    client.put("/api/editor/state", json=payload)

    editor_dir = isolated_data_dir / C.EDITOR_SUBDIR
    assert [p.name for p in editor_dir.iterdir()] == [editor_store.STATE_FILENAME]
    assert client.get("/api/editor/state").json()["state"]["creature"]["name"] == "second"


def test_incomplete_draft_is_accepted(client):
    """Чернетка може не проходити §9.1 — це нормально для редактора."""
    r = client.put("/api/editor/state", json={
        "creature": {"body": {"shape": "circle", "radius": 0.8}, "limbs": []},
        "environment": {},
    })
    assert r.status_code == 200
    assert client.get("/api/editor/state").json()["state"]["creature"]["limbs"] == []


def test_corrupted_file_does_not_break_api(client, isolated_data_dir):
    path = isolated_data_dir / C.EDITOR_SUBDIR / editor_store.STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ це не json", encoding="utf-8")
    assert client.get("/api/editor/state").json()["exists"] is False


def test_delete_clears_state(client, payload):
    client.put("/api/editor/state", json=payload)
    assert client.delete("/api/editor/state").json()["exists"] is False
    assert client.get("/api/editor/state").json()["exists"] is False


def test_oversized_state_rejected(client, payload):
    payload["environment"]["junk"] = "x" * (editor_store.MAX_STATE_BYTES + 10)
    r = client.put("/api/editor/state", json=payload)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


# ---------------------------------------------------------------------------
# Іменовані варіанти сцени
# ---------------------------------------------------------------------------
def test_presets_empty_list(client):
    r = client.get("/api/editor/presets")
    assert r.status_code == 200
    assert r.json() == {"presets": [], "limit": editor_store.MAX_PRESETS}


def test_save_list_load_preset(client, payload, isolated_data_dir):
    put = client.put("/api/editor/presets/симетричний плавець", json=payload)
    assert put.status_code == 200
    meta = put.json()
    assert meta["name"] == "симетричний плавець"
    assert (meta["limbs"], meta["segments"], meta["obstacles"], meta["waypoints"]) == (2, 4, 1, 2)
    assert meta["creature_name"] == "swimmer-v1"

    # Файл лежить у проєкті під читабельною назвою
    path = isolated_data_dir / C.EDITOR_SUBDIR / editor_store.PRESETS_SUBDIR / "симетричний плавець.json"
    assert path.is_file()

    rows = client.get("/api/editor/presets").json()["presets"]
    assert [r["name"] for r in rows] == ["симетричний плавець"]

    got = client.get("/api/editor/presets/симетричний плавець").json()
    assert got["exists"] is True
    assert got["state"]["creature"] == payload["creature"]
    assert got["state"]["ga"] == payload["ga"]


def test_preset_does_not_touch_current_scene(client, payload):
    """Збереження варіанта не перезаписує last_session.json."""
    client.put("/api/editor/presets/варіант-A", json=payload)
    assert client.get("/api/editor/state").json()["exists"] is False


def test_preset_overwrite_and_delete(client, payload):
    client.put("/api/editor/presets/варіант-A", json=payload)
    payload["creature"]["name"] = "updated"
    client.put("/api/editor/presets/варіант-A", json=payload)

    rows = client.get("/api/editor/presets").json()["presets"]
    assert len(rows) == 1
    assert client.get("/api/editor/presets/варіант-A").json()["state"]["creature"]["name"] == "updated"

    left = client.delete("/api/editor/presets/варіант-A").json()["presets"]
    assert left == []
    assert client.get("/api/editor/presets/варіант-A").status_code == 404


def test_presets_sorted_newest_first(client, payload, monkeypatch):
    stamps = iter(["2026-01-01T10:00:00+00:00", "2026-01-02T10:00:00+00:00"])
    monkeypatch.setattr(editor_store, "_now", lambda: next(stamps))
    client.put("/api/editor/presets/старий", json=payload)
    client.put("/api/editor/presets/новий", json=payload)
    rows = client.get("/api/editor/presets").json()["presets"]
    assert [r["name"] for r in rows] == ["новий", "старий"]


@pytest.mark.parametrize(
    "name",
    [
        "..",            # вихід на рівень вище
        ".",             # нормалізується клієнтом у колекцію → 405
        ".hidden",       # прихований файл
        "../../etc/passwd",
        "a/b", "a\\b",   # розділювачі шляху
        "назва.",        # крапка в кінці
    ],
)
def test_preset_name_traversal_rejected(client, payload, name, isolated_data_dir):
    r = client.put(f"/api/editor/presets/{name}", json=payload)
    assert r.status_code in (400, 404, 405), (name, r.status_code)
    if r.status_code == 400:
        assert r.json()["error"]["code"] == "INVALID_REQUEST"
    # поза каталогом presets не з'явилось нічого
    assert not list(isolated_data_dir.glob("*.json"))
    assert not list(isolated_data_dir.glob("*/*.json"))


@pytest.mark.parametrize("name", ["..", ".", ".hidden", "a/b", "a\\b", "вар\x00іант", "x" * 65, "назва."])
def test_preset_path_rejects_unsafe_names(name, isolated_data_dir):
    """Рівень сховища: деякі назви HTTP-клієнт навіть не пропустить у URL."""
    with pytest.raises(editor_store.InvalidPresetName):
        editor_store.preset_path(name)


def test_preset_name_whitespace_trimmed(client, payload, isolated_data_dir):
    r = client.put("/api/editor/presets/  варіант-B  ", json=payload)
    assert r.status_code == 200
    assert r.json()["name"] == "варіант-B"
    assert [m["name"] for m in client.get("/api/editor/presets").json()["presets"]] == ["варіант-B"]


def test_preset_name_too_long_rejected(client, payload):
    r = client.put("/api/editor/presets/" + "я" * 65, json=payload)
    assert r.status_code == 400


def test_preset_limit_enforced(client, payload, monkeypatch):
    monkeypatch.setattr(editor_store, "MAX_PRESETS", 2)
    assert client.put("/api/editor/presets/один", json=payload).status_code == 200
    assert client.put("/api/editor/presets/два", json=payload).status_code == 200
    over = client.put("/api/editor/presets/три", json=payload)
    assert over.status_code == 409
    assert over.json()["error"]["code"] == "CONFLICT"
    # перезапис наявного працює навіть на межі ліміту
    assert client.put("/api/editor/presets/два", json=payload).status_code == 200


def test_corrupted_preset_skipped_in_list(client, payload, isolated_data_dir):
    client.put("/api/editor/presets/добрий", json=payload)
    bad = isolated_data_dir / C.EDITOR_SUBDIR / editor_store.PRESETS_SUBDIR / "битий.json"
    bad.write_text("{ не json", encoding="utf-8")
    rows = client.get("/api/editor/presets").json()["presets"]
    assert [r["name"] for r in rows] == ["добрий"]
    assert client.get("/api/editor/presets/битий").status_code == 404


# ---------------------------------------------------------------------------
# Бібліотека істот
# ---------------------------------------------------------------------------
def test_creature_library_roundtrip(client, demo_creature, isolated_data_dir):
    put = client.put("/api/editor/creatures/зірочка", json={"creature": demo_creature})
    assert put.status_code == 200
    meta = put.json()
    assert (meta["name"], meta["limbs"], meta["segments"], meta["shape"]) == ("зірочка", 2, 4, "circle")
    assert meta["creature"]["limbs"] == demo_creature["limbs"]      # потрібне для мініатюри

    path = isolated_data_dir / C.EDITOR_SUBDIR / editor_store.CREATURES_SUBDIR / "зірочка.json"
    assert path.is_file()

    rows = client.get("/api/editor/creatures").json()
    assert [r["name"] for r in rows["creatures"]] == ["зірочка"]
    assert rows["limit"] == editor_store.MAX_PRESETS

    got = client.get("/api/editor/creatures/зірочка").json()
    assert got["creature"]["body"]["radius"] == 0.8
    assert got["saved_at"]


def test_creature_library_validates_morphology(client, demo_creature):
    """На відміну від сцени-чернетки, бібліотека приймає лише валідні §9.1."""
    bad = {"body": {"shape": "circle", "radius": 9.0, "density": 1.0}, "limbs": []}
    r = client.put("/api/editor/creatures/бите", json={"creature": bad})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert client.get("/api/editor/creatures").json()["creatures"] == []


def test_creature_library_is_separate_from_scene_presets(client, demo_creature, payload):
    client.put("/api/editor/creatures/істота", json={"creature": demo_creature})
    client.put("/api/editor/presets/сцена", json=payload)
    assert [r["name"] for r in client.get("/api/editor/creatures").json()["creatures"]] == ["істота"]
    assert [r["name"] for r in client.get("/api/editor/presets").json()["presets"]] == ["сцена"]
    assert client.get("/api/editor/creatures/сцена").status_code == 404


def test_creature_delete_and_missing(client, demo_creature):
    client.put("/api/editor/creatures/істота", json={"creature": demo_creature})
    assert client.delete("/api/editor/creatures/істота").json()["creatures"] == []
    assert client.get("/api/editor/creatures/істота").status_code == 404


@pytest.mark.parametrize("name", ["..", ".hidden", "a/b", "назва."])
def test_creature_name_traversal_rejected(client, demo_creature, name, isolated_data_dir):
    r = client.put(f"/api/editor/creatures/{name}", json={"creature": demo_creature})
    assert r.status_code in (400, 404, 405)
    assert not list(isolated_data_dir.glob("*.json"))
    assert not list(isolated_data_dir.glob("*/*.json"))


# ---------------------------------------------------------------------------
# Бібліотека хижаків
# ---------------------------------------------------------------------------
def test_predator_library_roundtrip(client, isolated_data_dir):
    params = {"r_body": 1.2, "v_patrol": 3.0, "v_chase": 6.0,
              "r_detect": 10.0, "r_aura": 6.0, "r_catch": 1.5}
    put = client.put("/api/editor/predators/швидкий", json={"predator": params})
    assert put.status_code == 200
    meta = put.json()
    assert (meta["name"], meta["r_body"], meta["v_chase"]) == ("швидкий", 1.2, 6.0)
    assert meta["predator"]["r_aura"] == 6.0

    path = isolated_data_dir / C.EDITOR_SUBDIR / editor_store.PREDATORS_SUBDIR / "швидкий.json"
    assert path.is_file()

    rows = client.get("/api/editor/predators").json()
    assert [r["name"] for r in rows["predators"]] == ["швидкий"]
    assert client.get("/api/editor/predators/швидкий").json()["predator"]["v_patrol"] == 3.0

    assert client.delete("/api/editor/predators/швидкий").json()["predators"] == []
    assert client.get("/api/editor/predators/швидкий").status_code == 404


def test_predator_library_rejects_bad_radii(client):
    bad = {"r_detect": 2.0, "r_aura": 5.0, "r_catch": 1.0}      # r_aura > r_detect
    r = client.put("/api/editor/predators/бите", json={"predator": bad})
    assert r.status_code == 422
    assert client.get("/api/editor/predators").json()["predators"] == []


def test_predator_library_has_no_waypoints(client):
    """Маршрут — геометрія сцени, у бібліотеці його бути не може."""
    r = client.put("/api/editor/predators/з-маршрутом",
                   json={"predator": {"waypoints": [[0, 0], [1, 1]]}})
    assert r.status_code == 422


def test_three_libraries_are_independent(client, demo_creature, payload):
    client.put("/api/editor/creatures/і", json={"creature": demo_creature})
    client.put("/api/editor/predators/х", json={"predator": {}})
    client.put("/api/editor/presets/с", json=payload)
    assert [r["name"] for r in client.get("/api/editor/creatures").json()["creatures"]] == ["і"]
    assert [r["name"] for r in client.get("/api/editor/predators").json()["predators"]] == ["х"]
    assert [r["name"] for r in client.get("/api/editor/presets").json()["presets"]] == ["с"]
