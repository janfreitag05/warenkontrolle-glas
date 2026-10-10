import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.detector import MockDetector, load_parts
from app.main import app
from tools import generate_images as gen

SEED = 42
COUNT = 20
HIDDEN = ["injected_error", "gt_defect", *config.INJECTED_ERRORS]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "station.db")
    gen.generate(COUNT, SEED, data_dir=tmp_path, db_path=config.DB_PATH)
    with TestClient(app) as client:
        yield client


def query(sql, params=()):
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def count(table):
    return query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


def infeed_all(client):
    while not client.post("/api/station/next").json()["done"]:
        pass


def pending(ai_verdict, with_detections=0):
    rows = query(
        "SELECT i.id FROM inspection i WHERE i.ai_verdict = ? "
        "AND NOT EXISTS (SELECT 1 FROM review r WHERE r.inspection_id = i.id) "
        "AND (SELECT COUNT(*) FROM detection d WHERE d.inspection_id = i.id) >= ? ORDER BY i.id",
        (ai_verdict, with_detections),
    )
    assert rows, f"no pending {ai_verdict} inspection in the sample"
    return rows[0]["id"]


def snapshot(inspection_id):
    return (
        query("SELECT * FROM inspection WHERE id = ?", (inspection_id,)),
        query("SELECT * FROM detection WHERE inspection_id = ? ORDER BY id", (inspection_id,)),
    )


# --- Page -------------------------------------------------------------------------

def test_station_page_is_german(env):
    response = env.get("/station")
    assert response.status_code == 200
    for text in ("Prüfstation", "Teile laufen ein", "Zu prüfen", 'href="/station/history"'):
        assert text in response.text
    assert f"{config.STATION_INFEED_INTERVAL_S:g}".replace(".", ",") in response.text
    # no controls for the infeed (it starts with the page) and no infeed band any more
    for text in ("Nächstes Teil", "Automatischer Einlauf", 'id="btn-next"', 'id="auto-toggle"', "Einlaufband"):
        assert text not in response.text


# --- Infeed -----------------------------------------------------------------------

def test_infeed_stores_inspection_and_detections(env):
    response = env.post("/api/station/next")
    assert response.status_code == 201
    summary = response.json()["inspection"]

    inspection = query("SELECT * FROM inspection WHERE id = ?", (summary["id"],))[0]
    part = query("SELECT serial FROM part WHERE id = ?", (inspection["part_id"],))[0]
    assert part["serial"] == summary["serial"]
    assert inspection["detector_version"] == config.DETECTOR_VERSION
    assert summary["status"] == ("AUTO_DONE" if inspection["ai_verdict"] == "OK" else "PENDING_REVIEW")

    conn = db.connect()
    try:
        parts = load_parts(conn)
    finally:
        conn.close()
    expected = MockDetector(parts=parts).analyze(next(p for p in parts if p.serial == part["serial"]))
    stored = query("SELECT defect_type, confidence, injected_error FROM detection WHERE inspection_id = ? ORDER BY id", (summary["id"],))
    assert [(d.defect_type, d.confidence, d.injected_error) for d in expected] == [
        (s["defect_type"], s["confidence"], s["injected_error"]) for s in stored
    ]


def test_same_part_cannot_be_inspected_twice(env):
    part_id = query("SELECT id FROM part ORDER BY id LIMIT 1")[0]["id"]
    assert env.post(f"/api/station/parts/{part_id}/infeed").status_code == 201
    second = env.post(f"/api/station/parts/{part_id}/infeed")
    assert second.status_code == 409
    assert "bereits geprüft" in second.json()["detail"]
    assert count("inspection") == 1
    # "next part" skips it as well
    nxt = env.post("/api/station/next").json()["inspection"]
    assert query("SELECT part_id FROM inspection WHERE id = ?", (nxt["id"],))[0]["part_id"] != part_id


def test_infeed_until_all_done(env):
    infeed_all(env)
    assert count("inspection") == COUNT
    assert query("SELECT COUNT(DISTINCT part_id) AS n FROM inspection")[0]["n"] == COUNT
    state = env.get("/api/station/state").json()
    assert state["all_done"] is True
    assert env.post("/api/station/next").json() == {"done": True, "inspection": None}


