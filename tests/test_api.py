import os
import sqlite3
from datetime import date

import pytest

from home_management import create_app
from home_management.db import add_months

PASSWORD = "secret-maison"


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.db"), "APP_PASSWORD": PASSWORD,
                       "SECRET_KEY": "test-key"})


def login(client, login_value="maison", password=PASSWORD):
    return client.post("/api/login", json={"login": login_value, "password": password})


@pytest.fixture
def client(app):
    client = app.test_client()
    assert login(client).status_code == 200
    return client


def history(client):
    return [h["summary"] for h in client.get("/api/history").get_json()["items"]]


def test_add_months_clamps_day():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)


def test_login_required(app):
    client = app.test_client()
    assert client.get("/api/lists").status_code == 401
    assert client.get("/api/status").get_json()["authenticated"] is False
    assert login(client, password="nope").status_code == 403
    assert login(client, login_value="inconnu").status_code == 403
    res = login(client, login_value="MAISON")  # identifiant insensible à la casse
    assert res.status_code == 200 and res.get_json()["household"]["is_admin"] is True
    assert client.get("/api/lists").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/lists").status_code == 401


def test_event_crud_recurrence_and_history(client):
    res = client.post("/api/events", json={
        "title": "Piscine", "start": "2026-11-02T18:00", "end": "2026-11-02T19:00",
        "recurrence": "weekly", "alerts": [30, 15],
    }, headers={"X-User": "L%C3%A9a"})
    assert res.status_code == 201
    event = res.get_json()
    assert event["alerts"] == [15, 30]

    occ = client.get("/api/events?start=2026-11-01&end=2026-11-30").get_json()
    assert [o["occurrence_start"] for o in occ] == [
        "2026-11-02T18:00", "2026-11-09T18:00", "2026-11-16T18:00", "2026-11-23T18:00", "2026-11-30T18:00"]

    # Plage lointaine : le calcul saute directement aux bonnes occurrences.
    far = client.get("/api/events?start=2030-01-01&end=2030-01-10").get_json()
    assert [o["occurrence_start"] for o in far] == ["2030-01-07T18:00"]

    assert client.delete(f"/api/events/{event['id']}?occurrence=2026-11-09T18:00").status_code == 200
    occ = client.get("/api/events?start=2026-11-01&end=2026-11-15").get_json()
    assert [o["occurrence_start"] for o in occ] == ["2026-11-02T18:00"]

    client.put(f"/api/events/{event['id']}", json={**event, "title": "Natation", "recurrence_until": "2026-11-20"})
    occ = client.get("/api/events?start=2026-11-01&end=2026-12-31").get_json()
    assert len(occ) == 2 and occ[0]["title"] == "Natation"

    client.delete(f"/api/events/{event['id']}")
    assert client.get("/api/events?start=2026-11-01&end=2026-12-31").get_json() == []

    items = client.get("/api/history?module=calendar").get_json()["items"]
    assert [i["action"] for i in items] == ["delete", "update", "delete", "create"]
    assert items[-1]["actor"] == "Léa"


def test_monthly_and_all_day_events(client):
    client.post("/api/events", json={"title": "Loyer", "start": "2026-01-31T00:00", "all_day": True,
                                     "recurrence": "monthly"})
    occ = client.get("/api/events?start=2026-02-01&end=2026-03-31").get_json()
    assert [o["occurrence_start"] for o in occ] == ["2026-02-28T00:00", "2026-03-31T00:00"]
    assert occ[0]["occurrence_end"] == "2026-02-28T23:59"


def test_event_validation(client):
    assert client.post("/api/events", json={"title": "", "start": "2026-11-02T18:00"}).status_code == 400
    assert client.post("/api/events", json={"title": "x", "start": "bad"}).status_code == 400
    res = client.post("/api/events", json={"title": "x", "start": "2026-11-02T18:00", "end": "2026-11-02T17:00"})
    assert res.status_code == 400 and "fin" in res.get_json()["error"]


