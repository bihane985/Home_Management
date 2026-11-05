"""Courses : listes multiples, articles, rayons et suggestions."""
import re
import unicodedata

from flask import Blueprint, abort, jsonify, request

from .db import actor, fetch_one, get_db, hid, log, now_iso, payload, rows, text

bp = Blueprint("shopping", __name__, url_prefix="/api")

# Ordre « parcours de magasin ».
CATEGORIES = [
    "Fruits & légumes",
    "Boulangerie",
    "Boucherie & poisson",
    "Crèmerie",
    "Épicerie",
    "Surgelés",
    "Boissons",
    "Hygiène & beauté",
    "Entretien",
    "Bébé",
    "Animaux",
    "Autre",
]

KEYWORDS = {
    "Fruits & légumes": "pomme poire banane orange citron fraise framboise raisin kiwi ananas mangue melon pasteque "
    "peche abricot cerise tomate salade laitue carotte courgette aubergine poivron oignon ail echalote "
    "pomme de terre patate poireau brocoli chou epinard haricot champignon concombre avocat radis fruit "
    "legume herbe persil basilic coriandre menthe",
    "Boulangerie": "pain baguette brioche croissant viennoiserie pain de mie biscotte",
    "Boucherie & poisson": "viande boeuf steak poulet dinde porc jambon lardon saucisse veau agneau poisson "
    "saumon thon cabillaud crevette merguez escalope roti charcuterie",
    "Crèmerie": "lait beurre creme yaourt yogourt fromage oeuf oeufs emmental comte mozzarella parmesan "
    "camembert chevre skyr",
    "Épicerie": "pates riz farine sucre sel poivre huile vinaigre cafe the chocolat cereale biscuit confiture "
    "miel conserve sauce moutarde ketchup mayonnaise epice semoule lentille quinoa gateau bonbon chips",
    "Surgelés": "surgele glace pizza frites congele",
    "Boissons": "eau jus soda coca biere vin sirop limonade",
    "Hygiène & beauté": "shampoing shampooing savon gel douche dentifrice brosse a dent deodorant coton "
    "rasoir papier toilette mouchoir creme solaire",
    "Entretien": "lessive adoucissant liquide vaisselle eponge javel nettoyant sac poubelle essuie tout "
    "pastille lave vaisselle",
    "Bébé": "couche lingette bebe biberon petit pot",
    "Animaux": "croquette litiere patee chat chien",
}


def normalize(value):
    value = unicodedata.normalize("NFKD", value.lower())
    return "".join(c for c in value if not unicodedata.combining(c)).replace("œ", "oe")


def guess_category(name):
    known = get_db().execute("SELECT category FROM item_catalog WHERE household_id = ? AND name = ?",
                             (hid(), name)).fetchone()
    if known:
        return known["category"]
    # Mots du nom, au singulier approximatif ; le premier mot reconnu l'emporte (« jus d'orange » -> Boissons).
    words = [w.rstrip("sx") if len(w) > 3 else w for w in re.split(r"\W+", normalize(name)) if w]
    best, best_pos = "Autre", len(words)
    for category, keywords in KEYWORDS.items():
        for keyword in keywords.split():
            if len(keyword) >= 3 and keyword in words and words.index(keyword) < best_pos:
                best, best_pos = category, words.index(keyword)
    return best


def category_of(data, name):
    category = text(data, "category", 60)
    if not category:
        return guess_category(name)
    if category not in CATEGORIES:
        abort(400, "Rayon invalide.")
    return category


def remember(name, category):
    get_db().execute(
        "INSERT INTO item_catalog (household_id, name, category, uses, last_used) VALUES (?, ?, ?, 1, ?) "
        "ON CONFLICT(household_id, name) DO UPDATE SET category = excluded.category, uses = uses + 1, "
        "last_used = excluded.last_used",
        (hid(), name, category, now_iso()),
    )


def list_row(list_id):
    return fetch_one("SELECT * FROM shopping_lists WHERE id = ? AND household_id = ?", (list_id, hid()))


