import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


@dataclass
class Container:
    cid: int
    name: str
    steps_per_gram: int = 2
    capacity_g: int = 1000
    remaining_g: int = 1000


@dataclass
class WetContainer:
    cid: int
    name: str
    ms_per_ml: int
    capacity_ml: int
    remaining_ml: int


@dataclass
class Recipe:
    rid: int
    name: str


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        "CREATE TABLE IF NOT EXISTS dry_containers (id INTEGER PRIMARY KEY, name TEXT, steps_per_gram INTEGER, capacity_g INTEGER, remaining_g INTEGER)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS wet_containers (id INTEGER PRIMARY KEY, name TEXT, ms_per_ml INTEGER, capacity_ml INTEGER, remaining_ml INTEGER)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS recipes (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS recipe_dry (recipe_id INTEGER, dry_id INTEGER, grams INTEGER)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS recipe_wet (recipe_id INTEGER, wet_id INTEGER, ml INTEGER)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, recipe TEXT, batches INTEGER, status TEXT)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
    )

    # Ensure wet columns exist for older databases
    columns = [row[1] for row in cur.execute("PRAGMA table_info(wet_containers)").fetchall()]
    if "capacity_ml" not in columns:
        cur.execute("ALTER TABLE wet_containers ADD COLUMN capacity_ml INTEGER")
    if "remaining_ml" not in columns:
        cur.execute("ALTER TABLE wet_containers ADD COLUMN remaining_ml INTEGER")

    # Ensure dry columns exist for older databases
    columns = [row[1] for row in cur.execute("PRAGMA table_info(dry_containers)").fetchall()]
    if "steps_per_gram" not in columns:
        cur.execute("ALTER TABLE dry_containers ADD COLUMN steps_per_gram INTEGER")
    if "capacity_g" not in columns:
        cur.execute("ALTER TABLE dry_containers ADD COLUMN capacity_g INTEGER")
    if "remaining_g" not in columns:
        cur.execute("ALTER TABLE dry_containers ADD COLUMN remaining_g INTEGER")

    # Seed/repair dry containers - ensure all 6 exist
    for i in range(1, 7):
        existing = cur.execute("SELECT COUNT(*) FROM dry_containers WHERE id=?", (i,)).fetchone()[0]
        if existing == 0:
            cur.execute(
                "INSERT INTO dry_containers(id, name, steps_per_gram, capacity_g, remaining_g) VALUES(?, ?, ?, ?, ?)",
                (i, f"DRY {i}", 2, 1000, 1000),
            )
    cur.execute("UPDATE dry_containers SET steps_per_gram=COALESCE(steps_per_gram, 2)")
    cur.execute("UPDATE dry_containers SET capacity_g=COALESCE(capacity_g, 1000)")
    cur.execute("UPDATE dry_containers SET remaining_g=COALESCE(remaining_g, capacity_g, 1000)")

    # Seed/repair wet containers - ensure all 4 exist
    for i in range(1, 5):
        existing = cur.execute("SELECT COUNT(*) FROM wet_containers WHERE id=?", (i,)).fetchone()[0]
        if existing == 0:
            cur.execute(
                "INSERT INTO wet_containers(id, name, ms_per_ml, capacity_ml, remaining_ml) VALUES(?, ?, ?, ?, ?)",
                (i, f"WET {i}", 100, 1000, 1000),
            )
    cur.execute("UPDATE wet_containers SET capacity_ml=COALESCE(capacity_ml, 1000)")
    cur.execute("UPDATE wet_containers SET remaining_ml=COALESCE(remaining_ml, capacity_ml, 1000)")
    cur.execute("UPDATE wet_containers SET ms_per_ml=COALESCE(ms_per_ml, 100)")

    if cur.execute("SELECT COUNT(*) FROM settings WHERE key='security_key'").fetchone()[0] == 0:
        cur.execute("INSERT INTO settings(key, value) VALUES('security_key', '1234')")

    conn.commit()
    conn.close()