def test_state_queue_holds_only_flagged_parts(env):
    infeed_all(env)
    state = env.get("/api/station/state").json()
    assert state["queue"], "sample should contain flagged parts"
    assert {q["ai_verdict"] for q in state["queue"]} <= {"NOK", "MANUAL"}
    assert "band" not in state
    counts = state["counts"]
    assert counts["today"] == COUNT
    assert counts["today_auto_ok"] + len(state["queue"]) == COUNT
    assert counts["open"] == len(state["queue"])


def test_today_counter_matches_independent_sql(env):
    for _ in range(7):
        env.post("/api/station/next")
    # an inspection from yesterday must not count
    conn = db.connect()
    try:
        conn.execute("UPDATE inspection SET inspected_at = datetime('now', 'localtime', '-1 day') WHERE id = (SELECT MIN(id) FROM inspection)")
        conn.commit()
    finally:
        conn.close()
    counts = env.get("/api/station/state").json()["counts"]
    sql_today = query("SELECT COUNT(*) AS n FROM inspection WHERE date(inspected_at) = date('now', 'localtime')")[0]["n"]
    sql_auto = query(
        "SELECT COUNT(*) AS n FROM inspection WHERE date(inspected_at) = date('now', 'localtime') AND ai_verdict = 'OK'"
    )[0]["n"]
    assert counts["today"] == sql_today == 6
    assert counts["today_auto_ok"] == sql_auto


def test_unknown_part_and_inspection(env):
    assert env.post("/api/station/parts/99999/infeed").status_code == 404
    assert env.get("/api/station/inspections/99999").status_code == 404


def test_image_is_served(env):
    inspection_id = env.post("/api/station/next").json()["inspection"]["id"]
    response = env.get(f"/api/station/inspections/{inspection_id}/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


# --- Review -----------------------------------------------------------------------

def test_confirm_saves_review_and_keeps_ai_result(env):
    infeed_all(env)
    inspection_id = pending("NOK")
    before = snapshot(inspection_id)

    response = env.post(f"/api/station/inspections/{inspection_id}/review", json={"decision": "CONFIRMED"})
    assert response.status_code == 201
    assert response.json()["status"] == "REVIEWED"

    assert snapshot(inspection_id) == before
    review = query("SELECT * FROM review WHERE inspection_id = ?", (inspection_id,))
    assert len(review) == 1
    assert review[0]["decision"] == "CONFIRMED"
    assert review[0]["final_verdict"] == "NOK"
    assert inspection_id not in [q["id"] for q in env.get("/api/station/state").json()["queue"]]


def test_second_review_is_rejected(env):
    infeed_all(env)
    inspection_id = pending("NOK")
    url = f"/api/station/inspections/{inspection_id}/review"
    assert env.post(url, json={"decision": "CONFIRMED"}).status_code == 201
    second = env.post(url, json={"decision": "CORRECTED", "final_verdict": "OK"})
    assert second.status_code == 409
    assert "bereits eine Bewertung" in second.json()["detail"]
    assert count("review") == 1
    assert count("review_change") == 0


def test_correction_saves_review_changes(env):
    infeed_all(env)
    inspection_id = pending("NOK", with_detections=1)
    before = snapshot(inspection_id)
    detections = before[1]
    changes = [{"change": "REJECTED", "detection_id": detections[0]["id"]}]
    if len(detections) > 1:
        other = "KRATZER" if detections[1]["defect_type"] != "KRATZER" else "RISS"
        changes.append({"change": "TYPE_CHANGED", "detection_id": detections[1]["id"], "new_defect_type": other})
    changes.append({"change": "MISSED", "new_defect_type": "DRUCKSTELLE"})

    response = env.post(
        f"/api/station/inspections/{inspection_id}/review",
        json={"decision": "CORRECTED", "final_verdict": "OK", "comment": "  Fehlalarm  ", "changes": changes},
    )
    assert response.status_code == 201, response.text

    review = query("SELECT * FROM review WHERE inspection_id = ?", (inspection_id,))[0]
    assert (review["decision"], review["final_verdict"], review["comment"]) == ("CORRECTED", "OK", "Fehlalarm")
    stored = query(
        "SELECT detection_id, change, new_defect_type FROM review_change WHERE review_id = ? ORDER BY id",
        (review["id"],),
    )
    assert stored == [
        {"detection_id": c.get("detection_id"), "change": c["change"], "new_defect_type": c.get("new_defect_type")}
        for c in changes
    ]
    assert snapshot(inspection_id) == before
    assert response.json()["review"]["changes"] == stored


def test_manual_part_needs_explicit_verdict(env):
    infeed_all(env)
    inspection_id = pending("MANUAL")
    url = f"/api/station/inspections/{inspection_id}/review"
    assert env.post(url, json={"decision": "CONFIRMED"}).status_code == 400
    response = env.post(url, json={"decision": "CONFIRMED", "final_verdict": "OK"})
    assert response.status_code == 201
    assert response.json()["review"]["final_verdict"] == "OK"


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"decision": "CORRECTED"}, "Endurteil"),
        ({"decision": "CORRECTED", "final_verdict": "NOK"}, "Keine Änderung"),
        ({"decision": "CONFIRMED", "final_verdict": "OK"}, "übernimmt"),
        ({"decision": "CORRECTED", "final_verdict": "OK", "changes": [{"change": "MISSED", "new_defect_type": "SPIEGELUNG"}]}, "Fehlertyp"),
        ({"decision": "CORRECTED", "final_verdict": "OK", "changes": [{"change": "REJECTED", "detection_id": 999999}]}, "gehört nicht"),
        ({"decision": "CONFIRMED", "changes": [{"change": "MISSED", "new_defect_type": "RISS"}]}, "keine Korrekturen"),
    ],
)
def test_invalid_reviews_are_rejected(env, body, fragment):
    infeed_all(env)
    inspection_id = pending("NOK", with_detections=1)
    response = env.post(f"/api/station/inspections/{inspection_id}/review", json=body)
    assert response.status_code == 400
    assert fragment in response.json()["detail"]
    assert count("review") == 0