def item_row(item_id):
    """Article, à condition qu'il soit sur une liste du foyer."""
    return fetch_one(
        "SELECT i.* FROM shopping_items i JOIN shopping_lists l ON l.id = i.list_id "
        "WHERE i.id = ? AND l.household_id = ?",
        (item_id, hid()),
    )


# --- Listes --------------------------------------------------------------

@bp.get("/lists")
def get_lists():
    return jsonify(
        lists=rows(
            "SELECT l.id, l.name, l.icon, l.position, "
            "COALESCE(SUM(i.checked = 0), 0) AS remaining, COUNT(i.id) AS total "
            "FROM shopping_lists l LEFT JOIN shopping_items i ON i.list_id = l.id "
            "WHERE l.household_id = ? GROUP BY l.id ORDER BY l.position, l.id",
            (hid(),),
        ),
        categories=CATEGORIES,
    )


@bp.post("/lists")
def create_list():
    data = payload()
    name, icon = text(data, "name", 60, required=True), text(data, "icon", 8) or "🛒"
    conn = get_db()
    position = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM shopping_lists WHERE household_id = ?",
                            (hid(),)).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO shopping_lists (household_id, name, icon, position, created_at) VALUES (?, ?, ?, ?, ?)",
        (hid(), name, icon, position, now_iso()),
    )
    log("shopping", "create", f"Liste créée : {name}", cur.lastrowid)
    conn.commit()
    return jsonify(list_row(cur.lastrowid)), 201


@bp.put("/lists/<int:list_id>")
def update_list(list_id):
    before = list_row(list_id)
    data = payload()
    name, icon = text(data, "name", 60, required=True), text(data, "icon", 8) or before["icon"]
    conn = get_db()
    conn.execute("UPDATE shopping_lists SET name = ?, icon = ? WHERE id = ?", (name, icon, list_id))
    if name != before["name"]:
        log("shopping", "update", f"Liste renommée : {before['name']} → {name}", list_id)
    conn.commit()
    return jsonify(list_row(list_id))


@bp.delete("/lists/<int:list_id>")
def delete_list(list_id):
    shopping_list = list_row(list_id)
    conn = get_db()
    if conn.execute("SELECT COUNT(*) FROM shopping_lists WHERE household_id = ?", (hid(),)).fetchone()[0] <= 1:
        abort(409, "Il faut garder au moins une liste.")
    items = rows("SELECT name, quantity, checked FROM shopping_items WHERE list_id = ?", (list_id,))
    conn.execute("DELETE FROM shopping_lists WHERE id = ?", (list_id,))
    log("shopping", "delete", f"Liste supprimée : {shopping_list['name']} ({len(items)} article(s))", list_id,
        {"list": shopping_list, "items": items})
    conn.commit()
    return jsonify(ok=True)


# --- Articles ------------------------------------------------------------

@bp.get("/lists/<int:list_id>/items")
def get_items(list_id):
    list_row(list_id)
    return jsonify(rows("SELECT * FROM shopping_items WHERE list_id = ? ORDER BY checked, id", (list_id,)))