def test_ics_feed(client):
    client.post("/api/events", json={"title": "Dentiste, Dr Martin", "start": "2026-11-05T09:30",
                                     "alerts": [60], "recurrence": "yearly"})
    url = client.get("/api/settings").get_json()["ics_url"]
    token = url.split("token=")[1]
    assert client.get("/calendar.ics?token=wrong").status_code == 403
    assert client.get("/calendar.ics").status_code == 403
    body = client.get(f"/calendar.ics?token={token}").get_data(as_text=True)
    assert "SUMMARY:Dentiste\\, Dr Martin" in body
    assert "DTSTART:20261105T093000" in body
    assert "TRIGGER:-PT60M" in body and "RRULE:FREQ=YEARLY" in body

    # Régénérer le lien invalide l'ancien.
    new_url = client.post("/api/household/ics-token").get_json()["ics_url"]
    assert new_url != url
    assert client.get(f"/calendar.ics?token={token}").status_code == 403


def test_chores_complete_and_undo(client):
    member = client.post("/api/members", json={"name": "Tom", "color": "#10b981"}).get_json()
    chore = client.post("/api/chores", json={"title": "Aspirateur", "freq_unit": "week", "freq_interval": 1,
                                             "member_id": member["id"]}).get_json()
    today = client.get("/api/chores").get_json()["today"]
    assert chore["due_date"] == today

    done = client.post(f"/api/chores/{chore['id']}/complete", headers={"X-User": "Tom"}).get_json()
    assert done["due_date"] > today and done["last_done_by"] == "Tom"
    assert client.get("/api/chores/stats").get_json()["per_person"] == [{"done_by": "Tom", "count": 1}]

    undone = client.post(f"/api/chores/{chore['id']}/undo").get_json()
    assert undone["due_date"] == today and undone["last_done_by"] is None

    once = client.post("/api/chores", json={"title": "Réparer la porte"}).get_json()
    client.post(f"/api/chores/{once['id']}/complete")
    assert [c["title"] for c in client.get("/api/chores").get_json()["chores"]] == ["Aspirateur"]
    assert len(client.get("/api/chores?include_done=1").get_json()["chores"]) == 2
    assert client.post(f"/api/chores/{once['id']}/complete").status_code == 409

    # Supprimer un membre libère ses tâches.
    client.delete(f"/api/members/{member['id']}")
    assert client.get("/api/chores").get_json()["chores"][0]["member_id"] is None
    assert "Tâche ajoutée : Aspirateur" in history(client)


def test_shopping_multi_lists(client):
    data = client.get("/api/lists").get_json()
    main = data["lists"][0]
    assert main["name"] == "Courses" and "Crèmerie" in data["categories"]
    bio = client.post("/api/lists", json={"name": "Marché"}).get_json()

    created = client.post("/api/items", json={"name": "pommes", "quantity": "1 kg",
                                              "list_ids": [main["id"], bio["id"]]}).get_json()
    assert len(created) == 2 and created[0]["name"] == "Pommes"
    assert created[0]["category"] == "Fruits & légumes"

    lait = client.post("/api/items", json={"name": "Lait demi-écrémé", "list_id": main["id"]}).get_json()[0]
    assert lait["category"] == "Crèmerie"
    assert client.post("/api/items", json={"name": "Jus d'orange", "list_id": main["id"]}).get_json()[0][
        "category"] == "Boissons"

    # Ré-ajouter un article déjà présent ne crée pas de doublon.
    again = client.post("/api/items", json={"name": "LAIT DEMI-ÉCRÉMÉ", "quantity": "6", "list_id": main["id"]})
    assert again.get_json()[0]["id"] == lait["id"] and again.get_json()[0]["quantity"] == "6"

    client.put(f"/api/items/{lait['id']}", json={"checked": True})
    lists = {l["id"]: l for l in client.get("/api/lists").get_json()["lists"]}
    assert lists[main["id"]]["remaining"] == 2 and lists[main["id"]]["total"] == 3

    assert client.post(f"/api/lists/{main['id']}/clear-checked").get_json() == {"removed": 1}
    names = [i["name"] for i in client.get(f"/api/lists/{main['id']}/items").get_json()]
    assert names == ["Pommes", "Jus d'orange"]

    # Le rayon choisi est mémorisé pour la prochaine fois.
    pommes = created[0]
    client.put(f"/api/items/{pommes['id']}", json={"name": "Pommes", "category": "Autre", "list_id": bio["id"]})
    again = client.post("/api/items", json={"name": "pommes", "list_id": bio["id"]}).get_json()[0]
    assert again["category"] == "Autre"
    assert client.get("/api/catalog?q=pom").get_json()[0]["name"] == "Pommes"

    client.delete(f"/api/lists/{bio['id']}")
    assert client.delete(f"/api/lists/{main['id']}").status_code == 409
    assert client.get("/api/sync").get_json()["rev"] > 0


