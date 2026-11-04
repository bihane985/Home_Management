"""Authentification, synchronisation, membres du foyer, historique et réglages."""
import hashlib
import hmac

from flask import Blueprint, abort, current_app, jsonify, request, session, url_for

from . import __version__
from .db import color, fetch_one, get_db, log, now_iso, payload, rows, text

bp = Blueprint("core", __name__, url_prefix="/api")

PUBLIC_ENDPOINTS = {"core.status", "core.login"}
MODULES = {"calendar", "chores", "shopping", "settings"}


def auth_required():
    return bool(current_app.config["APP_PASSWORD"])


def ics_token():
    token = current_app.config["ICS_TOKEN"]
    if token:
        return token
    secret = current_app.config["SECRET_KEY"].encode()
    return hmac.new(secret, b"home-management-ics", hashlib.sha256).hexdigest()[:32]


@bp.before_app_request
def check_auth():
    if not request.path.startswith("/api/") or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if auth_required() and not session.get("auth"):
        abort(401, "Authentification requise.")
    return None


@bp.get("/status")
def status():
    return jsonify(
        version=__version__,
        auth_required=auth_required(),
        authenticated=not auth_required() or bool(session.get("auth")),
    )


@bp.post("/login")
def login():
    password = str(payload().get("password", ""))
    if not auth_required():
        return jsonify(ok=True)
    if not hmac.compare_digest(password.encode(), current_app.config["APP_PASSWORD"].encode()):
        abort(403, "Mot de passe incorrect.")
    session.permanent = True
    session["auth"] = True
    return jsonify(ok=True)


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/sync")
def sync():
    """Révision courante : les appareils rechargent leurs données quand elle change."""
    rev = get_db().execute("SELECT COALESCE(MAX(id), 0) FROM history").fetchone()[0]
    return jsonify(rev=rev)


# --- Membres -------------------------------------------------------------

@bp.get("/members")
def list_members():
    return jsonify(rows("SELECT id, name, color FROM members ORDER BY name COLLATE NOCASE"))


@bp.post("/members")
def create_member():
    data = payload()
    name, member_color = text(data, "name", 60, required=True), color(data)
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO members (name, color, created_at) VALUES (?, ?, ?)", (name, member_color, now_iso())
    )
    log("settings", "create", f"Membre ajouté : {name}", cur.lastrowid)
    conn.commit()
    return jsonify(fetch_one("SELECT id, name, color FROM members WHERE id = ?", (cur.lastrowid,))), 201


@bp.put("/members/<int:member_id>")
def update_member(member_id):
    fetch_one("SELECT id FROM members WHERE id = ?", (member_id,))
    data = payload()
    name, member_color = text(data, "name", 60, required=True), color(data)
    conn = get_db()
    conn.execute("UPDATE members SET name = ?, color = ? WHERE id = ?", (name, member_color, member_id))
    log("settings", "update", f"Membre modifié : {name}", member_id)
    conn.commit()
    return jsonify(fetch_one("SELECT id, name, color FROM members WHERE id = ?", (member_id,)))


@bp.delete("/members/<int:member_id>")
def delete_member(member_id):
    member = fetch_one("SELECT id, name FROM members WHERE id = ?", (member_id,))
    conn = get_db()
    conn.execute("DELETE FROM members WHERE id = ?", (member_id,))
    log("settings", "delete", f"Membre retiré : {member['name']}", member_id)
    conn.commit()
    return jsonify(ok=True)


# --- Historique ----------------------------------------------------------

@bp.get("/history")
def history():
    module = request.args.get("module", "")
    limit = min(max(request.args.get("limit", 50, type=int), 1), 200)
    before = request.args.get("before", type=int)
    where, params = [], []
    if module in MODULES:
        where.append("module = ?")
        params.append(module)
    if before:
        where.append("id < ?")
        params.append(before)
    sql = "SELECT id, ts, actor, module, action, entity_id, summary FROM history"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    items = rows(sql, (*params, limit + 1))
    return jsonify(items=items[:limit], more=len(items) > limit)


# --- Réglages ------------------------------------------------------------

@bp.get("/settings")
def settings():
    return jsonify(
        ics_url=url_for("ics.feed", token=ics_token(), _external=True),
        timezone=current_app.config["TIMEZONE"],
        version=__version__,
    )
