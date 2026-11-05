"""Accès SQLite, foyers, utilitaires de dates et journal d'historique."""
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
from datetime import date, datetime
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from flask import abort, current_app, g, request
from werkzeug.security import generate_password_hash

SCHEMA_VERSION = 1

# Toutes les données appartiennent à un foyer (household_id). Les articles et les réalisations de tâches
# sont rattachés au foyer via leur liste / leur tâche.
SCHEMA_TABLES = """
CREATE TABLE IF NOT EXISTS households (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    login            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash    TEXT NOT NULL,
    is_admin         INTEGER NOT NULL DEFAULT 0,
    session_version  INTEGER NOT NULL DEFAULT 1,  -- incrémenté pour déconnecter tous les appareils
    ics_token        TEXT NOT NULL UNIQUE,
    city             TEXT NOT NULL DEFAULT '',
    latitude         REAL,
    longitude        REAL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    id            INTEGER PRIMARY KEY,
    household_id  INTEGER NOT NULL,
    name          TEXT NOT NULL,
    color         TEXT NOT NULL DEFAULT '#4f46e5',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id                INTEGER PRIMARY KEY,
    household_id      INTEGER NOT NULL,
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

CREATE TABLE IF NOT EXISTS chores (
    id             INTEGER PRIMARY KEY,
    household_id   INTEGER NOT NULL,
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

CREATE TABLE IF NOT EXISTS shopping_lists (
    id            INTEGER PRIMARY KEY,
    household_id  INTEGER NOT NULL,
    name          TEXT NOT NULL,
    icon          TEXT NOT NULL DEFAULT '🛒',
    position      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
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

-- Articles déjà saisis par le foyer : sert aux suggestions et mémorise le rayon choisi.
CREATE TABLE IF NOT EXISTS item_catalog (
    household_id  INTEGER NOT NULL,
    name          TEXT NOT NULL COLLATE NOCASE,
    category      TEXT NOT NULL DEFAULT 'Autre',
    uses          INTEGER NOT NULL DEFAULT 0,
    last_used     TEXT NOT NULL,
    PRIMARY KEY (household_id, name)
);

CREATE TABLE IF NOT EXISTS history (
    id            INTEGER PRIMARY KEY,
    household_id  INTEGER NOT NULL,
    ts            TEXT NOT NULL,
    actor         TEXT NOT NULL DEFAULT '',
    module        TEXT NOT NULL,                  -- calendar | chores | shopping | settings
    action        TEXT NOT NULL,
    entity_id     INTEGER,
    summary       TEXT NOT NULL,
    data          TEXT
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_members_household ON members(household_id);
CREATE INDEX IF NOT EXISTS idx_events_household ON events(household_id, start);
CREATE INDEX IF NOT EXISTS idx_chores_household ON chores(household_id);
CREATE INDEX IF NOT EXISTS idx_completions_chore ON chore_completions(chore_id, id);
CREATE INDEX IF NOT EXISTS idx_lists_household ON shopping_lists(household_id);
CREATE INDEX IF NOT EXISTS idx_items_list ON shopping_items(list_id);
CREATE INDEX IF NOT EXISTS idx_history_household ON history(household_id, module, id);
"""

# Tables rattachées directement à un foyer (les autres suivent par clé étrangère).
HOUSEHOLD_TABLES = ("members", "events", "chores", "shopping_lists", "item_catalog", "history")

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,39}$")


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


def columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate_v1(conn):
    """Base mono-foyer (v1.1) -> multi-foyers : toutes les données existantes vont au foyer n°1."""
    for table in ("members", "events", "chores", "shopping_lists", "history"):
        if "household_id" not in columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN household_id INTEGER NOT NULL DEFAULT 1")
    if "household_id" not in columns(conn, "item_catalog"):
        conn.executescript("""
            ALTER TABLE item_catalog RENAME TO item_catalog_v0;
            CREATE TABLE item_catalog (
                household_id  INTEGER NOT NULL,
                name          TEXT NOT NULL COLLATE NOCASE,
                category      TEXT NOT NULL DEFAULT 'Autre',
                uses          INTEGER NOT NULL DEFAULT 0,
                last_used     TEXT NOT NULL,
                PRIMARY KEY (household_id, name)
            );
            INSERT INTO item_catalog (household_id, name, category, uses, last_used)
                SELECT 1, name, category, uses, last_used FROM item_catalog_v0;
            DROP TABLE item_catalog_v0;
        """)