# --- Foyers ----------------------------------------------------------------

def make_friend(admin_client, app):
    res = admin_client.post("/api/admin/households", json={"name": "Chez Julie", "login": "julie",
                                                          "password": "julie-123"})
    assert res.status_code == 201
    friend = app.test_client()
    assert login(friend, "julie", "julie-123").status_code == 200
    return res.get_json(), friend


def test_households_are_isolated(app, client):
    mine = client.post("/api/events", json={"title": "Secret", "start": "2026-11-05T10:00"}).get_json()
    my_list = client.get("/api/lists").get_json()["lists"][0]
    my_item = client.post("/api/items", json={"name": "Chocolat", "list_id": my_list["id"]}).get_json()[0]
    member = client.post("/api/members", json={"name": "Léa"}).get_json()
    chore = client.post("/api/chores", json={"title": "Vaisselle"}).get_json()

    info, friend = make_friend(client, app)
    assert info["login"] == "julie" and info["is_admin"] == 0

    # Rien de mon foyer n'est visible ni modifiable par le sien.
    assert friend.get("/api/events?start=2026-11-01&end=2026-11-30").get_json() == []
    assert friend.get(f"/api/events/{mine['id']}").status_code == 404
    assert friend.delete(f"/api/events/{mine['id']}").status_code == 404
    assert friend.get("/api/members").get_json() == []
    assert friend.get("/api/chores").get_json()["chores"] == []
    assert friend.post(f"/api/chores/{chore['id']}/complete").status_code == 404
    assert friend.put(f"/api/items/{my_item['id']}", json={"checked": True}).status_code == 404
    assert friend.get(f"/api/lists/{my_list['id']}/items").status_code == 404
    assert friend.post("/api/items", json={"name": "x", "list_id": my_list["id"]}).status_code == 404
    assert friend.get("/api/catalog?q=choc").get_json() == []
    assert friend.post("/api/events", json={"title": "x", "start": "2026-11-05T10:00",
                                            "member_id": member["id"]}).status_code == 400
    assert friend.get("/api/history").get_json()["items"] == []

    # Il a sa propre liste par défaut et son propre lien iCal.
    friend_lists = friend.get("/api/lists").get_json()["lists"]
    assert [l["name"] for l in friend_lists] == ["Courses"] and friend_lists[0]["id"] != my_list["id"]
    friend.post("/api/events", json={"title": "Anniv Julie", "start": "2026-11-06T10:00"})
    token = friend.get("/api/settings").get_json()["ics_url"].split("token=")[1]
    body = client.get(f"/calendar.ics?token={token}").get_data(as_text=True)
    assert "Anniv Julie" in body and "Secret" not in body and "X-WR-CALNAME:Chez Julie" in body


