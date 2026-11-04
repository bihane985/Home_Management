"""Tâches ménagères : tâches ponctuelles ou récurrentes, réalisations et statistiques."""
from datetime import timedelta

from flask import Blueprint, abort, jsonify, request

from .db import (
    actor,
    add_months,
    fetch_one,
    get_db,
    log,
    member_ref,
    now_iso,
    parse_date,
    payload,
    rows,
    text,
    today,
)

bp = Blueprint("chores", __name__, url_prefix="/api/chores")

UNITS = {"none", "day", "week", "month"}


def serialize(row):
    chore = dict(row)
    chore["active"] = bool(chore["active"])
    return chore


def clean(data):
    unit = data.get("freq_unit") or "none"
    if unit not in UNITS:
        abort(400, "Fréquence invalide.")
    try:
        interval = int(data.get("freq_interval") or 1)
    except (TypeError, ValueError):
        abort(400, "Intervalle invalide.")
    if not 1 <= interval <= 365:
        abort(400, "L'intervalle doit être compris entre 1 et 365.")
    due = data.get("due_date")
    due = parse_date(due, "échéance").isoformat() if due else None
    if unit != "none" and due is None:
        due = today().isoformat()
    return {
        "title": text(data, "title", required=True),
        "notes": text(data, "notes", 1000),
        "room": text(data, "room", 60),
        "member_id": member_ref(data),
        "freq_unit": unit,
        "freq_interval": interval if unit != "none" else 1,
        "due_date": due,
    }


def next_due(chore, done_on):
    unit, n = chore["freq_unit"], chore["freq_interval"]
    if unit == "day":
        return done_on + timedelta(days=n)
    if unit == "week":
        return done_on + timedelta(weeks=n)
    return add_months(done_on, n)


@bp.get("")
def list_chores():
    chores = rows(
        "SELECT * FROM chores WHERE active = 1 ORDER BY due_date IS NULL, due_date, title COLLATE NOCASE"
    )
    if request.args.get("include_done"):
        chores += rows("SELECT * FROM chores WHERE active = 0 ORDER BY last_done_at DESC LIMIT 50")
    return jsonify(today=today().isoformat(), chores=[serialize(c) for c in chores])


@bp.post("")
def create_chore():
    fields = clean(payload())
    now = now_iso()
    conn = get_db()
    cur = conn.execute(
        f"INSERT INTO chores ({', '.join(fields)}, created_at, updated_at) "
        f"VALUES ({', '.join('?' * len(fields))}, ?, ?)",
        (*fields.values(), now, now),
    )
    log("chores", "create", f"Tâche ajoutée : {fields['title']}", cur.lastrowid, fields)
    conn.commit()
    return jsonify(serialize(fetch_one("SELECT * FROM chores WHERE id = ?", (cur.lastrowid,)))), 201


@bp.put("/<int:chore_id>")
def update_chore(chore_id):
    before = fetch_one("SELECT * FROM chores WHERE id = ?", (chore_id,))
    fields = clean(payload())
    conn = get_db()
    conn.execute(
        f"UPDATE chores SET {', '.join(f'{k} = ?' for k in fields)}, updated_at = ? WHERE id = ?",
        (*fields.values(), now_iso(), chore_id),
    )
    log("chores", "update", f"Tâche modifiée : {fields['title']}", chore_id, {"before": before, "after": fields})
    conn.commit()
    return jsonify(serialize(fetch_one("SELECT * FROM chores WHERE id = ?", (chore_id,))))


@bp.delete("/<int:chore_id>")
def delete_chore(chore_id):
    chore = fetch_one("SELECT * FROM chores WHERE id = ?", (chore_id,))
    conn = get_db()
    conn.execute("DELETE FROM chores WHERE id = ?", (chore_id,))
    log("chores", "delete", f"Tâche supprimée : {chore['title']}", chore_id, chore)
    conn.commit()
    return jsonify(ok=True)


@bp.post("/<int:chore_id>/complete")
def complete_chore(chore_id):
    chore = fetch_one("SELECT * FROM chores WHERE id = ?", (chore_id,))
    if not chore["active"]:
        abort(409, "Cette tâche est déjà terminée.")
    who, now, done_on = actor(), now_iso(), today()
    conn = get_db()
    conn.execute(
        "INSERT INTO chore_completions (chore_id, title, done_at, done_by, prev_due, prev_active) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (chore_id, chore["title"], now, who, chore["due_date"]),
    )
    if chore["freq_unit"] == "none":
        conn.execute(
            "UPDATE chores SET active = 0, last_done_at = ?, last_done_by = ?, updated_at = ? WHERE id = ?",
            (now, who, now, chore_id),
        )
        summary = f"Tâche terminée : {chore['title']}"
    else:
        due = next_due(chore, done_on).isoformat()
        conn.execute(
            "UPDATE chores SET due_date = ?, last_done_at = ?, last_done_by = ?, updated_at = ? WHERE id = ?",
            (due, now, who, now, chore_id),
        )
        summary = f"Tâche faite : {chore['title']} (prochaine fois le {due[8:10]}/{due[5:7]})"
    log("chores", "complete", summary, chore_id)
    conn.commit()
    return jsonify(serialize(fetch_one("SELECT * FROM chores WHERE id = ?", (chore_id,))))


@bp.post("/<int:chore_id>/undo")
def undo_completion(chore_id):
    chore = fetch_one("SELECT * FROM chores WHERE id = ?", (chore_id,))
    last = get_db().execute(
        "SELECT * FROM chore_completions WHERE chore_id = ? ORDER BY id DESC LIMIT 1", (chore_id,)
    ).fetchone()
    if last is None:
        abort(409, "Aucune réalisation à annuler.")
    conn = get_db()
    conn.execute("DELETE FROM chore_completions WHERE id = ?", (last["id"],))
    previous = conn.execute(
        "SELECT done_at, done_by FROM chore_completions WHERE chore_id = ? ORDER BY id DESC LIMIT 1", (chore_id,)
    ).fetchone()
    conn.execute(
        "UPDATE chores SET due_date = ?, active = ?, last_done_at = ?, last_done_by = ?, updated_at = ? WHERE id = ?",
        (
            last["prev_due"],
            last["prev_active"],
            previous["done_at"] if previous else None,
            previous["done_by"] if previous else None,
            now_iso(),
            chore_id,
        ),
    )
    log("chores", "undo", f"Réalisation annulée : {chore['title']}", chore_id)
    conn.commit()
    return jsonify(serialize(fetch_one("SELECT * FROM chores WHERE id = ?", (chore_id,))))


@bp.get("/stats")
def stats():
    days = min(max(request.args.get("days", 30, type=int), 1), 365)
    since = (today() - timedelta(days=days - 1)).isoformat()
    per_person = rows(
        "SELECT done_by, COUNT(*) AS count FROM chore_completions WHERE done_at >= ? "
        "GROUP BY done_by ORDER BY count DESC",
        (since,),
    )
    return jsonify(days=days, per_person=per_person)
