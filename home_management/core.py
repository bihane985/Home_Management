"""Connexion par foyer, synchronisation, membres, historique, réglages et administration des foyers."""
import secrets
import time

from flask import Blueprint, abort, current_app, g, jsonify, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from . import __version__
from .db import (
    HOUSEHOLD_TABLES,
    color,
    create_household,
    fetch_one,
    get_db,
    hid,
    household,
    log,
    login_name,
    now_iso,
    password,
    payload,
    rows,
    text,
)

bp = Blueprint("core", __name__, url_prefix="/api")

PUBLIC_ENDPOINTS = {"core.status", "core.login", "core.logout"}
MODULES = {"calendar", "chores", "shopping", "settings"}


def current_household():
    """Foyer de la session, ou None si la session est absente ou révoquée."""
    household_id = session.get("hid")
    if household_id is None and session.get("auth"):
        # Session ouverte avant la gestion des foyers (v1.1) : elle appartient au foyer n°1.
        household_id = 1
        row = get_db().execute("SELECT session_version FROM households WHERE id = 1").fetchone()
        session.update(hid=1, ver=row["session_version"] if row else 1)
        session.pop("auth", None)
    if household_id is None:
        return None
    row = get_db().execute("SELECT * FROM households WHERE id = ?", (household_id,)).fetchone()
    if row is None or row["session_version"] != session.get("ver"):
        session.clear()
        return None
    return dict(row)


def public(h):
    return {"id": h["id"], "name": h["name"], "login": h["login"], "is_admin": bool(h["is_admin"]),
            "city": h["city"], "latitude": h["latitude"], "longitude": h["longitude"]}


def open_session(h):
    session.clear()
    session.permanent = True
    session.update(hid=h["id"], ver=h["session_version"])


@bp.before_app_request
def check_auth():
    if not request.path.startswith("/api/") or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    g.household = current_household()
    if g.household is None:
        abort(401, "Authentification requise.")
    return None


def require_admin():
    if not household()["is_admin"]:
        abort(403, "Réservé au foyer administrateur.")


@bp.get("/status")
def status():
    h = current_household()
    return jsonify(version=__version__, authenticated=h is not None, household=public(h) if h else None)


@bp.post("/login")
def login():
    data = payload()
    login_value = str(data.get("login") or "").strip().lower()
    pwd = str(data.get("password") or "")
    h = get_db().execute("SELECT * FROM households WHERE login = ?", (login_value,)).fetchone()
    if h is None or not check_password_hash(h["password_hash"], pwd):
        time.sleep(1)  # freine les essais de mots de passe en série
        abort(403, "Identifiant ou mot de passe incorrect.")
    open_session(h)
    return jsonify(household=public(h))


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/sync")
def sync():
    """Révision courante du foyer : les appareils rechargent leurs données quand elle change."""
    rev = get_db().execute("SELECT COALESCE(MAX(id), 0) FROM history WHERE household_id = ?", (hid(),)).fetchone()[0]
    return jsonify(rev=rev)


# --- Membres -------------------------------------------------------------

def member(member_id):
    return fetch_one("SELECT id, name, color FROM members WHERE id = ? AND household_id = ?", (member_id, hid()))


@bp.get("/members")
def list_members():
    return jsonify(rows("SELECT id, name, color FROM members WHERE household_id = ? ORDER BY name COLLATE NOCASE",
                        (hid(),)))


@bp.post("/members")
def create_member():
    data = payload()
    name, member_color = text(data, "name", 60, required=True), color(data)
    conn = get_db()
    cur = conn.execute("INSERT INTO members (household_id, name, color, created_at) VALUES (?, ?, ?, ?)",
                       (hid(), name, member_color, now_iso()))
    log("settings", "create", f"Membre ajouté : {name}", cur.lastrowid)
    conn.commit()
    return jsonify(member(cur.lastrowid)), 201


@bp.put("/members/<int:member_id>")
def update_member(member_id):
    member(member_id)
    data = payload()
    name, member_color = text(data, "name", 60, required=True), color(data)
    conn = get_db()
    conn.execute("UPDATE members SET name = ?, color = ? WHERE id = ?", (name, member_color, member_id))
    log("settings", "update", f"Membre modifié : {name}", member_id)
    conn.commit()
    return jsonify(member(member_id))


@bp.delete("/members/<int:member_id>")
def delete_member(member_id):
    m = member(member_id)
    conn = get_db()
    conn.execute("DELETE FROM members WHERE id = ?", (member_id,))
    log("settings", "delete", f"Membre retiré : {m['name']}", member_id)
    conn.commit()
    return jsonify(ok=True)


# --- Historique ----------------------------------------------------------

@bp.get("/history")
def history():
    module = request.args.get("module", "")
    limit = min(max(request.args.get("limit", 50, type=int), 1), 200)
    before = request.args.get("before", type=int)
    where, params = ["household_id = ?"], [hid()]
    if module in MODULES:
        where.append("module = ?")
        params.append(module)
    if before:
        where.append("id < ?")
        params.append(before)
    items = rows(
        f"SELECT id, ts, actor, module, action, entity_id, summary FROM history WHERE {' AND '.join(where)} "
        "ORDER BY id DESC LIMIT ?",
        (*params, limit + 1),
    )
    return jsonify(items=items[:limit], more=len(items) > limit)


# --- Réglages du foyer ---------------------------------------------------

@bp.get("/settings")
def settings():
    return jsonify(
        household=public(household()),
        ics_url=url_for("ics.feed", token=household()["ics_token"], _external=True),
        timezone=current_app.config["TIMEZONE"],
        version=__version__,
    )