def legacy_ics_token():
    """Jeton iCal de la v1.1 : conservé pour que les abonnements existants continuent de fonctionner."""
    secret = current_app.config["SECRET_KEY"].encode()
    return hmac.new(secret, b"home-management-ics", hashlib.sha256).hexdigest()[:32]


def create_household(conn, name, login, password, is_admin=False, ics_token=None):
    cur = conn.execute(
        "INSERT INTO households (name, login, password_hash, is_admin, ics_token, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, login, generate_password_hash(password), int(is_admin), ics_token or secrets.token_hex(16), now_iso()),
    )
    conn.execute(
        "INSERT INTO shopping_lists (household_id, name, icon, position, created_at) VALUES (?, 'Courses', '🛒', 0, ?)",
        (cur.lastrowid, now_iso()),
    )
    return cur.lastrowid


def init_db():
    conn = get_db()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    legacy = version == 0 and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'events'").fetchone() is not None
    if legacy:
        path = current_app.config["DATABASE"]
        backup = path + ".avant-foyers.bak"
        if os.path.exists(path) and not os.path.exists(backup):
            shutil.copy2(path, backup)

    conn.executescript(SCHEMA_TABLES)
    if legacy:
        migrate_v1(conn)
    conn.executescript(SCHEMA_INDEXES)

    if conn.execute("SELECT COUNT(*) FROM households").fetchone()[0] == 0:
        # Premier foyer = administrateur. Son mot de passe initial est APP_PASSWORD (ensuite modifiable dans l'app).
        token = current_app.config["ICS_TOKEN"] or (legacy_ics_token() if legacy else None)
        password = current_app.config["APP_PASSWORD"] or "maison"
        if legacy:
            conn.execute(
                "INSERT INTO households (id, name, login, password_hash, is_admin, ics_token, created_at) "
                "VALUES (1, 'Maison', 'maison', ?, 1, ?, ?)",
                (generate_password_hash(password), token, now_iso()),
            )
            if not conn.execute("SELECT 1 FROM shopping_lists").fetchone():
                conn.execute("INSERT INTO shopping_lists (household_id, name, created_at) VALUES (1, 'Courses', ?)",
                             (now_iso(),))
        else:
            create_household(conn, "Maison", "maison", password, is_admin=True, ics_token=token)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()


# --- Foyer courant -------------------------------------------------------

def household():
    return g.household


def hid():
    return g.household["id"]


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


def login_name(data, key="login"):
    value = text(data, key, 40, required=True).lower()
    if not LOGIN_RE.match(value):
        abort(400, "Identifiant invalide : 3 à 40 caractères, lettres minuscules, chiffres, « . », « - » ou « _ ».")
    return value


def password(data, key="password"):
    value = str(data.get(key) or "")
    if len(value) < 6:
        abort(400, "Le mot de passe doit faire au moins 6 caractères.")
    return value[:200]


def member_ref(data, key="member_id"):
    value = data.get(key)
    if value in (None, "", 0):
        return None
    try:
        member_id = int(value)
    except (TypeError, ValueError):
        abort(400, "Membre invalide.")
    if not get_db().execute("SELECT 1 FROM members WHERE id = ? AND household_id = ?", (member_id, hid())).fetchone():
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
        "INSERT INTO history (household_id, ts, actor, module, action, entity_id, summary, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            hid(),
            now_iso(),
            actor(),
            module,
            action,
            entity_id,
            summary[:300],
            json.dumps(data, ensure_ascii=False, default=str) if data is not None else None,
        ),
    )
