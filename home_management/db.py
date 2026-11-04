"""Accès SQLite, utilitaires de dates et journal d'historique."""
import json
import re
import sqlite3
from datetime import date, datetime
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from flask import abort, current_app, g, request

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    color       TEXT NOT NULL DEFAULT '#4f46e5',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id                INTEGER PRIMARY KEY,
    title             TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    location          TEXT NOT NULL DEFAULT '',
    start             TEXT NOT NULL,              -- YYYY-MM-DDTHH:MM (heure locale du foyer)
    end               TEXT NOT NULL,
    all_day           INTEGER NOT NULL DEFAULT 0,
    color             TEXT NOT NULL DEFAULT '#4f46e5',
    member_id         INTEGER REFERENCES members(id) ON DELETE SET NULL,
    alerts            TEXT NOT NULL DEFAULT '[]', -- minutes avant le début
    recurrence        TEXT NOT NULL DEFAULT 'none',
    recurrence_until  TEXT,
    exdates           TEXT NOT NULL DEFAULT '[]', -- occurrences supprimées
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start);

CREATE TABLE IF NOT EXISTS chores (
    id             INTEGER PRIMARY KEY,
    title          TEXT NOT NULL,
    notes          TEXT NOT NULL DEFAULT '',
    room           TEXT NOT NULL DEFAULT '',
    member_id      INTEGER REFERENCES members(id) ON DELETE SET NULL,
    freq_unit      TEXT NOT NULL DEFAULT 'none',  -- none | day | week | month
    freq_interval  INTEGER NOT NULL DEFAULT 1,
    due_date       TEXT,                          -- YYYY-MM-DD
    active         INTEGER NOT NULL DEFAULT 1,
    last_done_at   TEXT,
    last_done_by   TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chore_completions (
    id           INTEGER PRIMARY KEY,
    chore_id     INTEGER NOT NULL REFERENCES chores(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    done_at      TEXT NOT NULL,
    done_by      TEXT NOT NULL DEFAULT '',
    prev_due     TEXT,
    prev_active  INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_completions_chore ON chore_completions(chore_id, id);

CREATE TABLE IF NOT EXISTS shopping_lists (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    icon        TEXT NOT NULL DEFAULT '🛒',
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shopping_items (
    id          INTEGER PRIMARY KEY,
    list_id     INTEGER NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    quantity    TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'Autre',
    note        TEXT NOT NULL DEFAULT '',
    checked     INTEGER NOT NULL DEFAULT 0,
    checked_at  TEXT,
    added_by    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_list ON shopping_items(list_id);

-- Articles déjà saisis : sert aux suggestions et mémorise le rayon choisi.
CREATE TABLE IF NOT EXISTS item_catalog (
    name       TEXT PRIMARY KEY COLLATE NOCASE,
    category   TEXT NOT NULL DEFAULT 'Autre',
    uses       INTEGER NOT NULL DEFAULT 0,
    last_used  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT '',
    module     TEXT NOT NULL,                     -- calendar | chores | shopping | settings
    action     TEXT NOT NULL,
    entity_id  INTEGER,
    summary    TEXT NOT NULL,
    data       TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_module ON history(module, id);
"""

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM shopping_lists").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO shopping_lists (name, icon, position, created_at) VALUES (?, ?, 0, ?)",
            ("Courses", "🛒", now_iso()),
        )
    conn.commit()


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()


# --- Dates ---------------------------------------------------------------

def local_now():
    """Heure locale du foyer (le serveur PythonAnywhere tourne en UTC)."""
    return datetime.now(ZoneInfo(current_app.config["TIMEZONE"])).replace(tzinfo=None)


def today():
    return local_now().date()


def now_iso():
    return local_now().isoformat(timespec="seconds")


def add_months(value, months):
    """Ajoute des mois en ramenant le jour au dernier jour du mois si besoin (31 janv. -> 28 févr.)."""
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    next_month = date(year + month // 12, month % 12 + 1, 1)
    last_day = (next_month - date(year, month, 1)).days
    return value.replace(year=year, month=month, day=min(value.day, last_day))


def parse_date(value, field):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        abort(400, f"Date invalide pour « {field} ».")


def parse_datetime(value, field):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        abort(400, f"Date/heure invalide pour « {field} ».")
    return parsed.replace(second=0, microsecond=0, tzinfo=None)


# --- Requêtes ------------------------------------------------------------

def payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "Corps JSON attendu.")
    return data


def text(data, key, max_len=200, required=False, default=""):
    value = data.get(key, default)
    value = "" if value is None else str(value).strip()
    if required and not value:
        abort(400, f"Le champ « {key} » est obligatoire.")
    return value[:max_len]


def color(data, key="color", default="#4f46e5"):
    value = data.get(key) or default
    if not COLOR_RE.match(str(value)):
        abort(400, "Couleur invalide.")
    return value


def member_ref(data, key="member_id"):
    value = data.get(key)
    if value in (None, "", 0):
        return None
    try:
        member_id = int(value)
    except (TypeError, ValueError):
        abort(400, "Membre invalide.")
    if not get_db().execute("SELECT 1 FROM members WHERE id = ?", (member_id,)).fetchone():
        abort(400, "Membre inconnu.")
    return member_id


def fetch_one(sql, params=()):
    row = get_db().execute(sql, params).fetchone()
    if row is None:
        abort(404, "Élément introuvable.")
    return dict(row)


def rows(sql, params=()):
    return [dict(r) for r in get_db().execute(sql, params).fetchall()]


# --- Historique ----------------------------------------------------------

def actor():
    """Nom de la personne qui utilise l'appareil (en-tête X-User, encodé URI)."""
    return unquote(request.headers.get("X-User", ""))[:60].strip()


def log(module, action, summary, entity_id=None, data=None):
    get_db().execute(
        "INSERT INTO history (ts, actor, module, action, entity_id, summary, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            now_iso(),
            actor(),
            module,
            action,
            entity_id,
            summary[:300],
            json.dumps(data, ensure_ascii=False, default=str) if data is not None else None,
        ),
    )