@bp.put("/household")
def update_household():
    """Nom du foyer et lieu de la météo."""
    data = payload()
    h = household()
    name = text(data, "name", 60) or h["name"]
    city, lat, lon = h["city"], h["latitude"], h["longitude"]
    if "city" in data:
        city = text(data, "city", 100)
        try:
            lat = float(data["latitude"]) if data.get("latitude") is not None else None
            lon = float(data["longitude"]) if data.get("longitude") is not None else None
        except (TypeError, ValueError):
            abort(400, "Coordonnées invalides.")
        if (lat is None) != (lon is None) or (lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180)):
            abort(400, "Coordonnées invalides.")
    conn = get_db()
    conn.execute("UPDATE households SET name = ?, city = ?, latitude = ?, longitude = ? WHERE id = ?",
                 (name, city, lat, lon, h["id"]))
    if name != h["name"]:
        log("settings", "update", f"Foyer renommé : {h['name']} → {name}")
    if city != h["city"] or lat != h["latitude"]:
        log("settings", "update", f"Météo : {city or 'lieu retiré'}")
    conn.commit()
    return jsonify(household=public(fetch_one("SELECT * FROM households WHERE id = ?", (h["id"],))))


def bump_sessions(conn, household_id):
    """Invalide toutes les sessions du foyer ; la session courante reste ouverte si c'est le même foyer."""
    conn.execute("UPDATE households SET session_version = session_version + 1 WHERE id = ?", (household_id,))
    if household_id == session.get("hid"):
        session["ver"] = conn.execute("SELECT session_version FROM households WHERE id = ?",
                                      (household_id,)).fetchone()[0]


@bp.post("/household/password")
def change_password():
    data = payload()
    h = household()
    if not check_password_hash(h["password_hash"], str(data.get("current") or "")):
        time.sleep(1)
        abort(403, "Mot de passe actuel incorrect.")
    new = password(data, "new")
    conn = get_db()
    conn.execute("UPDATE households SET password_hash = ? WHERE id = ?", (generate_password_hash(new), h["id"]))
    if data.get("logout_others", True):
        bump_sessions(conn, h["id"])
    log("settings", "update", "Mot de passe du foyer modifié")
    conn.commit()
    return jsonify(ok=True)


@bp.post("/household/logout-others")
def logout_others():
    conn = get_db()
    bump_sessions(conn, hid())
    log("settings", "update", "Autres appareils déconnectés")
    conn.commit()
    return jsonify(ok=True)


@bp.post("/household/ics-token")
def regenerate_ics_token():
    conn = get_db()
    conn.execute("UPDATE households SET ics_token = ? WHERE id = ?", (secrets.token_hex(16), hid()))
    log("settings", "update", "Lien d'abonnement calendrier régénéré")
    conn.commit()
    g.household = fetch_one("SELECT * FROM households WHERE id = ?", (hid(),))
    return settings()


# --- Administration des foyers -------------------------------------------

def admin_view(household_id):
    return fetch_one(
        "SELECT h.id, h.name, h.login, h.is_admin, h.created_at, "
        "(SELECT COUNT(*) FROM members m WHERE m.household_id = h.id) AS members, "
        "(SELECT MAX(ts) FROM history x WHERE x.household_id = h.id) AS last_activity "
        "FROM households h WHERE h.id = ?",
        (household_id,),
    )


@bp.get("/admin/households")
def admin_list():
    require_admin()
    ids = [r["id"] for r in get_db().execute("SELECT id FROM households ORDER BY name COLLATE NOCASE")]
    return jsonify([admin_view(i) for i in ids])


def unique_login(conn, value, exclude_id=None):
    row = conn.execute("SELECT id FROM households WHERE login = ?", (value,)).fetchone()
    if row and row["id"] != exclude_id:
        abort(409, f"L'identifiant « {value} » est déjà utilisé.")


@bp.post("/admin/households")
def admin_create():
    require_admin()
    data = payload()
    name, login_value, pwd = text(data, "name", 60, required=True), login_name(data), password(data)
    conn = get_db()
    unique_login(conn, login_value)
    new_id = create_household(conn, name, login_value, pwd)
    log("settings", "create", f"Foyer créé : {name} ({login_value})", new_id)
    conn.commit()
    return jsonify(admin_view(new_id)), 201


@bp.put("/admin/households/<int:household_id>")
def admin_update(household_id):
    require_admin()
    target = fetch_one("SELECT * FROM households WHERE id = ?", (household_id,))
    data = payload()
    name, login_value = text(data, "name", 60, required=True), login_name(data)
    conn = get_db()
    unique_login(conn, login_value, exclude_id=household_id)
    conn.execute("UPDATE households SET name = ?, login = ? WHERE id = ?", (name, login_value, household_id))
    summary = f"Foyer modifié : {name} ({login_value})"
    if data.get("password"):
        conn.execute("UPDATE households SET password_hash = ? WHERE id = ?",
                     (generate_password_hash(password(data)), household_id))
        bump_sessions(conn, household_id)
        summary += ", mot de passe réinitialisé"
    log("settings", "update", summary, household_id, {"before": {"name": target["name"], "login": target["login"]}})
    conn.commit()
    return jsonify(admin_view(household_id))


@bp.delete("/admin/households/<int:household_id>")
def admin_delete(household_id):
    require_admin()
    if household_id == hid():
        abort(409, "Vous ne pouvez pas supprimer votre propre foyer.")
    target = fetch_one("SELECT * FROM households WHERE id = ?", (household_id,))
    conn = get_db()
    # Les articles et réalisations de tâches suivent par ON DELETE CASCADE.
    for table in HOUSEHOLD_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE household_id = ?", (household_id,))
    conn.execute("DELETE FROM households WHERE id = ?", (household_id,))
    log("settings", "delete", f"Foyer supprimé : {target['name']} ({target['login']})", household_id)
    conn.commit()
    return jsonify(ok=True)