def get_dry_containers(db_path: Path) -> List[Container]:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("SELECT id, name, steps_per_gram, capacity_g, remaining_g FROM dry_containers ORDER BY id").fetchall()
    conn.close()
    return [
        Container(
            cid=row[0],
            name=row[1],
            steps_per_gram=row[2] if row[2] is not None else 2,
            capacity_g=row[3] if row[3] is not None else 1000,
            remaining_g=row[4] if row[4] is not None else (row[3] if row[3] is not None else 1000),
        )
        for row in rows
    ]


def get_wet_containers(db_path: Path) -> List[WetContainer]:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, name, ms_per_ml, capacity_ml, remaining_ml FROM wet_containers ORDER BY id"
    ).fetchall()
    conn.close()
    return [WetContainer(cid=row[0], name=row[1], ms_per_ml=row[2], capacity_ml=row[3], remaining_ml=row[4]) for row in rows]


def set_dry_containers(db_path: Path, items: List[Tuple[str, int, int, int]]) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM dry_containers")
    for i, (name, steps_per_gram, capacity_g, remaining_g) in enumerate(items, start=1):
        cur.execute(
            "INSERT INTO dry_containers(id, name, steps_per_gram, capacity_g, remaining_g) VALUES(?, ?, ?, ?, ?)",
            (i, name, steps_per_gram, capacity_g, remaining_g),
        )
    conn.commit()
    conn.close()


def apply_dry_dispense(db_path: Path, dry_items: List[Tuple[int, int]]) -> None:
    if not dry_items:
        return
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    for cid, used_g in dry_items:
        row = cur.execute(
            "SELECT capacity_g, remaining_g FROM dry_containers WHERE id=?",
            (cid,),
        ).fetchone()
        if not row:
            continue
        capacity_g, remaining_g = row
        if capacity_g is None:
            capacity_g = 1000
        if remaining_g is None:
            remaining_g = capacity_g
        new_remaining = max(0, int(remaining_g) - int(used_g))
        new_remaining = min(new_remaining, int(capacity_g))
        cur.execute(
            "UPDATE dry_containers SET remaining_g=? WHERE id=?",
            (new_remaining, cid),
        )
    conn.commit()
    conn.close()


def set_wet_containers(db_path: Path, items: List[Tuple[str, int, int, int]]) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM wet_containers")
    for i, (name, ms_per_ml, capacity_ml, remaining_ml) in enumerate(items, start=1):
        cur.execute(
            "INSERT INTO wet_containers(id, name, ms_per_ml, capacity_ml, remaining_ml) VALUES(?, ?, ?, ?, ?)",
            (i, name, ms_per_ml, capacity_ml, remaining_ml),
        )
    conn.commit()
    conn.close()


def list_recipes(db_path: Path) -> List[Recipe]:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("SELECT id, name FROM recipes ORDER BY name").fetchall()
    conn.close()
    return [Recipe(rid=row[0], name=row[1]) for row in rows]


def get_recipe_detail(db_path: Path, recipe_id: int):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    recipe = cur.execute("SELECT id, name FROM recipes WHERE id=?", (recipe_id,)).fetchone()
    dry = cur.execute("SELECT dry_id, grams FROM recipe_dry WHERE recipe_id=? ORDER BY dry_id", (recipe_id,)).fetchall()
    wet = cur.execute("SELECT wet_id, ml FROM recipe_wet WHERE recipe_id=? ORDER BY wet_id", (recipe_id,)).fetchall()
    conn.close()
    return recipe, dry, wet


def save_recipe(db_path: Path, name: str, dry_grams: List[int], wet_ml: List[int]) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO recipes(name) VALUES(?)", (name,))
    rid = cur.execute("SELECT id FROM recipes WHERE name=?", (name,)).fetchone()[0]

    cur.execute("DELETE FROM recipe_dry WHERE recipe_id=?", (rid,))
    for i, grams in enumerate(dry_grams, start=1):
        cur.execute("INSERT INTO recipe_dry(recipe_id, dry_id, grams) VALUES(?, ?, ?)", (rid, i, grams))

    cur.execute("DELETE FROM recipe_wet WHERE recipe_id=?", (rid,))
    for i, ml in enumerate(wet_ml, start=1):
        cur.execute("INSERT INTO recipe_wet(recipe_id, wet_id, ml) VALUES(?, ?, ?)", (rid, i, ml))

    conn.commit()
    conn.close()