@bp.post("/items")
def add_items():
    """Ajoute un article sur une ou plusieurs listes (list_ids)."""
    data = payload()
    list_ids = data.get("list_ids") or ([data["list_id"]] if data.get("list_id") else [])
    try:
        list_ids = list(dict.fromkeys(int(i) for i in list_ids))
    except (TypeError, ValueError):
        abort(400, "Listes invalides.")
    if not list_ids:
        abort(400, "Choisissez au moins une liste.")
    lists = [list_row(i) for i in list_ids]

    name = text(data, "name", 100, required=True)
    name = name[0].upper() + name[1:]
    quantity, note = text(data, "quantity", 40), text(data, "note", 200)
    category = category_of(data, name)
    now, who = now_iso(), actor()
    conn = get_db()
    created = []
    for shopping_list in lists:
        # Déjà sur la liste : on le réutilise (et on le remet à acheter s'il était coché) plutôt que de le dupliquer.
        existing = next(
            (r for r in conn.execute("SELECT * FROM shopping_items WHERE list_id = ?", (shopping_list["id"],))
             if r["name"].casefold() == name.casefold()),
            None,
        )
        if existing:
            conn.execute(
                "UPDATE shopping_items SET checked = 0, checked_at = NULL, quantity = ?, note = ?, updated_at = ? "
                "WHERE id = ?",
                (quantity or existing["quantity"], note or existing["note"], now, existing["id"]),
            )
            created.append(existing["id"])
            continue
        cur = conn.execute(
            "INSERT INTO shopping_items (list_id, name, quantity, category, note, added_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (shopping_list["id"], name, quantity, category, note, who, now, now),
        )
        created.append(cur.lastrowid)
    remember(name, category)
    label = f"{name} ({quantity})" if quantity else name
    log("shopping", "create", f"Article ajouté : {label} → {', '.join(l['name'] for l in lists)}", created[0])
    conn.commit()
    placeholders = ", ".join("?" * len(created))
    return jsonify(rows(f"SELECT * FROM shopping_items WHERE id IN ({placeholders})", created)), 201


@bp.put("/items/<int:item_id>")
def update_item(item_id):
    item = item_row(item_id)
    data = payload()
    conn = get_db()
    now = now_iso()

    if set(data) == {"checked"}:
        checked = bool(data["checked"])
        conn.execute(
            "UPDATE shopping_items SET checked = ?, checked_at = ?, updated_at = ? WHERE id = ?",
            (int(checked), now if checked else None, now, item_id),
        )
        log("shopping", "check" if checked else "uncheck",
            f"{'Acheté' if checked else 'Remis dans la liste'} : {item['name']}", item_id)
    else:
        name = text(data, "name", 100, required=True)
        category = category_of(data, name)
        list_id = int(data.get("list_id") or item["list_id"])
        target = list_row(list_id)
        conn.execute(
            "UPDATE shopping_items SET name = ?, quantity = ?, category = ?, note = ?, list_id = ?, updated_at = ? "
            "WHERE id = ?",
            (name, text(data, "quantity", 40), category, text(data, "note", 200), list_id, now, item_id),
        )
        remember(name, category)
        summary = f"Article modifié : {name}"
        if list_id != item["list_id"]:
            summary += f" (déplacé vers {target['name']})"
        log("shopping", "update", summary, item_id, {"before": item})
    conn.commit()
    return jsonify(item_row(item_id))


@bp.delete("/items/<int:item_id>")
def delete_item(item_id):
    item = item_row(item_id)
    conn = get_db()
    conn.execute("DELETE FROM shopping_items WHERE id = ?", (item_id,))
    log("shopping", "delete", f"Article retiré : {item['name']}", item_id, item)
    conn.commit()
    return jsonify(ok=True)


@bp.post("/lists/<int:list_id>/clear-checked")
def clear_checked(list_id):
    shopping_list = list_row(list_id)
    conn = get_db()
    names = [r["name"] for r in conn.execute(
        "SELECT name FROM shopping_items WHERE list_id = ? AND checked = 1", (list_id,))]
    if names:
        conn.execute("DELETE FROM shopping_items WHERE list_id = ? AND checked = 1", (list_id,))
        log("shopping", "clear", f"{len(names)} article(s) acheté(s) retiré(s) de {shopping_list['name']}", list_id,
            {"items": names})
        conn.commit()
    return jsonify(removed=len(names))


@bp.get("/catalog")
def catalog():
    """Suggestions : articles déjà achetés, les plus fréquents d'abord."""
    query = request.args.get("q", "").strip()
    limit = min(request.args.get("limit", 10, type=int), 30)
    if query:
        items = rows(
            "SELECT name, category, uses FROM item_catalog WHERE household_id = ? AND name LIKE ? "
            "ORDER BY uses DESC, name LIMIT ?",
            (hid(), f"%{query}%", limit),
        )
    else:
        items = rows("SELECT name, category, uses FROM item_catalog WHERE household_id = ? "
                     "ORDER BY uses DESC, last_used DESC LIMIT ?", (hid(), limit))
    return jsonify(items)
