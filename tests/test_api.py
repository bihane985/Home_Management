import pytest

from home_management import create_app
from home_management.db import add_months
from datetime import date


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.db"), "APP_PASSWORD": ""})


@pytest.fixture
def client(app):
    return app.test_client()


def history(client):
    return [h["summary"] for h in client.get("/api/history").get_json()["items"]]


def test_add_months_clamps_day():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)


def test_auth_required_when_password_set(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "a.db"), "APP_PASSWORD": "secret"})
    client = app.test_client()
    assert client.get("/api/lists").status_code == 401
    assert client.get("/api/status").get_json()["authenticated"] is False
    assert client.post("/api/login", json={"password": "nope"}).status_code == 403
    assert client.post("/api/login", json={"password": "secret"}).status_code == 200
    assert client.get("/api/lists").status_code == 200


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


def test_ics_feed(app, client):
    client.post("/api/events", json={"title": "Dentiste, Dr Martin", "start": "2026-11-05T09:30",
                                     "alerts": [60], "recurrence": "yearly"})
    url = client.get("/api/settings").get_json()["ics_url"]
    token = url.split("token=")[1]
    assert client.get("/calendar.ics?token=wrong").status_code == 403
    body = client.get(f"/calendar.ics?token={token}").get_data(as_text=True)
    assert "SUMMARY:Dentiste\\, Dr Martin" in body
    assert "DTSTART:20261105T093000" in body
    assert "TRIGGER:-PT60M" in body and "RRULE:FREQ=YEARLY" in body


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
