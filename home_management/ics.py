"""Flux iCalendar : abonnement depuis l'app Calendrier de l'iPhone (alertes natives)."""
from datetime import datetime, timedelta

from flask import Blueprint, Response, abort, request

from .calendar import FMT, serialize
from .db import get_db, now_iso, rows

bp = Blueprint("ics", __name__)

RRULE = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY"}


def escape(value):
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    """Coupe les lignes à 75 octets comme l'exige la RFC 5545."""
    raw = line.encode()
    if len(raw) <= 75:
        return line
    parts, current = [], b""
    for char in line:
        encoded = char.encode()
        if len(current) + len(encoded) > (75 if not parts else 74):
            parts.append(current.decode())
            current = b""
        current += encoded
    parts.append(current.decode())
    return "\r\n ".join(parts)


def ics_dt(value, all_day):
    dt = datetime.strptime(value, FMT)
    return dt.strftime("%Y%m%d") if all_day else dt.strftime("%Y%m%dT%H%M%S")


def build_event(event, stamp):
    all_day = event["all_day"]
    prop = ";VALUE=DATE" if all_day else ""
    end = event["end"]
    if all_day:  # DTEND est exclusif pour les journées entières
        end = (datetime.strptime(end, FMT) + timedelta(days=1)).strftime(FMT)
    lines = [
        "BEGIN:VEVENT",
        f"UID:event-{event['id']}@home-management",
        f"DTSTAMP:{stamp}",
        f"DTSTART{prop}:{ics_dt(event['start'], all_day)}",
        f"DTEND{prop}:{ics_dt(end, all_day)}",
        f"SUMMARY:{escape(event['title'])}",
    ]
    if event["location"]:
        lines.append(f"LOCATION:{escape(event['location'])}")
    if event["description"]:
        lines.append(f"DESCRIPTION:{escape(event['description'])}")
    if event["recurrence"] != "none":
        rule = f"RRULE:FREQ={RRULE[event['recurrence']]}"
        if event["recurrence_until"]:
            rule += f";UNTIL={event['recurrence_until'].replace('-', '')}T235959"
        lines.append(rule)
        for exdate in event["exdates"]:
            lines.append(f"EXDATE{prop}:{ics_dt(exdate, all_day)}")
    for minutes in event["alerts"]:
        sign = "-" if minutes >= 0 else ""
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{escape(event['title'])}",
            f"TRIGGER:{sign}PT{abs(minutes)}M",
            "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return lines


@bp.get("/calendar.ics")
def feed():
    token = request.args.get("token", "")
    household = get_db().execute("SELECT id, name FROM households WHERE ics_token = ?", (token,)).fetchone()
    if not token or household is None:
        abort(403)
    stamp = datetime.fromisoformat(now_iso()).strftime("%Y%m%dT%H%M%S")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Home_Management//FR",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{escape(household['name'])}",
        "X-PUBLISHED-TTL:PT15M",
        "REFRESH-INTERVAL;VALUE=DURATION:PT15M",
    ]
    for row in rows("SELECT * FROM events WHERE household_id = ? ORDER BY start", (household["id"],)):
        lines += build_event(serialize(row), stamp)
    lines.append("END:VCALENDAR")
    body = "\r\n".join(fold(line) for line in lines) + "\r\n"
    return Response(body, mimetype="text/calendar", headers={"Content-Disposition": "inline; filename=maison.ics"})