def test_admin_rights(app, client):
    info, friend = make_friend(client, app)
    assert friend.get("/api/admin/households").status_code == 403
    assert friend.post("/api/admin/households", json={"name": "x", "login": "xxx", "password": "123456"}
                       ).status_code == 403
    assert [h["login"] for h in client.get("/api/admin/households").get_json()] == ["julie", "maison"]

    res = client.post("/api/admin/households", json={"name": "Doublon", "login": "Julie", "password": "123456"})
    assert res.status_code == 409
    assert client.post("/api/admin/households", json={"name": "x", "login": "a b", "password": "123456"}
                       ).status_code == 400
    assert client.post("/api/admin/households", json={"name": "x", "login": "court", "password": "123"}
                       ).status_code == 400

    # Réinitialiser son mot de passe le déconnecte.
    client.put(f"/api/admin/households/{info['id']}", json={"name": "Chez Julie", "login": "julie",
                                                            "password": "nouveau-mdp"})
    assert friend.get("/api/lists").status_code == 401
    assert login(friend, "julie", "nouveau-mdp").status_code == 200

    me = client.get("/api/status").get_json()["household"]
    assert client.delete(f"/api/admin/households/{me['id']}").status_code == 409
    friend.post("/api/members", json={"name": "Julie"})
    assert client.delete(f"/api/admin/households/{info['id']}").status_code == 200
    assert friend.get("/api/members").status_code == 401
    assert login(friend, "julie", "nouveau-mdp").status_code == 403


def test_password_change_and_logout_others(app, client):
    tablet = app.test_client()
    login(tablet)
    assert client.post("/api/household/password", json={"current": "faux", "new": "nouveau-mdp"}).status_code == 403
    assert client.post("/api/household/password", json={"current": PASSWORD, "new": "court"}).status_code == 400
    assert client.post("/api/household/password", json={"current": PASSWORD, "new": "nouveau-mdp"}).status_code == 200
    assert client.get("/api/lists").status_code == 200  # l'appareil qui a changé le mot de passe reste connecté
    assert tablet.get("/api/lists").status_code == 401
    assert login(tablet).status_code == 403
    assert login(tablet, password="nouveau-mdp").status_code == 200

    client.post("/api/household/logout-others")
    assert tablet.get("/api/lists").status_code == 401
    assert client.get("/api/lists").status_code == 200


def test_household_settings_and_weather_location(client):
    res = client.put("/api/household", json={"name": "Famille Leport", "city": "Lyon, France",
                                             "latitude": 45.75, "longitude": 4.85})
    assert res.get_json()["household"]["city"] == "Lyon, France"
    settings = client.get("/api/settings").get_json()["household"]
    assert settings["name"] == "Famille Leport" and settings["latitude"] == 45.75
    assert client.put("/api/household", json={"city": "x", "latitude": 200, "longitude": 0}).status_code == 400
    assert "Météo : Lyon, France" in history(client)


# --- Migration d'une base v1.1 (mono-foyer) --------------------------------

