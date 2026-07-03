import json
from typing import List, Dict


def build_dispense_payload(recipe: str, batches: int, dry: List[Dict], wet: List[Dict]) -> str:
    dry_items = [
        {
            "id": int(item.get("id", 0)),
            "g": int(item.get("g", 0)),
            "steps_per_gram": int(item.get("steps_per_gram", 2)),
        }
        for item in dry
    ]
    wet_items = [
        {
            "id": int(item.get("id", 0)),
            "ml": float(item.get("ml", 0)),
            "ms_per_ml": int(item.get("ms_per_ml", 100)),
        }
        for item in wet
    ]
    payload = {
        "cmd": "dispense",
        "recipe": recipe,
        "batches": int(batches),
        "dry": dry_items,
        "wet": wet_items,
    }
    return json.dumps(payload) + "\n"


def build_single_dispense_payload(kind: str, cid: int, amount: float) -> str:
    if str(kind).upper().startswith("D"):
        payload = {
            "cmd": "dispense",
            "dry": [{"id": int(cid), "g": int(amount)}],
            "wet": [],
        }
    else:
        payload = {
            "cmd": "dispense",
            "dry": [],
            "wet": [{"id": int(cid), "ml": float(amount)}],
        }
    return json.dumps(payload) + "\n"


def build_stop_payload() -> str:
    return json.dumps({"cmd": "stop"}) + "\n"


def build_clean_payload() -> str:
    return json.dumps({"cmd": "clean"}) + "\n"


def build_levels_payload() -> str:
    return json.dumps({"cmd": "levels"}) + "\n"
