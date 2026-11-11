import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from dice_duck.api import create_app
from dice_duck.verify import main as verify_main
from dice_duck.verify import verify_bundle


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "dice.sqlite3"
    app = create_app(str(db), str(tmp_path / "key.pem"))
    return TestClient(app), db


def new_session(client):
    r = client.post("/sessions", json={"title": "Testrunde"})
    assert r.status_code == 201
    return r.json()


def roll(client, sid, expr="1d20+5", actor="llm-gm"):
    r = client.post(f"/sessions/{sid}/rolls", json={"expr": expr, "actor": actor, "reason": "Test"})
    assert r.status_code == 201, r.text
    return r.json()


def test_full_flow(env):
    client, _ = env
    s = new_session(client)
    sid = s["id"]
    assert s["status"] == "open" and s["server_seed"] is None and len(s["server_seed_hash"]) == 64

    for player, value in [("Anna", "Drachenfeuer 42"), ("Ben", "🦆🦆🦆")]:
        assert client.post(f"/sessions/{sid}/seed-contributions",
                           json={"player": player, "value": value}).status_code == 200

    r1 = roll(client, sid, "2d20kh1+5")
    r2 = roll(client, sid, "4d6dl1", actor="Anna")
    assert r1["record"]["nonce"] == 0 and r2["record"]["nonce"] == 1
    assert r2["record"]["prev_hash"] == r1["entry_hash"]
    assert 6 <= r1["record"]["total"] <= 25
    assert 3 <= r2["record"]["total"] <= 18

    # Nach dem ersten Wurf ist der Seed gesperrt
    r = client.post(f"/sessions/{sid}/seed-contributions", json={"player": "Eve", "value": "x"})
    assert r.status_code == 409

    assert client.get(f"/sessions/{sid}/rolls/1").json() == client.get(f"/sessions/{sid}/rolls").json()[1]

    v = client.get(f"/sessions/{sid}/verify").json()
    assert v["ok"] and not v["outcomes_recomputed"]

    revealed = client.post(f"/sessions/{sid}/reveal").json()
    assert revealed["status"] == "revealed" and revealed["server_seed"]

    v = client.get(f"/sessions/{sid}/verify").json()
    assert v == {**v, "ok": True, "rolls_checked": 2, "outcomes_recomputed": True}

    # Nach dem Aufdecken darf nicht mehr gewürfelt werden
    r = client.post(f"/sessions/{sid}/rolls", json={"expr": "1d6", "actor": "llm-gm"})
    assert r.status_code == 409


def test_errors(env):
    client, _ = env
    assert client.get("/sessions/nope").status_code == 404
    sid = new_session(client)["id"]
    r = client.post(f"/sessions/{sid}/rolls", json={"expr": "2x6", "actor": "gm"})
    assert r.status_code == 422 and "Würfelnotation" in r.json()["detail"]
    assert client.post(f"/sessions/{sid}/rolls", json={"expr": "1d6"}).status_code == 422
    assert client.get(f"/sessions/{sid}/rolls/0").status_code == 404


def test_info_endpoints(env):
    client, _ = env
    assert client.get("/public-key").json()["algorithm"] == "Ed25519"
    assert client.get("/llm-tool").json()["name"] == "roll_dice"


def _tamper(db, sql, *params):
    conn = sqlite3.connect(db)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _revealed_session_with_rolls(client, n=3):
    sid = new_session(client)["id"]
    for _ in range(n):
        roll(client, sid)
    client.post(f"/sessions/{sid}/reveal")
    return sid


def test_detects_changed_result(env):
    client, db = env
    sid = _revealed_session_with_rolls(client)
    entry = client.get(f"/sessions/{sid}/rolls/1").json()
    rec = entry["record"]
    rec["total"] = 25  # „natürlich eine 20!"
    _tamper(db, "UPDATE rolls SET record_json = ? WHERE session_id = ? AND nonce = 1",
            json.dumps(rec), sid)
    v = client.get(f"/sessions/{sid}/verify").json()
    assert not v["ok"]
    assert any("entry_hash" in e for e in v["errors"])
    assert any("Signatur" in e for e in v["errors"])


def test_detects_deleted_roll(env):
    client, db = env
    sid = _revealed_session_with_rolls(client)
    _tamper(db, "DELETE FROM rolls WHERE session_id = ? AND nonce = 1", sid)
    v = client.get(f"/sessions/{sid}/verify").json()
    assert not v["ok"] and any("Nonce" in e for e in v["errors"])


def test_detects_truncated_tail(env):
    client, db = env
    sid = _revealed_session_with_rolls(client)
    _tamper(db, "DELETE FROM rolls WHERE session_id = ? AND nonce = 2", sid)
    v = client.get(f"/sessions/{sid}/verify").json()
    assert not v["ok"] and any("head_hash" in e or "roll_count" in e for e in v["errors"])


def test_detects_swapped_seed(env):
    client, db = env
    sid = _revealed_session_with_rolls(client)
    _tamper(db, "UPDATE sessions SET server_seed = ? WHERE id = ?", bytes(32), sid)
    v = client.get(f"/sessions/{sid}/verify").json()
    assert not v["ok"] and any("server_seed" in e for e in v["errors"])


def test_offline_verify_cli(env, tmp_path, capsys):
    client, _ = env
    sid = _revealed_session_with_rolls(client)
    export = client.get(f"/sessions/{sid}/export").json()
    assert verify_bundle(export)["ok"]

    path = tmp_path / "export.json"
    path.write_text(json.dumps(export), encoding="utf-8")
    assert verify_main([str(path)]) == 0

    export["rolls"][0]["record"]["actor"] = "jemand anderes"
    path.write_text(json.dumps(export), encoding="utf-8")
    assert verify_main([str(path)]) == 1


def test_key_persists_across_restarts(tmp_path):
    a = create_app(str(tmp_path / "d.sqlite3"), str(tmp_path / "k.pem"))
    b = create_app(str(tmp_path / "d.sqlite3"), str(tmp_path / "k.pem"))
    pk = lambda app: TestClient(app).get("/public-key").json()["public_key"]  # noqa: E731
    assert pk(a) == pk(b)