def test_type_change_to_same_type_is_rejected(env):
    infeed_all(env)
    inspection_id = pending("NOK", with_detections=1)
    detection = snapshot(inspection_id)[1][0]
    response = env.post(
        f"/api/station/inspections/{inspection_id}/review",
        json={
            "decision": "CORRECTED",
            "final_verdict": "OK",
            "changes": [{"change": "TYPE_CHANGED", "detection_id": detection["id"], "new_defect_type": detection["defect_type"]}],
        },
    )
    assert response.status_code == 400


def test_auto_ok_parts_cannot_be_reviewed(env):
    infeed_all(env)
    auto = query("SELECT id FROM inspection WHERE ai_verdict = 'OK' LIMIT 1")[0]["id"]
    response = env.post(f"/api/station/inspections/{auto}/review", json={"decision": "CORRECTED", "final_verdict": "NOK"})
    assert response.status_code == 409


# --- Hidden data ------------------------------------------------------------------

def test_no_response_contains_hidden_data(env):
    infeed_all(env)
    # the sample really contains injected errors, so the check is meaningful
    assert query("SELECT COUNT(*) AS n FROM detection WHERE injected_error IS NOT NULL")[0]["n"] > 0

    inspection_id = pending("NOK", with_detections=1)
    responses = [
        env.get("/station"),
        env.get("/api/station/state"),
        env.post(f"/api/station/inspections/{inspection_id}/review", json={"decision": "CONFIRMED"}),
        env.post("/api/station/next"),
    ]
    for row in query("SELECT id FROM inspection"):
        responses.append(env.get(f"/api/station/inspections/{row['id']}"))
    part_id = query("SELECT id FROM part LIMIT 1")[0]["id"]
    responses.append(env.post(f"/api/station/parts/{part_id}/infeed"))

    for response in responses:
        for word in HIDDEN:
            assert word not in response.text, (response.url, word)