def rename_recipe(db_path: Path, recipe_id: int, new_name: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE recipes SET name=? WHERE id=?", (new_name, recipe_id))
    conn.commit()
    conn.close()


def update_recipe(db_path: Path, recipe_id: int, name: str, dry_grams: List[int], wet_ml: List[int]) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE recipes SET name=? WHERE id=?", (name, recipe_id))

    cur.execute("DELETE FROM recipe_dry WHERE recipe_id=?", (recipe_id,))
    for i, grams in enumerate(dry_grams, start=1):
        cur.execute("INSERT INTO recipe_dry(recipe_id, dry_id, grams) VALUES(?, ?, ?)", (recipe_id, i, grams))

    cur.execute("DELETE FROM recipe_wet WHERE recipe_id=?", (recipe_id,))
    for i, ml in enumerate(wet_ml, start=1):
        cur.execute("INSERT INTO recipe_wet(recipe_id, wet_id, ml) VALUES(?, ?, ?)", (recipe_id, i, ml))

    conn.commit()
    conn.close()


def delete_recipe(db_path: Path, recipe_id: int) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM recipe_dry WHERE recipe_id=?", (recipe_id,))
    cur.execute("DELETE FROM recipe_wet WHERE recipe_id=?", (recipe_id,))
    cur.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))
    conn.commit()
    conn.close()


def log_dispense(db_path: Path, ts: str, recipe: str, batches: int, status: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("INSERT INTO logs(ts, recipe, batches, status) VALUES(?, ?, ?, ?)", (ts, recipe, batches, status))
    conn.commit()
    conn.close()


def get_logs(db_path: Path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("SELECT ts, recipe, batches, status FROM logs ORDER BY id DESC").fetchall()
    conn.close()
    return rows


def get_setting(db_path: Path, key: str, default: str = "") -> str:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    row = cur.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else default


def set_setting(db_path: Path, key: str, value: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, value))
    conn.commit()
    conn.close()


def reset_factory(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM recipes")
    cur.execute("DELETE FROM recipe_dry")
    cur.execute("DELETE FROM recipe_wet")
    cur.execute("DELETE FROM logs")
    cur.execute("DELETE FROM dry_containers")
    cur.execute("DELETE FROM wet_containers")
    for i in range(1, 7):
        cur.execute(
            "INSERT INTO dry_containers(id, name, steps_per_gram, capacity_g, remaining_g) VALUES(?, ?, ?, ?, ?)",
            (i, f"DRY {i}", 2, 1000, 1000),
        )
    for i in range(1, 5):
        cur.execute(
            "INSERT INTO wet_containers(id, name, ms_per_ml, capacity_ml, remaining_ml) VALUES(?, ?, ?, ?, ?)",
            (i, f"WET {i}", 100, 1000, 1000),
        )
    cur.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('security_key', '1234')")
    conn.commit()
    conn.close()


def apply_wet_dispense(db_path: Path, wet_items: List[Tuple[int, int]]) -> None:
    if not wet_items:
        return
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    for cid, used_ml in wet_items:
        row = cur.execute(
            "SELECT capacity_ml, remaining_ml FROM wet_containers WHERE id=?",
            (cid,),
        ).fetchone()
        if not row:
            continue
        capacity_ml, remaining_ml = row
        if capacity_ml is None:
            capacity_ml = 1000
        if remaining_ml is None:
            remaining_ml = capacity_ml
        new_remaining = max(0, int(remaining_ml) - int(used_ml))
        new_remaining = min(new_remaining, int(capacity_ml))
        cur.execute(
            "UPDATE wet_containers SET remaining_ml=? WHERE id=?",
            (new_remaining, cid),
        )
    conn.commit()
    conn.close()