LEGACY_SCHEMA = """
CREATE TABLE members (id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#4f46e5',
    created_at TEXT NOT NULL);
CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '', start TEXT NOT NULL, end TEXT NOT NULL, all_day INTEGER NOT NULL DEFAULT 0,
    color TEXT NOT NULL DEFAULT '#4f46e5', member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    alerts TEXT NOT NULL DEFAULT '[]', recurrence TEXT NOT NULL DEFAULT 'none', recurrence_until TEXT,
    exdates TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX idx_events_start ON events(start);
CREATE TABLE chores (id INTEGER PRIMARY KEY, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
    room TEXT NOT NULL DEFAULT '', member_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
    freq_unit TEXT NOT NULL DEFAULT 'none', freq_interval INTEGER NOT NULL DEFAULT 1, due_date TEXT,
    active INTEGER NOT NULL DEFAULT 1, last_done_at TEXT, last_done_by TEXT, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE TABLE chore_completions (id INTEGER PRIMARY KEY,
    chore_id INTEGER NOT NULL REFERENCES chores(id) ON DELETE CASCADE, title TEXT NOT NULL, done_at TEXT NOT NULL,
    done_by TEXT NOT NULL DEFAULT '', prev_due TEXT, prev_active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE shopping_lists (id INTEGER PRIMARY KEY, name TEXT NOT NULL, icon TEXT NOT NULL DEFAULT '🛒',
    position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE shopping_items (id INTEGER PRIMARY KEY,
    list_id INTEGER NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE, name TEXT NOT NULL,
    quantity TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT 'Autre', note TEXT NOT NULL DEFAULT '',
    checked INTEGER NOT NULL DEFAULT 0, checked_at TEXT, added_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE item_catalog (name TEXT PRIMARY KEY COLLATE NOCASE, category TEXT NOT NULL DEFAULT 'Autre',
    uses INTEGER NOT NULL DEFAULT 0, last_used TEXT NOT NULL);
CREATE TABLE history (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '',
    module TEXT NOT NULL, action TEXT NOT NULL, entity_id INTEGER, summary TEXT NOT NULL, data TEXT);

INSERT INTO members VALUES (1, 'Léa', '#ec4899', '2026-11-04T10:00:00');
INSERT INTO events (id, title, start, end, alerts, created_at, updated_at)
    VALUES (1, 'Dîner', '2026-11-10T19:30', '2026-11-10T22:00', '[60]', '2026-11-04T10:00:00', '2026-11-04T10:00:00');
INSERT INTO chores (id, title, freq_unit, due_date, created_at, updated_at)
    VALUES (1, 'Poubelles', 'week', '2026-11-04', '2026-11-04T10:00:00', '2026-11-04T10:00:00');
INSERT INTO shopping_lists VALUES (1, 'Courses', '🛒', 0, '2026-11-04T10:00:00');
INSERT INTO shopping_items (id, list_id, name, created_at, updated_at)
    VALUES (1, 1, 'Lait', '2026-11-04T10:00:00', '2026-11-04T10:00:00');
INSERT INTO item_catalog VALUES ('Lait', 'Crèmerie', 3, '2026-11-04T10:00:00');
INSERT INTO history (ts, module, action, summary) VALUES ('2026-11-04T10:00:00', 'calendar', 'create', 'Ajout');
"""


def test_migration_from_single_household(tmp_path):
    from home_management.db import legacy_ics_token

    path = tmp_path / "home.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.close()

    app = create_app({"TESTING": True, "DATABASE": str(path), "APP_PASSWORD": PASSWORD, "SECRET_KEY": "prod-key"})
    assert os.path.exists(str(path) + ".avant-foyers.bak")

    # Un appareil connecté avant la mise à jour reste connecté (cookie de session v1.1).
    legacy_device = app.test_client()
    with legacy_device.session_transaction() as sess:
        sess["auth"] = True
    assert legacy_device.get("/api/members").get_json() == [{"id": 1, "name": "Léa", "color": "#ec4899"}]

    client = app.test_client()
    assert login(client).status_code == 200
    assert [e["title"] for e in client.get("/api/events?start=2026-11-01&end=2026-11-30").get_json()] == ["Dîner"]
    assert client.get("/api/chores").get_json()["chores"][0]["title"] == "Poubelles"
    assert [i["name"] for i in client.get("/api/lists/1/items").get_json()] == ["Lait"]
    assert client.get("/api/catalog?q=lai").get_json()[0]["category"] == "Crèmerie"
    assert client.get("/api/history").get_json()["items"][0]["summary"] == "Ajout"

    # L'abonnement iPhone existant (jeton dérivé de SECRET_KEY) continue de fonctionner.
    with app.app_context():
        token = legacy_ics_token()
    assert client.get("/api/settings").get_json()["ics_url"].endswith(token)
    assert "Dîner" in client.get(f"/calendar.ics?token={token}").get_data(as_text=True)

    # Relancer l'app ne refait pas la migration.
    create_app({"TESTING": True, "DATABASE": str(path), "APP_PASSWORD": "autre", "SECRET_KEY": "prod-key"})
    assert login(app.test_client()).status_code == 200
