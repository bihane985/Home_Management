"""Calendrier : événements, récurrences et alertes."""
import json
from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify, request

from .db import (
    add_months,
    color,
    fetch_one,
    get_db,
    hid,
    log,
    member_ref,
    now_iso,
    parse_date,
    parse_datetime,
    payload,
    rows,
    text,
)

bp = Blueprint("calendar", __name__, url_prefix="/api/events")

RECURRENCES = {"none", "daily", "weekly", "monthly", "yearly"}
MAX_ALERT_MINUTES = 60 * 24 * 28
FMT = "%Y-%m-%dT%H:%M"


def fmt(value):
    return value.strftime(FMT)


def serialize(row):
    event = dict(row)
    event["all_day"] = bool(event["all_day"])
    event["alerts"] = json.loads(event["alerts"])
    event["exdates"] = json.loads(event["exdates"])
    return event


def clean(data):
    all_day = bool(data.get("all_day"))
    start = parse_datetime(data.get("start"), "début")
    end = parse_datetime(data["end"], "fin") if data.get("end") else None
    if all_day:
        start = start.replace(hour=0, minute=0)
        end = (end or start).replace(hour=23, minute=59)
    elif end is None:
        end = start + timedelta(hours=1)
    if end < start:
        abort(400, "La fin doit être après le début.")

    alerts = data.get("alerts") or []
    if not isinstance(alerts, list):
        abort(400, "Alertes invalides.")
    try:
        alerts = sorted({int(a) for a in alerts})
    except (TypeError, ValueError):
        abort(400, "Alertes invalides.")
    if len(alerts) > 5 or any(abs(a) > MAX_ALERT_MINUTES for a in alerts):
        abort(400, "Alertes invalides (5 maximum, 4 semaines au plus).")

    recurrence = data.get("recurrence") or "none"
    if recurrence not in RECURRENCES:
        abort(400, "Récurrence invalide.")
    until = data.get("recurrence_until") if recurrence != "none" else None
    if until:
        until = parse_date(until, "fin de récurrence").isoformat()

    return {
        "title": text(data, "title", required=True),
        "description": text(data, "description", 2000),
        "location": text(data, "location"),
        "start": fmt(start),
        "end": fmt(end),
        "all_day": int(all_day),
        "color": color(data),
        "member_id": member_ref(data),
        "alerts": json.dumps(alerts),
        "recurrence": recurrence,
        "recurrence_until": until or None,
    }


def occurrences(event, range_start, range_end):
    """Occurrences (début, fin) de l'événement qui chevauchent [range_start, range_end[."""
    start = datetime.strptime(event["start"], FMT)
    duration = datetime.strptime(event["end"], FMT) - start
    exdates = set(event["exdates"])
    until = None
    if event["recurrence_until"]:
        until = datetime.fromisoformat(event["recurrence_until"]).replace(hour=23, minute=59)
    freq = event["recurrence"]

    def candidates():
        if freq == "none":
            yield start
        elif freq in ("daily", "weekly"):
            step = timedelta(days=1 if freq == "daily" else 7)
            skip = max((range_start - duration - start) // step, 0)
            current = start + skip * step
            while True:
                yield current
                current += step
        else:
            months = 1 if freq == "monthly" else 12
            elapsed = (range_start.year - start.year) * 12 + range_start.month - start.month - 1
            n = max(elapsed // months, 0)
            while True:
                yield add_months(start, n * months)
                n += 1

    for occ_start in candidates():
        if occ_start >= range_end or (until and occ_start > until):
            break
        occ_end = occ_start + duration
        if occ_end >= range_start and fmt(occ_start) not in exdates:
            yield occ_start, occ_end


def event_row(event_id):
    return fetch_one("SELECT * FROM events WHERE id = ? AND household_id = ?", (event_id, hid()))


def events_between(household_id, range_start, range_end):
    candidates = rows(
        "SELECT * FROM events WHERE household_id = ? AND start < ? AND (recurrence != 'none' OR end >= ?) "
        "AND (recurrence_until IS NULL OR recurrence_until >= ?)",
        (household_id, fmt(range_end), fmt(range_start), range_start.date().isoformat()),
    )
    result = []
    for row in candidates:
        event = serialize(row)
        for occ_start, occ_end in occurrences(event, range_start, range_end):
            result.append({**event, "occurrence_start": fmt(occ_start), "occurrence_end": fmt(occ_end)})
    result.sort(key=lambda e: (e["occurrence_start"][:10], not e["all_day"], e["occurrence_start"]))
    return result


@bp.get("")
def list_events():
    start = parse_date(request.args.get("start"), "start")
    end = parse_date(request.args.get("end"), "end")
    if end < start or (end - start).days > 400:
        abort(400, "Période invalide.")
    range_start = datetime.combine(start, datetime.min.time())
    range_end = datetime.combine(end + timedelta(days=1), datetime.min.time())
    return jsonify(events_between(hid(), range_start, range_end))


@bp.get("/<int:event_id>")
def get_event(event_id):
    return jsonify(serialize(event_row(event_id)))


@bp.post("")
def create_event():
    fields = {"household_id": hid(), **clean(payload())}
    now = now_iso()
    conn = get_db()
    cols = ", ".join(fields)
    cur = conn.execute(
        f"INSERT INTO events ({cols}, created_at, updated_at) VALUES ({', '.join('?' * len(fields))}, ?, ?)",
        (*fields.values(), now, now),
    )
    log("calendar", "create", f"Événement ajouté : {fields['title']} ({describe(fields)})", cur.lastrowid, fields)
    conn.commit()
    return jsonify(serialize(event_row(cur.lastrowid))), 201


@bp.put("/<int:event_id>")
def update_event(event_id):
    before = event_row(event_id)
    fields = clean(payload())
    conn = get_db()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE events SET {assignments}, updated_at = ? WHERE id = ?", (*fields.values(), now_iso(), event_id)
    )
    log("calendar", "update", f"Événement modifié : {fields['title']} ({describe(fields)})", event_id,
        {"before": before, "after": fields})
    conn.commit()
    return jsonify(serialize(event_row(event_id)))


@bp.delete("/<int:event_id>")
def delete_event(event_id):
    event = serialize(event_row(event_id))
    occurrence = request.args.get("occurrence")
    conn = get_db()
    if occurrence and event["recurrence"] != "none":
        occurrence = fmt(parse_datetime(occurrence, "occurrence"))
        exdates = sorted(set(event["exdates"]) | {occurrence})
        conn.execute("UPDATE events SET exdates = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(exdates), now_iso(), event_id))
        log("calendar", "delete", f"Occurrence supprimée : {event['title']} du {human(occurrence, event['all_day'])}",
            event_id)
    else:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        log("calendar", "delete", f"Événement supprimé : {event['title']} ({describe(event)})", event_id, event)
    conn.commit()
    return jsonify(ok=True)


def human(value, all_day):
    dt = datetime.strptime(value, FMT)
    return dt.strftime("%d/%m/%Y") if all_day else dt.strftime("%d/%m/%Y %H:%M")


def describe(event):
    label = human(event["start"], event["all_day"])
    if event["recurrence"] != "none":
        label += ", " + {"daily": "chaque jour", "weekly": "chaque semaine", "monthly": "chaque mois",
                         "yearly": "chaque année"}[event["recurrence"]]
    return label
